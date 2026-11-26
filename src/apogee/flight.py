"""A complete flight, from the launch rail to touchdown.

:func:`simulate` flies a rocket with the 6-DOF model of :mod:`apogee.dynamics`
up to apogee, opens its parachutes, follows the descent and stops on landing.
It returns a :class:`Flight`: the sampled trajectory, the events along the way
and the numbers people ask about first::

    >>> from apogee import load_rocket
    >>> from apogee.cli import example_path
    >>> flight = simulate(load_rocket(example_path("rocket.yaml")))
    >>> [e.name for e in flight.events]
    ['Rail exit', 'Burnout', 'Apogee', 'Drogue parachute opens', 'Main parachute opens', 'Landing']
    >>> bool(4500 < flight.apogee < 5200)                     # metres above the pad
    True

Events
------
Each event is found between two integration steps and located by bisection on
the cubic Hermite interpolant through the step's end points (positions and
velocities), which is as accurate as the RK4 step itself:

* **Rail exit**: the CG has travelled the rail's guided length.
* **Burnout**: the end of the thrust curve.
* **Apogee**: vertical velocity changes sign.
* **Parachute opens**: at apogee, or on the way down at the altitude set by
  ``deploy:`` in the rocket file (at apogee if the rocket never gets that high).
* **Landing**: the CG returns to pad height.

Descent
-------
Once a parachute is open the rocket hangs under it and its attitude no longer
matters, so the descent is a 3-DOF point mass: weight against the drag of the
open canopies, ``½ ρ V² Σ(Cd A)``, in the air-relative velocity, so the wind
carries it. This is the same model OpenRocket uses under a parachute. Canopies
open instantly; opening loads are not modelled. Without a parachute the 6-DOF
model flies the rocket ballistically all the way down.
"""

from __future__ import annotations

import csv
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

import numpy as np
from numpy.typing import NDArray

from apogee import rotation
from apogee.atmosphere import atmosphere
from apogee.components import Parachute
from apogee.control import AirbrakeLog, FlightComputer
from apogee.dynamics import (
    ATTITUDE,
    POSITION,
    RATES,
    STATE_SIZE,
    VELOCITY,
    Dynamics,
    Trajectory,
)
from apogee.earth import G0, gravity
from apogee.environment import LaunchSite
from apogee.rocket import Rocket

__all__ = ["Event", "Flight", "FlightError", "simulate"]

Vector = NDArray[np.float64]
Derivative = Callable[[float, Vector], Vector]

#: Recommended minimum speed off the rail [m/s]; below it fins do not yet
#: steer reliably (common high-power practice; NAR's floor is 30 ft/s, ~9 m/s).
MIN_RAIL_EXIT_SPEED = 15.0
#: Hardest sensible landing [m/s]; 20-25 ft/s is the usual upper limit under a main.
MAX_LANDING_SPEED = 7.5

_BISECTIONS = 40
_NOSE = np.array([1.0, 0.0, 0.0])


class FlightError(ValueError):
    """The rocket cannot fly as described (for example, it never leaves the pad)."""


@dataclass(frozen=True)
class Event:
    """Something that happens at one instant of the flight.

    Attributes:
        name: What happened, e.g. ``"Apogee"``.
        time: Seconds after ignition.
        position: East, north, up from the pad [m].
        velocity: Velocity over the ground [m/s].
        note: A short remark, e.g. why a parachute opened early.
    """

    name: str
    time: float
    position: Vector
    velocity: Vector
    note: str = ""

    @property
    def altitude(self) -> float:
        """Height above the pad [m]."""
        return float(self.position[2])

    @property
    def speed(self) -> float:
        """Ground speed [m/s]."""
        return float(np.linalg.norm(self.velocity))


