r"""Rocket motors: RASP ``.eng`` thrust curves, impulse and burning mass.

The RASP format is the plain-text format every motor database (thrustcurve.org,
OpenRocket, RockSim) can export. A file holds one or more motors; each starts
with a header line and is followed by ``time thrust`` pairs::

    ; comment lines start with a semicolon
    M1670-BS 75 757 0 3.101 5.231 CTI
    0.055 100
    ...
    3.9 0

Header fields: designation, diameter [mm], length [mm], ejection delays
(dash-separated, ``0`` or ``P`` for plugged), propellant mass [kg], loaded
mass [kg] and manufacturer. The curve starts at ``(0 s, 0 N)`` implicitly and
must end at zero thrust.

Quick use::

    >>> from apogee.motor import parse_eng
    >>> motor, = parse_eng("T1 24 70 0 0.01 0.02 X\n0.1 10\n0.9 10\n1.0 0\n")
    >>> round(motor.total_impulse, 2)       # N·s, trapezoidal rule
    9.0
    >>> motor.impulse_class
    'C'

Mass model: the propellant mass falls in proportion to the impulse already
delivered, ``m_p(t) = m_p0 · (1 − I(t)/I_total)``. This is the usual assumption
when only a thrust curve is known (it is exact for a constant exhaust
velocity). The casing is a thin-walled tube and the
propellant a solid cylinder, both the full motor length, so the motor's centre
of gravity stays at its mid-length while its mass and inertia shrink.

Reference: "RASP Engine File Format", thrustcurve.org/info/raspformat.html;
impulse classes from NFPA 1122 / 1127 (each letter doubles the impulse,
A = 1.26 to 2.50 N·s).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

__all__ = ["Motor", "MotorFileError", "load_motor", "parse_eng"]


class MotorFileError(ValueError):
    """An ``.eng`` file could not be read; the message names the line."""


def impulse_class(total_impulse: float) -> str:
    """Return the NFPA impulse class letter for a total impulse [N·s].

    ``A`` covers 1.26 to 2.50 N·s and each following letter doubles the upper
    limit (``B`` to 5 N·s, ``C`` to 10 N·s, ...). Smaller motors are
    ``1/2A`` (0.626 to 1.25), ``1/4A`` and ``1/8A``.

    >>> impulse_class(8.8), impulse_class(6026.0)
    ('C', 'M')
    """
    if total_impulse <= 0.0:
        raise ValueError("total impulse must be positive")
    # 1e-9 absorbs floating-point noise exactly on a class boundary.
    n = math.ceil(math.log2(total_impulse / 2.5) - 1e-9)
    if n >= 0:
        return chr(ord("A") + n)
    return f"1/{2 ** (-n)}A"


@dataclass(frozen=True)
class Motor:
    """A solid rocket motor described by its thrust curve.

    Times are seconds from ignition; masses kilograms; lengths metres.

    Attributes:
        designation: Name from the file, e.g. ``"M1670-BS"``.
        manufacturer: Manufacturer code, e.g. ``"CTI"``.
        diameter: Casing outer diameter [m].
        length: Casing length [m].
        delays: Ejection delays [s] listed in the file (empty if plugged).
        propellant_mass: Propellant mass at ignition [kg].
        loaded_mass: Total motor mass at ignition [kg].
        time: Thrust-curve times [s], starting at 0.
        thrust: Thrust at each time [N], ending at 0.
        warnings: Data problems found while loading (the file was still usable).
    """

    designation: str
    manufacturer: str
    diameter: float
    length: float
    delays: tuple[float, ...]
    propellant_mass: float
    loaded_mass: float
    time: NDArray[np.float64]
    thrust: NDArray[np.float64]
    warnings: tuple[str, ...] = ()
    _impulse: NDArray[np.float64] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        dt = np.diff(self.time)
        steps = 0.5 * (self.thrust[1:] + self.thrust[:-1]) * dt
        object.__setattr__(self, "_impulse", np.concatenate(([0.0], np.cumsum(steps))))

    # --- Thrust curve ---------------------------------------------------------

    @property
    def burn_time(self) -> float:
        """Time at which thrust reaches zero [s]."""
        return float(self.time[-1])

    @property
    def total_impulse(self) -> float:
        """Area under the thrust curve [N·s] (trapezoidal rule)."""
        return float(self._impulse[-1])

    @property
    def average_thrust(self) -> float:
        """Total impulse divided by burn time [N]."""
        return self.total_impulse / self.burn_time

    @property
    def peak_thrust(self) -> float:
        """Maximum thrust [N]."""
        return float(self.thrust.max())

    @property
    def impulse_class(self) -> str:
        """NFPA letter class of the total impulse, e.g. ``"M"``."""
        return impulse_class(self.total_impulse)

    @property
    def specific_impulse(self) -> float:
        """Delivered specific impulse ``I / (m_p g0)`` [s]."""
        return self.total_impulse / (self.propellant_mass * 9.80665)

    @property
    def case_mass(self) -> float:
        """Mass of everything that is not propellant [kg]."""
        return self.loaded_mass - self.propellant_mass

    def thrust_at(self, t: float) -> float:
        """Thrust [N] at time ``t`` [s] after ignition, linearly interpolated."""
        return float(np.interp(t, self.time, self.thrust, left=0.0, right=0.0))

    def impulse_at(self, t: float) -> float:
        """Impulse delivered between ignition and ``t`` [N·s]."""
        if t <= 0.0:
            return 0.0
        if t >= self.burn_time:
            return self.total_impulse
        i = int(np.searchsorted(self.time, t)) - 1
        t0, f0 = self.time[i], self.thrust[i]
        slope = (self.thrust[i + 1] - f0) / (self.time[i + 1] - t0)
        dt = t - t0
        return float(self._impulse[i] + f0 * dt + 0.5 * slope * dt * dt)

    # --- Mass properties --------------------------------------------------------

    def mass_at(self, t: float) -> float:
        """Motor mass [kg] at time ``t`` [s] after ignition."""
        burned = self.impulse_at(t) / self.total_impulse
        return self.case_mass + self.propellant_mass * (1.0 - burned)

    def inertia_at(self, t: float) -> tuple[float, float]:
        """Axial and transverse moments of inertia about the motor's CG [kg·m²].

        Casing: thin-walled tube. Propellant: solid cylinder. Both span the
        whole motor length.
        """
        r, length = 0.5 * self.diameter, self.length
        m_case = self.case_mass
        m_prop = self.mass_at(t) - m_case
        ixx = m_case * r**2 + 0.5 * m_prop * r**2
        iyy = m_case * (6.0 * r**2 + length**2) / 12.0 + m_prop * (3.0 * r**2 + length**2) / 12.0
        return ixx, iyy

    # --- Checks -----------------------------------------------------------------

    @property
    def designation_class(self) -> str | None:
        """Impulse class letter stated in the designation, if there is one."""
        name = self.designation.upper()
        for prefix in ("1/8A", "1/4A", "1/2A"):
            if name.startswith(prefix):
                return prefix
        return name[0] if name[:1].isalpha() else None

    def impulse_check(self) -> str | None:
        """Compare the integrated impulse with the class in the designation.

        Returns:
            ``None`` if they agree, otherwise a sentence describing the mismatch.
        """
        stated = self.designation_class
        if stated is None or stated == self.impulse_class:
            return None
        return (
            f"{self.designation}: the thrust curve integrates to "
            f"{self.total_impulse:,.1f} N·s, which is class {self.impulse_class}, "
            f"not {stated}; check the file"
        )


def _parse_header(fields: list[str], lineno: int) -> dict[str, object]:
    if len(fields) < 7:
        raise MotorFileError(
            f"line {lineno}: motor header needs 7 fields (name, diameter mm, length mm, "
            f"delays, propellant kg, total kg, manufacturer), found {len(fields)}"
        )
    name, diameter, length, delays, prop, total, maker = fields[:7]
    try:
        values = [float(v) for v in (diameter, length, prop, total)]
    except ValueError:
        raise MotorFileError(
            f"line {lineno}: diameter, length and masses must be numbers in {fields!r}"
        ) from None
    if min(values) <= 0.0:
        raise MotorFileError(f"line {lineno}: diameter, length and masses must be positive")
    if values[2] > values[3]:
        raise MotorFileError(
            f"line {lineno}: propellant mass {values[2]} kg is more than the "
            f"loaded mass {values[3]} kg; the fields may be swapped"
        )
    delay_list: list[float] = []
    for d in delays.split("-"):
        try:
            delay_list.append(float(d))
        except ValueError:
            continue  # "P" (plugged) or other markers
    return {
        "designation": name,
        "manufacturer": maker,
        "diameter": values[0] / 1000.0,
        "length": values[1] / 1000.0,
        "delays": tuple(d for d in delay_list if d > 0.0),
        "propellant_mass": values[2],
        "loaded_mass": values[3],
    }


def _finish(header: dict[str, object], points: list[tuple[float, float]], lineno: int) -> Motor:
    name = header["designation"]
    if not points:
        raise MotorFileError(f"line {lineno}: motor {name} has a header but no thrust data")
    warnings: list[str] = []
    if points[0][0] > 0.0:
        points.insert(0, (0.0, 0.0))
    if points[-1][1] != 0.0:
        warnings.append(f"{name}: thrust curve did not end at zero; a zero point was added")
        points.append((points[-1][0] + 1e-3, 0.0))
    t = np.array([p[0] for p in points])
    f = np.array([p[1] for p in points])
    motor = Motor(time=t, thrust=f, **header)  # type: ignore[arg-type]
    mismatch = motor.impulse_check()
    if mismatch:
        warnings.append(mismatch)
    if warnings:
        object.__setattr__(motor, "warnings", tuple(warnings))
    return motor


def parse_eng(text: str) -> list[Motor]:
    """Parse the text of a RASP ``.eng`` file into motors.

    Raises:
        MotorFileError: On malformed input, naming the line and what to fix.
    """
    motors: list[Motor] = []
    header: dict[str, object] | None = None
    points: list[tuple[float, float]] = []
    header_line = 0
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        fields = line.split()
        if len(fields) >= 7 or header is None:
            if header is not None:
                motors.append(_finish(header, points, header_line))
            header, points, header_line = _parse_header(fields, lineno), [], lineno
            continue
        try:
            t, f = float(fields[0]), float(fields[1])
        except (ValueError, IndexError):
            raise MotorFileError(
                f"line {lineno}: expected 'time thrust' (two numbers), got {raw.strip()!r}"
            ) from None
        if f < 0.0:
            raise MotorFileError(f"line {lineno}: thrust cannot be negative ({f} N)")
        if points and t <= points[-1][0]:
            raise MotorFileError(
                f"line {lineno}: time {t} s is not after the previous point "
                f"({points[-1][0]} s); times must increase"
            )
        points.append((t, f))
    if header is None:
        raise MotorFileError("no motor found; an .eng file needs a header line and thrust data")
    motors.append(_finish(header, points, header_line))
    return motors


def load_motor(path: str | Path, designation: str | None = None) -> Motor:
    """Load a motor from an ``.eng`` file.

    Args:
        path: Path to the file.
        designation: Which motor to take when the file holds several. Matches
            the full name or its start, case-insensitively (``"m1670"``).

    Raises:
        FileNotFoundError: If the file does not exist.
        MotorFileError: If the file is malformed or the motor is not in it.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise FileNotFoundError(f"motor file not found: {path}") from None
    try:
        motors = parse_eng(text)
    except MotorFileError as exc:
        raise MotorFileError(f"{path.name}: {exc}") from None
    if designation is None:
        if len(motors) > 1:
            names = ", ".join(m.designation for m in motors)
            raise MotorFileError(f"{path.name} holds several motors ({names}); pick one by name")
        return motors[0]
    wanted = designation.upper()
    for m in motors:
        if m.designation.upper().startswith(wanted):
            return m
    names = ", ".join(m.designation for m in motors)
    raise MotorFileError(f"no motor named {designation!r} in {path.name}; it has: {names}")
