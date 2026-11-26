"""Six-degree-of-freedom equations of motion and the RK4 integrator.

The rocket is a rigid body whose mass, centre of gravity and inertia change
as the motor burns. Its state is 13 numbers::

    y = [x, y, z,  vx, vy, vz,  qw, qx, qy, qz,  ωx, ωy, ωz]
         position   velocity    attitude          body rates
         (world, m) (world, m/s) (body→world)     (body, rad/s)

World axes are east-north-up with the origin at the CG on the pad (see
:mod:`apogee.environment`). Body axes have ``x`` along the rocket towards the
nose and ``y``, ``z`` across it.

Quick use, a Kestrel flight up to apogee::

    >>> from apogee import load_rocket
    >>> from apogee.cli import example_path
    >>> from apogee.dynamics import Dynamics, integrate
    >>> from apogee.environment import LaunchSite, Wind
    >>> rocket = load_rocket(example_path("rocket.yaml"))
    >>> site = LaunchSite(rail_length=5.2, wind=Wind(speed=4.0, direction=270.0))
    >>> flight = integrate(Dynamics(rocket, site), t_end=60.0, dt=0.02,
    ...                    stop=lambda t, y: t > 5.0 and y[5] < 0.0)
    >>> bool(4500 < flight.altitude.max() < 5500)             # metres above the pad
    True

What acts on the rocket
-----------------------
* **Thrust** from the motor's thrust curve, along the body axis through the CG.
* **Gravity** from the inverse-square law (:func:`apogee.earth.gravity`).
* **Drag** ``½ ρ V² A Cd`` against the air-relative velocity, with ``Cd`` from
  :meth:`apogee.aero.Aerodynamics.drag` at the current Mach number, altitude
  and motor state.
* **Normal force** ``½ ρ V² A CN(α)`` across the body, acting at the centre
  of pressure, which turns the nose into the relative wind (weathercocking).
* **Airbrake drag** ``½ ρ V² · cd · A_max · δ`` against the air-relative
  velocity when the rocket has an airbrake module, with the panel extension
  ``δ`` (0 to 1) set from outside through ``brake_deployment`` (see
  :mod:`apogee.control`). The panels are symmetric, so their drag acts along
  the axis and adds no turning moment.
* **Aerodynamic pitch damping** ``−C ω`` and **jet damping**
  ``−ṁ (x_nozzle − x_cg)² ω``: exhaust leaving the nozzle carries away
  transverse momentum and slows the rocket's pitching (W. Box, C. Bishop and
  E. Hunt, *Estimating the dynamic and aerodynamic parameters of passively
  controlled high power rockets for flight simulation*, 2009, eq. 8).

The rotation follows Euler's equations for a body whose principal axes are
its body axes, ``I ω̇ = M − ω × (I ω)``. On the rail the rocket can only slide
along it: attitude is frozen and forces across the rail are taken by the rail.

Integration is classic fixed-step fourth-order Runge-Kutta. The quaternion is
renormalised after every step to remove round-off drift (the drift itself is
below 10⁻¹⁰ per second at the default step, see the tests).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from apogee import rotation
from apogee.aero import Aerodynamics
from apogee.atmosphere import atmosphere
from apogee.earth import G0, gravity
from apogee.environment import LaunchSite
from apogee.rocket import Rocket

__all__ = [
    "Dynamics",
    "ForceBreakdown",
    "Trajectory",
    "integrate",
    "rk4_step",
]

Vector = NDArray[np.float64]
Derivative = Callable[[float, Vector], Vector]

POSITION = slice(0, 3)
VELOCITY = slice(3, 6)
ATTITUDE = slice(6, 10)
RATES = slice(10, 13)
STATE_SIZE = 13

_BODY_AXIS = np.array([1.0, 0.0, 0.0])
#: Below this airspeed [m/s] aerodynamic forces are ignored (they are ~1e-6 N).
_MIN_AIRSPEED = 1e-3


@dataclass(frozen=True)
class ForceBreakdown:
    """Everything acting on the rocket at one instant.

    Forces are in world axes [N], moments in body axes about the CG [N·m].
    """

    thrust: Vector
    gravity: Vector
    drag: Vector
    normal: Vector
    moment: Vector
    mass: float
    on_rail: bool
    airspeed: float
    mach: float
    alpha: float
    dynamic_pressure: float

    @property
    def total(self) -> Vector:
        """Sum of all forces before any rail reaction [N]."""
        return np.asarray(self.thrust + self.gravity + self.drag + self.normal)


@dataclass
class Dynamics:
    """The equations of motion of one rocket at one launch site.

    Args:
        rocket: The rocket, from :func:`apogee.load_rocket`.
        site: Launch rail, pad altitude and wind.
        aerodynamics: ``False`` flies in a vacuum (useful for checks).
        gravity: ``False`` switches gravity off (useful for checks).
        uniform_gravity: Use ``g0`` at every altitude instead of the
            inverse-square law.
    """

    rocket: Rocket
    site: LaunchSite = field(default_factory=LaunchSite)
    aerodynamics: bool = True
    gravity: bool = True
    uniform_gravity: bool = False

    def __post_init__(self) -> None:
        self.aero = Aerodynamics(self.rocket) if self.aerodynamics else None
        self.rail = self.site.rail_direction
        self.released = False
        self.brakes = self.rocket.airbrakes
        #: Airbrake panel extension, 0 (stowed) to 1 (fully out).
        self.brake_deployment = 0.0
        motor = self.rocket.motor
        burn = self.rocket.burnout_time
        # Mass properties are tabulated once: rebuilding them from every part at
        # each RK4 stage would dominate the run time.
        times = np.linspace(0.0, burn, 400) if burn > 0.0 else np.array([0.0])
        if motor is not None:
            times = np.union1d(times, motor.time)
        props = [self.rocket.mass_properties(float(t)) for t in times]
        self._t = times
        self._mass = np.array([p.mass for p in props])
        self._cg = np.array([p.cg for p in props])
        self._ixx = np.array([p.ixx for p in props])
        self._iyy = np.array([p.iyy for p in props])
        if motor is not None:
            self._nozzle = self.rocket.motor_position + motor.length
            self._flow = motor.propellant_mass / motor.total_impulse  # kg per N·s
        else:
            self._nozzle = self.rocket.length
            self._flow = 0.0

    # --- Rocket properties over time ---------------------------------------------

    def thrust(self, t: float) -> float:
        """Motor thrust [N] at time ``t`` after ignition."""
        return self.rocket.motor.thrust_at(t) if self.rocket.motor else 0.0

    def mass_flow(self, t: float) -> float:
        """Propellant mass flow ``ṁ`` [kg/s]; burning mass is proportional to impulse."""
        return self._flow * self.thrust(t)

    def mass_properties(self, t: float) -> tuple[float, float, float, float]:
        """``(mass, cg, ixx, iyy)`` at time ``t`` [kg, m from nose, kg·m², kg·m²]."""
        tt = self._t
        return (
            float(np.interp(t, tt, self._mass)),
            float(np.interp(t, tt, self._cg)),
            float(np.interp(t, tt, self._ixx)),
            float(np.interp(t, tt, self._iyy)),
        )

    # --- State ---------------------------------------------------------------------

    def initial_state(self) -> Vector:
        """At rest on the rail, nose pointing up it."""
        y = np.zeros(STATE_SIZE)
        y[ATTITUDE] = rotation.from_two_vectors(_BODY_AXIS, self.rail)
        return y

    def on_rail(self, y: Vector) -> bool:
        """Whether the rocket is still guided by the rail.

        Once :func:`integrate` has seen the rocket leave the rail it sets
        ``released``, so a rocket coming back down past the pad is not caught
        by the rail again.
        """
        if self.released:
            return False
        return bool(np.dot(y[POSITION], self.rail) < self.site.rail_length)

    # --- Forces and moments ------------------------------------------------------

    def forces(self, t: float, y: Vector) -> ForceBreakdown:
        """Forces and moments on the rocket in state ``y`` at time ``t``."""
        mass, cg, _, _ = self.mass_properties(t)
        q = y[ATTITUDE]
        omega = y[RATES]
        rot = rotation.to_matrix(q)
        axis = rot[:, 0]
        height = float(y[2])
        altitude = self.site.altitude + height

        thrust_n = self.thrust(t)
        thrust = thrust_n * axis
        if not self.gravity:
            g = 0.0
        elif self.uniform_gravity:
            g = G0
        else:
            g = gravity(altitude)
        weight = np.array([0.0, 0.0, -mass * g])

        zero = np.zeros(3)
        drag, normal, moment = zero, zero, zero.copy()
        airspeed = mach = alpha = qbar = 0.0
        on_rail = self.on_rail(y)

        if self.aero is not None:
            air = atmosphere(altitude)
            v_air = y[VELOCITY] - self.site.wind.velocity(height)
            airspeed = float(np.linalg.norm(v_air))
            if airspeed > _MIN_AIRSPEED:
                mach = airspeed / air.speed_of_sound
                qbar = 0.5 * air.density * airspeed**2
                area = self.aero.reference_area
                cd = (
                    self.aero.drag(
                        mach,
                        thrusting=thrust_n > 0.0,
                        speed_of_sound=air.speed_of_sound,
                        kinematic_viscosity=air.kinematic_viscosity,
                    ).total
                    * self.rocket.drag_scale
                )
                if self.brakes is not None and self.brake_deployment > 0.0:
                    cd += self.brakes.drag_area * self.brake_deployment / area
                drag = -qbar * area * cd * v_air / airspeed

                if not on_rail:
                    u = rot.T @ v_air  # air-relative velocity in body axes
                    cross = math.hypot(u[1], u[2])
                    alpha = math.atan2(cross, u[0])
                    if cross > 1e-12:
                        cn, x_cp = self.aero.normal_force(alpha, mach)
                        f_body = np.array([0.0, -u[1], -u[2]]) * (qbar * area * cn / cross)
                        normal = rot @ f_body
                        arm = np.array([cg - x_cp, 0.0, 0.0])  # CP relative to CG, body x
                        moment += np.cross(arm, f_body)
                    c_damp = self.aero.damping_coefficient(air.density, airspeed, cg, mach)
                    moment -= c_damp * np.array([0.0, omega[1], omega[2]])

        if not on_rail:
            jet = self.mass_flow(t) * (self._nozzle - cg) ** 2
            moment -= jet * np.array([0.0, omega[1], omega[2]])

        return ForceBreakdown(
            thrust=thrust,
            gravity=weight,
            drag=drag,
            normal=normal,
            moment=moment,
            mass=mass,
            on_rail=on_rail,
            airspeed=airspeed,
            mach=mach,
            alpha=alpha,
            dynamic_pressure=qbar,
        )

    def derivative(self, t: float, y: Vector) -> Vector:
        """Time derivative of the state, ``dy/dt = f(t, y)``."""
        f = self.forces(t, y)
        dy = np.zeros(STATE_SIZE)
        dy[POSITION] = y[VELOCITY]

        if f.on_rail:
            along = float(np.dot(f.total, self.rail)) / f.mass
            resting = (
                np.dot(y[POSITION], self.rail) <= 0.0 and np.dot(y[VELOCITY], self.rail) <= 0.0
            )
            if resting and along < 0.0:
                along = 0.0  # sitting on the launch stop until thrust exceeds weight
            dy[VELOCITY] = along * self.rail
            return dy  # attitude and rates frozen by the rail

        dy[VELOCITY] = f.total / f.mass
        omega = y[RATES]
        _, _, ixx, iyy = self.mass_properties(t)
        inertia = np.array([ixx, iyy, iyy])
        dy[ATTITUDE] = rotation.rate(y[ATTITUDE], omega)
        dy[RATES] = (f.moment - np.cross(omega, inertia * omega)) / inertia
        return dy


# --- Integration -------------------------------------------------------------------


def rk4_step(f: Derivative, t: float, y: Vector, dt: float) -> Vector:
    """Advance ``dy/dt = f(t, y)`` by one classic fourth-order Runge-Kutta step."""
    k1 = f(t, y)
    k2 = f(t + 0.5 * dt, y + 0.5 * dt * k1)
    k3 = f(t + 0.5 * dt, y + 0.5 * dt * k2)
    k4 = f(t + dt, y + dt * k3)
    return np.asarray(y + dt / 6.0 * (k1 + 2.0 * k2 + 2.0 * k3 + k4))


@dataclass(frozen=True)
class Trajectory:
    """A sampled flight: ``t`` has shape ``(N,)`` and ``y`` shape ``(N, 13)``."""

    t: Vector
    y: Vector
    site: LaunchSite = field(default_factory=LaunchSite)

    @property
    def position(self) -> Vector:
        """CG position, east-north-up from the pad [m], shape ``(N, 3)``."""
        return self.y[:, POSITION]

    @property
    def velocity(self) -> Vector:
        """Velocity over the ground [m/s], shape ``(N, 3)``."""
        return self.y[:, VELOCITY]

    @property
    def attitude(self) -> Vector:
        """Body-to-world quaternions ``[w, x, y, z]``, shape ``(N, 4)``."""
        return self.y[:, ATTITUDE]

    @property
    def rates(self) -> Vector:
        """Body angular rates [rad/s], shape ``(N, 3)``."""
        return self.y[:, RATES]

    @property
    def altitude(self) -> Vector:
        """Height above the pad [m]."""
        return self.y[:, 2]

    @property
    def speed(self) -> Vector:
        """Ground speed [m/s]."""
        return np.asarray(np.linalg.norm(self.velocity, axis=1))

    @property
    def axis(self) -> Vector:
        """Direction the nose points, world axes, shape ``(N, 3)``."""
        return np.array([rotation.rotate(q, _BODY_AXIS) for q in self.attitude])

    @property
    def pitch_from_vertical(self) -> Vector:
        """Angle between the rocket's axis and straight up [degrees]."""
        return np.degrees(np.arccos(np.clip(self.axis[:, 2], -1.0, 1.0)))


