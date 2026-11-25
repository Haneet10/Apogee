import io
import math

import numpy as np
import pytest

from apogee.cli import example_path, main
from apogee.environment import LaunchSite, Wind
from apogee.flight import simulate
from apogee.montecarlo import (
    Ellipse,
    Scatter,
    confidence_ellipse,
    draw_samples,
    monte_carlo,
)
from apogee.rocket import load_rocket

FAST = {"dt": 0.05, "descent_dt": 0.5}
SITE = LaunchSite(wind=Wind(speed=4.0, direction=270.0))


@pytest.fixture(scope="module")
def kestrel():
    return load_rocket(example_path("rocket.yaml"))


# --- Perturbed rockets -------------------------------------------------------------


def test_perturbed_scales_dry_mass_and_inertia_but_not_cg(kestrel):
    base, heavy = kestrel.dry_mass_properties(), kestrel.perturbed(dry_mass=1.05)
    props = heavy.dry_mass_properties()
    assert props.mass == pytest.approx(1.05 * base.mass)
    assert props.iyy == pytest.approx(1.05 * base.iyy)
    assert props.cg == pytest.approx(base.cg)
    # The motor is untouched.
    assert heavy.mass_properties(0.0).mass - props.mass == pytest.approx(kestrel.motor.loaded_mass)


def test_perturbed_thrust_scales_impulse_at_same_burn(kestrel):
    hot = kestrel.perturbed(thrust=1.04)
    assert hot.motor.total_impulse == pytest.approx(1.04 * kestrel.motor.total_impulse)
    assert hot.motor.burn_time == kestrel.motor.burn_time
    assert hot.motor.propellant_mass == kestrel.motor.propellant_mass


def test_perturbed_parachute_cd(kestrel):
    slow = kestrel.perturbed(parachute_cd=1.1)
    for a, b in zip(kestrel.parachutes, slow.parachutes, strict=True):
        assert b.cd == pytest.approx(1.1 * a.cd)


def test_perturbed_drag_changes_apogee_the_right_way(kestrel):
    nominal = simulate(kestrel, **FAST).apogee
    assert simulate(kestrel.perturbed(drag=1.1), **FAST).apogee < nominal
    assert simulate(kestrel.perturbed(drag=0.9), **FAST).apogee > nominal


def test_perturbed_rejects_non_positive_multipliers(kestrel):
    with pytest.raises(ValueError, match="thrust multiplier"):
        kestrel.perturbed(thrust=0.0)


def test_unperturbed_copy_flies_identically(kestrel):
    a = simulate(kestrel, SITE, **FAST)
    b = simulate(kestrel.perturbed(), SITE, **FAST)
    assert a.apogee == b.apogee
    assert a.landing_distance == b.landing_distance


# --- Sampling ----------------------------------------------------------------------


def test_samples_have_the_requested_spread():
    scatter = Scatter(wind_speed=1.5, wind_direction=20.0, dry_mass=0.03, thrust=0.04)
    s = draw_samples(SITE, scatter, 20000, seed=3)
    wind = np.array([x.wind_speed for x in s])
    direction = np.array([x.wind_direction for x in s])
    mass = np.array([x.dry_mass for x in s])
    thrust = np.array([x.thrust for x in s])
    assert wind.mean() == pytest.approx(4.0, abs=0.03)
    assert wind.std() == pytest.approx(1.5, rel=0.02)
    assert direction.mean() == pytest.approx(270.0, abs=0.5)
    assert direction.std() == pytest.approx(20.0, rel=0.02)
    assert mass.std() == pytest.approx(0.03, rel=0.02)
    assert thrust.mean() == pytest.approx(1.0, abs=0.001)


def inputs(samples):
    """The drawn inputs only (results are NaN until flown, and NaN != NaN)."""
    return [
        (s.wind_speed, s.wind_direction, s.tilt, s.heading, s.dry_mass, s.thrust) for s in samples
    ]


def test_sampling_is_reproducible_and_seed_dependent():
    a = inputs(draw_samples(SITE, Scatter(), 50, seed=11))
    assert a == inputs(draw_samples(SITE, Scatter(), 50, seed=11))
    assert a != inputs(draw_samples(SITE, Scatter(), 50, seed=12))


def test_samples_stay_physical():
    calm = LaunchSite()
    wild = Scatter(wind_speed=5.0, tilt=3.0, dry_mass=0.5, thrust=0.5, drag=0.5)
    for s in draw_samples(calm, wild, 2000, seed=5):
        assert s.wind_speed >= 0.0
        assert s.tilt >= 0.0
        assert 0.0 <= s.heading < 360.0
        assert 0.5 <= s.dry_mass <= 1.5
        assert 0.5 <= s.thrust <= 1.5


def test_negative_tilt_leans_the_other_way():
    site = LaunchSite(tilt=0.1, heading=90.0)
    s = draw_samples(site, Scatter(tilt=2.0, heading=0.0), 400, seed=1)
    flipped = [x for x in s if abs(x.heading - 270.0) < 1e-9]
    assert flipped, "some draws should go through vertical and lean west"
    assert all(x.tilt >= 0.0 for x in s)


def test_scatter_rejects_negative_values():
    with pytest.raises(ValueError, match="wind_speed"):
        Scatter(wind_speed=-1.0)


def test_runs_must_be_positive():
    with pytest.raises(ValueError, match="runs"):
        draw_samples(SITE, Scatter(), 0, seed=1)


# --- Confidence ellipse ------------------------------------------------------------


