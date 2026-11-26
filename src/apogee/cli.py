"""Command-line interface: ``apogee <command> ...``.

Commands
--------
``apogee atmosphere [ALTITUDE ...]``
    Print a 1976 US Standard Atmosphere table. Altitudes accept a unit suffix
    (``m``, ``km`` or ``ft``) and default to sea level through 20 km.
``apogee rocket [FILE]``
    Load a rocket YAML file and print its parts, mass, centre of gravity and
    moments of inertia at liftoff and burnout. Without FILE, the bundled
    example rocket (Kestrel) is shown.
``apogee motor [FILE]``
    Summarise a RASP ``.eng`` thrust curve: impulse, class check, thrust.
``apogee aero [FILE]``
    Barrowman centre of pressure and stability margin through the burn, and
    the drag coefficient by source from Mach 0.1 upwards.
``apogee simulate [FILE]``
    Fly the rocket from the rail to touchdown: an event table, the headline
    numbers (apogee, max speed and acceleration, rail-exit speed, descent
    rates, landing point) and a CSV of every time step.
``apogee dispersion [FILE]``
    Monte Carlo: fly the rocket many times with the wind, rail, mass, impulse,
    drag and parachutes scattered, then report the spread of apogees and the
    landing zone as a 95 % ellipse, with a CSV of every flight.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import re
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

import numpy as np

from apogee import __version__
from apogee.aero import Aerodynamics
from apogee.atmosphere import MAX_ALTITUDE, MIN_ALTITUDE, OutOfAtmosphereError, atmosphere
from apogee.components import MassProperties
from apogee.earth import G0
from apogee.environment import LaunchSite, Wind
from apogee.flight import Flight, FlightError, simulate
from apogee.montecarlo import Dispersion, Scatter, monte_carlo
from apogee.motor import Motor, MotorFileError, load_motor
from apogee.playback import write_playback
from apogee.rocket import Rocket, RocketFileError, load_rocket

_UNIT_TO_METRES = {"m": 1.0, "km": 1000.0, "ft": 0.3048}
_ALTITUDE_RE = re.compile(r"^\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*(m|km|ft)?\s*$")
_DEFAULT_ALTITUDES = [float(km) * 1000.0 for km in range(0, 21, 2)]


def parse_altitude(text: str) -> float:
    """Parse ``"1500"``, ``"1.5km"`` or ``"5000ft"`` into metres.

    Raises:
        argparse.ArgumentTypeError: With a message that shows valid examples.
    """
    match = _ALTITUDE_RE.match(text.replace(",", "").replace("_", ""))
    if match is None:
        raise argparse.ArgumentTypeError(
            f"cannot read altitude {text!r}; use a number of metres or add a unit, "
            "e.g. 1500, 1.5km or 5000ft"
        )
    value, unit = match.groups()
    return float(value) * _UNIT_TO_METRES[unit or "m"]


def _mach(text: str) -> float:
    """Parse a Mach number for argparse."""
    try:
        value = float(text)
    except ValueError:
        value = -1.0
    if not 0.0 <= value <= 5.0:
        raise argparse.ArgumentTypeError(f"Mach number must be between 0 and 5, got {text!r}")
    return value


def _sig(x: float, digits: int = 5) -> str:
    """Format to ``digits`` significant figures without scientific notation."""
    if x == 0.0:
        return "0"
    decimals = max(0, digits - 1 - math.floor(math.log10(abs(x))))
    return f"{x:,.{decimals}f}"


_COLUMNS: list[tuple[str, str]] = [
    ("Altitude", "m"),
    ("Temp", "K"),
    ("Temp", "°C"),
    ("Pressure", "Pa"),
    ("Density", "kg/m³"),
    ("Sound speed", "m/s"),
    ("Viscosity", "µPa·s"),
]


def _atmosphere_rows(altitudes: Sequence[float]) -> list[list[str]]:
    air = atmosphere(np.asarray(altitudes, dtype=np.float64))
    rows = []
    for i, z in enumerate(altitudes):
        t = float(air.temperature[i])
        rows.append(
            [
                f"{z:,.0f}",
                f"{t:.2f}",
                f"{t - 273.15:.2f}",
                _sig(float(air.pressure[i])),
                _sig(float(air.density[i])),
                f"{float(air.speed_of_sound[i]):.2f}",
                f"{float(air.dynamic_viscosity[i]) * 1e6:.4f}",
            ]
        )
    return rows


def _table(
    columns: Sequence[tuple[str, str]],
    rows: Sequence[Sequence[str]],
    out: TextIO,
    *,
    left: int = 0,
) -> None:
    """Write an aligned table with a unit row. The first ``left`` columns are left-aligned."""
    head = [name for name, _ in columns]
    units = [f"[{unit}]" if unit else "" for _, unit in columns]
    widths = [max(len(c) for c in col) for col in zip(head, units, *rows, strict=False)]

    def line(cells: Sequence[str]) -> str:
        return "  ".join(
            c.ljust(w) if i < left else c.rjust(w)
            for i, (c, w) in enumerate(zip(cells, widths, strict=True))
        ).rstrip()

    out.write(line(head) + "\n")
    if any(units):
        out.write(line(units) + "\n")
    out.write("  ".join("─" * w for w in widths) + "\n")
    for row in rows:
        out.write(line(row) + "\n")


def _print_table(rows: list[list[str]], out: TextIO) -> None:
    _table(_COLUMNS, rows, out)
    out.write("\n1976 US Standard Atmosphere · geometric altitude above mean sea level\n")


def _write_csv(altitudes: Sequence[float], out: TextIO) -> None:
    air = atmosphere(np.asarray(altitudes, dtype=np.float64))
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(
        [
            "altitude_m",
            "temperature_K",
            "pressure_Pa",
            "density_kg_m3",
            "speed_of_sound_m_s",
            "dynamic_viscosity_Pa_s",
            "kinematic_viscosity_m2_s",
        ]
    )
    for i, z in enumerate(altitudes):
        writer.writerow(
            [
                f"{z:.6g}",
                f"{air.temperature[i]:.6f}",
                f"{air.pressure[i]:.8g}",
                f"{air.density[i]:.8g}",
                f"{air.speed_of_sound[i]:.6f}",
                f"{air.dynamic_viscosity[i]:.8g}",
                f"{air.kinematic_viscosity[i]:.8g}",
            ]
        )


def _cmd_atmosphere(args: argparse.Namespace, out: TextIO) -> int:
    altitudes: list[float] = args.altitudes or _DEFAULT_ALTITUDES
    try:
        if args.csv:
            _write_csv(altitudes, out)
        else:
            _print_table(_atmosphere_rows(altitudes), out)
    except OutOfAtmosphereError as exc:
        return _error(exc)
    return 0


def example_path(name: str) -> Path:
    """Locate a bundled example file (installed package or source checkout)."""
    installed = Path(__file__).parent / "examples" / name
    if installed.exists():
        return installed
    return Path(__file__).parents[2] / "examples" / name


def _error(message: object) -> int:
    print(f"apogee: error: {message}", file=sys.stderr)
    return 2


_SPARK = " ▁▂▃▄▅▆▇█"


def thrust_sparkline(motor: Motor, width: int = 48) -> str:
    """Draw the thrust curve as one line of block characters."""
    t = np.linspace(0.0, motor.burn_time, width)
    f = np.interp(t, motor.time, motor.thrust)
    levels = np.rint(f / f.max() * (len(_SPARK) - 1)).astype(int)
    return "".join(_SPARK[max(lv, 1 if fv > 0 else 0)] for lv, fv in zip(levels, f, strict=True))


def _motor_lines(motor: Motor) -> list[tuple[str, str]]:
    check = motor.impulse_check()
    verdict = "matches the designation" if check is None else "does NOT match the designation"
    return [
        (
            "Total impulse",
            f"{motor.total_impulse:,.1f} N·s  (class {motor.impulse_class}, {verdict})",
        ),
        ("Average thrust", f"{motor.average_thrust:,.1f} N"),
        ("Peak thrust", f"{motor.peak_thrust:,.1f} N"),
        ("Burn time", f"{motor.burn_time:.2f} s"),
        (
            "Propellant",
            f"{motor.propellant_mass:.3f} kg of {motor.loaded_mass:.3f} kg loaded",
        ),
        ("Specific impulse", f"{motor.specific_impulse:.1f} s"),
    ]


def _cmd_motor(args: argparse.Namespace, out: TextIO) -> int:
    try:
        motor = load_motor(args.file or example_path("motors/Cesaroni_M1670.eng"), args.designation)
    except (FileNotFoundError, MotorFileError) as exc:
        return _error(exc)
    out.write(
        f"{motor.manufacturer} {motor.designation}  ·  "
        f"{motor.diameter * 1000:.0f} × {motor.length * 1000:.0f} mm\n\n"
    )
    for label, value in _motor_lines(motor):
        out.write(f"{label:<17} {value}\n")
    spark = thrust_sparkline(motor)
    out.write(f"\n{'Thrust curve':<17} {spark}\n")
    out.write(f"{'':<17} 0 s{f'{motor.burn_time:.2f} s':>{len(spark) - 3}}\n")
    for w in motor.warnings:
        out.write(f"\nwarning: {w}\n")
    return 0


def _props_row(label: str, p: MassProperties) -> list[str]:
    # Significant figures, so a 50 g model rocket reads as clearly as a 50 kg one.
    return [label, _sig(p.mass, 4), f"{p.cg * 1000:,.1f}", _sig(p.ixx, 4), _sig(p.iyy, 4)]


def _cmd_rocket(args: argparse.Namespace, out: TextIO) -> int:
    try:
        rocket: Rocket = load_rocket(args.file or example_path("rocket.yaml"))
    except (FileNotFoundError, RocketFileError) as exc:
        return _error(exc)

    out.write(f"{rocket.name}\n")
    if rocket.description:
        out.write(f"{rocket.description}\n")
    out.write(
        f"Length {rocket.length * 1000:,.0f} mm  ·  diameter {rocket.diameter * 1000:.0f} mm  ·  "
        f"{len(rocket.parts)} parts\n\n"
    )

    rows = []
    for placed in rocket.parts:
        p = placed.mass_properties()
        indent = "  " if placed.parent else ""
        measured = " *" if placed.component.mass_override is not None else ""
        rows.append(
            [
                f"{indent}{placed.component.name}{measured}",
                f"{p.mass * 1000:,.1f}",
                f"{placed.position * 1000:,.0f}",
                f"{p.cg * 1000:,.0f}",
            ]
        )
    motor = rocket.motor
    motor_props = rocket.motor_mass_properties(0.0)
    if motor is not None and motor_props is not None:
        rows.append(
            [
                f"Motor {motor.designation} ({motor.manufacturer})",
                f"{motor_props.mass * 1000:,.1f}",
                f"{rocket.motor_position * 1000:,.0f}",
                f"{motor_props.cg * 1000:,.0f}",
            ]
        )
    _table([("Part", ""), ("Mass", "g"), ("Front", "mm"), ("CG", "mm")], rows, out, left=1)
    if any(p.component.mass_override is not None for p in rocket.parts):
        out.write("* measured mass\n")
    out.write("Positions are measured aft from the nose tip.\n\n")

    dry = rocket.dry_mass_properties()
    states = [_props_row("Dry, no motor", dry)]
    if motor is not None:
        lift = rocket.mass_properties(0.0)
        burnout = rocket.mass_properties(motor.burn_time)
        states += [_props_row("Liftoff", lift), _props_row("Burnout", burnout)]
    _table(
        [("", ""), ("Mass", "kg"), ("CG", "mm"), ("Ixx roll", "kg·m²"), ("Iyy pitch", "kg·m²")],
        states,
        out,
        left=1,
    )

    if motor is not None:
        weight = lift.mass * 9.80665
        shift = (lift.cg - burnout.cg) * 1000
        out.write(
            f"\nMotor   {motor.manufacturer} {motor.designation}  ·  "
            f"class {motor.impulse_class}  ·  "
            f"{motor.total_impulse:,.0f} N·s  ·  {motor.burn_time:.2f} s burn\n"
            f"Thrust-to-weight at liftoff {motor.average_thrust / weight:.1f} average, "
            f"{motor.peak_thrust / weight:.1f} peak\n"
            f"The CG moves {abs(shift):.0f} mm {'forward' if shift > 0 else 'aft'} "
            "as the propellant burns.\n"
        )
    for chute in rocket.parachutes:
        when = (
            "at apogee"
            if chute.deploy_altitude is None
            else f"at {chute.deploy_altitude:,.0f} m on descent"
        )
        out.write(f"{chute.name}: {chute.diameter:.2f} m, Cd {chute.cd:.2f}, opens {when}\n")
    for w in rocket.warnings:
        out.write(f"\nwarning: {w}\n")
    if args.file is None:
        out.write(
            "\nThis is the built-in example. Describe your own rocket in YAML and run\n"
            "  apogee rocket my_rocket.yaml\n"
        )
    return 0


_DEFAULT_MACHS = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.2, 1.5, 2.0]


def stability_verdict(margin: float) -> str:
    """One plain-language phrase about a static margin in calibres."""
    if margin < 0.0:
        return "UNSTABLE: the CP is ahead of the CG; add fin area or nose weight"
    if margin < 1.0:
        return "marginal: below the usual 1-calibre minimum; add fin area or nose weight"
    if margin <= 3.0:
        return "stable"
    return "very stable: expect it to weathercock noticeably into the wind"


def _cmd_aero(args: argparse.Namespace, out: TextIO) -> int:
    try:
        rocket = load_rocket(args.file or example_path("rocket.yaml"))
        aero = Aerodynamics(rocket)
    except (FileNotFoundError, RocketFileError, ValueError) as exc:
        return _error(exc)
    d = aero.reference_diameter

    out.write(f"{rocket.name}  ·  aerodynamics\n")
    out.write(
        f"Reference diameter {d * 1000:.0f} mm  ·  area {aero.reference_area * 1e4:.1f} cm²  ·  "
        f"finish: {rocket.finish} ({rocket.roughness * 1e6:g} µm)\n\n"
    )

    parts = aero.components(0.0)
    rows = [[c.name, f"{c.cn_alpha:.3f}", f"{c.cp * 1000:,.0f}"] for c in parts]
    rows.append(
        ["Whole rocket", f"{aero.cn_alpha(0.0):.3f}", f"{aero.center_of_pressure() * 1000:,.0f}"]
    )
    _table([("Component", ""), ("CNα", "1/rad"), ("CP", "mm")], rows, out, left=1)
    out.write("Barrowman, small angle of attack, Mach 0. Positions from the nose tip.\n\n")

    cp = aero.center_of_pressure()
    states = [("Liftoff", 0.0)]
    if rocket.motor is not None:
        states.append(("Burnout", rocket.motor.burn_time))
    margin_rows = []
    margins = []
    for label, t in states:
        cg = rocket.mass_properties(t).cg
        margins.append((cp - cg) / d)
        margin_rows.append([label, f"{cg * 1000:,.0f}", f"{cp * 1000:,.0f}", f"{margins[-1]:.2f}"])
    _table([("", ""), ("CG", "mm"), ("CP", "mm"), ("Margin", "cal")], margin_rows, out, left=1)
    out.write(f"Off the rail: {stability_verdict(margins[0])}.")
    if len(margins) > 1:
        out.write(" The margin grows as the CG moves forward through the burn.")
    out.write("\n")
    alpha = math.radians(5.0)
    out.write(
        f"At 5° angle of attack the body's own lift moves the CP to "
        f"{aero.center_of_pressure(0.0, alpha) * 1000:,.0f} mm "
        f"(liftoff margin {aero.stability_margin(0.0, alpha=alpha):.2f} cal).\n\n"
    )

    machs: list[float] = args.mach or _DEFAULT_MACHS
    drag_rows = []
    for m in machs:
        coast = aero.drag(m, args.altitude)
        burn = aero.drag(m, args.altitude, thrusting=True)
        drag_rows.append(
            [
                f"{m:.2f}",
                f"{coast.reynolds / 1e6:.1f}",
                f"{coast.friction:.3f}",
                f"{coast.fin_pressure:.3f}",
                f"{coast.base:.3f}",
                f"{coast.nose:.3f}",
                f"{coast.total:.3f}",
                f"{burn.total:.3f}",
                f"{aero.center_of_pressure(m) * 1000:,.0f}",
            ]
        )
    _table(
        [
            ("Mach", ""),
            ("Re", "million"),
            ("Friction", ""),
            ("Fins", ""),
            ("Base", ""),
            ("Nose", ""),
            ("Cd coast", ""),
            ("Cd burn", ""),
            ("CP", "mm"),
        ],
        drag_rows,
        out,
    )
    out.write(
        f"Zero-lift drag coefficients at {args.altitude:,.0f} m, on the reference area. "
        "'Cd burn' has the motor exhaust\nfilling part of the base.\n"
    )
    limit = aero.max_reliable_mach()
    if any(m > limit for m in machs):
        out.write(
            f"Above Mach {limit:.1f} the nose's wave drag is not modelled yet for this "
            "shape,\nso Cd there is a lower bound.\n"
        )
    for w in [*rocket.warnings, *aero.warnings]:
        out.write(f"\nwarning: {w}\n")
    return 0


_COMPASS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


#: Where the browser flight viewer is published.
VIEWER_URL = "https://haneet10.github.io/Apogee/"


def compass(bearing: float) -> str:
    """Nearest of the eight compass points, e.g. ``compass(260) == "W"``."""
    return _COMPASS[round(bearing / 45.0) % 8]


def _non_negative(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        value = -1.0
    if value < 0.0 or not math.isfinite(value):
        raise argparse.ArgumentTypeError(f"expected a number of zero or more, got {text!r}")
    return value


def _site_description(site: LaunchSite) -> str:
    rail = f"{site.rail_length:g} m rail"
    rail += f", tilted {site.tilt:g}° to {compass(site.heading)}" if site.tilt else ", vertical"
    wind = site.wind
    calm = (
        "calm"
        if wind.speed == 0.0
        else (f"wind {wind.speed:g} m/s from {compass(wind.direction)} ({wind.direction:g}°)")
    )
    pad = f"pad at {site.altitude:,.0f} m" if site.altitude else "pad at sea level"
    return f"{rail}  ·  {calm}  ·  {pad}"


def default_csv_path(rocket_file: str | None, rocket: Rocket) -> Path:
    """``kestrel-flight.csv``: the rocket's name, or its file name if it has none."""
    named = rocket.name and rocket.name != "Unnamed rocket"
    stem = rocket.name if named or not rocket_file else Path(rocket_file).stem
    slug = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-") or "rocket"
    return Path(f"{slug}-flight.csv")


