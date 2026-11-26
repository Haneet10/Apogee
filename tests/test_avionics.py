"""Sensors and the Kalman filter of the airbrake flight computer."""

import numpy as np
import pytest

from apogee.avionics import AltitudeFilter, FlightComputerSettings, Sensors
from apogee.earth import G0

# --- Settings ----------------------------------------------------------------------


def test_default_settings_are_valid():
    s = FlightComputerSettings()
    assert s.target_apogee is None
    assert s.rate == 50.0
    assert s.control_rate == 10.0


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"target_apogee": 0.0}, "above the pad"),
        ({"rate": 0.0}, "rate must be positive"),
        ({"barometer_noise": -1.0}, "zero or more"),
        ({"rate": 10.0, "control_rate": 20.0}, "cannot be faster"),
        ({"lockout": float("nan")}, "zero or more"),
    ],
)
def test_settings_reject_nonsense(kwargs, match):
    with pytest.raises(ValueError, match=match):
        FlightComputerSettings(**kwargs)


# --- Sensors -----------------------------------------------------------------------


def test_noise_free_sensors_read_the_truth():
    s = Sensors(FlightComputerSettings(barometer_noise=0.0, accelerometer_noise=0.0))
    assert s.read(812.0, -12.5) == (812.0, -12.5)


def test_sensor_noise_has_the_requested_spread():
    s = Sensors(FlightComputerSettings(barometer_noise=2.0, accelerometer_noise=0.5), seed=4)
    readings = np.array([s.read(100.0, 3.0) for _ in range(20000)])
    assert readings[:, 0].mean() == pytest.approx(100.0, abs=0.05)
    assert readings[:, 0].std() == pytest.approx(2.0, rel=0.03)
    assert readings[:, 1].std() == pytest.approx(0.5, rel=0.03)


def test_sensor_noise_is_reproducible():
    settings = FlightComputerSettings()
    a = [Sensors(settings, seed=9).read(0.0, 0.0) for _ in range(3)]
    b = [Sensors(settings, seed=9).read(0.0, 0.0) for _ in range(3)]
    assert a == b


# --- Kalman filter -----------------------------------------------------------------


def fly_ballistic(settings, *, v0=250.0, seconds=20.0, seed=1):
    """Vacuum coast from 1 km at ``v0``: returns truth and estimates per step."""
    dt = 1.0 / settings.rate
    kf = AltitudeFilter(settings, dt)
    kf.x[:] = [1000.0, v0, -G0]  # already tracking at the start of the coast
    sensors = Sensors(settings, seed)
    rows = []
    for k in range(1, int(seconds / dt) + 1):
        t = k * dt
        h = 1000.0 + v0 * t - 0.5 * G0 * t * t
        v = v0 - G0 * t
        kf.step(*sensors.read(h, -G0))
        rows.append((h, v, kf.altitude, kf.velocity))
    return np.array(rows)


def test_filter_tracks_a_ballistic_coast():
    rows = fly_ballistic(FlightComputerSettings())
    settled = rows[100:]
    alt_err = settled[:, 2] - settled[:, 0]
    vel_err = settled[:, 3] - settled[:, 1]
    # Far better than the 1.5 m barometer on its own, and velocity is never measured.
    assert np.sqrt(np.mean(alt_err**2)) < 0.8
    assert np.sqrt(np.mean(vel_err**2)) < 0.5


def test_filter_beats_differentiating_the_barometer():
    settings = FlightComputerSettings()
    dt = 1.0 / settings.rate
    rows = fly_ballistic(settings)
    rng = np.random.default_rng(2)
    baro = rows[:, 0] + settings.barometer_noise * rng.standard_normal(len(rows))
    naive = np.diff(baro) / dt
    naive_err = np.sqrt(np.mean((naive - rows[1:, 1]) ** 2))
    kf_err = np.sqrt(np.mean((rows[100:, 3] - rows[100:, 1]) ** 2))
    assert kf_err < naive_err / 50.0


def test_filter_starts_at_rest_and_follows_a_climb():
    kf = AltitudeFilter(FlightComputerSettings(), dt=0.02)
    assert kf.altitude == kf.velocity == kf.acceleration == 0.0
    for k in range(1, 301):  # 6 s at 20 m/s², noise-free
        t = 0.02 * k
        kf.step(10.0 * t * t, 20.0)
    assert kf.velocity == pytest.approx(120.0, rel=0.01)
    assert kf.acceleration == pytest.approx(20.0, rel=0.01)


def test_covariance_stays_symmetric_and_positive_definite():
    settings = FlightComputerSettings()
    kf = AltitudeFilter(settings, 0.02)
    sensors = Sensors(settings, 3)
    for _ in range(2000):
        kf.step(*sensors.read(0.0, 0.0))
    assert np.allclose(kf.p, kf.p.T)
    assert np.all(np.linalg.eigvalsh(kf.p) > 0.0)


def test_filter_rejects_bad_time_step():
    with pytest.raises(ValueError, match="time step"):
        AltitudeFilter(FlightComputerSettings(), dt=0.0)
