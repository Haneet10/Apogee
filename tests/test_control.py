"""Airbrake apogee control, closed around the 6-DOF simulator."""

import math

import numpy as np
import pytest

from apogee.cli import example_path
from apogee.control import ApogeePredictor, FlightComputer, _hermite_peak
from apogee.environment import LaunchSite, Wind
from apogee.flight import simulate
from apogee.rocket import RocketFileError, load_rocket, rocket_from_dict

FAST = {"dt": 0.02, "descent_dt": 0.5}
TARGET = 4400.0
SITE = LaunchSite(wind=Wind(speed=4.0, direction=270.0))


@pytest.fixture(scope="module")
def ab():
    return load_rocket(example_path("kestrel-airbrakes.yaml"))


@pytest.fixture(scope="module")
def flights(ab):
    return {
        "active": simulate(ab, SITE, **FAST),
        "passive": simulate(ab, SITE, airbrakes=False, **FAST),
    }


# --- Rocket file -------------------------------------------------------------------


def test_example_has_airbrakes_and_a_flight_computer(ab):
    brakes = ab.airbrakes
    assert brakes is not None
    assert brakes.panels == 3
    assert brakes.max_area == pytest.approx(3 * 0.060 * 0.035)
    assert brakes.travel_time == pytest.approx(0.4)
    assert ab.target_apogee == TARGET
    assert ab.flight_computer.rate == 50.0
    assert ab.airbrake_position is not None


def test_airbrakes_add_their_mass(ab):
    kestrel = load_rocket(example_path("rocket.yaml"))
    extra = ab.dry_mass_properties().mass - kestrel.dry_mass_properties().mass
    # The 420 g module plus a 120 mm fiberglass bay.
    assert 0.42 < extra < 0.60


def test_with_target_overrides_and_stows(ab):
    assert ab.with_target(3000.0).target_apogee == 3000.0
    assert ab.with_target(None).target_apogee is None
    with pytest.raises(ValueError, match="no airbrakes"):
        load_rocket(example_path("rocket.yaml")).with_target(3000.0)


def minimal(contents=None, **top):
    return {
        "name": "Test",
        "components": [
            {"nose_cone": {"shape": "ogive", "length": "300mm", "diameter": "54mm",
                           "thickness": "2mm", "material": "fiberglass"}},
            {"body_tube": {"length": "600mm", "thickness": "1.5mm", "material": "fiberglass",
                           "contents": contents or []}},
        ],
        **top,
    }  # fmt: skip


BRAKES = {
    "airbrakes": {"panel_width": "30mm", "panel_height": "15mm", "length": "50mm", "mass": "100g"}
}


def test_airbrakes_parse_with_defaults():
    r = rocket_from_dict(minimal([BRAKES]))
    b = r.airbrakes
    assert b.panels == 3 and b.cd == 1.2 and b.max_mach == 0.8
    assert b.travel_time == 0.5
    assert r.flight_computer is None


@pytest.mark.parametrize(
    ("brakes", "match"),
    [
        ({**BRAKES["airbrakes"], "travel_time": "fast"}, "travel_time"),
        ({**BRAKES["airbrakes"], "panels": 20}, "1 to 12"),
        ({**BRAKES["airbrakes"], "panel_hieght": "1mm"}, "did you mean 'panel_height'"),
        ({k: v for k, v in BRAKES["airbrakes"].items() if k != "mass"}, "'mass'"),
    ],
)
def test_bad_airbrakes_are_explained(brakes, match):
    with pytest.raises(RocketFileError, match=match):
        rocket_from_dict(minimal([{"airbrakes": brakes}]))