def integrate(
    dynamics: Dynamics | Derivative,
    t_end: float,
    dt: float = 0.01,
    *,
    y0: Vector | None = None,
    t0: float = 0.0,
    stop: Callable[[float, Vector], bool] | None = None,
    normalize: bool = True,
) -> Trajectory:
    """Integrate a flight with fixed-step RK4.

    Args:
        dynamics: A :class:`Dynamics`, or any ``f(t, y)`` with the same state layout.
        t_end: Stop time [s].
        dt: Step [s]. 0.01 s resolves the pitch oscillation of hobby rockets
            (periods of 0.3 s and longer) with RK4 errors far below 1 mm.
        y0: Initial state (default: at rest on the rail).
        t0: Initial time [s] after ignition.
        stop: Optional ``stop(t, y)``; integration ends at the first step where
            it returns ``True`` (that step is kept).
        normalize: Renormalise the quaternion after each step.

    Returns:
        The trajectory, including the initial state.
    """
    if dt <= 0.0:
        raise ValueError(f"time step must be positive, got {dt}")
    if t_end <= t0:
        raise ValueError(f"t_end ({t_end}) must be after t0 ({t0})")
    model = dynamics if isinstance(dynamics, Dynamics) else None
    if model is not None:
        f: Derivative = model.derivative
        site = model.site
        y = model.initial_state() if y0 is None else np.array(y0, dtype=np.float64)
        model.released = False
        model.released = not model.on_rail(y)
    else:
        if y0 is None or isinstance(dynamics, Dynamics):
            raise ValueError("an initial state y0 is needed when integrating a plain function")
        f, site, y = dynamics, LaunchSite(), np.array(y0, dtype=np.float64)

    n = math.ceil((t_end - t0) / dt - 1e-9)
    ts = [t0]
    ys = [y.copy()]
    t = t0
    for i in range(1, n + 1):
        h = min(dt, t_end - t)
        y = rk4_step(f, t, y, h)
        if normalize:
            y[ATTITUDE] /= np.linalg.norm(y[ATTITUDE])
        t = t0 + i * dt if i < n else t_end
        ts.append(t)
        ys.append(y.copy())
        if model is not None and not model.released and not model.on_rail(y):
            model.released = True
        if stop is not None and stop(t, y):
            break
    return Trajectory(np.array(ts), np.array(ys), site)
