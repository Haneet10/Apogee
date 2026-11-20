import numpy as np
import pytest

from apogee.earth import EARTH_RADIUS, G0, geometric_altitude, geopotential_altitude, gravity


def test_gravity_at_sea_level_is_g0():
    assert gravity(0.0) == G0


def test_gravity_inverse_square():
    assert gravity(EARTH_RADIUS) == pytest.approx(G0 / 4)


def test_geopotential_round_trip():
    z = np.linspace(-5_000.0, 86_000.0, 50)
    np.testing.assert_allclose(
        geometric_altitude(geopotential_altitude(z)), z, rtol=1e-12, atol=1e-9
    )


def test_geopotential_known_value():
    # 11 km geometric is 10,981 m geopotential (USSA 1976 Table I).
    assert geopotential_altitude(11_000.0) == pytest.approx(10_981.0, abs=0.5)