def test_flight_computer_section_parses_units():
    r = rocket_from_dict(
        minimal(
            [BRAKES],
            flight_computer={
                "target_apogee": "10000ft",
                "rate": "100Hz",
                "control_rate": 20,
                "barometer_noise": "50cm",
                "accelerometer_noise": "0.05g",
                "lockout": "500ms",
            },
        )
    )
    fc = r.flight_computer
    assert fc.target_apogee == pytest.approx(3048.0)
    assert fc.rate == 100.0 and fc.control_rate == 20.0
    assert fc.barometer_noise == pytest.approx(0.5)
    assert fc.accelerometer_noise == pytest.approx(0.05 * 9.80665)
    assert fc.lockout == pytest.approx(0.5)


def test_flight_computer_errors_are_explained():
    with pytest.raises(RocketFileError, match="flight_computer: unknown setting 'target'"):
        rocket_from_dict(minimal([BRAKES], flight_computer={"target": "1km"}))
    with pytest.raises(RocketFileError, match="cannot be faster"):
        rocket_from_dict(minimal([BRAKES], flight_computer={"rate": 10, "control_rate": 50}))


def test_target_without_airbrakes_warns():
    r = rocket_from_dict(minimal(flight_computer={"target_apogee": "1000m"}))
    assert any("no airbrakes" in w for w in r.warnings)


def test_only_one_airbrake_module():
    with pytest.raises(RocketFileError, match="only one airbrake"):
        rocket_from_dict(minimal([BRAKES, BRAKES]))


# --- Predictor ---------------------------------------------------------------------


def test_hermite_peak_of_a_parabola_is_exact():
    # Thrown up at 10 m/s under 10 m/s²: back to the start after 2 s, peak 5 m.
    assert _hermite_peak(0.0, 0.0, 10.0, -10.0, -10.0, -10.0, 2.0) == pytest.approx(5.0)


def test_predictor_matches_the_6dof_coast(ab, flights):
    """From 3 s after burnout, the point-mass prediction lands within 0.5 % of the 6-DOF apogee."""
    passive = flights["passive"]
    i = int(np.searchsorted(passive.t, ab.burnout_time + 3.0))
    y = passive.trajectory.y[i]
    tilt = math.radians(float(passive.trajectory.pitch_from_vertical[i]))
    p = ApogeePredictor(ab, SITE)
    predicted = p.apogee(float(y[2]), float(y[5]), tilt, deployment=0.0)
    assert predicted == pytest.approx(passive.apogee, rel=0.005)


def test_more_brake_means_lower_apogee(ab):
    p = ApogeePredictor(ab, LaunchSite())
    apogees = [p.apogee(2000.0, 250.0, 0.1, deployment=d) for d in np.linspace(0, 1, 6)]
    assert all(np.diff(apogees) < 0.0)


def test_setting_for_hits_the_target_or_clips(ab):
    p = ApogeePredictor(ab, LaunchSite())
    stowed = p.apogee(2000.0, 250.0, 0.1, deployment=0.0)
    opened = p.apogee(2000.0, 250.0, 0.1, deployment=1.0)
    goal = 0.5 * (stowed + opened)
    d, apo = p.setting_for(goal, 2000.0, 250.0, 0.1)
    assert 0.0 < d < 1.0
    assert apo == pytest.approx(goal, abs=0.5)
    assert p.apogee(2000.0, 250.0, 0.1, deployment=d) == pytest.approx(goal, abs=0.5)
    assert p.setting_for(stowed + 100.0, 2000.0, 250.0, 0.1)[0] == 0.0
    assert p.setting_for(opened - 100.0, 2000.0, 250.0, 0.1)[0] == 1.0


def test_predictor_ignores_the_as_built_scatter(ab):
    """The flight computer only knows the rocket as designed."""
    a = ApogeePredictor(ab, LaunchSite())
    b = ApogeePredictor(ab.perturbed(drag=1.2, dry_mass=1.1), LaunchSite())
    assert a.apogee(2000.0, 250.0, 0.1, deployment=0.3) == b.apogee(
        2000.0, 250.0, 0.1, deployment=0.3
    )


# --- Closed loop -------------------------------------------------------------------