def _flight_report(flight: Flight, out: TextIO) -> None:
    rows = []
    for e in flight.events:
        rows.append(
            [
                e.name,
                f"{e.time:,.2f}",
                f"{e.altitude:,.0f}" if e.altitude >= 100 else f"{abs(e.altitude):,.1f}",
                f"{e.speed:,.1f}",
                e.note,
            ]
        )
    columns = [("Event", ""), ("Time", "s"), ("Altitude", "m"), ("Speed", "m/s")]
    if any(e.note for e in flight.events):
        columns.append(("", ""))
    else:
        rows = [r[:-1] for r in rows]
    _table(columns, rows, out, left=1)
    out.write("\n")

    speed, t_speed = flight.max_speed
    accel, t_accel = flight.max_acceleration
    lines = [
        (
            "Apogee",
            f"{flight.apogee:,.0f} m above the pad, {flight.time_to_apogee:.1f} s after ignition",
        ),
        ("Max speed", f"{speed:,.0f} m/s (Mach {flight.max_mach:.2f}) at {t_speed:.1f} s"),
        ("Max acceleration", f"{accel / G0:.1f} g at {t_accel:.2f} s"),
        ("Off the rail", f"{flight.rail_exit_speed:.1f} m/s"),
    ]
    rates = []
    for e in flight.events:
        if e.name.endswith(" opens"):
            chute = e.name.removesuffix(" opens")
            rate = flight.descent_rate(f"under {chute}")
            if rate is not None:
                rates.append(f"{rate:.1f} m/s under the {chute[0].lower()}{chute[1:]}")
    if rates:
        lines.append(("Descent", ", then ".join(rates)))
    landing = flight.landing
    if landing is not None:
        dist = flight.landing_distance
        where = (
            "on the pad"
            if dist < 1.0
            else f"{dist:,.0f} m {compass(flight.landing_bearing)} of the pad "
            f"(bearing {flight.landing_bearing:.0f}°)"
        )
        lines.append(("Landing", f"{where} at {-float(landing.velocity[2]):.1f} m/s"))
    lines.append(("Flight time", f"{flight.flight_time:,.1f} s"))
    width = max(len(k) for k, _ in lines)
    for label, value in lines:
        out.write(f"{label:<{width}}  {value}\n")