def test_ellipse_of_a_known_rotated_gaussian():
    rng = np.random.default_rng(42)
    sx, sy, angle = 300.0, 100.0, math.radians(30.0)  # long axis 30° from north
    u = rng.normal(0.0, sx, 50000)
    v = rng.normal(0.0, sy, 50000)
    east = 1000.0 + u * math.sin(angle) + v * math.cos(angle)
    north = -200.0 + u * math.cos(angle) - v * math.sin(angle)
    e = confidence_ellipse(east, north, 0.95)
    k = math.sqrt(-2.0 * math.log(0.05))  # 2.4477
    assert e.east == pytest.approx(1000.0, abs=5.0)
    assert e.north == pytest.approx(-200.0, abs=5.0)
    assert e.semi_major == pytest.approx(k * sx, rel=0.01)
    assert e.semi_minor == pytest.approx(k * sy, rel=0.01)
    assert e.bearing == pytest.approx(30.0, abs=0.5)
    # It holds the promised share of the population.
    assert np.mean(e.contains(east, north)) == pytest.approx(0.95, abs=0.005)


@pytest.mark.parametrize("p", [0.5, 0.9, 0.99])
def test_ellipse_coverage_matches_probability(p):
    rng = np.random.default_rng(int(p * 100))
    x, y = rng.normal(0, 50, 40000), rng.normal(0, 80, 40000)
    assert np.mean(confidence_ellipse(x, y, p).contains(x, y)) == pytest.approx(p, abs=0.006)


def test_ellipse_contains_its_centre_and_not_far_points():
    e = Ellipse(
        east=10.0, north=0.0, semi_major=5.0, semi_minor=1.0, bearing=90.0, probability=0.95
    )
    assert e.contains(10.0, 0.0)
    assert e.contains(14.9, 0.0)  # along the long (east-west) axis
    assert not e.contains(10.0, 1.1)
    assert e.area == pytest.approx(math.pi * 5.0)


def test_ellipse_input_checks():
    with pytest.raises(ValueError, match="probability"):
        confidence_ellipse(np.zeros(5), np.zeros(5), 1.0)
    with pytest.raises(ValueError, match="at least 3"):
        confidence_ellipse(np.zeros(2), np.zeros(2))


# --- Full Monte Carlo runs ---------------------------------------------------------


def test_zero_scatter_repeats_the_nominal_flight(kestrel):
    d = monte_carlo(kestrel, SITE, runs=3, scatter=Scatter.none(), workers=1, **FAST)
    for s in d.samples:
        assert s.apogee == pytest.approx(d.nominal.apogee, abs=1e-9)
        assert s.east == pytest.approx(d.nominal.east, abs=1e-9)
    nominal = simulate(kestrel, SITE, **FAST)
    assert d.nominal.apogee == pytest.approx(nominal.apogee, abs=1e-9)
    assert d.nominal.landing_distance == pytest.approx(nominal.landing_distance, abs=1e-9)


def test_same_seed_same_flights_with_any_worker_count(kestrel):
    serial = monte_carlo(kestrel, SITE, runs=4, seed=9, workers=1, **FAST)
    parallel = monte_carlo(kestrel, SITE, runs=4, seed=9, workers=2, **FAST)
    assert serial.samples == parallel.samples


def test_dispersion_lands_downwind_with_spread(kestrel):
    d = monte_carlo(kestrel, SITE, runs=12, seed=2, workers=2, **FAST)
    assert len(d.samples) == 12
    assert np.all(d.east > 0.0)  # a west wind carries every flight east
    assert d.landing_distance.std() > 50.0
    assert d.radius(0.5) <= d.radius(0.95)
    progress = []
    monte_carlo(
        kestrel,
        SITE,
        runs=2,
        seed=2,
        workers=1,
        progress=lambda i, n: progress.append((i, n)),
        **FAST,
    )
    assert progress == [(1, 3), (2, 3), (3, 3)]  # the nominal flight counts too


def test_dispersion_csv(kestrel, tmp_path):
    d = monte_carlo(kestrel, SITE, runs=3, seed=4, workers=1, **FAST)
    buf = io.StringIO()
    assert d.write_csv(buf) == 3
    lines = buf.getvalue().splitlines()
    assert lines[0].startswith("run,wind_speed,wind_direction")
    assert lines[0].endswith("east,north,landing_speed")
    assert len(lines) == 4
    path = tmp_path / "d.csv"
    d.write_csv(path)
    assert path.read_text().splitlines() == lines


# --- CLI ---------------------------------------------------------------------------


def test_cli_dispersion(tmp_path):
    out = io.StringIO()
    csv_path = tmp_path / "k.csv"
    code = main(
        [
            "dispersion",
            "--wind",
            "4",
            "--runs",
            "6",
            "--workers",
            "1",
            "--dt",
            "0.05",
            "-o",
            str(csv_path),
        ],
        out,
    )
    text = out.getvalue()
    assert code == 0
    assert "Scatter (1σ): wind ±1 m/s, ±15°" in text
    assert "6 flights, seed 1" in text
    for label in ("Apogee", "Landing distance", "Landing zone", "Recovery"):
        assert label in text
    assert "95 % ellipse centred" in text
    assert "m E of the pad" in text
    assert len(csv_path.read_text().splitlines()) == 7


def test_cli_dispersion_custom_scatter_and_no_csv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = io.StringIO()
    code = main(
        [
            "dispersion",
            "--runs",
            "3",
            "--workers",
            "1",
            "--dt",
            "0.05",
            "--mass-scatter",
            "5",
            "--wind-scatter",
            "0",
            "-o",
            "none",
        ],
        out,
    )
    assert code == 0
    assert "mass ±5 %" in out.getvalue()
    assert "wind ±0 m/s" in out.getvalue()
    assert not list(tmp_path.iterdir())


def test_cli_dispersion_rejects_bad_runs(capsys):
    with pytest.raises(SystemExit):
        main(["dispersion", "--runs", "0"])
    assert "whole number of 1 or more" in capsys.readouterr().err
