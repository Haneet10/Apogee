"""Tests for the 1976 US Standard Atmosphere.

Reference values are from *U.S. Standard Atmosphere, 1976* (NOAA-S/T 76-1562):
layer base values from Table 4 and property values at geometric altitudes from
Table I, which prints five significant figures.
"""

import math

import numpy as np
import pytest

from apogee.atmosphere import (
    _H_BASE,
    _P_BASE,
    _T_BASE,
    MAX_ALTITUDE,
    MIN_ALTITUDE,
    R_AIR,
    OutOfAtmosphereError,
    atmosphere,
)
from apogee.earth import geometric_altitude, gravity

# altitude [m], T [K], p [Pa], rho [kg/m^3], a [m/s], mu [Pa s]   (Table I)
PUBLISHED = [
    (0.0, 288.150, 1.01325e5, 1.2250, 340.29, 1.7894e-5),
    (1_000.0, 281.651, 8.9876e4, 1.1117, 336.43, 1.7579e-5),
    (11_000.0, 216.774, 2.2700e4, 3.6480e-1, 295.15, 1.4223e-5),
    (20_000.0, 216.650, 5.5293e3, 8.8910e-2, 295.07, 1.4216e-5),
    (32_000.0, 228.490, 8.8906e2, 1.3555e-2, 303.02, 1.4859e-5),
    (50_000.0, 270.650, 7.9779e1, 1.0269e-3, 329.80, 1.7037e-5),
    (80_000.0, 198.639, 1.0524e0, 1.8458e-5, 282.54, 1.3208e-5),
    (86_000.0, 186.87, 3.7338e-1, 6.958e-6, 274.10, 1.2529e-5),
]

# Five significant figures -> worst-case rounding of 5e-5 relative; allow 1e-4.
REL = 1e-4


@pytest.mark.parametrize(("z", "t", "p", "rho", "a", "mu"), PUBLISHED)
def test_matches_published_table(z, t, p, rho, a, mu):
    air = atmosphere(z)
    assert air.temperature == pytest.approx(t, rel=REL)
    assert air.pressure == pytest.approx(p, rel=REL)
    assert air.density == pytest.approx(rho, rel=REL)
    assert air.speed_of_sound == pytest.approx(a, rel=REL)
    assert air.dynamic_viscosity == pytest.approx(mu, rel=REL)


def test_layer_bases_match_table_4():
    # Base temperatures and pressures derived from sea level must reproduce Table 4.
    assert _T_BASE == pytest.approx([288.15, 216.65, 216.65, 228.65, 270.65, 270.65, 214.65])
    assert _P_BASE == pytest.approx(
        [101325.0, 22632.06, 5474.889, 868.0187, 110.9063, 66.93887, 3.956420], rel=2e-7
    )


def test_top_of_model_matches_table_4_base_of_layer_7():
    # Layer 7 starts at H = 84.852 km with T_M = 186.946 K and p = 0.3733836 Pa.
    air = atmosphere(geometric_altitude(84_852.0))
    assert air.pressure == pytest.approx(0.3733836, rel=1e-6)


@pytest.mark.parametrize("h_base", _H_BASE[1:])
def test_continuous_across_layer_boundaries(h_base):
    z = geometric_altitude(h_base)
    below, above = atmosphere(z - 1e-6), atmosphere(z + 1e-6)
    for field in ("temperature", "pressure", "density", "speed_of_sound"):
        assert getattr(below, field) == pytest.approx(getattr(above, field), rel=1e-9)


def test_hydrostatic_equilibrium():
    # dp/dz = -rho g(z), checked with a central difference across the whole model.
    z = np.linspace(MIN_ALTITUDE + 10.0, MAX_ALTITUDE - 10.0, 400)
    dz = 0.5
    dpdz = (atmosphere(z + dz).pressure - atmosphere(z - dz).pressure) / (2 * dz)
    air = atmosphere(z)
    np.testing.assert_allclose(dpdz, -air.density * gravity(z), rtol=1e-6)


def test_ideal_gas_law_holds_below_80_km():
    z = np.linspace(MIN_ALTITUDE, 79_000.0, 200)
    air = atmosphere(z)
    np.testing.assert_allclose(air.pressure, air.density * R_AIR * air.temperature, rtol=1e-12)


def test_molecular_weight_drops_above_80_km():
    # Above 80 km M/M0 < 1, so p > rho R_air T by exactly 1/(M/M0); at 86 km M/M0 = 0.999579.
    air = atmosphere(86_000.0)
    ratio = air.density * R_AIR * air.temperature / air.pressure
    assert ratio == pytest.approx(0.999579, abs=1e-9)


def test_kinematic_viscosity_is_mu_over_rho():
    air = atmosphere(3_000.0)
    assert air.kinematic_viscosity == pytest.approx(air.dynamic_viscosity / air.density)


def test_sea_level_speed_of_sound_closed_form():
    assert atmosphere(0.0).speed_of_sound == pytest.approx(math.sqrt(1.4 * R_AIR * 288.15))


def test_scalar_in_gives_floats_out():
    air = atmosphere(1_500)
    assert isinstance(air.density, float)
    assert isinstance(air.altitude, float)


def test_array_in_matches_scalar_calls_and_keeps_shape():
    z = np.array([[0.0, 5_000.0], [40_000.0, 85_000.0]])
    air = atmosphere(z)
    assert air.pressure.shape == (2, 2)
    for idx in np.ndindex(z.shape):
        assert air.pressure[idx] == pytest.approx(atmosphere(float(z[idx])).pressure, rel=1e-14)


def test_below_sea_level_extends_troposphere():
    air = atmosphere(-5_000.0)
    assert air.temperature == pytest.approx(320.676, rel=REL)  # Table I
    assert air.pressure == pytest.approx(1.7776e5, rel=REL)


@pytest.mark.parametrize("bad", [-5_001.0, 86_001.0, 1e6])
def test_out_of_range_raises_helpful_error(bad):
    with pytest.raises(OutOfAtmosphereError, match=r"outside the 1976 US Standard Atmosphere"):
        atmosphere(bad)


def test_out_of_range_in_array_raises():
    with pytest.raises(OutOfAtmosphereError, match="90,000 m"):
        atmosphere([0.0, 90_000.0])


def test_nan_raises():
    with pytest.raises(OutOfAtmosphereError, match="NaN"):
        atmosphere(float("nan"))


def test_error_is_a_value_error():
    assert issubclass(OutOfAtmosphereError, ValueError)