@dataclass(frozen=True)
class Flight:
    """The result of :func:`simulate`.

    Attributes:
        rocket: The rocket that flew.
        trajectory: Every integration step, including the event instants.
        events: In time order.
        phase: What the rocket was doing at each sample (``"rail"``,
            ``"boost"``, ``"coast"``, ``"under Main parachute"``, ...).
        acceleration: Acceleration over the ground at each sample [m/s²],
            shape ``(N, 3)``.
        warnings: Plain-language problems with the flight.
        brake_deployment: Airbrake panel extension (0 to 1) at each sample,
            for a rocket flown with its airbrakes active.
        airbrake_log: What the flight computer estimated and commanded.
        target_apogee: The apogee the flight computer aimed for [m].
    """

    rocket: Rocket
    trajectory: Trajectory
    events: list[Event]
    phase: list[str]
    acceleration: Vector
    warnings: list[str] = field(default_factory=list)
    brake_deployment: Vector | None = None
    airbrake_log: AirbrakeLog | None = None
    target_apogee: float | None = None

    # --- Lookups -------------------------------------------------------------

    def event(self, name: str) -> Event | None:
        """The first event called ``name``, or ``None``."""
        return next((e for e in self.events if e.name == name), None)

    @property
    def site(self) -> LaunchSite:
        """Where it flew from."""
        return self.trajectory.site

    @property
    def t(self) -> Vector:
        """Sample times [s]."""
        return self.trajectory.t

    # --- Summary numbers -----------------------------------------------------

    @property
    def apogee(self) -> float:
        """Highest point above the pad [m]."""
        e = self.event("Apogee")
        return e.altitude if e else float(self.trajectory.altitude.max())

    @property
    def time_to_apogee(self) -> float:
        """Seconds from ignition to apogee."""
        e = self.event("Apogee")
        return e.time if e else float(self.t[np.argmax(self.trajectory.altitude)])

    @property
    def apogee_error(self) -> float | None:
        """Apogee minus the flight computer's target [m], if it flew to one."""
        return None if self.target_apogee is None else self.apogee - self.target_apogee

    @property
    def mach(self) -> Vector:
        """Air-relative Mach number at each sample."""
        wind = self.site.wind
        z = self.trajectory.altitude
        v_air = self.trajectory.velocity - np.array([wind.velocity(float(h)) for h in z])
        sound = atmosphere(np.clip(self.site.altitude + z, -5000.0, 86000.0)).speed_of_sound
        return np.asarray(np.linalg.norm(v_air, axis=1) / sound)

    @property
    def max_speed(self) -> tuple[float, float]:
        """``(speed [m/s], time [s])`` of the fastest moment."""
        i = int(np.argmax(self.trajectory.speed))
        return float(self.trajectory.speed[i]), float(self.t[i])

    @property
    def max_mach(self) -> float:
        """Highest air-relative Mach number."""
        return float(self.mach.max())

    @property
    def max_acceleration(self) -> tuple[float, float]:
        """``(acceleration [m/s²], time [s])``, the peak on the way up (to apogee)."""
        up = self.t <= self.time_to_apogee
        a = np.linalg.norm(self.acceleration[up], axis=1)
        i = int(np.argmax(a))
        return float(a[i]), float(self.t[up][i])

    @property
    def rail_exit_speed(self) -> float:
        """Speed as the rocket leaves the rail [m/s]."""
        e = self.event("Rail exit")
        return e.speed if e else 0.0

    @property
    def landing(self) -> Event | None:
        """The touchdown event, if the flight ended on the ground."""
        return self.event("Landing")

    @property
    def landing_distance(self) -> float:
        """Horizontal distance from the pad to the landing point [m]."""
        p = self.trajectory.position[-1]
        return float(math.hypot(p[0], p[1]))

    @property
    def landing_bearing(self) -> float:
        """Compass bearing from the pad to the landing point [degrees]."""
        p = self.trajectory.position[-1]
        return math.degrees(math.atan2(p[0], p[1])) % 360.0

    @property
    def flight_time(self) -> float:
        """Seconds from ignition to the end of the simulation."""
        return float(self.t[-1])

    def descent_rate(self, phase: str) -> float | None:
        """Sink rate [m/s] at the end of a descent phase, e.g. ``"under Main parachute"``."""
        idx = [i for i, p in enumerate(self.phase) if p == phase]
        return float(-self.trajectory.velocity[idx[-1], 2]) if idx else None

    # --- Output --------------------------------------------------------------

    CSV_COLUMNS = (
        "time_s",
        "east_m",
        "north_m",
        "altitude_m",
        "v_east_mps",
        "v_north_mps",
        "v_up_mps",
        "speed_mps",
        "mach",
        "acceleration_g",
        "pitch_deg",
        "q_w",
        "q_x",
        "q_y",
        "q_z",
        "phase",
    )

    def write_csv(self, target: str | Path | TextIO) -> int:
        """Write every sample as CSV (SI units in the column names); returns the row count."""
        if isinstance(target, str | Path):
            with open(target, "w", newline="", encoding="utf-8") as fh:
                return self.write_csv(fh)
        traj = self.trajectory
        mach = self.mach
        accel = np.linalg.norm(self.acceleration, axis=1) / G0
        pitch = traj.pitch_from_vertical
        writer = csv.writer(target, lineterminator="\n")
        writer.writerow(self.CSV_COLUMNS)
        for i in range(len(traj.t)):
            p, v, q = traj.position[i], traj.velocity[i], traj.attitude[i]
            writer.writerow(
                [
                    f"{traj.t[i]:.4f}",
                    *(f"{c:.3f}" for c in p),
                    *(f"{c:.3f}" for c in v),
                    f"{traj.speed[i]:.3f}",
                    f"{mach[i]:.4f}",
                    f"{accel[i]:.3f}",
                    f"{pitch[i]:.3f}",
                    *(f"{c:.6f}" for c in q),
                    self.phase[i],
                ]
            )
        return len(traj.t)


