"""Monte Carlo dispersion: where the rocket might really land.

No rocket flies exactly as drawn. The wind gusts and veers, the rail is set up
a little off, the airframe comes out heavier than planned, one reload delivers
more impulse than the next. :func:`monte_carlo` flies the rocket many times
with each of those inputs drawn from a normal distribution (:class:`Scatter`)
and gathers the apogees and landing points into a :class:`Dispersion`::

    >>> from apogee import LaunchSite, Wind, load_rocket
    >>> from apogee.cli import example_path
    >>> kestrel = load_rocket(example_path("rocket.yaml"))
    >>> site = LaunchSite(wind=Wind(speed=4.0, direction=270))
    >>> d = monte_carlo(kestrel, site, runs=4, seed=7, workers=1, dt=0.05, descent_dt=0.5)
    >>> len(d.samples)
    4
    >>> e = d.ellipse(0.95)
    >>> bool(e.east > 1000)          # downwind of the pad
    True

Method
------
Every random draw is made up front from one seeded generator, so a seed always
gives the same flights whatever the number of worker processes. Multipliers
(mass, thrust, drag, parachute Cd) are clipped to 0.5-1.5 and the wind speed to
zero or more, so a wild draw cannot produce a nonsensical rocket.

The landing points are summarised by a confidence ellipse: for points with
covariance ``Σ``, the ellipse ``xᵀ Σ⁻¹ x = k²`` with ``k² = −2 ln(1 − p)``
(the chi-squared quantile with two degrees of freedom) holds a fraction ``p``
of a bivariate normal population. Its semi-axes are ``k √λ`` for the
eigenvalues ``λ`` of ``Σ``, along the eigenvectors.

The default scatter follows the stochastic 6-DOF study of Box, Bishop and Hunt,
"Stochastic six-degree-of-freedom flight simulator for passively controlled
high-power rockets", *J. Aerospace Engineering* 24(1), 2011, and the motor
impulse spread allowed by NFPA 1127 certification testing.
"""

from __future__ import annotations

import csv
import math
import os
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import TextIO

import numpy as np
from numpy.typing import NDArray

from apogee.environment import LaunchSite, Wind
from apogee.flight import simulate
from apogee.rocket import Rocket

__all__ = ["Dispersion", "Ellipse", "Sample", "Scatter", "confidence_ellipse", "monte_carlo"]

Vector = NDArray[np.float64]

_MULTIPLIER_RANGE = (0.5, 1.5)


@dataclass(frozen=True)
class Scatter:
    """One-standard-deviation uncertainty of each input.

    Attributes:
        wind_speed: Wind speed at 10 m [m/s].
        wind_direction: Direction the wind blows from [degrees].
        tilt: Rail angle from vertical, as set up on the pad [degrees].
        heading: Direction the rail leans [degrees].
        dry_mass: Airframe mass, as a fraction (0.02 is ±2 %).
        thrust: Motor total impulse, as a fraction.
        drag: Zero-lift drag coefficient, as a fraction.
        parachute_cd: Canopy drag coefficients, as a fraction.
    """

    wind_speed: float = 1.0
    wind_direction: float = 15.0
    tilt: float = 0.5
    heading: float = 5.0
    dry_mass: float = 0.02
    thrust: float = 0.03
    drag: float = 0.05
    parachute_cd: float = 0.05

    def __post_init__(self) -> None:
        for f in fields(self):
            value = getattr(self, f.name)
            if not (value >= 0.0 and math.isfinite(value)):
                raise ValueError(f"scatter in {f.name} must be zero or more, got {value}")

    @classmethod
    def none(cls) -> Scatter:
        """No scatter at all: every run is the nominal flight."""
        return cls(**{f.name: 0.0 for f in fields(cls)})

    def describe(self) -> str:
        """One line for reports, e.g. ``wind ±1 m/s, ±15°  ·  rail ±0.5°, ±5°  ·  mass ±2 %...``."""

        def pct(x: float) -> str:
            return f"±{100.0 * x:g} %"

        return (
            f"wind ±{self.wind_speed:g} m/s, ±{self.wind_direction:g}°  ·  "
            f"rail ±{self.tilt:g}°, ±{self.heading:g}°  ·  "
            f"mass {pct(self.dry_mass)}  ·  impulse {pct(self.thrust)}  ·  "
            f"drag {pct(self.drag)}  ·  parachute Cd {pct(self.parachute_cd)}"
        )


