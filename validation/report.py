"""Compare Apogee with the RocketPy reference flights and draw the figures.

Prints the error tables used in ``docs/validation.md`` and writes the plots in
``docs/img/``. Needs matplotlib (``pip install matplotlib``); the reference
numbers come from ``validation/rocketpy/`` (see ``rocketpy_reference.py``), so
RocketPy itself is not needed.

    python validation/report.py            # tables and comparison plots
    python validation/report.py --dispersion   # also the Monte Carlo figure (~1 min)
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Ellipse as EllipsePatch

from apogee import Flight, LaunchSite, Wind, load_rocket, simulate
from apogee.earth import G0
from apogee.montecarlo import monte_carlo

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / "validation" / "rocketpy"
IMG = ROOT / "docs" / "img"

INK = "#1d2430"
REFERENCE = "#c8553d"
GRID = "#d9dde3"
MUTED = "#6b7480"

ROWS = [
    ("Apogee", "apogee_m", "m", "{:,.1f}"),
    ("Time to apogee", "time_to_apogee_s", "s", "{:.2f}"),
    ("Max speed", "max_speed_mps", "m/s", "{:.1f}"),
    ("Max Mach", "max_mach", "", "{:.3f}"),
    ("Max acceleration", "max_acceleration_g", "g", "{:.2f}"),
    ("Rail exit speed", "rail_exit_speed_mps", "m/s", "{:.2f}"),
    ("Altitude at burnout", "burnout_altitude_m", "m", "{:,.1f}"),
    ("Main parachute opens", "main_deploy_time_s", "s", "{:.1f}"),
    ("Landing", "landing_time_s", "s", "{:.1f}"),
    ("Landing distance", "landing_distance_m", "m", "{:,.0f}"),
    ("Static margin, liftoff", "static_margin_liftoff_cal", "cal", "{:.3f}"),
    ("Static margin, burnout", "static_margin_burnout_cal", "cal", "{:.3f}"),
]


def site_for(launch: dict[str, float]) -> LaunchSite:
    """The reference case's launch conditions as an Apogee launch site."""
    return LaunchSite(
        rail_length=launch["rail_length"],
        tilt=launch["tilt"],
        heading=launch["heading"],
        wind=Wind(speed=launch["wind_speed"], direction=270.0),
    )


def apogee_numbers(flight: Flight) -> dict[str, float]:
    """Apogee's flight in the reference file's keys."""
    from apogee import Aerodynamics

    aero = Aerodynamics(flight.rocket)
    burnout = flight.event("Burnout")
    main = flight.event("Main parachute opens")
    accel, _ = flight.max_acceleration
    return {
        "apogee_m": flight.apogee,
        "time_to_apogee_s": flight.time_to_apogee,
        "max_speed_mps": flight.max_speed[0],
        "max_mach": flight.max_mach,
        "max_acceleration_g": accel / G0,
        "rail_exit_speed_mps": flight.rail_exit_speed,
        "burnout_altitude_m": burnout.altitude if burnout else math.nan,
        "main_deploy_time_s": main.time if main else math.nan,
        "landing_time_s": flight.flight_time,
        "landing_distance_m": flight.landing_distance,
        "static_margin_liftoff_cal": aero.stability_margin(0.0),
        "static_margin_burnout_cal": aero.stability_margin(flight.rocket.burnout_time),
    }


def table(ours: dict[str, float], ref: dict[str, float]) -> str:
    """A Markdown table: quantity, Apogee, RocketPy, difference."""
    lines = [
        "| Quantity | Apogee | RocketPy | Difference |",
        "|---|--:|--:|--:|",
    ]
    for label, key, unit, fmt in ROWS:
        a, b = ours[key], ref[key]
        u = f" {unit}" if unit else ""
        if key == "landing_distance_m":  # can be near zero: compare in metres
            diff = f"{a - b:+,.0f} m"
        else:
            diff = f"{(a - b) / b * 100.0:+.2f} %"
        lines.append(f"| {label} | {fmt.format(a)}{u} | {fmt.format(b)}{u} | {diff} |")
    return "\n".join(lines)


def read_series(name: str) -> dict[str, np.ndarray]:
    """A RocketPy time series written by ``rocketpy_reference.py``."""
    with (REF / f"{name}.csv").open() as fh:
        rows = list(csv.DictReader(fh))
    return {k: np.array([float(r[k]) for r in rows]) for k in rows[0]}


def style(ax: plt.Axes) -> None:
    """Quiet axes: light grid, no top or right spine."""
    ax.grid(True, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=9)