# --- Interpolation between steps ---------------------------------------------------


def _hermite(
    y0: Vector, y1: Vector, a0: Vector, a1: Vector, dt: float, tau: float
) -> tuple[Vector, Vector]:
    """Position and velocity at fraction ``tau`` of a step.

    Position is the cubic Hermite through both ends' positions and velocities;
    velocity is the cubic Hermite through both ends' velocities and accelerations.
    """
    h00 = 2 * tau**3 - 3 * tau**2 + 1
    h10 = tau**3 - 2 * tau**2 + tau
    h01 = -2 * tau**3 + 3 * tau**2
    h11 = tau**3 - tau**2
    p0, p1 = y0[POSITION], y1[POSITION]
    v0, v1 = y0[VELOCITY], y1[VELOCITY]
    pos = h00 * p0 + h10 * dt * v0 + h01 * p1 + h11 * dt * v1
    vel = h00 * v0 + h10 * dt * a0 + h01 * v1 + h11 * dt * a1
    return np.asarray(pos), np.asarray(vel)


def _state_at(
    y0: Vector, y1: Vector, a0: Vector, a1: Vector, dt: float, tau: float
) -> tuple[Vector, Vector]:
    """Full state and acceleration at fraction ``tau`` of a step."""
    y = y0 + tau * (y1 - y0)  # attitude and rates: linear, then renormalised
    y[POSITION], y[VELOCITY] = _hermite(y0, y1, a0, a1, dt, tau)
    norm = np.linalg.norm(y[ATTITUDE])
    if norm > 0.0:
        y[ATTITUDE] /= norm
    return y, a0 + tau * (a1 - a0)


def _crossing(
    g: Callable[[Vector, Vector], float],
    y0: Vector,
    y1: Vector,
    a0: Vector,
    a1: Vector,
    dt: float,
) -> float | None:
    """Fraction of the step at which ``g(position, velocity)`` goes from ≥ 0 to < 0."""
    g0 = g(y0[POSITION], y0[VELOCITY])
    g1 = g(y1[POSITION], y1[VELOCITY])
    if not (g0 >= 0.0 > g1):
        return None
    lo, hi = 0.0, 1.0
    for _ in range(_BISECTIONS):
        mid = 0.5 * (lo + hi)
        if g(*_hermite(y0, y1, a0, a1, dt, mid)) >= 0.0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# --- Simulation --------------------------------------------------------------------


