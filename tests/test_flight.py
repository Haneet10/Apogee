import copy
import csv
import io
import math

import numpy as np
import pytest
import yaml

from apogee.atmosphere import atmosphere
from apogee.cli import example_path
from apogee.earth import gravity
from apogee.environment import LaunchSite, Wind
from apogee.flight import FlightError, simulate
from apogee.rocket import load_rocket, rocket_from_dict

EXAMPLE = example_path("rocket.yaml")
KESTREL_YAML = yaml.safe_load(EXAMPLE.read_text())


def kestrel(edit=None):
    """Kestrel, optionally changed by ``edit(data)`` before loading."""
    data = copy.deepcopy(KESTREL_YAML)
    if edit is not None:
        edit(data)
    return rocket_from_dict(data, base_dir=EXAMPLE.parent)


def chute(data, name):
    """The YAML mapping of the parachute called ``name``."""
    for comp in data["components"]:
        for item in next(iter(comp.values())).get("contents", []):
            if "parachute" in item and item["parachute"]["name"] == name:
                return item["parachute"]
    raise KeyError(name)


@pytest.fixture(scope="module")
def calm():
    return simulate(load_rocket(EXAMPLE), LaunchSite(rail_length=5.2))


# --- Events --------------------------------------------------------------------


def test_events_happen_in_order(calm):
    names = [e.name for e in calm.events]
    assert names == [
        "Rail exit",
        "Burnout",
        "Apogee",
        "Drogue parachute opens",
        "Main parachute opens",
        "Landing",
    ]
    times = [e.time for e in calm.events]
    assert times == sorted(times)
    assert calm.flight_time == pytest.approx(calm.event("Landing").time)


def test_events_are_located_exactly(calm):
    rocket = calm.rocket
    assert calm.event("Rail exit").altitude == pytest.approx(5.2, abs=1e-6)
    assert calm.event("Burnout").time == pytest.approx(rocket.burnout_time)
    assert calm.event("Main parachute opens").altitude == pytest.approx(300.0, abs=1e-6)
    assert calm.event("Landing").altitude == pytest.approx(0.0, abs=1e-6)
    apogee = calm.event("Apogee")
    assert apogee.velocity[2] == pytest.approx(0.0, abs=1e-3)
    # The interpolated apogee is the true peak: no sample is higher.
    assert apogee.altitude >= calm.trajectory.altitude.max() - 1e-9


def test_apogee_converges_with_step_size(calm):
    coarse = simulate(calm.rocket, calm.site, dt=0.02, descent_dt=0.5)
    assert coarse.apogee == pytest.approx(calm.apogee, abs=0.5)
    assert coarse.time_to_apogee == pytest.approx(calm.time_to_apogee, abs=0.01)


def test_phases_follow_the_flight(calm):
    order = []
    for p in calm.phase:
        if not order or order[-1] != p:
            order.append(p)
    assert order == [
        "rail",
        "boost",
        "coast",
        "under Drogue parachute",
        "under Main parachute",
    ]


def test_kestrel_headline_numbers_are_plausible(calm):
    assert 4500 < calm.apogee < 5200
    assert 35 < calm.rail_exit_speed < 50
    assert 1.3 < calm.max_mach < 1.7
    accel, t = calm.max_acceleration
    assert 15 < accel / 9.80665 < 25 and t < calm.rocket.burnout_time
    assert calm.landing_distance < 1.0  # calm and vertical: back on the pad
    assert calm.warnings == []


# --- Descent -------------------------------------------------------------------


def terminal_velocity(mass, cda, height):
    rho = float(atmosphere(height).density)
    return math.sqrt(2.0 * mass * gravity(height) / (rho * cda))