def _cmd_simulate(args: argparse.Namespace, out: TextIO) -> int:
    try:
        rocket = load_rocket(args.file or example_path("rocket.yaml"))
        site = LaunchSite(
            rail_length=args.rail,
            tilt=args.tilt,
            heading=args.heading,
            altitude=args.elevation,
            wind=Wind(speed=args.wind, direction=args.wind_from),
        )
        if args.target is not None:
            rocket = rocket.with_target(args.target)
        if args.no_airbrakes and rocket.airbrakes is not None:
            rocket = rocket.with_target(None)
        flight = simulate(rocket, site, dt=args.dt)
        passive = (
            simulate(rocket, site, dt=args.dt, airbrakes=False)
            if flight.target_apogee is not None
            else None
        )
    except (FileNotFoundError, RocketFileError, FlightError, ValueError) as exc:
        return _error(exc)

    motor = rocket.motor
    assert motor is not None  # simulate() refuses rockets without one
    out.write(f"{rocket.name}  ·  {motor.manufacturer} {motor.designation}\n")
    out.write(_site_description(site) + "\n\n")
    _flight_report(flight, out)
    if passive is not None:
        out.write("\n")
        _airbrake_report(flight, passive, out)

    if args.output != "none":
        path = Path(args.output) if args.output else default_csv_path(args.file, rocket)
        try:
            n = flight.write_csv(path)
        except OSError as exc:
            return _error(f"cannot write {path}: {exc.strerror or exc}")
        out.write(f"\nWrote {n:,} time steps to {path}\n")
    if args.json:
        try:
            n = write_playback(flight, args.json, passive)
        except OSError as exc:
            return _error(f"cannot write {args.json}: {exc.strerror or exc}")
        out.write(f"Wrote a {n:,}-sample playback to {args.json}; open it at {VIEWER_URL}\n")
    for w in [*rocket.warnings, *flight.warnings]:
        out.write(f"\nwarning: {w}\n")
    return 0