class _Run:
    """Mutable bookkeeping for one :func:`simulate` call."""

    def __init__(self, dynamics: Dynamics) -> None:
        self.dyn = dynamics
        self.t: list[float] = []
        self.y: list[Vector] = []
        self.a: list[Vector] = []
        self.phase: list[str] = []
        self.brake: list[float] = []
        self.events: list[Event] = []
        self.warnings: list[str] = []

    def add(self, t: float, y: Vector, a: Vector, phase: str) -> None:
        self.t.append(t)
        self.y.append(y.copy())
        self.a.append(a.copy())
        self.phase.append(phase)
        self.brake.append(self.dyn.brake_deployment)

    def mark(self, name: str, t: float, y: Vector, note: str = "") -> None:
        self.events.append(Event(name, t, y[POSITION].copy(), y[VELOCITY].copy(), note))

    def replace_last(self, t: float, y: Vector, a: Vector) -> None:
        self.t[-1], self.y[-1], self.a[-1] = t, y.copy(), a.copy()


def _above(height: float) -> Callable[[Vector, Vector], float]:
    """Event function: positive while the CG is above ``height`` metres."""
    return lambda p, v: float(p[2]) - height


def _descent_step(dynamics: Dynamics, drag_area: float, t: float, y: Vector, limit: float) -> float:
    """Time step for the point-mass descent.

    Drag relaxes the velocity towards terminal at a rate ``λ = ρ Cd·A |v| / m``
    (just after a canopy opens it is fastest). Keeping ``λ dt ≤ 0.5`` keeps RK4
    well inside its stability limit (``λ dt ≈ 2.8``) and accurate.
    """
    height = float(y[2])
    rho = float(atmosphere(min(max(dynamics.site.altitude + height, -5000.0), 86000.0)).density)
    speed = float(np.linalg.norm(y[VELOCITY] - dynamics.site.wind.velocity(height)))
    rate = rho * drag_area * speed / dynamics.mass_properties(t)[0]
    return limit if rate <= 0.0 else min(limit, 0.5 / rate)


def _descent_derivative(dynamics: Dynamics, drag_area: float) -> Derivative:
    """Point mass hanging under canopies with a total ``Cd·A`` of ``drag_area`` [m²]."""
    site = dynamics.site

    def f(t: float, y: Vector) -> Vector:
        mass = dynamics.mass_properties(t)[0]
        height = float(y[2])
        altitude = site.altitude + height
        v_air = y[VELOCITY] - site.wind.velocity(height)
        speed = float(np.linalg.norm(v_air))
        rho = float(atmosphere(min(max(altitude, -5000.0), 86000.0)).density)
        dy = np.zeros(STATE_SIZE)
        dy[POSITION] = y[VELOCITY]
        dy[VELOCITY] = -0.5 * rho * speed * drag_area / mass * v_air
        dy[5] -= gravity(altitude) if dynamics.gravity else 0.0
        return dy

    return f


def _deploy_note(chute: Parachute, apogee: float) -> str:
    if chute.deploy_altitude is not None and chute.deploy_altitude >= apogee:
        return f"set for {chute.deploy_altitude:,.0f} m, above apogee"
    return ""