def test_descent_rates_match_terminal_velocity(calm):
    mass = calm.rocket.mass_properties(calm.rocket.burnout_time).mass
    drogue, main = calm.rocket.parachutes[1], calm.rocket.parachutes[0]
    assert drogue.name == "Drogue parachute"
    cd_a_drogue = drogue.cd * drogue.canopy_area
    cd_a_both = cd_a_drogue + main.cd * main.canopy_area
    # By 300 m the drogue has long reached terminal velocity; at the ground so has the main.
    assert calm.descent_rate("under Drogue parachute") == pytest.approx(
        terminal_velocity(mass, cd_a_drogue, 300.0), rel=2e-3
    )
    assert -calm.landing.velocity[2] == pytest.approx(
        terminal_velocity(mass, cd_a_both, 0.0), rel=2e-3
    )


def test_wind_carries_the_rocket_downwind_at_wind_speed():
    wind = Wind(speed=5.0, direction=270.0, exponent=0.0)  # same wind at every height
    flight = simulate(load_rocket(EXAMPLE), LaunchSite(rail_length=5.2, wind=wind), dt=0.02)
    v_land = flight.landing.velocity
    assert v_land[0] == pytest.approx(5.0, rel=1e-3)  # drifting with the air
    assert abs(v_land[1]) < 1e-6
    assert flight.landing_bearing == pytest.approx(90.0, abs=0.5)  # blown east
    # Long before the main opens it is drifting at wind speed, and keeps doing so.
    main = flight.event("Main parachute opens")
    assert main.velocity[0] == pytest.approx(5.0, rel=1e-3)
    drift = flight.landing.position[0] - main.position[0]
    assert drift == pytest.approx(5.0 * (flight.flight_time - main.time), rel=1e-3)


def test_parachute_set_above_apogee_opens_at_apogee():
    rocket = kestrel(lambda d: chute(d, "Main parachute").update(deploy="8000m"))
    flight = simulate(rocket, dt=0.02)
    main = flight.event("Main parachute opens")
    assert main.time == pytest.approx(flight.time_to_apogee)
    assert "above apogee" in main.note


def test_no_parachute_comes_down_ballistic():
    def strip(data):
        for comp in data["components"]:
            body = next(iter(comp.values()))
            if "contents" in body:
                body["contents"] = [c for c in body["contents"] if "parachute" not in c]

    flight = simulate(kestrel(strip), dt=0.02)
    assert [e.name for e in flight.events][-2:] == ["Apogee", "Landing"]
    assert flight.phase[-1] == "ballistic"
    assert -flight.landing.velocity[2] > 50.0
    assert any("no parachute" in w for w in flight.warnings)


# --- Problems ------------------------------------------------------------------


def test_too_heavy_to_lift_off_is_explained():
    def ballast(data):
        data["components"][1]["body_tube"]["contents"].append(
            {"mass": {"mass": "400kg", "position": "300mm"}}
        )

    with pytest.raises(FlightError, match="never leaves the pad"):
        simulate(kestrel(ballast), dt=0.05)


def test_short_rail_is_flagged():
    flight = simulate(load_rocket(EXAMPLE), LaunchSite(rail_length=0.1), dt=0.02)
    assert flight.rail_exit_speed < 15.0
    assert any("leaves the rail" in w for w in flight.warnings)


def test_a_rocket_without_a_motor_is_refused():
    with pytest.raises(FlightError, match="no motor"):
        simulate(kestrel(lambda d: d.pop("motor")))


# --- CSV -----------------------------------------------------------------------


def test_csv_holds_every_sample(calm):
    buffer = io.StringIO()
    n = calm.write_csv(buffer)
    rows = list(csv.DictReader(io.StringIO(buffer.getvalue())))
    assert n == len(rows) == len(calm.t)
    assert tuple(rows[0]) == calm.CSV_COLUMNS
    peak = max(float(r["altitude_m"]) for r in rows)
    assert peak == pytest.approx(calm.apogee, abs=1e-3)
    assert rows[-1]["phase"] == "under Main parachute"
    assert float(rows[-1]["altitude_m"]) == pytest.approx(0.0, abs=1e-3)
    assert np.isclose(max(float(r["mach"]) for r in rows), calm.max_mach, atol=1e-4)
