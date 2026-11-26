"""Active airbrake control: fly to an exact apogee.

Competition rockets are scored on how close they come to a target altitude
(Spaceport America Cup and Launch Canada both reward it), and no passive rocket
can do that reliably: a few percent of scatter in motor impulse, mass and drag
moves Kestrel's apogee by well over a hundred metres. An airbrake module fixes
it in flight. :class:`FlightComputer` is the software that would run on the
rocket, closed around the 6-DOF simulator by :func:`apogee.flight.simulate`::

    >>> from apogee import load_rocket, simulate
    >>> from apogee.cli import example_path
    >>> ab = load_rocket(example_path("kestrel-airbrakes.yaml"))
    >>> flight = simulate(ab, dt=0.02)
    >>> abs(flight.apogee - 4400.0) < 15.0             # metres from the target
    True

The loop, every tick
--------------------
1. **Sense** (50 Hz). :class:`~apogee.avionics.Sensors` read the true altitude
   and vertical acceleration with noise.
2. **Estimate** (50 Hz). :class:`~apogee.avionics.AltitudeFilter`, a Kalman
   filter, turns the readings into altitude, vertical velocity and
   acceleration. Burnout is detected from the estimate (acceleration turning
   negative after liftoff), not read off a clock.
3. **Learn the drag** (10 Hz). While coasting the only forces are gravity and
   drag, so the measured deceleration gives the rocket's real drag. Its ratio
   to the pre-flight drag model is low-pass filtered into a correction factor
   ``k``; a rocket that came out draggier, or heavier, than designed is
   noticed within a second or two.
4. **Predict** (10 Hz). :class:`ApogeePredictor` integrates a point-mass
   coast from the current estimate to the top, with the drag model times
   ``k`` plus the airbrakes held at a trial setting.
5. **Decide** (10 Hz). Predicted apogee falls monotonically as the brakes
   open, so the setting that lands exactly on the target is a one-dimensional
   root. It is found by regula falsi (Illinois variant) to half a metre. If
   even fully stowed brakes fall short, they stay stowed; if fully open still
   overshoots, they open all the way. Two rules keep the panels calm instead
   of chasing sensor noise: the setting is only re-planned when the current
   one is predicted to miss by more than 2 m, and it is frozen once the
   rocket is rising slower than 30 m/s, where the panels have almost no
   authority left.
6. **Actuate**. The servo moves the panels towards the command no faster
   than the module's ``travel_time`` allows. The panels open only after the
   burnout ``lockout`` and below ``max_mach``, and retract when the flight
   computer sees the rocket start to fall.

This is model-predictive control with a one-step horizon, the scheme most
university airbrake teams fly, and it re-plans from fresh sensor data ten
times a second, so model errors are corrected as the rocket gets closer to
apogee and the prediction gets shorter.

The predictor's model
---------------------
A point mass in the vertical plane of flight with the burnout mass of the
rocket *as designed*, gravity ``g(h)``, standard-atmosphere density and speed
of sound, and drag ``½ ρ V² (k · CdA_rocket(M) + δ · cd · A_brakes)``. The
rocket's ``Cd(M)`` comes from :class:`~apogee.aero.Aerodynamics` before
launch. The flight path angle comes from the attitude (gyros). Wind and
the true mass and drag are not known; the drag correction absorbs most of
the difference. The predictor uses RK4 with a 0.2 s step, accurate to
centimetres over a 20 s coast, so its error is all model, none numerics.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import numpy as np
from numpy.typing import NDArray

from apogee.aero import Aerodynamics
from apogee.atmosphere import atmosphere
from apogee.avionics import AltitudeFilter, FlightComputerSettings, Sensors
from apogee.components import Airbrakes
from apogee.earth import G0, gravity
from apogee.environment import LaunchSite
from apogee.rocket import Rocket

__all__ = ["AirbrakeLog", "ApogeePredictor", "FlightComputer"]

Vector = NDArray[np.float64]

_PREDICT_DT = 0.2
_TABLE_STEP = 25.0  # m
_TABLE_TOP = 30000.0  # m above the pad
_MACH_STEP = 0.02
_MACH_TOP = 3.0
_MIN_COS = 0.2  # flight paths flatter than ~78° from vertical are treated as 78°
_LIFTOFF_ACCEL = 2.0 * G0
_LEARN_SPEED = 60.0  # m/s; below this drag is too small to measure well
_LEARN_GAIN = 0.15  # per control tick; ~0.7 s time constant at 10 Hz
_K_LIMITS = (0.5, 2.0)
_DEADBAND = 2.0  # m; re-plan only when the current setting is predicted this far off
_HOLD_SPEED = 30.0  # m/s; below this the setting is frozen


class ApogeePredictor:
    """Where will the rocket top out from here, with the brakes at ``δ``?

    Built once before launch from the rocket as designed; :meth:`apogee` is
    then cheap enough (pure Python, ~100 RK4 steps) to call many times per
    control tick.

    >>> from apogee import load_rocket
    >>> from apogee.cli import example_path
    >>> p = ApogeePredictor(load_rocket(example_path("kestrel-airbrakes.yaml")), LaunchSite())
    >>> stowed = p.apogee(1000.0, 250.0, 0.05, deployment=0.0)
    >>> opened = p.apogee(1000.0, 250.0, 0.05, deployment=1.0)
    >>> bool(opened < stowed - 500.0)
    True
    """

    def __init__(self, rocket: Rocket, site: LaunchSite) -> None:
        nominal = replace(rocket, dry_mass_scale=1.0, drag_scale=1.0)
        aero = Aerodynamics(nominal)
        area = aero.reference_area
        mid = atmosphere(site.altitude + 1500.0)
        machs = np.arange(0.0, _MACH_TOP + 1e-9, _MACH_STEP)
        self._cda = [
            area
            * aero.drag(
                max(float(m), 0.01),
                thrusting=False,
                speed_of_sound=float(mid.speed_of_sound),
                kinematic_viscosity=float(mid.kinematic_viscosity),
            ).total
            for m in machs
        ]
        heights = np.arange(0.0, _TABLE_TOP + 1e-9, _TABLE_STEP)
        alt = np.clip(site.altitude + heights, -5000.0, 86000.0)
        air = atmosphere(alt)
        self._rho = [float(v) for v in air.density]
        self._sound = [float(v) for v in air.speed_of_sound]
        self._g = [float(v) for v in gravity(alt)]
        self.mass = nominal.mass_properties(nominal.burnout_time).mass
        brakes = rocket.airbrakes
        self.brake_cda = brakes.drag_area if brakes is not None else 0.0

    # --- Tables ----------------------------------------------------------------

    @staticmethod
    def _lookup(table: list[float], x: float, step: float) -> float:
        u = x / step
        if u <= 0.0:
            return table[0]
        i = int(u)
        if i >= len(table) - 1:
            return table[-1]
        f = u - i
        return table[i] + f * (table[i + 1] - table[i])

    def air(self, height: float) -> tuple[float, float, float]:
        """``(density, speed of sound, gravity)`` at ``height`` above the pad."""
        return (
            self._lookup(self._rho, height, _TABLE_STEP),
            self._lookup(self._sound, height, _TABLE_STEP),
            self._lookup(self._g, height, _TABLE_STEP),
        )

    def rocket_cda(self, mach: float) -> float:
        """Drag area ``Cd · A`` of the coasting rocket, brakes stowed [m²]."""
        return self._lookup(self._cda, mach, _MACH_STEP)

    # --- Prediction --------------------------------------------------------------

    def apogee(
        self,
        height: float,
        v_up: float,
        tilt: float,
        *,
        deployment: float,
        drag_factor: float = 1.0,
    ) -> float:
        """Predicted apogee [m above the pad].

        Args:
            height: Current height above the pad [m].
            v_up: Current vertical velocity [m/s].
            tilt: Angle of the flight path from vertical [rad].
            deployment: Airbrake setting held from now on, 0 to 1.
            drag_factor: Correction ``k`` on the rocket's drag model.
        """
        if v_up <= 0.0:
            return height
        cos = max(math.cos(tilt), _MIN_COS)
        h, vz = height, v_up
        vx = v_up * math.sqrt(1.0 - cos * cos) / cos
        brake = self.brake_cda * min(max(deployment, 0.0), 1.0)
        half_over_m = 0.5 / self.mass
        dt = _PREDICT_DT
        lookup, rho_t, a_t, g_t, cda_t = (
            self._lookup,
            self._rho,
            self._sound,
            self._g,
            self._cda,
        )

        def accel(hh: float, ux: float, uz: float) -> tuple[float, float]:
            speed = math.sqrt(ux * ux + uz * uz)
            rho = lookup(rho_t, hh, _TABLE_STEP)
            mach = speed / lookup(a_t, hh, _TABLE_STEP)
            c = half_over_m * rho * speed * (drag_factor * lookup(cda_t, mach, _MACH_STEP) + brake)
            return -c * ux, -c * uz - lookup(g_t, hh, _TABLE_STEP)

        for _ in range(2000):
            ax1, az1 = accel(h, vx, vz)
            h2, vx2, vz2 = h + 0.5 * dt * vz, vx + 0.5 * dt * ax1, vz + 0.5 * dt * az1
            ax2, az2 = accel(h2, vx2, vz2)
            h3, vx3, vz3 = h + 0.5 * dt * vz2, vx + 0.5 * dt * ax2, vz + 0.5 * dt * az2
            ax3, az3 = accel(h3, vx3, vz3)
            h4, vx4, vz4 = h + dt * vz3, vx + dt * ax3, vz + dt * az3
            ax4, az4 = accel(h4, vx4, vz4)
            h_new = h + dt / 6.0 * (vz + 2.0 * vz2 + 2.0 * vz3 + vz4)
            vx_new = vx + dt / 6.0 * (ax1 + 2.0 * ax2 + 2.0 * ax3 + ax4)
            vz_new = vz + dt / 6.0 * (az1 + 2.0 * az2 + 2.0 * az3 + az4)
            if vz_new <= 0.0:
                # Cubic Hermite through the step; its maximum is the apogee.
                return _hermite_peak(h, h_new, vz, vz_new, az1, az4, dt)
            h, vx, vz = h_new, vx_new, vz_new
        return h

    def setting_for(
        self,
        target: float,
        height: float,
        v_up: float,
        tilt: float,
        *,
        drag_factor: float = 1.0,
        tolerance: float = 0.5,
    ) -> tuple[float, float]:
        """``(deployment, predicted apogee)`` that tops out at ``target``.

        Clipped to the brakes' range: 0 if the rocket will fall short anyway,
        1 if even fully open overshoots.
        """
        lo, hi = 0.0, 1.0
        f_lo = self.apogee(height, v_up, tilt, deployment=lo, drag_factor=drag_factor) - target
        if f_lo <= 0.0:
            return 0.0, f_lo + target
        f_hi = self.apogee(height, v_up, tilt, deployment=hi, drag_factor=drag_factor) - target
        if f_hi >= 0.0:
            return 1.0, f_hi + target
        side = 0
        x, fx = lo, f_lo
        for _ in range(30):
            x = (lo * f_hi - hi * f_lo) / (f_hi - f_lo)
            fx = self.apogee(height, v_up, tilt, deployment=x, drag_factor=drag_factor) - target
            if abs(fx) <= tolerance:
                break
            if fx > 0.0:
                lo, f_lo = x, fx
                if side == 1:
                    f_hi *= 0.5  # Illinois: stop the far end from sticking
                side = 1
            else:
                hi, f_hi = x, fx
                if side == -1:
                    f_lo *= 0.5
                side = -1
        return x, fx + target


def _hermite_peak(
    h0: float, h1: float, v0: float, v1: float, a0: float, a1: float, dt: float
) -> float:
    """Highest point of the cubic Hermite interpolant on one step (``v0 > 0 ≥ v1``)."""
    lo, hi = 0.0, 1.0
    for _ in range(30):
        mid = 0.5 * (lo + hi)
        s = mid
        # derivative of the Hermite position polynomial, in m/s
        v = (
            (6 * s * s - 6 * s) * (h0 - h1) / dt
            + (3 * s * s - 4 * s + 1) * v0
            + (3 * s * s - 2 * s) * v1
        )
        if v > 0.0:
            lo = mid
        else:
            hi = mid
    s = 0.5 * (lo + hi)
    h00 = 2 * s**3 - 3 * s**2 + 1
    h10 = s**3 - 2 * s**2 + s
    h01 = -2 * s**3 + 3 * s**2
    h11 = s**3 - s**2
    del a0, a1  # the velocity end points already pin the cubic
    return h00 * h0 + h10 * dt * v0 + h01 * h1 + h11 * dt * v1


@dataclass
class AirbrakeLog:
    """What the flight computer thought and did, one row per control tick.

    Attributes:
        t: Tick times [s].
        altitude: Kalman-filtered altitude [m].
        velocity: Kalman-filtered vertical velocity [m/s].
        predicted_apogee: Where it expected to top out with the brakes as
            they were [m], ``nan`` before burnout.
        command: Airbrake setting asked of the servo, 0 to 1.
        deployment: Actual panel extension, 0 to 1.
        drag_factor: Learned correction on the drag model.
    """

    t: list[float] = field(default_factory=list)
    altitude: list[float] = field(default_factory=list)
    velocity: list[float] = field(default_factory=list)
    predicted_apogee: list[float] = field(default_factory=list)
    command: list[float] = field(default_factory=list)
    deployment: list[float] = field(default_factory=list)
    drag_factor: list[float] = field(default_factory=list)

    def arrays(self) -> dict[str, Vector]:
        """Every column as a NumPy array."""
        return {k: np.asarray(v, dtype=np.float64) for k, v in self.__dict__.items()}


class FlightComputer:
    """The airbrake flight software, stepped alongside the simulator.

    :func:`apogee.flight.simulate` calls :meth:`step` after every integration
    step with the true state; the computer only ever looks at it through its
    sensors (plus attitude, from gyros). It returns the panel extension to
    fly the next step with.
    """

    def __init__(
        self,
        rocket: Rocket,
        site: LaunchSite,
        settings: FlightComputerSettings,
        *,
        seed: int = 0,
    ) -> None:
        brakes = rocket.airbrakes
        if brakes is None:
            raise ValueError(f"{rocket.name} has no airbrakes")
        if settings.target_apogee is None:
            raise ValueError("the flight computer needs a target apogee")
        self.brakes: Airbrakes = brakes
        self.settings = settings
        self.target: float = settings.target_apogee
        self.sensors = Sensors(settings, seed)
        self.dt = 1.0 / settings.rate
        self.filter = AltitudeFilter(settings, self.dt)
        self.predictor = ApogeePredictor(rocket, site)
        self.ticks_per_plan = max(1, round(settings.rate / settings.control_rate))
        self.log = AirbrakeLog()

        self.command = 0.0
        self.deployment = 0.0
        self.drag_factor = 1.0
        self.predicted = math.nan
        self.lifted_off = False
        self.burnout_time: float | None = None
        self.apogee_seen = False
        self._next = self.dt
        self._tick = 0
        self._now = 0.0

    @property
    def armed(self) -> bool:
        """Whether the panels may open now (after the lockout, rising)."""
        return (
            self.burnout_time is not None
            and not self.apogee_seen
            and self._now >= self.burnout_time + self.settings.lockout
        )

    def step(
        self, t: float, height: float, vertical_acceleration: float, tilt: float, dt: float
    ) -> float:
        """Advance to time ``t`` given the true state; returns the panel extension.

        Args:
            t: Simulation time [s].
            height: True height above the pad [m].
            vertical_acceleration: True vertical acceleration [m/s²].
            tilt: Angle of the flight path from vertical [rad], from the gyros.
            dt: Length of the integration step just taken [s].
        """
        while self._next <= t + 1e-9:
            self._now = self._next
            self._sample(height, vertical_acceleration, tilt)
            self._next += self.dt
        # Servo: slew towards the command at the module's travel rate.
        rate = dt / self.brakes.travel_time
        gap = self.command - self.deployment
        self.deployment += max(-rate, min(rate, gap))
        self.deployment = min(max(self.deployment, 0.0), 1.0)
        return self.deployment

    def _sample(self, height: float, vertical_acceleration: float, tilt: float) -> None:
        baro, accel = self.sensors.read(height, vertical_acceleration)
        self.filter.step(baro, accel)
        h, v, a = self.filter.altitude, self.filter.velocity, self.filter.acceleration

        if not self.lifted_off and a > _LIFTOFF_ACCEL:
            self.lifted_off = True
        if self.lifted_off and self.burnout_time is None and a < 0.0:
            self.burnout_time = self._now
        if self.burnout_time is not None and not self.apogee_seen and v < 0.0:
            self.apogee_seen = True
            self.command = 0.0

        self._tick += 1
        if self._tick % self.ticks_per_plan:
            return
        if self.burnout_time is not None and not self.apogee_seen:
            self._learn_drag(h, v, a, tilt)
            self._plan(h, v, tilt)
        self.log.t.append(self._now)
        self.log.altitude.append(h)
        self.log.velocity.append(v)
        self.log.predicted_apogee.append(self.predicted)
        self.log.command.append(self.command)
        self.log.deployment.append(self.deployment)
        self.log.drag_factor.append(self.drag_factor)

    def _learn_drag(self, h: float, v: float, a: float, tilt: float) -> None:
        if v < _LEARN_SPEED:
            return
        p = self.predictor
        rho, sound, g = p.air(h)
        cos = max(math.cos(tilt), _MIN_COS)
        speed = v / cos
        # Vertical drag deceleration = ½ρ|V|v_z (k CdA + δ cd A) / m.
        measured_cda = -(a + g) * 2.0 * p.mass / (rho * speed * v)
        model = p.rocket_cda(speed / sound)
        if model <= 0.0:
            return
        k = (measured_cda - p.brake_cda * self.deployment) / model
        k = min(max(k, _K_LIMITS[0]), _K_LIMITS[1])
        self.drag_factor += _LEARN_GAIN * (k - self.drag_factor)

    def _plan(self, h: float, v: float, tilt: float) -> None:
        p = self.predictor
        _, sound, _ = p.air(h)
        mach = v / max(math.cos(tilt), _MIN_COS) / sound
        k = self.drag_factor
        if not (self.armed and mach < self.brakes.max_mach):
            self.command = 0.0
        elif v >= _HOLD_SPEED:
            on_course = p.apogee(h, v, tilt, deployment=self.command, drag_factor=k)
            if abs(on_course - self.target) > _DEADBAND:
                self.command, _ = p.setting_for(self.target, h, v, tilt, drag_factor=k)
        # Below _HOLD_SPEED the panels have almost no authority left, so the
        # setting is held instead of chasing sensor noise.
        self.predicted = p.apogee(h, v, tilt, deployment=self.deployment, drag_factor=k)
