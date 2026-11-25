"""Apogee against RocketPy: the reference flights in validation/rocketpy/.

The reference numbers were produced by ``validation/rocketpy_reference.py``
(RocketPy 1.13.0, an independent 6-DOF simulator) from the same rocket file.
These tests re-fly Kestrel with Apogee and hold it to the agreement reported in
``docs/validation.md``, so a change that moves Apogee away from the reference
fails here.
"""

import json
from pathlib import Path

import pytest

from apogee.aero import Aerodynamics
from apogee.cli import example_path
from apogee.earth import G0
from apogee.environment import LaunchSite, Wind
from apogee.flight import simulate
from apogee.rocket import load_rocket

REFERENCE = json.loads(
    (Path(__file__).parents[1] / "validation" / "rocketpy" / "reference.json").read_text()
)
CASES = REFERENCE["cases"]


def site_for(launch):
    return LaunchSite(
        rail_length=launch["rail_length"],
        tilt=launch["tilt"],
        heading=launch["heading"],
        wind=Wind(speed=launch["wind_speed"], direction=270.0),
    )


@pytest.fixture(scope="module")
def flights():
    rocket = load_rocket(example_path("rocket.yaml"))
    return {name: simulate(rocket, site_for(case["launch"])) for name, case in CASES.items()}


# Relative tolerances: a little above the agreement in docs/validation.md.
ASCENT = [
    ("apogee_m", lambda f: f.apogee, 0.002),
    ("time_to_apogee_s", lambda f: f.time_to_apogee, 0.002),
    ("max_speed_mps", lambda f: f.max_speed[0], 0.002),
    ("max_mach", lambda f: f.max_mach, 0.002),
    ("max_acceleration_g", lambda f: f.max_acceleration[0] / G0, 0.01),
    ("rail_exit_speed_mps", lambda f: f.rail_exit_speed, 0.002),
    ("burnout_altitude_m", lambda f: f.event("Burnout").altitude, 0.002),
    ("main_deploy_time_s", lambda f: f.event("Main parachute opens").time, 0.003),
    ("landing_time_s", lambda f: f.flight_time, 0.012),
]


@pytest.mark.parametrize("case", sorted(CASES))
@pytest.mark.parametrize(("key", "measure", "tolerance"), ASCENT, ids=[a[0] for a in ASCENT])
def test_flight_matches_rocketpy(flights, case, key, measure, tolerance):
    assert measure(flights[case]) == pytest.approx(CASES[case][key], rel=tolerance)


def test_static_margin_matches_rocketpy():
    # RocketPy computes its own Barrowman CP; the CG is the shared input.
    aero = Aerodynamics(load_rocket(example_path("rocket.yaml")))
    ref = CASES["calm"]
    assert aero.stability_margin(0.0) == pytest.approx(ref["static_margin_liftoff_cal"], abs=0.005)
    assert aero.stability_margin(3.9) == pytest.approx(ref["static_margin_burnout_cal"], abs=0.005)


def test_calm_flight_lands_near_the_pad(flights):
    # Apogee has no Earth rotation; RocketPy's Coriolis term drifts it ~10 m west.
    assert flights["calm"].landing_distance < 1.0
    assert CASES["calm"]["landing_distance_m"] < 15.0


def test_wind_drift_agrees_within_the_documented_gap(flights):
    # Known gap (docs/validation.md): Apogee turns into the wind more slowly just
    # after the rail, mostly from body lift at angle of attack, which RocketPy
    # leaves out. It shows as ~130 m more downwind drift on the way up.
    ours = flights["windy"].landing_distance
    ref = CASES["windy"]["landing_distance_m"]
    assert ours == pytest.approx(ref, rel=0.07)
    assert ours > ref  # if this flips, the explanation in the docs needs revisiting
