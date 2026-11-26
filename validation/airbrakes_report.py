"""Draw the airbrake figure in ``docs/img/airbrakes.png`` and print its numbers.

Left: one Kestrel AB flight, the flight computer's predicted apogee converging
on the target as the panels open. Right: apogee miss of the same scattered
flights flown passively and with the airbrakes. Needs matplotlib.

    python validation/airbrakes_report.py            # 100 flights each way (~3 min)
    python validation/airbrakes_report.py --runs 30  # quicker
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from report import GRID, IMG, INK, MUTED, REFERENCE, style

from apogee import LaunchSite, Wind, load_rocket, simulate
from apogee.cli import example_path
from apogee.montecarlo import monte_carlo

BRAKE = "#3a6ea5"


def main() -> None:
    """Fly the study, print the numbers and draw the figure."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=100)
    args = parser.parse_args()

    rocket = load_rocket(example_path("kestrel-airbrakes.yaml"))
    target = rocket.target_apogee
    assert target is not None
    site = LaunchSite(wind=Wind(speed=4.0, direction=270.0))
    flight = simulate(rocket, site)
    passive_flight = simulate(rocket, site, airbrakes=False)
    log = flight.airbrake_log.arrays()

    results = {}
    for label, active in (("Passive", False), ("Airbrakes", True)):
        d = monte_carlo(rocket, site, runs=args.runs, seed=1, airbrakes=active)
        results[label] = d.apogee - target
        err = results[label]
        print(
            f"{label:<10} mean {err.mean():+7.1f} m   1σ {err.std(ddof=1):6.1f} m   "
            f"worst {np.abs(err).max():6.1f} m   within ±15 m "
            f"{100 * np.mean(np.abs(err) <= 15):.0f} %"
        )
    print(f"Nominal: {flight.apogee:,.1f} m with airbrakes, {passive_flight.apogee:,.1f} m without")

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})
    fig, (left, right) = plt.subplots(
        1, 2, figsize=(10, 3.9), dpi=160, gridspec_kw={"width_ratios": [1.35, 1]}
    )
    for ax in (left, right):
        style(ax)

    coast = ~np.isnan(log["predicted_apogee"])
    t = log["t"]
    left.axhline(target, color=REFERENCE, lw=1, ls=(0, (4, 3)))
    left.text(t[coast][0], target + 14, f"target {target:,.0f} m", color=REFERENCE, fontsize=9)
    left.plot(t[coast], log["predicted_apogee"][coast], color=INK, lw=1.4, label="Predicted apogee")
    left.set_ylim(target - 120, max(log["predicted_apogee"][coast].max(), target) + 60)
    left.set_xlim(t[coast][0] - 0.3, flight.time_to_apogee + 0.5)
    left.set_xlabel("Time after ignition [s]", color=MUTED)
    left.set_ylabel("Predicted apogee [m]", color=MUTED)
    left.set_title("The flight computer homes in on the target", loc="left", fontsize=11, color=INK)
    twin = left.twinx()
    twin.fill_between(t, 100 * log["deployment"], color=BRAKE, alpha=0.16, lw=0, step="post")
    twin.plot(t, 100 * log["deployment"], color=BRAKE, lw=1, drawstyle="steps-post")
    twin.set_ylim(0, 100)
    twin.set_ylabel("Airbrakes open [%]", color=BRAKE)
    twin.tick_params(colors=BRAKE, labelsize=9)
    for side in ("top", "left", "bottom"):
        twin.spines[side].set_visible(False)
    twin.spines["right"].set_color(BRAKE)
    left.set_zorder(twin.get_zorder() + 1)
    left.patch.set_visible(False)

    passive, active = results["Passive"], results["Airbrakes"]
    lo = min(passive.min(), active.min(), -20.0)
    hi = max(passive.max(), 20.0)
    bins = np.linspace(lo, hi, 40)
    right.hist(passive, bins=bins, color=MUTED, alpha=0.55, label="Passive")
    right.hist(active, bins=bins, color=BRAKE, alpha=0.9, label="Airbrakes")
    right.axvline(0, color=REFERENCE, lw=1, ls=(0, (4, 3)))
    right.set_xlabel("Apogee minus target [m]", color=MUTED)
    right.set_ylabel(f"Flights (of {args.runs})", color=MUTED)
    right.set_title("Same scattered flights, two ways", loc="left", fontsize=11, color=INK)
    right.legend(frameon=False, fontsize=9, labelcolor=INK)
    right.grid(True, color=GRID, linewidth=0.6)
    right.text(
        0.98,
        0.62,
        f"1σ {passive.std(ddof=1):,.0f} m → {active.std(ddof=1):.1f} m",
        transform=right.transAxes,
        ha="right",
        color=INK,
        fontsize=9.5,
    )

    fig.tight_layout()
    IMG.mkdir(parents=True, exist_ok=True)
    fig.savefig(IMG / "airbrakes.png", facecolor="white")
    print(f"Wrote {IMG / 'airbrakes.png'}")


if __name__ == "__main__":
    main()
