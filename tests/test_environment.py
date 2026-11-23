import math

import numpy as np
import pytest

from apogee import rotation
from apogee.environment import LaunchSite, Wind


def test_wind_power_law():
    wind = Wind(speed=5.0, direction=0.0)
    assert wind.speed_at(10.0) == pytest.approx(5.0)
    assert wind.speed_at(1280.0) == pytest.approx(5.0 * 128 ** (1 / 7))  # = 10 m/s
    assert wind.speed_at(0.0) == 0.0
    assert Wind(speed=5.0, exponent=0.0).speed_at(0.0) == 5.0


@pytest.mark.parametrize(
    ("bearing", "expected"),
    [(0.0, (0, -1, 0)), (90.0, (-1, 0, 0)), (180.0, (0, 1, 0)), (270.0, (1, 0, 0))],
)
def test_wind_blows_away_from_where_it_comes_from(bearing, expected):
    v = Wind(speed=1.0, direction=bearing, exponent=0.0).velocity(100.0)
    np.testing.assert_allclose(v, expected, atol=1e-15)


def test_wind_rejects_negative_speed():
    with pytest.raises(ValueError, match="direction"):
        Wind(speed=-3.0)


def test_rail_direction():
    np.testing.assert_allclose(LaunchSite().rail_direction, [0, 0, 1])
    d = LaunchSite(tilt=30.0, heading=90.0).rail_direction
    np.testing.assert_allclose(d, [0.5, 0.0, math.sqrt(3) / 2], atol=1e-15)
    with pytest.raises(ValueError, match="tilt"):
        LaunchSite(tilt=95.0)
    with pytest.raises(ValueError, match="rail length"):
        LaunchSite(rail_length=-1.0)


# --- Quaternions ------------------------------------------------------------------


def test_quaternion_matrix_is_a_rotation():
    q = rotation.from_axis_angle([1.0, 2.0, -0.5], 1.1)
    r = rotation.to_matrix(q)
    np.testing.assert_allclose(r @ r.T, np.eye(3), atol=1e-14)
    assert np.linalg.det(r) == pytest.approx(1.0)
    v = np.array([0.3, -1.0, 2.0])
    np.testing.assert_allclose(rotation.rotate_inverse(q, rotation.rotate(q, v)), v)


def test_quaternion_product_composes_rotations():
    a = rotation.from_axis_angle([0, 0, 1], 0.4)
    b = rotation.from_axis_angle([0, 1, 0], -0.9)
    v = np.array([1.0, 2.0, 3.0])
    np.testing.assert_allclose(
        rotation.rotate(rotation.multiply(a, b), v), rotation.rotate(a, rotation.rotate(b, v))
    )


@pytest.mark.parametrize("target", [(0, 0, 1), (0.2, -0.4, 0.9), (-1, 0, 0), (1, 0, 0)])
def test_from_two_vectors(target):
    q = rotation.from_two_vectors([1.0, 0.0, 0.0], target)
    t = np.asarray(target, dtype=float)
    np.testing.assert_allclose(
        rotation.rotate(q, [1.0, 0.0, 0.0]), t / np.linalg.norm(t), atol=1e-12
    )


def test_quaternion_rate_spins_about_body_axis():
    """Integrating q̇ at a constant rate reproduces the axis-angle rotation."""
    q = np.array([1.0, 0.0, 0.0, 0.0])
    omega = np.array([0.0, 0.0, 2.0])
    dt = 1e-4
    for _ in range(5000):  # 0.5 s at 2 rad/s = 1 rad about z
        q = q + dt * rotation.rate(q, omega)
        q /= np.linalg.norm(q)
    np.testing.assert_allclose(q, rotation.from_axis_angle([0, 0, 1], 1.0), atol=1e-4)
    with pytest.raises(ValueError, match="zero vector"):
        rotation.from_axis_angle([0, 0, 0], 1.0)
