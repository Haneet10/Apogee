"""Fly Kestrel in RocketPy and save the results as Apogee's reference flights.

RocketPy (https://github.com/RocketPy-Team/RocketPy) is an independent,
peer-reviewed 6-DOF simulator (Ceotto et al., *J. Spacecraft and Rockets* 58(4),
2021) checked against real flights. It shares no code with Apogee: its own
equations of motion, adaptive LSODA integrator, Barrowman lift and centre of
pressure, motor mass model, launch rail and parachute descent.

To compare like with like, both simulators are given the same *inputs*:

* the rocket's geometry, from ``examples/rocket.yaml``;
* Apogee's dry mass, centre of gravity and inertia (Apogee's mass model is
  checked part by part in ``docs/rocket.md``);
* Apogee's drag coefficient against Mach number, power on and off (the drag
  build-up is checked in ``docs/aerodynamics.md``; RocketPy has no drag model
  of its own and always takes a Cd curve);
* the same thrust curve, propellant mass, 1976 Standard Atmosphere, gravity,
  launch rail, wind profile and parachutes.

Everything downstream of those inputs is computed independently, so the
comparison tests Apogee's flight mechanics: thrust and mass flow, aerodynamic
lift and stability, rail exit, weathercocking, wind drift, events and descent.

Regenerate the reference (needs ``pip install rocketpy==1.13.0``)::

    python validation/rocketpy_reference.py

It writes ``validation/rocketpy/reference.json`` (headline numbers for each
case) and ``validation/rocketpy/<case>.csv`` (altitude, speed and downrange
distance every 0.1 s, for the plots in ``docs/validation.md``). The test suite
reads these files; it does not need RocketPy installed.
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rocketpy

from apogee import Aerodynamics, gravity, load_rocket
from apogee.components import NoseCone

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent / "rocketpy"
ROCKET_FILE = ROOT / "examples" / "rocket.yaml"
MOTOR_FILE = ROOT / "examples" / "motors" / "Cesaroni_M1670.eng"
BURNOUT = 3.9  # s, last point of the M1670 thrust curve


@dataclass(frozen=True)
class Case:
    """One launch condition, flown by both simulators."""

    name: str
    title: str
    rail_length: float
    tilt: float  # degrees from vertical
    heading: float  # degrees from north
    wind_speed: float  # m/s at 10 m, 1/7 power law, blowing towards the east


CASES = (
    Case("calm", "Calm air, vertical 5 m rail", 5.0, 0.0, 0.0, 0.0),
    Case("windy", "4 m/s west wind, rail tilted 5° east", 5.0, 5.0, 90.0, 4.0),
)


def wind_speed(case: Case, height: float) -> float:
    """Apogee's default 1/7 power-law profile (see apogee.environment.Wind)."""
    if height <= 0.0 or case.wind_speed == 0.0:
        return 0.0
    return case.wind_speed * (height / 10.0) ** (1.0 / 7.0)


def drag_curves() -> tuple[list[list[float]], list[list[float]]]:
    """Apogee's zero-lift Cd against Mach, motor off and on."""
    aero = Aerodynamics(load_rocket(ROCKET_FILE))
    machs = np.round(np.arange(0.0, 2.01, 0.02), 2)
    off = [[float(m), aero.drag(float(m)).total] for m in machs]
    on = [[float(m), aero.drag(float(m), thrusting=True).total] for m in machs]
    return off, on