def _airbrake_report(flight: Flight, passive: Flight, out: TextIO) -> None:
    """How the flight computer did against its target and against doing nothing."""
    target = flight.target_apogee
    log = flight.airbrake_log
    assert target is not None and log is not None
    error = flight.apogee - target
    cols = log.arrays()
    deployment = cols["deployment"]
    opened = flight.event("Airbrakes open")
    lines = [
        ("Target apogee", f"{target:,.0f} m"),
        ("Reached", f"{flight.apogee:,.1f} m ({error:+.1f} m, {100 * error / target:+.2f} %)"),
        (
            "Without airbrakes",
            f"{passive.apogee:,.0f} m ({passive.apogee - target:+,.0f} m)",
        ),
    ]
    if opened is not None:
        busy = deployment[deployment > 0.0]
        mach = opened.speed / _sound(flight.site.altitude + opened.altitude)
        lines.append(
            (
                "Airbrakes",
                f"opened at {opened.time:.1f} s, Mach {mach:.2f}; "
                f"{100 * float(busy.mean()):.0f} % open on average, "
                f"{100 * float(deployment.max()):.0f} % at most",
            )
        )
    else:
        lines.append(("Airbrakes", "stayed stowed: the rocket would not reach the target"))
    k = float(cols["drag_factor"][-1])
    lines.append(("Drag learned", f"{k:.3f} × the pre-flight drag model"))
    width = max(len(k) for k, _ in lines)
    for label, value in lines:
        out.write(f"{label:<{width}}  {value}\n")