def plot_comparison(flights: dict[str, Flight]) -> None:
    """Altitude against time (calm) and the ground track (windy)."""
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})
    fig, (left, right) = plt.subplots(1, 2, figsize=(10, 3.8), dpi=160)

    calm, ref = flights["calm"], read_series("calm")
    left.plot(ref["t_s"], ref["altitude_m"], color=REFERENCE, lw=3.2, alpha=0.45, label="RocketPy")
    left.plot(calm.t, calm.trajectory.position[:, 2], color=INK, lw=1.2, label="Apogee")
    left.set_xlim(0, 60)
    left.set_ylim(0, 5400)
    left.set_xlabel("Time after ignition [s]", color=MUTED)
    left.set_ylabel("Altitude [m]", color=MUTED)
    left.set_title("Calm air: ascent to apogee", loc="left", fontsize=11, color=INK)
    style(left)
    left.legend(frameon=False, fontsize=9)

    windy, ref = flights["windy"], read_series("windy")
    pos = windy.trajectory.position
    right.plot(ref["east_m"], ref["altitude_m"], color=REFERENCE, lw=3.2, alpha=0.45)
    right.plot(pos[:, 0], pos[:, 2], color=INK, lw=1.2)
    right.set_xlabel("Distance east of the pad [m]", color=MUTED)
    right.set_ylabel("Altitude [m]", color=MUTED)
    right.set_title("4 m/s west wind: the whole flight", loc="left", fontsize=11, color=INK)
    style(right)

    fig.tight_layout()
    fig.savefig(IMG / "validation-rocketpy.png", facecolor="white")
    plt.close(fig)


def plot_dispersion() -> None:
    """Landing points of 300 scattered Kestrel flights with the 95 % ellipse."""
    rocket = load_rocket(ROOT / "examples" / "rocket.yaml")
    site = LaunchSite(wind=Wind(speed=4.0, direction=270.0))
    d = monte_carlo(rocket, site, runs=300, seed=1)
    e = d.ellipse(0.95)

    fig, ax = plt.subplots(figsize=(6.4, 5.2), dpi=160)
    ax.scatter(d.east, d.north, s=9, color=INK, alpha=0.55, linewidths=0, label="Landing")
    patch = EllipsePatch(
        (e.east, e.north),
        2 * e.semi_major,
        2 * e.semi_minor,
        angle=90.0 - e.bearing,  # matplotlib: counter-clockwise from +x (east)
        fill=False,
        color=REFERENCE,
        lw=1.4,
        label="95 % ellipse",
    )
    ax.add_patch(patch)
    ax.plot([0], [0], marker="^", color=REFERENCE, ms=8, ls="none", label="Pad")
    ax.plot(
        [d.nominal.east],
        [d.nominal.north],
        marker="+",
        color=INK,
        ms=11,
        mew=1.6,
        ls="none",
        label="Nominal flight",
    )
    ax.set_aspect("equal")
    ax.set_xlabel("East of the pad [m]", color=MUTED)
    ax.set_ylabel("North of the pad [m]", color=MUTED)
    ax.set_title(
        f"Kestrel, 4 m/s west wind: {len(d.samples)} scattered flights",
        loc="left",
        fontsize=11,
        color=INK,
    )
    style(ax)
    ax.legend(frameon=False, fontsize=9, loc="upper left")
    fig.tight_layout()
    fig.savefig(IMG / "dispersion.png", facecolor="white")
    plt.close(fig)
    held = float(np.mean(e.contains(d.east, d.north)))
    print(
        f"\nDispersion: apogee {d.apogee.mean():,.0f} ± {d.apogee.std(ddof=1):,.0f} m; "
        f"ellipse centre ({e.east:,.0f}, {e.north:,.0f}) m, {2 * e.semi_major:,.0f} × "
        f"{2 * e.semi_minor:,.0f} m, bearing {e.bearing:.0f}°, holds {held:.1%}; "
        f"95 % radius {d.radius(0.95):,.0f} m"
    )


def main() -> None:
    """Print the tables and draw the figures."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dispersion", action="store_true", help="also draw the Monte Carlo map")
    args = parser.parse_args()

    reference = json.loads((REF / "reference.json").read_text())
    rocket = load_rocket(ROOT / "examples" / "rocket.yaml")
    flights = {}
    for name, ref in reference["cases"].items():
        flights[name] = simulate(rocket, site_for(ref["launch"]))
        print(f"\n### {ref['title']}\n")
        print(table(apogee_numbers(flights[name]), ref))
    IMG.mkdir(parents=True, exist_ok=True)
    plot_comparison(flights)
    if args.dispersion:
        plot_dispersion()


if __name__ == "__main__":
    main()