def test_airbrakes_fly_to_the_target(flights):
    active, passive = flights["active"], flights["passive"]
    assert passive.apogee > TARGET + 200.0
    assert active.target_apogee == TARGET
    assert abs(active.apogee_error) < 5.0
    assert passive.apogee_error is None
    assert passive.brake_deployment is None


def test_airbrake_events_bracket_the_coast(ab, flights):
    names = [e.name for e in flights["active"].events]
    assert names.index("Burnout") < names.index("Airbrakes open") < names.index("Apogee")
    assert "Airbrakes stowed" in names
    opened = flights["active"].event("Airbrakes open")
    lockout = ab.flight_computer.lockout
    assert opened.time >= ab.burnout_time + lockout


def test_airbrakes_open_only_below_their_mach_limit(ab, flights):
    f = flights["active"]
    open_ = f.brake_deployment > 0.0
    assert open_.any()
    assert f.mach[open_].max() < ab.airbrakes.max_mach + 0.02


def test_servo_respects_its_travel_rate(ab, flights):
    f = flights["active"]
    up = f.t < f.time_to_apogee  # they are stowed at once at apogee, under the drogue
    steps = np.diff(f.brake_deployment[up])
    dts = np.diff(f.t[up])
    assert np.all(np.abs(steps) <= dts / ab.airbrakes.travel_time + 1e-9)
    assert f.brake_deployment.min() >= 0.0 and f.brake_deployment.max() <= 1.0


def test_brakes_are_stowed_under_the_parachutes(flights):
    f = flights["active"]
    descent = f.t > f.time_to_apogee + 1e-6
    assert np.all(f.brake_deployment[descent] == 0.0)


def test_flight_computer_log_is_consistent(flights):
    log = flights["active"].airbrake_log.arrays()
    n = len(log["t"])
    assert n > 100
    assert all(len(v) == n for v in log.values())
    coasting = ~np.isnan(log["predicted_apogee"])
    # Once the brakes have settled the prediction sits on the target.
    late = coasting & (log["t"] > log["t"][coasting][0] + 6.0) & (log["velocity"] > 40.0)
    assert np.all(np.abs(log["predicted_apogee"][late] - TARGET) < 10.0)
    assert 0.8 < log["drag_factor"][-1] < 1.2


def test_controller_corrects_a_draggier_heavier_rocket(ab):
    flown = ab.perturbed(drag=1.06, dry_mass=1.03, airbrake_cd=0.9)
    f = simulate(flown, SITE, **FAST)
    assert abs(f.apogee_error) < 8.0
    assert f.airbrake_log.drag_factor[-1] > 1.0  # it noticed the extra drag


def test_unreachable_target_keeps_the_brakes_stowed(ab):
    f = simulate(ab.with_target(6000.0), LaunchSite(), **FAST)
    assert f.event("Airbrakes open") is None
    assert np.all(f.brake_deployment == 0.0)
    assert f.apogee_error < 0.0


def test_too_low_a_target_saturates_the_brakes(ab):
    f = simulate(ab.with_target(3000.0), LaunchSite(), **FAST)
    assert f.brake_deployment.max() == pytest.approx(1.0)
    assert f.apogee > 3000.0  # not enough authority


def test_sensor_noise_is_seeded(ab):
    a = simulate(ab, LaunchSite(), seed=3, **FAST)
    b = simulate(ab, LaunchSite(), seed=3, **FAST)
    assert a.apogee == b.apogee


def test_flight_computer_needs_brakes_and_a_target(ab):
    kestrel = load_rocket(example_path("rocket.yaml"))
    with pytest.raises(ValueError, match="no airbrakes"):
        FlightComputer(kestrel, LaunchSite(), ab.flight_computer)
    with pytest.raises(ValueError, match="target"):
        FlightComputer(ab, LaunchSite(), ab.with_target(None).flight_computer)