def build(case: Case) -> rocketpy.Flight:
    """Kestrel in RocketPy, flown under ``case``."""
    apogee_rocket = load_rocket(ROCKET_FILE)
    motor_spec = apogee_rocket.motor
    assert motor_spec is not None
    dry = apogee_rocket.dry_mass_properties()

    env = rocketpy.Environment(gravity=lambda h: gravity(float(h)), elevation=0.0)
    env.set_atmospheric_model(
        type="custom_atmosphere",
        pressure=None,  # None = the 1976 US Standard Atmosphere
        temperature=None,
        wind_u=lambda h: wind_speed(case, float(h)),
        wind_v=0.0,
    )

    # Casing as a thin tube, propellant as a solid cylinder burning in
    # proportion to delivered impulse: the same assumptions as apogee.motor.
    r, length, case_mass = 0.5 * motor_spec.diameter, motor_spec.length, motor_spec.case_mass
    i_case = case_mass * (6.0 * r**2 + length**2) / 12.0
    motor = rocketpy.GenericMotor(
        thrust_source=str(MOTOR_FILE),
        burn_time=motor_spec.burn_time,
        chamber_radius=r,
        chamber_height=length,
        chamber_position=0.5 * length,
        propellant_initial_mass=motor_spec.propellant_mass,
        nozzle_radius=0.5 * r,
        dry_mass=case_mass,
        center_of_dry_mass_position=0.5 * length,
        dry_inertia=(i_case, i_case, case_mass * r**2),
        nozzle_position=0.0,
        coordinate_system_orientation="nozzle_to_combustion_chamber",
    )

    off, on = drag_curves()
    rocket = rocketpy.Rocket(
        radius=0.5 * apogee_rocket.diameter,
        mass=dry.mass,
        inertia=(dry.iyy, dry.iyy, dry.ixx),
        power_off_drag=off,
        power_on_drag=on,
        center_of_mass_without_motor=dry.cg,
        coordinate_system_orientation="nose_to_tail",
    )
    rocket.add_motor(motor, position=apogee_rocket.motor_position + length)

    for part in apogee_rocket.parts:
        c = part.component
        if isinstance(c, NoseCone):
            rocket.add_nose(length=c.length, kind="von karman", position=part.position)
        elif type(c).__name__ == "FinSet":
            rocket.add_trapezoidal_fins(
                n=c.count,
                root_chord=c.root_chord,
                tip_chord=c.tip_chord,
                span=c.span,
                position=part.position,
                sweep_length=c.sweep,
            )
    for chute in apogee_rocket.parachutes:
        rocket.add_parachute(
            chute.name,
            cd_s=chute.cd * math.pi * chute.diameter**2 / 4.0,
            trigger="apogee" if chute.deploy_altitude is None else chute.deploy_altitude,
            sampling_rate=100,
            lag=0.0,
        )

    return rocketpy.Flight(
        rocket=rocket,
        environment=env,
        rail_length=case.rail_length,
        inclination=90.0 - case.tilt,
        heading=case.heading,
        max_time=1200,
        rtol=1e-8,
        atol=None,
        terminate_on_apogee=False,
    )


def summarise(case: Case, flight: rocketpy.Flight) -> dict[str, float]:
    """Headline numbers in the same units Apogee reports."""
    t = np.asarray(flight.time)
    speed = np.asarray(flight.speed(t))
    accel = np.asarray(flight.acceleration(t))
    burn = t <= BURNOUT
    chutes = flight.parachute_events
    main_open = next((ev[0] for ev in chutes if ev[1].name == "Main parachute"), math.nan)
    x_land, y_land = float(flight.x_impact), float(flight.y_impact)
    return {
        "apogee_m": float(flight.apogee - flight.env.elevation),
        "time_to_apogee_s": float(flight.apogee_time),
        "max_speed_mps": float(speed.max()),
        "max_mach": float(flight.max_mach_number),
        "max_acceleration_g": float(accel[burn].max() / 9.80665),
        "rail_exit_speed_mps": float(flight.out_of_rail_velocity),
        "rail_exit_time_s": float(flight.out_of_rail_time),
        "burnout_altitude_m": float(flight.z(BURNOUT) - flight.env.elevation),
        "main_deploy_time_s": float(main_open),
        "landing_time_s": float(flight.t_final),
        "landing_east_m": x_land,
        "landing_north_m": y_land,
        "landing_distance_m": math.hypot(x_land, y_land),
        "static_margin_liftoff_cal": float(flight.rocket.static_margin(0.0)),
        "static_margin_burnout_cal": float(flight.rocket.static_margin(BURNOUT)),
    }


def write_series(case: Case, flight: rocketpy.Flight) -> None:
    """Altitude, speed and downrange distance every 0.1 s, rail to touchdown."""
    end = float(flight.t_final)
    with (OUT / f"{case.name}.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["t_s", "altitude_m", "speed_mps", "east_m", "north_m"])
        for t in np.arange(0.0, end, 0.1):
            w.writerow(
                [
                    f"{t:.1f}",
                    f"{float(flight.z(t)) - flight.env.elevation:.2f}",
                    f"{float(flight.speed(t)):.3f}",
                    f"{float(flight.x(t)):.2f}",
                    f"{float(flight.y(t)):.2f}",
                ]
            )


def main() -> None:
    """Fly every case and write the reference files."""
    OUT.mkdir(exist_ok=True)
    results: dict[str, object] = {
        "simulator": "RocketPy 1.13.0",
        "rocket": "examples/rocket.yaml",
        "cases": {},
    }
    cases: dict[str, object] = {}
    for case in CASES:
        flight = build(case)
        summary = summarise(case, flight)
        cases[case.name] = {"title": case.title, "launch": vars(case), **summary}
        write_series(case, flight)
        print(
            f"{case.name:6s} apogee {summary['apogee_m']:8.1f} m  "
            f"max speed {summary['max_speed_mps']:6.1f} m/s  "
            f"landing {summary['landing_distance_m']:7.1f} m"
        )
    results["cases"] = cases
    (OUT / "reference.json").write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
