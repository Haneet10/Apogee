"""The flight computer's senses: noisy sensors and a Kalman filter.

A real airbrake controller never sees the true state of the rocket. It reads a
barometer and an accelerometer, both noisy, and has to estimate how high and
how fast it is from them. This module models that chain:

* :class:`FlightComputerSettings`: what the flight computer is told before
  launch (the target apogee, how fast it runs, how noisy its sensors are), read
  from the ``flight_computer:`` section of a rocket file.
* :class:`Sensors`: turns the simulator's true altitude and acceleration into
  readings with Gaussian noise, from a seeded generator so a flight is
  reproducible.
* :class:`AltitudeFilter`: a three-state linear Kalman filter (altitude,
  vertical velocity, vertical acceleration) that fuses the two sensors.

Sensors
-------
The barometer reports altitude above the pad with white noise ``σ_baro``
(typical MEMS barometers such as the MS5611 give 0.5-2 m RMS after the
pressure-to-altitude conversion). The accelerometer, combined with attitude
from the gyros, reports vertical acceleration over the ground with white noise
``σ_accel``. Gyro-integrated attitude is taken as exact over the ~30 s ascent;
its drift (tenths of a degree) changes the vertical projection by far less
than the accelerometer noise.

Kalman filter
-------------
State ``x = [h, v, a]`` evolves as a constant-acceleration model driven by
white jerk of spectral density ``q``::

        ⎡1  Δt  Δt²/2⎤            ⎡Δt⁵/20  Δt⁴/8  Δt³/6⎤
    F = ⎢0   1   Δt  ⎥,   Q = q · ⎢Δt⁴/8   Δt³/3  Δt²/2⎥
        ⎣0   0    1  ⎦            ⎣Δt³/6   Δt²/2   Δt  ⎦

and both sensors are measured directly, ``z = [h_baro, a_accel] = H x + w``
with ``H = [[1, 0, 0], [0, 0, 1]]`` and ``R = diag(σ_baro², σ_accel²)``. The
standard predict / update equations follow (R. E. Kalman, *A new approach to
linear filtering and prediction problems*, 1960; the discrete white-jerk
``Q`` is from Y. Bar-Shalom et al., *Estimation with Applications to Tracking
and Navigation*, 2001, §6.3.3). Velocity is never measured: the filter infers
it from how altitude and acceleration agree, which is exactly why it beats
differentiating the barometer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

__all__ = ["AltitudeFilter", "FlightComputerSettings", "Sensors"]

Vector = NDArray[np.float64]


@dataclass(frozen=True)
class FlightComputerSettings:
    """What the flight computer knows before launch.

    Attributes:
        target_apogee: Altitude above the pad to stop at [m], or ``None`` to
            fly with the airbrakes stowed.
        rate: Sensor sampling and filter rate [Hz].
        control_rate: How often the controller re-plans the airbrakes [Hz].
        barometer_noise: Barometric altitude noise, one standard deviation [m].
        accelerometer_noise: Vertical acceleration noise, one standard
            deviation [m/s²].
        jerk_noise: Kalman filter process noise ``q`` [m²/s⁵]; larger trusts
            the sensors more and the motion model less.
        lockout: Seconds after burnout before the airbrakes may open (lets
            the flight computer confirm burnout and the rocket settle).
    """

    target_apogee: float | None = None
    rate: float = 50.0
    control_rate: float = 10.0
    barometer_noise: float = 1.5
    accelerometer_noise: float = 0.3
    jerk_noise: float = 20.0
    lockout: float = 1.0

    def __post_init__(self) -> None:
        if self.target_apogee is not None and not self.target_apogee > 0.0:
            raise ValueError(f"target apogee must be above the pad, got {self.target_apogee}")
        for label, value in (
            ("rate", self.rate),
            ("control_rate", self.control_rate),
            ("jerk_noise", self.jerk_noise),
        ):
            if not (value > 0.0 and math.isfinite(value)):
                raise ValueError(f"flight computer {label} must be positive, got {value}")
        for label, value in (
            ("barometer_noise", self.barometer_noise),
            ("accelerometer_noise", self.accelerometer_noise),
            ("lockout", self.lockout),
        ):
            if not (value >= 0.0 and math.isfinite(value)):
                raise ValueError(f"flight computer {label} must be zero or more, got {value}")
        if self.control_rate > self.rate:
            raise ValueError("the control rate cannot be faster than the sensor rate")


class Sensors:
    """A barometer and an accelerometer with white Gaussian noise.

    >>> s = Sensors(FlightComputerSettings(barometer_noise=0.0, accelerometer_noise=0.0))
    >>> s.read(1234.5, -9.81)
    (1234.5, -9.81)
    """

    def __init__(self, settings: FlightComputerSettings, seed: int = 0) -> None:
        self.settings = settings
        self._rng = np.random.default_rng(seed)

    def read(self, altitude: float, vertical_acceleration: float) -> tuple[float, float]:
        """``(barometric altitude [m], vertical acceleration [m/s²])`` as measured."""
        noise = self._rng.standard_normal(2)
        return (
            altitude + self.settings.barometer_noise * float(noise[0]),
            vertical_acceleration + self.settings.accelerometer_noise * float(noise[1]),
        )


class AltitudeFilter:
    """Three-state Kalman filter fusing barometric altitude and vertical acceleration.

    It starts on the pad, at rest, and is stepped at a fixed rate with
    :meth:`step`. The estimate is :attr:`x` (``[h, v, a]``) with covariance
    :attr:`p`.

    >>> f = AltitudeFilter(FlightComputerSettings(), dt=0.02)
    >>> for k in range(500):                     # climbing steadily at 100 m/s
    ...     _ = f.step(100.0 * 0.02 * (k + 1), 0.0)
    >>> abs(f.velocity - 100.0) < 2.0           # never measured, only inferred
    True
    """

    _H = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])

    def __init__(self, settings: FlightComputerSettings, dt: float) -> None:
        if not dt > 0.0:
            raise ValueError(f"filter time step must be positive, got {dt}")
        self.dt = dt
        self._f = np.array([[1.0, dt, 0.5 * dt * dt], [0.0, 1.0, dt], [0.0, 0.0, 1.0]])
        q = settings.jerk_noise
        self._q = q * np.array(
            [
                [dt**5 / 20.0, dt**4 / 8.0, dt**3 / 6.0],
                [dt**4 / 8.0, dt**3 / 3.0, dt**2 / 2.0],
                [dt**3 / 6.0, dt**2 / 2.0, dt],
            ]
        )
        # A perfect sensor would make R singular; a floor keeps the update well posed.
        self._r = np.diag(
            [
                max(settings.barometer_noise, 1e-3) ** 2,
                max(settings.accelerometer_noise, 1e-3) ** 2,
            ]
        )
        self.x: Vector = np.zeros(3)
        self.p: Vector = np.diag([1.0, 0.1, 0.1])

    @property
    def altitude(self) -> float:
        """Estimated height above the pad [m]."""
        return float(self.x[0])

    @property
    def velocity(self) -> float:
        """Estimated vertical velocity [m/s]."""
        return float(self.x[1])

    @property
    def acceleration(self) -> float:
        """Estimated vertical acceleration [m/s²]."""
        return float(self.x[2])

    def step(self, baro_altitude: float, vertical_acceleration: float) -> Vector:
        """Predict one time step ahead, then correct with the two readings."""
        f, h = self._f, self._H
        x = f @ self.x
        p = f @ self.p @ f.T + self._q
        z = np.array([baro_altitude, vertical_acceleration])
        s = h @ p @ h.T + self._r
        k = p @ h.T @ np.linalg.inv(s)
        self.x = x + k @ (z - h @ x)
        # Joseph form: stays symmetric and positive definite under round-off.
        i_kh = np.eye(3) - k @ h
        self.p = i_kh @ p @ i_kh.T + k @ self._r @ k.T
        return self.x