def simulate(
    rocket: Rocket,
    site: LaunchSite | None = None,
    *,
    dt: float = 0.01,
    descent_dt: float = 0.05,
    t_max: float = 3600.0,
    airbrakes: bool = True,
    seed: int = 0,
) -> Flight:
    """Fly ``rocket`` from the rail to the ground.

    Args:
        rocket: The rocket, from :func:`apogee.load_rocket`.
        site: Launch rail, pad altitude and wind (default: 5 m vertical rail,
            sea level, calm).
        dt: Time step for the 6-DOF flight [s].
        descent_dt: Time step under a parachute [s]; the point-mass descent is
            smooth, so a longer step loses nothing.
        t_max: Give up after this long [s].
        airbrakes: Fly the rocket's airbrakes, if it has them and its
            ``flight_computer`` has a target apogee (see :mod:`apogee.control`).
            ``False`` keeps them stowed, for the passive comparison.
        seed: Seed for the flight computer's sensor noise.

    Raises:
        FlightError: If the rocket has no motor or never leaves the pad.
    """
    if rocket.motor is None:
        raise FlightError("the rocket has no motor; add a `motor:` section to fly it")
    if dt <= 0.0 or descent_dt <= 0.0:
        raise ValueError("time steps must be positive")
    site = site or LaunchSite()
    dyn = Dynamics(rocket, site)
    run = _Run(dyn)
    burnout = rocket.burnout_time
    rail = dyn.rail

    at_apogee = [c for c in rocket.parachutes if c.deploy_altitude is None]
    by_altitude = sorted(
        (c for c in rocket.parachutes if c.deploy_altitude is not None),
        key=lambda c: -(c.deploy_altitude or 0.0),
    )
    open_chutes: list[Parachute] = []
    apogee = math.inf
    target = rocket.target_apogee if airbrakes else None
    computer = (
        FlightComputer(rocket, site, rocket.flight_computer, seed=seed)
        if target is not None and rocket.flight_computer is not None
        else None
    )
    brakes_open = False

    def ascent_phase(t: float, released: bool) -> str:
        if not released:
            return "rail"
        return "boost" if t < burnout else "coast"

    def descent_phase() -> str:
        return f"under {open_chutes[-1].name}" if open_chutes else "ballistic"

    f: Derivative = dyn.derivative
    step = dt
    t, y = 0.0, dyn.initial_state()
    dyn.released = False
    a = f(t, y)[VELOCITY]
    run.add(t, y, a, "rail")
    phase_after_apogee = False

    drag_area = 0.0
    while t < t_max:
        if open_chutes:
            step = _descent_step(dyn, drag_area, t, y, descent_dt)
        k1 = f(t, y)
        k2 = f(t + 0.5 * step, y + 0.5 * step * k1)
        k3 = f(t + 0.5 * step, y + 0.5 * step * k2)
        k4 = f(t + step, y + step * k3)
        y_new = y + step / 6.0 * (k1 + 2.0 * k2 + 2.0 * k3 + k4)
        if np.linalg.norm(y_new[ATTITUDE]) > 0.0:
            y_new[ATTITUDE] /= np.linalg.norm(y_new[ATTITUDE])
        t_new = t + step
        a0 = k1[VELOCITY]
        a1 = f(t_new, y_new)[VELOCITY]
        run.a[-1] = a0  # exact acceleration at the start of the step
        phase = descent_phase() if phase_after_apogee else ascent_phase(t_new, dyn.released)
        run.add(t_new, y_new, a1, phase)
        y_old, t_old = y, t
        t, y = t_new, y_new

        # Flight computer: sense, estimate, plan, move the panels.
        if computer is not None and not open_chutes:
            axis_up = float(rotation.rotate(y[ATTITUDE], _NOSE)[2])
            tilt = math.acos(min(max(axis_up, -1.0), 1.0))
            deployment = computer.step(t, float(y[2]), float(a1[2]), tilt, step)
            if deployment > 0.0 and not brakes_open:
                brakes_open = True
                run.mark("Airbrakes open", t, y)
            elif deployment <= 0.0 and brakes_open:
                brakes_open = False
                run.mark("Airbrakes stowed", t, y)
            dyn.brake_deployment = deployment

        # Rail exit
        if not dyn.released and not dyn.on_rail(y):
            dyn.released = True
            tau = _crossing(
                lambda p, v: site.rail_length - float(np.dot(p, rail)), y_old, y, a0, a1, step
            )
            frac = 1.0 if tau is None else tau
            ys, _ = _state_at(y_old, y, a0, a1, step, frac)
            run.mark("Rail exit", t_old + frac * step, ys)
            run.phase[-1] = ascent_phase(t, True)

        # Burnout
        if t_old < burnout <= t:
            tau = (burnout - t_old) / step
            ys, _ = _state_at(y_old, y, a0, a1, step, tau)
            run.mark("Burnout", burnout, ys)

        if not dyn.released and t > burnout + 1.0:
            raise FlightError(
                f"the rocket never leaves the pad: {rocket.motor.designation} cannot lift "
                f"{dyn.mass_properties(0.0)[0]:.2f} kg; use a bigger motor or a lighter rocket"
            )

        # Apogee
        if dyn.released and not phase_after_apogee:
            tau = _crossing(lambda p, v: float(v[2]), y_old, y, a0, a1, step)
            if tau is not None:
                t_apo = t_old + tau * step
                ys, a_apo = _state_at(y_old, y, a0, a1, step, tau)
                apogee = float(ys[2])
                run.mark("Apogee", t_apo, ys)
                phase_after_apogee = True
                opening = at_apogee + [
                    c for c in by_altitude if (c.deploy_altitude or 0.0) >= apogee
                ]
                if opening:
                    for c in opening:
                        run.mark(f"{c.name} opens", t_apo, ys, _deploy_note(c, apogee))
                        open_chutes.append(c)
                        if c in by_altitude:
                            by_altitude.remove(c)
                    # Restart from the apogee state under the canopy.
                    run.replace_last(t_apo, ys, a_apo)
                    t, y = t_apo, ys
                    y[RATES] = 0.0
                    drag_area = sum(c.cd * c.canopy_area for c in open_chutes)
                    if brakes_open:
                        brakes_open = False
                        run.mark("Airbrakes stowed", t_apo, ys)
                    dyn.brake_deployment = 0.0
                    run.brake[-1] = 0.0
                    f = _descent_derivative(dyn, drag_area)
                    step = descent_dt
                    run.phase[-1] = descent_phase()
                    run.a[-1] = f(t, y)[VELOCITY]
                    continue
                run.phase[-1] = "ballistic"

        # Deployment at altitude, on the way down
        if phase_after_apogee and by_altitude:
            tau = _crossing(_above(by_altitude[0].deploy_altitude or 0.0), y_old, y, a0, a1, step)
            if tau is not None:
                chute = by_altitude.pop(0)
                t_dep = t_old + tau * step
                ys, a_dep = _state_at(y_old, y, a0, a1, step, tau)
                run.mark(f"{chute.name} opens", t_dep, ys)
                open_chutes.append(chute)
                run.replace_last(t_dep, ys, a_dep)
                t, y = t_dep, ys
                y[RATES] = 0.0
                drag_area = sum(c.cd * c.canopy_area for c in open_chutes)
                f = _descent_derivative(dyn, drag_area)
                step = descent_dt
                run.phase[-1] = descent_phase()
                run.a[-1] = f(t, y)[VELOCITY]
                continue

        # Landing
        if dyn.released:
            tau = _crossing(lambda p, v: float(p[2]), y_old, y, a0, a1, step)
            if tau is not None:
                t_land = t_old + tau * step
                ys, a_land = _state_at(y_old, y, a0, a1, step, tau)
                run.replace_last(t_land, ys, a_land)
                run.mark("Landing", t_land, ys)
                break
    else:
        run.warnings.append(f"the flight had not landed after {t_max:,.0f} s; stopped there")

    run.events.sort(key=lambda e: e.time)
    flight = Flight(
        rocket=rocket,
        trajectory=Trajectory(np.array(run.t), np.array(run.y), site),
        events=run.events,
        phase=run.phase,
        acceleration=np.array(run.a),
        warnings=run.warnings,
        brake_deployment=np.array(run.brake) if computer is not None else None,
        airbrake_log=computer.log if computer is not None else None,
        target_apogee=target if computer is not None else None,
    )
    _check(flight)
    return flight


def _check(flight: Flight) -> None:
    """Add plain-language warnings about a finished flight."""
    w = flight.warnings
    exit_speed = flight.rail_exit_speed
    if 0.0 < exit_speed < MIN_RAIL_EXIT_SPEED:
        w.append(
            f"it leaves the rail at only {exit_speed:.1f} m/s "
            f"(aim for {MIN_RAIL_EXIT_SPEED:.0f}+); "
            "use a longer rail or a motor with more initial thrust"
        )
    if not flight.rocket.parachutes:
        w.append("no parachute: the rocket comes down ballistically")
    landing = flight.landing
    if landing is not None and flight.rocket.parachutes:
        sink = -float(landing.velocity[2])
        if sink > MAX_LANDING_SPEED:
            w.append(
                f"it lands at {sink:.1f} m/s (aim for under {MAX_LANDING_SPEED:.1f}); "
                "use a larger main parachute"
            )
