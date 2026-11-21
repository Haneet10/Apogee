"""Component mass properties checked against closed-form results."""

import math

import pytest

from apogee.components import (
    BodyTube,
    FinSet,
    MassComponent,
    MassProperties,
    NoseCone,
    NoseShape,
    Ring,
)

RHO = 1000.0


def test_body_tube_matches_hollow_cylinder_formulas():
    tube = BodyTube(name="t", length=1.0, diameter=0.1, thickness=0.002, density=RHO)
    ro, ri = 0.05, 0.048
    mass = RHO * math.pi * (ro**2 - ri**2) * 1.0
    p = tube.mass_properties()
    assert p.mass == pytest.approx(mass)
    assert p.cg == pytest.approx(0.5)
    assert p.ixx == pytest.approx(0.5 * mass * (ro**2 + ri**2))
    assert p.iyy == pytest.approx(mass * (3 * (ro**2 + ri**2) + 1.0) / 12)


def test_solid_cone_matches_closed_form():
    # Thickness larger than the base radius gives a solid cone.
    r, length = 0.05, 0.3
    nose = NoseCone(
        name="n",
        length=length,
        diameter=2 * r,
        thickness=1.0,
        density=RHO,
        shape=NoseShape.CONICAL,
    )
    p = nose.mass_properties()
    mass = RHO * math.pi * r**2 * length / 3
    assert p.mass == pytest.approx(mass, rel=1e-5)
    assert p.cg == pytest.approx(0.75 * length, rel=1e-5)  # 3/4 of the way from the tip
    assert p.ixx == pytest.approx(0.3 * mass * r**2, rel=1e-5)
    assert p.iyy == pytest.approx(mass * (3 * r**2 / 20 + 3 * length**2 / 80), rel=1e-5)


def test_thin_conical_shell_has_cg_at_two_thirds():
    r, length, t = 0.05, 0.3, 1e-5
    nose = NoseCone(
        name="n",
        length=length,
        diameter=2 * r,
        thickness=t,
        density=RHO,
        shape=NoseShape.CONICAL,
    )
    p = nose.mass_properties()
    assert p.cg == pytest.approx(2 * length / 3, rel=1e-3)
    # Radial wall t: mass ≈ ρ·2π·t·∫r dx = ρ·π·t·R·L
    assert p.mass == pytest.approx(RHO * math.pi * t * r * length, rel=1e-3)


@pytest.mark.parametrize("shape", list(NoseShape))
def test_every_profile_meets_the_body_and_is_monotonic(shape):
    import numpy as np

    x = np.linspace(0.0, 0.4, 401)
    r = shape.radius(x, 0.4, 0.05)
    assert r[0] == pytest.approx(0.0, abs=1e-12)
    assert r[-1] == pytest.approx(0.05, rel=1e-9)
    assert np.all(np.diff(r) >= -1e-12)


def test_blunter_noses_are_heavier_and_have_cg_further_forward():
    def props(shape):
        return NoseCone(
            name="n", length=0.4, diameter=0.1, thickness=0.002, density=RHO, shape=shape
        ).mass_properties()

    cone, ogive, ellipse = (
        props(NoseShape.CONICAL),
        props(NoseShape.OGIVE),
        props(NoseShape.ELLIPSOID),
    )
    assert cone.mass < ogive.mass < ellipse.mass
    assert cone.cg > ogive.cg > ellipse.cg


def fins(**kw):
    base = dict(
        name="f",
        length=0.3,
        count=3,
        tip_chord=0.1,
        span=0.12,
        sweep=0.18,
        thickness=0.003,
        density=RHO,
        body_radius=0.05,
    )
    base.update(kw)
    return FinSet(**base)


def test_fin_mass_and_centroid_match_trapezoid_formulas():
    f = fins()
    cr, ct, s, sweep = 0.3, 0.1, 0.12, 0.18
    p = f.mass_properties()
    assert f.area == pytest.approx(0.5 * (cr + ct) * s)
    assert p.mass == pytest.approx(3 * RHO * 0.003 * f.area)
    x_bar = sweep * (cr + 2 * ct) / (3 * (cr + ct)) + (cr**2 + cr * ct + ct**2) / (3 * (cr + ct))
    assert p.cg == pytest.approx(x_bar, rel=1e-6)


def test_rectangular_fins_inertia_closed_form():
    # Rectangular plate, chord c, span s, mounted at radius R.
    c, s, radius, n = 0.2, 0.1, 0.05, 4
    f = fins(length=c, tip_chord=c, span=s, sweep=0.0, count=n, body_radius=radius)
    p = f.mass_properties()
    m = p.mass
    # Σ m ρ² for a plate spanning R..R+s
    ixx = m * ((radius + s) ** 3 - radius**3) / (3 * s)
    assert p.ixx == pytest.approx(ixx, rel=1e-6)
    assert p.iyy == pytest.approx(m * c**2 / 12 + 0.5 * ixx, rel=1e-6)
    assert p.cg == pytest.approx(c / 2)


def test_measured_mass_scales_inertia_but_keeps_cg():
    tube = BodyTube(name="t", length=1.0, diameter=0.1, thickness=0.002, density=RHO)
    weighed = BodyTube(
        name="t", length=1.0, diameter=0.1, thickness=0.002, density=RHO, mass_override=1.0
    )
    a, b = tube.mass_properties(), weighed.mass_properties()
    assert b.mass == 1.0
    assert b.cg == a.cg
    assert b.iyy / a.iyy == pytest.approx(1.0 / a.mass)


def test_bulkhead_is_a_solid_disc():
    p = Ring(
        name="b", length=0.006, diameter=0.1, inner_diameter=0.0, density=RHO
    ).mass_properties()
    assert p.ixx == pytest.approx(0.5 * p.mass * 0.05**2)


def test_point_mass_has_no_inertia_of_its_own():
    p = MassComponent(name="m", length=0.0, mass=0.5).mass_properties()
    assert (p.mass, p.cg, p.ixx, p.iyy) == (0.5, 0.0, 0.0, 0.0)


def test_combine_two_point_masses_parallel_axis():
    a = MassProperties(1.0, 0.0, 0.0, 0.0)
    b = MassProperties(3.0, 1.0, 0.0, 0.0)
    c = MassProperties.combine([a, b])
    assert c.mass == 4.0
    assert c.cg == pytest.approx(0.75)
    assert c.iyy == pytest.approx(1.0 * 0.75**2 + 3.0 * 0.25**2)
    assert c.inertia_tensor.shape == (3, 3)
    with pytest.raises(ValueError):
        MassProperties.combine([])