def _sound(height: float) -> float:
    return float(atmosphere(min(max(height, -5000.0), 86000.0)).speed_of_sound)


def default_dispersion_path(rocket_file: str | None, rocket: Rocket) -> Path:
    """``kestrel-dispersion.csv``, named like :func:`default_csv_path`."""
    flight_csv = default_csv_path(rocket_file, rocket)
    return flight_csv.with_name(flight_csv.name.replace("-flight.csv", "-dispersion.csv"))


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        value = 0
    if value < 1:
        raise argparse.ArgumentTypeError(f"expected a whole number of 1 or more, got {text!r}")
    return value


def _dispersion_report(d: Dispersion, out: TextIO) -> None:
    def stats(values: np.ndarray, nominal: float, fmt: str) -> list[str]:
        p5, p95 = np.quantile(values, [0.05, 0.95])
        return [
            fmt.format(nominal),
            fmt.format(values.mean()),
            fmt.format(values.std(ddof=1)),
            fmt.format(p5),
            fmt.format(p95),
        ]

    def column(name: str) -> np.ndarray:
        return np.array([getattr(s, name) for s in d.samples])

    n = d.nominal
    rows = [
        ["Apogee", "[m]", *stats(d.apogee, n.apogee, "{:,.0f}")],
        ["Max speed", "[m/s]", *stats(column("max_speed"), n.max_speed, "{:,.0f}")],
        ["Off the rail", "[m/s]", *stats(column("rail_exit_speed"), n.rail_exit_speed, "{:.1f}")],
        [
            "Landing distance",
            "[m]",
            *stats(d.landing_distance, n.landing_distance, "{:,.0f}"),
        ],
        ["Landing speed", "[m/s]", *stats(column("landing_speed"), n.landing_speed, "{:.1f}")],
    ]
    columns = [
        ("", ""),
        ("", ""),
        ("Nominal", ""),
        ("Mean", ""),
        ("1σ", ""),
        ("5 %", ""),
        ("95 %", ""),
    ]
    _table(columns, rows, out, left=2)
    out.write("\n")

    e = d.ellipse(0.95)
    held = float(np.mean(e.contains(d.east, d.north)))
    centre_dist = math.hypot(e.east, e.north)
    bearing = math.degrees(math.atan2(e.east, e.north)) % 360.0
    centre = (
        "on the pad" if centre_dist < 1.0 else f"{centre_dist:,.0f} m {compass(bearing)} of the pad"
    )
    out.write(f"Landing zone  95 % ellipse centred {centre}\n")
    out.write(
        f"              {2 * e.semi_major:,.0f} m long (axis bearing {e.bearing:.0f}°) by "
        f"{2 * e.semi_minor:,.0f} m wide; it holds {100 * held:.0f} % of these flights\n"
    )
    out.write(f"Recovery      95 % of flights land within {d.radius(0.95):,.0f} m of the pad\n")