@dataclass(frozen=True)
class Sample:
    """The inputs of one Monte Carlo flight and what came of it.

    Inputs are absolute (wind speed [m/s], directions and angles [degrees]) or
    multipliers (``dry_mass``, ``thrust``, ``drag``, ``parachute_cd``). Results
    are in metres and metres per second; ``east`` and ``north`` are the
    landing point relative to the pad.
    """

    run: int
    wind_speed: float
    wind_direction: float
    tilt: float
    heading: float
    dry_mass: float
    thrust: float
    drag: float
    parachute_cd: float
    apogee: float = math.nan
    max_speed: float = math.nan
    rail_exit_speed: float = math.nan
    east: float = math.nan
    north: float = math.nan
    landing_speed: float = math.nan

    @property
    def landing_distance(self) -> float:
        """Distance from the pad to the landing point [m]."""
        return math.hypot(self.east, self.north)


@dataclass(frozen=True)
class Ellipse:
    """A confidence ellipse on the ground.

    Attributes:
        east: Centre, east of the pad [m].
        north: Centre, north of the pad [m].
        semi_major: Half the long axis [m].
        semi_minor: Half the short axis [m].
        bearing: Compass bearing of the long axis, 0-180 [degrees].
        probability: Fraction of landings it is expected to hold.
    """

    east: float
    north: float
    semi_major: float
    semi_minor: float
    bearing: float
    probability: float

    def contains(self, east: Vector | float, north: Vector | float) -> NDArray[np.bool_]:
        """Whether points (east, north) [m] fall inside the ellipse."""
        theta = math.radians(self.bearing)
        major = np.array([math.sin(theta), math.cos(theta)])  # unit vector, (east, north)
        de = np.asarray(east, dtype=np.float64) - self.east
        dn = np.asarray(north, dtype=np.float64) - self.north
        along = de * major[0] + dn * major[1]
        across = -de * major[1] + dn * major[0]
        major_axis = max(self.semi_major, 1e-12)
        minor_axis = max(self.semi_minor, 1e-12)
        result: NDArray[np.bool_] = (along / major_axis) ** 2 + (across / minor_axis) ** 2 <= 1.0
        return result

    @property
    def area(self) -> float:
        """Area of the ellipse [m²]."""
        return math.pi * self.semi_major * self.semi_minor


def confidence_ellipse(east: Vector, north: Vector, probability: float = 0.95) -> Ellipse:
    """The ellipse expected to hold ``probability`` of a bivariate-normal scatter.

    >>> rng = np.random.default_rng(0)
    >>> e = confidence_ellipse(rng.normal(0, 30, 20000), rng.normal(0, 10, 20000), 0.95)
    >>> round(e.semi_major / e.semi_minor)    # the axes keep the 3:1 ratio of the spreads
    3
    >>> bool(2.40 < e.semi_major / 30 < 2.50)  # scaled by k = 2.448 for 95 %
    True
    >>> round(e.bearing)                                            # long axis east-west
    90
    """
    if not 0.0 < probability < 1.0:
        raise ValueError(f"probability must be between 0 and 1, got {probability}")
    east = np.asarray(east, dtype=np.float64)
    north = np.asarray(north, dtype=np.float64)
    if east.size < 3:
        raise ValueError("a confidence ellipse needs at least 3 points")
    cov = np.cov(np.vstack([east, north]))
    values, vectors = np.linalg.eigh(cov)  # ascending
    values = np.clip(values, 0.0, None)
    k = math.sqrt(-2.0 * math.log(1.0 - probability))
    major = vectors[:, 1]
    bearing = math.degrees(math.atan2(major[0], major[1])) % 180.0
    return Ellipse(
        east=float(east.mean()),
        north=float(north.mean()),
        semi_major=k * math.sqrt(values[1]),
        semi_minor=k * math.sqrt(values[0]),
        bearing=bearing,
        probability=probability,
    )


