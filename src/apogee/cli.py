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
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

import numpy as np

from apogee import __version__
from apogee.atmosphere import MAX_ALTITUDE, MIN_ALTITUDE, OutOfAtmosphereError, atmosphere
from apogee.components import MassProperties
from apogee.motor import Motor, MotorFileError, load_motor
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