def _cmd_dispersion(args: argparse.Namespace, out: TextIO) -> int:
    try:
        rocket = load_rocket(args.file or example_path("rocket.yaml"))
        site = LaunchSite(
            rail_length=args.rail,
            tilt=args.tilt,
            heading=args.heading,
            altitude=args.elevation,
            wind=Wind(speed=args.wind, direction=args.wind_from),
        )
        scatter = Scatter(
            wind_speed=args.wind_scatter,
            wind_direction=args.wind_from_scatter,
            tilt=args.tilt_scatter,
            heading=args.heading_scatter,
            dry_mass=args.mass_scatter / 100.0,
            thrust=args.impulse_scatter / 100.0,
            drag=args.drag_scatter / 100.0,
            parachute_cd=args.chute_scatter / 100.0,
        )
    except (FileNotFoundError, RocketFileError, ValueError) as exc:
        return _error(exc)
    if rocket.motor is None:
        return _error("the rocket has no motor; add a `motor:` section to fly it")

    motor = rocket.motor
    out.write(f"{rocket.name}  ·  {motor.manufacturer} {motor.designation}\n")
    out.write(_site_description(site) + "\n")
    out.write(f"Scatter (1σ): {scatter.describe()}\n\n")
    out.flush()

    live = sys.stderr.isatty()
    started = time.monotonic()

    def progress(done: int, total: int) -> None:
        if live:
            sys.stderr.write(f"\rFlying {total:,} flights ... {done:,}/{total:,}")
            sys.stderr.flush()

    try:
        d = monte_carlo(
            rocket,
            site,
            runs=args.runs,
            scatter=scatter,
            seed=args.seed,
            workers=args.workers,
            dt=args.dt,
            progress=progress,
        )
    except (FlightError, ValueError) as exc:
        if live:
            sys.stderr.write("\n")
        return _error(exc)
    if live:
        sys.stderr.write("\r\033[K")
        sys.stderr.flush()

    elapsed = time.monotonic() - started
    out.write(f"{len(d.samples):,} flights, seed {d.seed}, {elapsed:.0f} s\n\n")
    _dispersion_report(d, out)

    if args.output != "none":
        path = Path(args.output) if args.output else default_dispersion_path(args.file, rocket)
        try:
            n = d.write_csv(path)
        except OSError as exc:
            return _error(f"cannot write {path}: {exc.strerror or exc}")
        out.write(f"\nWrote {n:,} flights to {path}\n")
    for w in rocket.warnings:
        out.write(f"\nwarning: {w}\n")
    return 0


def _cmd_airbrakes(args: argparse.Namespace, out: TextIO) -> int:
    try:
        rocket = load_rocket(args.file or example_path("kestrel-airbrakes.yaml"))
        if args.target is not None:
            rocket = rocket.with_target(args.target)
        site = LaunchSite(
            rail_length=args.rail,
            tilt=args.tilt,
            heading=args.heading,
            altitude=args.elevation,
            wind=Wind(speed=args.wind, direction=args.wind_from),
        )
    except (FileNotFoundError, RocketFileError, ValueError) as exc:
        return _error(exc)
    target = rocket.target_apogee
    if rocket.airbrakes is None:
        return _error(f"{rocket.name} has no airbrakes; add an 'airbrakes' part to a body tube")
    if target is None:
        return _error("no target apogee: set flight_computer.target_apogee or pass --target")
    motor = rocket.motor
    if motor is None:
        return _error("the rocket has no motor; add a `motor:` section to fly it")

    scatter = Scatter()
    out.write(f"{rocket.name}  ·  {motor.manufacturer} {motor.designation}\n")
    out.write(_site_description(site) + "\n")
    out.write(f"Target apogee {target:,.0f} m\n")
    out.write(f"Scatter (1σ): {scatter.describe(airbrakes=True)}\n\n")
    out.flush()
    live = sys.stderr.isatty()
    started = time.monotonic()
    results = {}
    for label, flying in (("Passive", False), ("Airbrakes", True)):

        def progress(done: int, total: int, label: str = label) -> None:
            if live:
                sys.stderr.write(f"\r{label}: flying {total:,} flights ... {done:,}/{total:,}")
                sys.stderr.flush()

        try:
            results[label] = monte_carlo(
                rocket,
                site,
                runs=args.runs,
                scatter=scatter,
                seed=args.seed,
                workers=args.workers,
                dt=args.dt,
                progress=progress,
                airbrakes=flying,
            )
        except (FlightError, ValueError) as exc:
            if live:
                sys.stderr.write("\n")
            return _error(exc)
    if live:
        sys.stderr.write("\r\033[K")
        sys.stderr.flush()
    elapsed = time.monotonic() - started
    out.write(f"{args.runs:,} flights each way, seed {args.seed}, {elapsed:.0f} s\n\n")

    reachable = results["Passive"].apogee >= target
    groups = [(label, d.apogee) for label, d in results.items()]
    if not reachable.all() and reachable.sum() >= 2:
        groups.append(("Airbrakes, reachable", results["Airbrakes"].apogee[reachable]))
    rows = []
    for label, apogees in groups:
        err = apogees - target
        rows.append(
            [
                label,
                f"{apogees.mean():,.0f}",
                f"{err.mean():+,.1f}",
                f"{err.std(ddof=1):,.1f}",
                f"{np.abs(err).max():,.1f}",
                f"{100 * np.mean(np.abs(err) <= 15.0):.0f} %",
            ]
        )
    columns = [
        ("", ""),
        ("Mean apogee", "m"),
        ("Mean miss", "m"),
        ("1σ miss", "m"),
        ("Worst miss", "m"),
        ("Within ±15 m", ""),
    ]
    _table(columns, rows, out, left=1)
    passive, active = results["Passive"], results["Airbrakes"]
    spread_p = float((passive.apogee - target).std(ddof=1))
    spread_a = float((active.apogee - target).std(ddof=1))
    out.write(
        f"\nThe airbrakes cut the 1σ apogee scatter from {spread_p:,.0f} m to {spread_a:,.1f} m.\n"
    )
    short = int(np.sum(passive.apogee < target))
    if short:
        out.write(
            f"{short} of {args.runs} flights could not reach the target even with the "
            "airbrakes stowed (they only add drag); the last row leaves them out.\n"
        )
    if args.output != "none":
        path = (
            Path(args.output)
            if args.output
            else Path(
                str(default_dispersion_path(args.file, rocket)).replace("-dispersion", "-airbrakes")
            )
        )
        try:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh, lineterminator="\n")
                writer.writerow(["run", "passive_apogee_m", "airbrakes_apogee_m", "target_m"])
                for sp, sa in zip(passive.samples, active.samples, strict=True):
                    writer.writerow(
                        [sp.run, f"{sp.apogee:.2f}", f"{sa.apogee:.2f}", f"{target:.1f}"]
                    )
        except OSError as exc:
            return _error(f"cannot write {path}: {exc.strerror or exc}")
        out.write(f"\nWrote {args.runs:,} paired flights to {path}\n")
    return 0