@dataclass(frozen=True)
class Dispersion:
    """The flights of a Monte Carlo run.

    Attributes:
        nominal: The flight with every input at its nominal value.
        samples: One entry per scattered flight, in run order.
        scatter: The uncertainties the inputs were drawn from.
        seed: Random seed; the same seed gives the same flights.
    """

    nominal: Sample
    samples: tuple[Sample, ...]
    scatter: Scatter
    seed: int

    def _column(self, name: str) -> Vector:
        return np.array([getattr(s, name) for s in self.samples], dtype=np.float64)

    @property
    def apogee(self) -> Vector:
        """Apogee of every flight [m above the pad]."""
        return self._column("apogee")

    @property
    def east(self) -> Vector:
        """Landing point of every flight, east of the pad [m]."""
        return self._column("east")

    @property
    def north(self) -> Vector:
        """Landing point of every flight, north of the pad [m]."""
        return self._column("north")

    @property
    def landing_distance(self) -> Vector:
        """Distance from the pad of every landing [m]."""
        return np.hypot(self.east, self.north)

    def ellipse(self, probability: float = 0.95) -> Ellipse:
        """Confidence ellipse of the landing points."""
        return confidence_ellipse(self.east, self.north, probability)

    def radius(self, fraction: float = 0.95) -> float:
        """Radius around the pad that holds ``fraction`` of the landings [m]."""
        return float(np.quantile(self.landing_distance, fraction))

    def write_csv(self, target: str | Path | TextIO) -> int:
        """Write one row per flight (inputs, then results); returns the row count."""
        names = [f.name for f in fields(Sample)]
        if isinstance(target, str | Path):
            with Path(target).open("w", newline="") as fh:
                return self._write(fh, names)
        return self._write(target, names)

    def _write(self, fh: TextIO, names: list[str]) -> int:
        w = csv.writer(fh)
        w.writerow(names)
        for s in self.samples:
            row = asdict(s)
            w.writerow([_fmt(row[n]) for n in names])
        return len(self.samples)


def _fmt(value: float | int) -> str:
    return str(value) if isinstance(value, int) else f"{value:.6g}"


def draw_samples(site: LaunchSite, scatter: Scatter, runs: int, seed: int) -> list[Sample]:
    """Draw the inputs of ``runs`` flights around ``site``'s nominal values."""
    if runs < 1:
        raise ValueError(f"runs must be at least 1, got {runs}")
    rng = np.random.default_rng(seed)
    z = rng.standard_normal((runs, 8))
    lo, hi = _MULTIPLIER_RANGE
    samples = []
    for i, row in enumerate(z):
        tilt = float(site.tilt + scatter.tilt * row[2])
        heading = float(site.heading + scatter.heading * row[3])
        if tilt < 0.0:  # leaning the other way
            tilt, heading = -tilt, heading + 180.0
        samples.append(
            Sample(
                run=i + 1,
                wind_speed=max(0.0, float(site.wind.speed + scatter.wind_speed * row[0])),
                wind_direction=float(site.wind.direction + scatter.wind_direction * row[1]) % 360.0,
                tilt=tilt,
                heading=heading % 360.0,
                dry_mass=float(np.clip(1.0 + scatter.dry_mass * row[4], lo, hi)),
                thrust=float(np.clip(1.0 + scatter.thrust * row[5], lo, hi)),
                drag=float(np.clip(1.0 + scatter.drag * row[6], lo, hi)),
                parachute_cd=float(np.clip(1.0 + scatter.parachute_cd * row[7], lo, hi)),
            )
        )
    return samples


