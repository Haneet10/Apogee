"""Reading physical quantities written the way people write them.

Rocket files are written by hand, usually with a ruler and a kitchen scale in
reach, so every length and mass in them may carry a unit::

    >>> parse_length("250mm")
    0.25
    >>> round(parse_mass("1.2 oz"), 6)
    0.034019
    >>> parse_length(0.5)          # bare numbers are SI: metres
    0.5

Bare numbers are always SI (metres, kilograms, kg/m³). Everything returned is SI.
"""

from __future__ import annotations

import re

__all__ = ["UnitError", "parse_density", "parse_length", "parse_mass", "parse_quantity"]

#: Conversion factors to SI for each kind of quantity.
UNITS: dict[str, dict[str, float]] = {
    "length": {"m": 1.0, "cm": 0.01, "mm": 0.001, "in": 0.0254, "ft": 0.3048},
    "mass": {"kg": 1.0, "g": 0.001, "lb": 0.45359237, "oz": 0.028349523125},
    "density": {"kg/m3": 1.0, "kg/m³": 1.0, "g/cm3": 1000.0, "g/cm³": 1000.0},
}

_QUANTITY_RE = re.compile(r"^\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*([^\s\d].*?)?\s*$")


class UnitError(ValueError):
    """A quantity could not be read; the message says what is accepted."""


def parse_quantity(value: object, kind: str, *, allow_zero: bool = False) -> float:
    """Convert ``value`` (a number or a string like ``"3.5 in"``) to SI.

    Args:
        value: A bare number (already SI) or a string with an optional unit.
        kind: ``"length"``, ``"mass"`` or ``"density"``.
        allow_zero: Accept zero. Negative values are always rejected.

    Raises:
        UnitError: If the value is not a number, has an unknown unit, or is
            negative (or zero when ``allow_zero`` is false).
    """
    units = UNITS[kind]
    examples = ", ".join(f"2.5{u}" for u in list(units)[:3])
    if isinstance(value, bool):
        raise UnitError(f"expected a {kind}, got {value!r}; write e.g. {examples}")
    if isinstance(value, int | float):
        number, factor = float(value), 1.0
    elif isinstance(value, str):
        match = _QUANTITY_RE.match(value.replace(",", "").replace("_", ""))
        if match is None:
            raise UnitError(f"cannot read {kind} {value!r}; write e.g. {examples}")
        number = float(match.group(1))
        unit = (match.group(2) or "").replace(" ", "").lower()
        if unit and unit not in units:
            raise UnitError(
                f"unknown {kind} unit {match.group(2)!r} in {value!r}; "
                f"use one of: {', '.join(units)}"
            )
        factor = units[unit] if unit else 1.0
    else:
        raise UnitError(f"expected a {kind}, got {value!r}; write e.g. {examples}")
    si = number * factor
    if si < 0.0 or (si == 0.0 and not allow_zero):
        rule = "zero or more" if allow_zero else "more than zero"
        raise UnitError(f"{kind} must be {rule}, got {value!r}")
    return si


def parse_length(value: object, *, allow_zero: bool = False) -> float:
    """Parse a length (``m``, ``cm``, ``mm``, ``in``, ``ft``) to metres."""
    return parse_quantity(value, "length", allow_zero=allow_zero)


def parse_mass(value: object, *, allow_zero: bool = False) -> float:
    """Parse a mass (``kg``, ``g``, ``lb``, ``oz``) to kilograms."""
    return parse_quantity(value, "mass", allow_zero=allow_zero)


def parse_density(value: object) -> float:
    """Parse a density (``kg/m3`` or ``g/cm3``) to kg/m³."""
    return parse_quantity(value, "density")