def _add_site_arguments(cmd: argparse.ArgumentParser) -> None:
    """Launch rail, wind and pad options shared by ``simulate`` and ``dispersion``."""
    default = LaunchSite()
    cmd.add_argument(
        "--rail",
        type=_non_negative,
        default=default.rail_length,
        metavar="M",
        help=f"guided length of the launch rail in metres (default: {default.rail_length:g})",
    )
    cmd.add_argument(
        "--tilt",
        type=_non_negative,
        default=0.0,
        metavar="DEG",
        help="rail angle from vertical in degrees (default: 0)",
    )
    cmd.add_argument(
        "--heading",
        type=float,
        default=0.0,
        metavar="DEG",
        help="compass bearing the rail leans towards (default: 0, north)",
    )
    cmd.add_argument(
        "--wind",
        type=_non_negative,
        default=0.0,
        metavar="M/S",
        help="wind speed 10 m above the pad (default: 0, calm)",
    )
    cmd.add_argument(
        "--wind-from",
        type=float,
        default=270.0,
        metavar="DEG",
        help="compass bearing the wind blows from (default: 270, west)",
    )
    cmd.add_argument(
        "--elevation",
        type=parse_altitude,
        default=0.0,
        metavar="ALT",
        help="pad height above sea level, e.g. 1400 or 4600ft (default: 0)",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the ``apogee`` argument parser."""
    parser = argparse.ArgumentParser(
        prog="apogee",
        description="Apogee: a 6-DOF rocket flight simulator you can read.",
    )
    parser.add_argument("--version", action="version", version=f"apogee {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    atm = sub.add_parser(
        "atmosphere",
        help="print 1976 US Standard Atmosphere properties",
        description=(
            "Print temperature, pressure, density, speed of sound and viscosity "
            f"from {MIN_ALTITUDE / 1000:.0f} km to {MAX_ALTITUDE / 1000:.0f} km."
        ),
        epilog="examples:  apogee atmosphere   ·   apogee atmosphere 0 1.5km 10000ft --csv",
    )
    atm.add_argument(
        "altitudes",
        nargs="*",
        type=parse_altitude,
        metavar="ALTITUDE",
        help="geometric altitude; plain numbers are metres, or add km / ft "
        "(default: 0 to 20 km every 2 km)",
    )
    atm.add_argument("--csv", action="store_true", help="write CSV (SI units) instead of a table")
    atm.set_defaults(handler=_cmd_atmosphere)

    roc = sub.add_parser(
        "rocket",
        help="show a rocket's parts, mass, CG and inertia",
        description="Load a rocket YAML file and print its mass breakdown, centre of "
        "gravity and moments of inertia, dry, at liftoff and at burnout.",
        epilog="example:  apogee rocket examples/rocket.yaml",
    )
    roc.add_argument(
        "file", metavar="FILE", nargs="?", help="rocket description (.yaml); default: example"
    )
    roc.set_defaults(handler=_cmd_rocket)

    mot = sub.add_parser(
        "motor",
        help="summarise a motor thrust curve (.eng)",
        description="Read a RASP .eng thrust curve and print its total impulse, impulse "
        "class check, thrust and burn time.",
        epilog="example:  apogee motor examples/motors/Cesaroni_M1670.eng",
    )
    mot.add_argument(
        "file", metavar="FILE", nargs="?", help="RASP thrust curve (.eng); default: example"
    )
    mot.add_argument("--designation", metavar="NAME", help="which motor, if the file holds several")
    mot.set_defaults(handler=_cmd_motor)

    aer = sub.add_parser(
        "aero",
        help="show centre of pressure, stability margin and drag",
        description="Compute Barrowman normal-force slopes, the centre of pressure and the "
        "static margin at liftoff and burnout, and the drag coefficient by source across "
        "a range of Mach numbers.",
        epilog="examples:  apogee aero   ·   apogee aero my_rocket.yaml --mach 0.3 0.6 0.9",
    )
    aer.add_argument(
        "file", metavar="FILE", nargs="?", help="rocket description (.yaml); default: example"
    )
    aer.add_argument(
        "--mach",
        nargs="+",
        type=_mach,
        metavar="M",
        help="Mach numbers for the drag table (default: 0.1 to 2)",
    )
    aer.add_argument(
        "--altitude",
        type=parse_altitude,
        default=0.0,
        metavar="ALT",
        help="altitude for the drag table, e.g. 1500 or 5000ft (default: sea level)",
    )
    aer.set_defaults(handler=_cmd_aero)

    sim = sub.add_parser(
        "simulate",
        help="fly the rocket from the rail to touchdown",
        description="Simulate the whole flight in 6-DOF up to apogee and under the "
        "parachutes to the ground. Prints the events and headline numbers and writes "
        "every time step to a CSV file.",
        epilog="examples:  apogee simulate   ·   "
        "apogee simulate my_rocket.yaml --wind 5 --wind-from 270 --rail 2.4",
    )
    sim.add_argument(
        "file", metavar="FILE", nargs="?", help="rocket description (.yaml); default: example"
    )
    _add_site_arguments(sim)
    sim.add_argument(
        "--dt",
        type=float,
        default=0.01,
        metavar="S",
        help="6-DOF time step in seconds (default: 0.01)",
    )
    sim.add_argument(
        "-o",
        "--output",
        metavar="CSV",
        help="where to write the time steps (default: <rocket>-flight.csv here; 'none' to skip)",
    )
    sim.add_argument(
        "--json",
        metavar="FILE",
        help="also write a playback file for the browser viewer (" + VIEWER_URL + ")",
    )
    sim.add_argument(
        "--target",
        type=parse_altitude,
        metavar="ALT",
        help="apogee for the airbrakes to aim for, e.g. 4400 or 14000ft "
        "(default: the rocket file's flight_computer target)",
    )
    sim.add_argument(
        "--no-airbrakes",
        action="store_true",
        help="keep the airbrakes stowed and fly passively",
    )
    sim.set_defaults(handler=_cmd_simulate)

    dis = sub.add_parser(
        "dispersion",
        help="Monte Carlo: scatter the inputs and map the landing zone",
        description="Fly the rocket many times with the wind, rail angle, airframe mass, "
        "motor impulse, drag and parachute drag each drawn from a normal distribution. "
        "Prints the spread of apogees and speeds, the 95 % landing ellipse and the "
        "recovery radius, and writes every flight to a CSV file.",
        epilog="examples:  apogee dispersion --wind 4   ·   "
        "apogee dispersion my_rocket.yaml --runs 500 --wind 6 --wind-scatter 2",
    )
    dis.add_argument(
        "file", metavar="FILE", nargs="?", help="rocket description (.yaml); default: example"
    )
    _add_site_arguments(dis)
    defaults = Scatter()
    dis.add_argument(
        "--runs",
        type=_positive_int,
        default=100,
        metavar="N",
        help="number of scattered flights (default: 100)",
    )
    dis.add_argument("--seed", type=int, default=1, metavar="N", help="random seed (default: 1)")
    for flag, attr, unit, scale in (
        ("--wind-scatter", "wind_speed", "m/s", 1.0),
        ("--wind-from-scatter", "wind_direction", "degrees", 1.0),
        ("--tilt-scatter", "tilt", "degrees", 1.0),
        ("--heading-scatter", "heading", "degrees", 1.0),
        ("--mass-scatter", "dry_mass", "%% of the airframe mass", 100.0),
        ("--impulse-scatter", "thrust", "%% of the motor's total impulse", 100.0),
        ("--drag-scatter", "drag", "%% of the drag coefficient", 100.0),
        ("--chute-scatter", "parachute_cd", "%% of the parachute Cd", 100.0),
    ):
        value = getattr(defaults, attr) * scale
        dis.add_argument(
            flag,
            type=_non_negative,
            default=value,
            metavar="X",
            help=f"1σ scatter in {unit} (default: {value:g})",
        )
    dis.add_argument(
        "--workers",
        type=_positive_int,
        default=None,
        metavar="N",
        help="processes to fly in parallel (default: one per CPU, up to 8)",
    )
    dis.add_argument(
        "--dt",
        type=float,
        default=0.02,
        metavar="S",
        help="6-DOF time step in seconds (default: 0.02)",
    )
    dis.add_argument(
        "-o",
        "--output",
        metavar="CSV",
        help="where to write the flights (default: <rocket>-dispersion.csv here; 'none' to skip)",
    )
    dis.set_defaults(handler=_cmd_dispersion)

    ab = sub.add_parser(
        "airbrakes",
        help="Monte Carlo: how close do the airbrakes fly to the target apogee?",
        description="Fly the rocket many times with scattered wind, rail angle, mass, "
        "impulse and drag, once with the airbrakes stowed and once with the flight "
        "computer flying them (each flight with its own sensor noise). Prints how far "
        "each misses the target apogee.",
        epilog="examples:  apogee airbrakes   ·   "
        "apogee airbrakes my_rocket.yaml --target 3000 --runs 200 --wind 4",
    )
    ab.add_argument(
        "file",
        metavar="FILE",
        nargs="?",
        help="rocket description with airbrakes (.yaml); default: Kestrel AB example",
    )
    _add_site_arguments(ab)
    ab.add_argument(
        "--target",
        type=parse_altitude,
        metavar="ALT",
        help="apogee to aim for (default: the rocket file's flight_computer target)",
    )
    ab.add_argument(
        "--runs",
        type=_positive_int,
        default=50,
        metavar="N",
        help="scattered flights each way (default: 50)",
    )
    ab.add_argument("--seed", type=int, default=1, metavar="N", help="random seed (default: 1)")
    ab.add_argument(
        "--workers",
        type=_positive_int,
        default=None,
        metavar="N",
        help="processes to fly in parallel (default: one per CPU, up to 8)",
    )
    ab.add_argument(
        "--dt",
        type=float,
        default=0.02,
        metavar="S",
        help="6-DOF time step in seconds (default: 0.02)",
    )
    ab.add_argument(
        "-o",
        "--output",
        metavar="CSV",
        help="where to write the paired apogees (default: <rocket>-airbrakes.csv; 'none' to skip)",
    )
    ab.set_defaults(handler=_cmd_airbrakes)
    return parser


def main(argv: Sequence[str] | None = None, out: TextIO | None = None) -> int:
    """Run the CLI and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help(out or sys.stdout)
        return 0
    try:
        code: int = args.handler(args, out or sys.stdout)
    except BrokenPipeError:  # output piped into `head` etc.; not an error
        sys.stdout = open(os.devnull, "w")  # noqa: SIM115 - silence the flush at exit
        return 0
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