def _fly(args: tuple[Rocket, LaunchSite, Sample, float, float]) -> Sample:
    """Fly one sample (a top-level function so worker processes can run it)."""
    rocket, site, s, dt, descent_dt = args
    flown = rocket.perturbed(
        dry_mass=s.dry_mass, thrust=s.thrust, drag=s.drag, parachute_cd=s.parachute_cd
    )
    wind = Wind(
        speed=s.wind_speed,
        direction=s.wind_direction,
        reference_height=site.wind.reference_height,
        exponent=site.wind.exponent,
    )
    here = LaunchSite(
        rail_length=site.rail_length,
        tilt=s.tilt,
        heading=s.heading,
        altitude=site.altitude,
        wind=wind,
    )
    flight = simulate(flown, here, dt=dt, descent_dt=descent_dt)
    landing = flight.landing
    east = north = speed = math.nan
    if landing is not None:
        east, north = float(landing.position[0]), float(landing.position[1])
        speed = -float(landing.velocity[2])
    return Sample(
        **{
            **asdict(s),
            "apogee": flight.apogee,
            "max_speed": flight.max_speed[0],
            "rail_exit_speed": flight.rail_exit_speed,
            "east": east,
            "north": north,
            "landing_speed": speed,
        }
    )


def monte_carlo(
    rocket: Rocket,
    site: LaunchSite | None = None,
    *,
    runs: int = 100,
    scatter: Scatter | None = None,
    seed: int = 1,
    workers: int | None = None,
    dt: float = 0.02,
    descent_dt: float = 0.25,
    progress: Callable[[int, int], None] | None = None,
) -> Dispersion:
    """Fly ``rocket`` ``runs`` times with scattered inputs.

    Args:
        rocket: The rocket as designed.
        site: Nominal launch conditions (default: 5 m vertical rail, calm).
        runs: Number of scattered flights.
        scatter: Input uncertainties (default: :class:`Scatter`'s values).
        seed: Random seed.
        workers: Worker processes (default: one per CPU, up to 8; 1 runs in
            this process).
        dt: 6-DOF time step [s]. The default 0.02 s moves Kestrel's apogee by
            under 0.3 m against 0.01 s, far below the scatter.
        descent_dt: Time step under the parachutes [s].
        progress: Called as ``progress(done, total)`` after each flight.
    """
    site = site or LaunchSite()
    scatter = scatter or Scatter()
    samples = draw_samples(site, scatter, runs, seed)
    nominal_in = Sample(
        run=0,
        wind_speed=site.wind.speed,
        wind_direction=site.wind.direction,
        tilt=site.tilt,
        heading=site.heading,
        dry_mass=1.0,
        thrust=1.0,
        drag=1.0,
        parachute_cd=1.0,
    )
    jobs = [(rocket, site, s, dt, descent_dt) for s in [nominal_in, *samples]]
    workers = workers or min(8, os.cpu_count() or 1)
    results: list[Sample] = []
    total = len(jobs)
    for i, result in enumerate(_map(jobs, workers), start=1):
        results.append(result)
        if progress is not None:
            progress(i, total)
    return Dispersion(nominal=results[0], samples=tuple(results[1:]), scatter=scatter, seed=seed)


def _map(
    jobs: Sequence[tuple[Rocket, LaunchSite, Sample, float, float]], workers: int
) -> Iterator[Sample]:
    if workers <= 1 or len(jobs) <= 2:
        yield from map(_fly, jobs)
        return
    with ProcessPoolExecutor(max_workers=workers) as pool:
        yield from pool.map(_fly, jobs, chunksize=max(1, len(jobs) // (4 * workers)))
