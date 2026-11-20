"""Command-line interface: ``apogee <command> ...``.

Commands
--------
``apogee atmosphere [ALTITUDE ...]``
    Print a 1976 US Standard Atmosphere table. Altitudes accept a unit suffix
    (``m``, ``km`` or ``ft``) and default to sea level through 20 km.
"""

from __future__ import annotations

import argparse
import csv
import math
import re
import sys
from collections.abc import Sequence
from typing import TextIO

import numpy as np

from apogee import __version__
from apogee.atmosphere import MAX_ALTITUDE, MIN_ALTITUDE, OutOfAtmosphereError, atmosphere

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


def _print_table(rows: list[list[str]], out: TextIO) -> None:
    head = [name for name, _ in _COLUMNS]
    units = [f"[{unit}]" for _, unit in _COLUMNS]
    widths = [max(len(c) for c in col) for col in zip(head, units, *rows, strict=False)]
    gap = "  "
    out.write(gap.join(h.rjust(w) for h, w in zip(head, widths, strict=True)) + "\n")
    out.write(gap.join(u.rjust(w) for u, w in zip(units, widths, strict=True)) + "\n")
    out.write(gap.join("─" * w for w in widths) + "\n")
    for row in rows:
        out.write(gap.join(c.rjust(w) for c, w in zip(row, widths, strict=True)) + "\n")
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
        print(f"apogee: error: {exc}", file=sys.stderr)
        return 2
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
    return parser


def main(argv: Sequence[str] | None = None, out: TextIO | None = None) -> int:
    """Run the CLI and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help(out or sys.stdout)
        return 0
    code: int = args.handler(args, out or sys.stdout)
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
