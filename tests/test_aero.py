import itertools
import math

import numpy as np
import pytest

from apogee.aero import (
    Aerodynamics,
    base_drag_coefficient,
    fin_leading_edge_drag,
    skin_friction_coefficient,
    slender_body_wave_drag,
)
from apogee.cli import example_path
from apogee.components import NoseShape
from apogee.rocket import load_rocket, rocket_from_dict


def simple_rocket(
    *, shape="conical", fins=4, finish=None, nose_length=0.3, body_diameter=None, with_fins=True
):
    """A 100 mm rocket: 300 mm nose, 1 m body, trapezoidal fins flush with the tail."""
    body = {"length": 1.0, "thickness": 0.002, "density": 1850}
    if body_diameter is not None:
        body["diameter"] = body_diameter
    if with_fins:
        body["contents"] = [
            {
                "fins": {
                    "count": fins,
                    "root_chord": 0.15,
                    "tip_chord": 0.05,
                    "span": 0.1,
                    "sweep": 0.1,
                    "thickness": 0.003,
                    "density": 1850,
                }
            }
        ]
    data = {
        "components": [
            {
                "nose_cone": {
                    "shape": shape,
                    "length": nose_length,
                    "diameter": 0.1,
                    "thickness": 0.002,
                    "density": 1850,
                }
            },
            {"body_tube": body},
        ]
    }
    if finish is not None:
        data["finish"] = finish
    return rocket_from_dict(data)


# --- Barrowman, checked by hand -------------------------------------------------
#
# Fins: N = 4, s = 0.1, Cr = 0.15, Ct = 0.05, sweep 0.1, d = 0.1, R = 0.05.
#   mid-chord line  l_m = sqrt(s² + (0.1 + 0.025 − 0.075)²) = sqrt(0.0125)
#   CNα (fins)      = 4 N (s/d)² / (1 + sqrt(1 + (2 l_m / (Cr + Ct))²))
#                   = 16 / (1 + sqrt(2.25)) = 6.4
#   interference    = 1 + R / (s + R) = 4/3       → 8.5333
#   CP from the root leading edge
#                   = (0.1/3)(0.15 + 0.1)/0.2 + (0.2 − 0.0075/0.2)/6 = 0.06875
#   root leading edge at 0.3 + 1.0 − 0.15 = 1.15 m → X_f = 1.21875 m
# Nose: cone, CNα = 2 at 2/3 × 0.3 = 0.2 m.
# Whole rocket: CNα = 10.5333, X_cp = (0.4 + 10.4) / 10.5333 = 1.02532 m.

FIN_CNA = 6.4 * 4.0 / 3.0
FIN_CP = 1.21875
TOTAL_CNA = 2.0 + FIN_CNA
TOTAL_CP = (2.0 * 0.2 + FIN_CNA * FIN_CP) / TOTAL_CNA


def test_fin_normal_force_and_cp_match_hand_calculation():
    nose, fins = Aerodynamics(simple_rocket()).components()
    assert fins.cn_alpha == pytest.approx(FIN_CNA, rel=1e-12)
    assert fins.cp == pytest.approx(FIN_CP, rel=1e-12)
    assert nose.cn_alpha == pytest.approx(2.0)
    assert nose.cp == pytest.approx(0.2, rel=1e-6)


def test_whole_rocket_cp_is_the_lift_weighted_average():
    aero = Aerodynamics(simple_rocket())
    assert aero.cn_alpha() == pytest.approx(TOTAL_CNA, rel=1e-9)
    assert aero.center_of_pressure() == pytest.approx(TOTAL_CP, rel=1e-6)
    assert TOTAL_CP == pytest.approx(1.025316, rel=1e-6)


@pytest.mark.parametrize(
    ("shape", "fraction", "tol"),
    [
        ("conical", 2 / 3, 1e-6),  # V = A L / 3
        ("von_karman", 1 / 2, 1e-6),  # V = A L / 2, exactly
        ("parabolic", 7 / 15, 1e-6),  # ∫(2ξ − ξ²)² dξ = 8/15
        ("ellipsoid", 1 / 3, 1e-6),  # half an ellipsoid, V = 2 A L / 3
        ("lv_haack", 7 / 16, 1e-6),
        ("ogive", 0.466, 2e-3),  # Barrowman's tabulated value
    ],
)
def test_nose_cp_from_volume_matches_closed_forms(shape, fraction, tol):
    rocket = simple_rocket(shape=shape, nose_length=0.5, with_fins=False)
    nose = Aerodynamics(rocket).components()[0]
    assert nose.cp / 0.5 == pytest.approx(fraction, abs=tol)


def test_more_fins_add_lift_with_interference():
    four = Aerodynamics(simple_rocket(fins=4)).components()[1].cn_alpha
    three = Aerodynamics(simple_rocket(fins=3)).components()[1].cn_alpha
    six = Aerodynamics(simple_rocket(fins=6)).components()[1].cn_alpha
    assert three == pytest.approx(0.75 * four)
    assert six == pytest.approx(1.5 * four * 0.913)


# --- Compressibility --------------------------------------------------------------


def test_fin_lift_follows_prandtl_glauert_trend_and_is_continuous():
    aero = Aerodynamics(simple_rocket())
    slopes = [aero.cn_alpha(m) for m in (0.0, 0.3, 0.6, 0.85)]
    assert slopes == sorted(slopes)
    for edge in (0.9, 1.5):
        assert aero.cn_alpha(edge - 1e-9) == pytest.approx(aero.cn_alpha(edge + 1e-9), rel=1e-6)


def test_supersonic_stubby_fin_blend_is_continuous():
    aero = Aerodynamics(simple_rocket())
    # These fins: A = 2 s² / area = 2; A β = 1 at β = 0.5, i.e. M = sqrt(1.25).
    m = math.sqrt(1.25)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("apogee.aero._TRANSONIC", (0.5, 1.0001))
        assert aero.cn_alpha(m - 1e-9) == pytest.approx(aero.cn_alpha(m + 1e-9), rel=1e-6)


def test_high_aspect_supersonic_fin_tends_to_ackeret():
    # A tall rectangular fin: aspect ratio 2 s² / A = 20, so tip loss is small.
    rocket = rocket_from_dict(
        {
            "components": [
                {
                    "nose_cone": {
                        "shape": "ogive",
                        "length": 0.3,
                        "diameter": 0.1,
                        "thickness": 0.002,
                        "density": 1000,
                    }
                },
                {
                    "body_tube": {
                        "length": 1.0,
                        "thickness": 0.002,
                        "density": 1000,
                        "contents": [
                            {
                                "fins": {
                                    "count": 2,
                                    "root_chord": 0.1,
                                    "tip_chord": 0.1,
                                    "span": 1.0,
                                    "thickness": 0.002,
                                    "density": 1000,
                                }
                            }
                        ],
                    }
                },
            ]
        }
    )
    aero = Aerodynamics(rocket)
    fins = aero.components(2.0)[1]
    beta = math.sqrt(3.0)
    ackeret = 4.0 / beta * 0.1 / aero.reference_area  # one fin's area, N/2 = 1
    interference = 1.0 + 0.05 / 1.05
    assert fins.cn_alpha / interference == pytest.approx(ackeret * (1 - 1 / (40 * beta)))


# --- Angle of attack and stability ------------------------------------------------


def test_small_angle_normal_force_is_linear():
    aero = Aerodynamics(simple_rocket())
    alpha = 1e-4
    cn, cp = aero.normal_force(alpha)
    assert cn == pytest.approx(TOTAL_CNA * alpha, rel=1e-3)
    assert cp == pytest.approx(TOTAL_CP, rel=1e-3)


def test_body_lift_moves_cp_forward_at_angle_of_attack():
    aero = Aerodynamics(simple_rocket())
    cps = [aero.center_of_pressure(alpha=math.radians(a)) for a in (0, 2, 5, 10)]
    assert cps == sorted(cps, reverse=True)
    # Galejs: CN_body = 1.1 (A_plan / A_ref) sin²α
    alpha = math.radians(10)
    cn, _ = aero.normal_force(alpha)
    body = 1.1 * aero.planform_area / aero.reference_area * math.sin(alpha) ** 2
    assert cn == pytest.approx(TOTAL_CNA * math.sin(alpha) + body)


def test_planform_of_cone_and_tube():
    aero = Aerodynamics(simple_rocket())
    cone, tube = 0.5 * 0.1 * 0.3, 0.1 * 1.0
    assert aero.planform_area == pytest.approx(cone + tube, rel=1e-6)
    assert aero.planform_centroid == pytest.approx((cone * 0.2 + tube * 0.8) / (cone + tube))


def test_stability_margin_is_cp_minus_cg_in_calibres():
    rocket = simple_rocket()
    aero = Aerodynamics(rocket)
    cg = rocket.mass_properties().cg
    assert aero.stability_margin() == pytest.approx((TOTAL_CP - cg) / 0.1, rel=1e-6)
    assert aero.stability_margin(cg=0.9) == pytest.approx((TOTAL_CP - 0.9) / 0.1, rel=1e-6)


def test_kestrel_is_stable_and_gets_more_stable_as_it_burns():
    rocket = load_rocket(example_path("rocket.yaml"))
    aero = Aerodynamics(rocket)
    liftoff = aero.stability_margin(t=0.0)
    burnout = aero.stability_margin(t=rocket.burnout_time)
    assert 1.5 < liftoff < 3.0
    assert burnout > liftoff
    assert aero.warnings == []


def test_warnings_for_finless_rockets_and_diameter_steps():
    assert "unstable" in Aerodynamics(simple_rocket(with_fins=False)).warnings[0]
    stepped = Aerodynamics(simple_rocket(body_diameter=0.12))
    assert "differ in diameter" in stepped.warnings[0]


# --- Drag ----------------------------------------------------------------------


def test_skin_friction_formulas():
    # 1 / (1.50 ln 10⁶ − 5.6)² by hand
    assert skin_friction_coefficient(1e6, 0.0, 0.0) == pytest.approx(0.0043725, rel=1e-4)
    # Laminar-range floor equals the formula at Re = 10⁴.
    assert skin_friction_coefficient(5e3, 0.0, 0.0) == pytest.approx(0.0148, rel=2e-3)
    # A rough surface at high Re is roughness-limited: 0.032 (ε/L)^0.2.
    assert skin_friction_coefficient(1e8, 0.0, 1e-4) == pytest.approx(0.032 * 1e-4**0.2)
    # Compressibility lowers friction.
    assert skin_friction_coefficient(1e7, 0.8, 0.0) == pytest.approx(
        skin_friction_coefficient(1e7, 0.0, 0.0) * (1 - 0.064)
    )
    assert skin_friction_coefficient(1e7, 2.0, 0.0) < skin_friction_coefficient(1e7, 0.9, 0.0)


@pytest.mark.parametrize("edge", [0.9, 1.0])
def test_leading_edge_drag_is_continuous(edge):
    # Hoerner's published fits meet to within 0.13 % at Mach 0.9.
    below, above = fin_leading_edge_drag(edge - 1e-9), fin_leading_edge_drag(edge + 1e-9)
    assert below == pytest.approx(above, rel=2e-3)


def test_leading_edge_and_base_drag_values():
    assert fin_leading_edge_drag(0.0) == 0.0
    assert base_drag_coefficient(1.0 - 1e-12) == pytest.approx(base_drag_coefficient(1.0))
    assert base_drag_coefficient(0.5) == pytest.approx(0.12 + 0.13 * 0.25)


def test_wetted_areas_of_cone_and_tube():
    aero = Aerodynamics(simple_rocket())
    cone = math.pi * 0.05 * math.hypot(0.05, 0.3)  # lateral area of a cone
    tube = math.pi * 0.1 * 1.0
    assert aero.body_wetted_area == pytest.approx(cone + tube, rel=1e-6)
    fin_area = 0.5 * (0.15 + 0.05) * 0.1
    assert aero.fin_wetted_area == pytest.approx(2 * 4 * fin_area)


def test_cone_nose_pressure_drag_subsonic():
    aero = Aerodynamics(simple_rocket())
    half_angle = math.atan2(0.05, 0.3)
    assert aero.drag(0.3).nose == pytest.approx(0.8 * math.sin(half_angle) ** 2)
    assert Aerodynamics(simple_rocket(shape="ogive")).drag(0.3).nose == 0.0


def test_motor_exhaust_removes_its_share_of_base_drag():
    rocket = load_rocket(example_path("rocket.yaml"))
    aero = Aerodynamics(rocket)
    coast, burn = aero.drag(0.4), aero.drag(0.4, thrusting=True)
    assert rocket.motor is not None
    motor_area = 0.25 * math.pi * rocket.motor.diameter**2
    expected = base_drag_coefficient(0.4) * motor_area / aero.reference_area
    assert coast.base - burn.base == pytest.approx(expected)
    assert coast.friction == burn.friction


def test_rougher_finish_means_more_drag():
    smooth = Aerodynamics(simple_rocket(finish="polished")).drag(0.5).total
    rough = Aerodynamics(simple_rocket(finish="rough")).drag(0.5).total
    assert rough > smooth * 1.3


def test_drag_uses_the_atmosphere_for_reynolds_number():
    aero = Aerodynamics(simple_rocket())
    low, high = aero.drag(0.5, 0.0), aero.drag(0.5, 10_000.0)
    assert high.reynolds < low.reynolds / 2  # thinner air, lower Reynolds number
    assert low.reynolds == pytest.approx(0.5 * 340.294 * 1.3 / 1.4607e-5, rel=1e-3)


def test_kestrel_drag_is_in_the_usual_range_for_a_finned_rocket():
    aero = Aerodynamics(load_rocket(example_path("rocket.yaml")))
    cd = aero.drag(0.3).total
    assert 0.35 < cd < 0.65
    assert aero.max_reliable_mach() == math.inf  # wave drag of its von Kármán nose is modelled


def test_negative_mach_is_rejected():
    with pytest.raises(ValueError, match="Mach"):
        Aerodynamics(simple_rocket()).drag(-0.1)


# --- Damping ---------------------------------------------------------------------


def test_damping_coefficient_by_hand():
    aero = Aerodynamics(simple_rocket())
    cg, rho, v = 0.8, 1.225, 100.0
    arms = 2.0 * (0.2 - cg) ** 2 + FIN_CNA * (FIN_CP - cg) ** 2
    expected = 0.5 * rho * v * aero.reference_area * arms
    assert aero.damping_coefficient(rho, v, cg) == pytest.approx(expected, rel=1e-6)
    assert aero.damping_coefficient(rho, 2 * v, cg) == pytest.approx(2 * expected, rel=1e-6)


# --- Wave drag of smooth noses ---------------------------------------------------


@pytest.mark.parametrize("fineness", [3.0, 5.0, 8.0])
def test_von_karman_wave_drag_is_von_karmans_closed_form(fineness):
    # Minimum-drag nose for its length and base: D/q = 4 S² / (π L²), i.e. Cd = (d/L)².
    assert slender_body_wave_drag(NoseShape.VON_KARMAN, fineness) == pytest.approx(
        1.0 / fineness**2, rel=1e-4
    )


@pytest.mark.parametrize("fineness", [3.0, 5.0, 8.0])
def test_lv_haack_wave_drag_by_hand(fineness):
    # S'(x) has two Fourier terms, A1 = 4S/(πL²) and A2 = 3CS/(πL²), so
    # Cd = (4 + 4.5 C²) / (4 f²) with C = 1/3.
    expected = (4.0 + 4.5 / 9.0) / (4.0 * fineness**2)
    assert slender_body_wave_drag(NoseShape.LV_HAACK, fineness) == pytest.approx(expected, rel=1e-4)


def test_von_karman_has_the_least_wave_drag_of_the_smooth_noses():
    kinds = [NoseShape.OGIVE, NoseShape.PARABOLIC, NoseShape.LV_HAACK]
    karman = slender_body_wave_drag(NoseShape.VON_KARMAN, 5.0)
    assert all(slender_body_wave_drag(k, 5.0) > karman for k in kinds)


def test_wave_drag_needs_a_smooth_pointed_nose():
    for shape in (NoseShape.CONICAL, NoseShape.ELLIPSOID):
        with pytest.raises(ValueError, match="smooth pointed"):
            slender_body_wave_drag(shape, 5.0)
    with pytest.raises(ValueError, match="fineness"):
        slender_body_wave_drag(NoseShape.OGIVE, 0.0)


def test_nose_wave_drag_rises_through_the_transonic_range():
    aero = Aerodynamics(load_rocket(example_path("rocket.yaml")))
    full = slender_body_wave_drag(NoseShape.VON_KARMAN, 510.0 / 102.0)
    assert aero.drag(0.8).nose == 0.0
    nose = [aero.drag(m).nose for m in np.linspace(0.8, 1.2, 9)]
    assert all(b > a for a, b in itertools.pairwise(nose))
    assert aero.drag(1.2).nose == pytest.approx(full)  # same reference area as the nose base
    assert aero.drag(2.0).nose == pytest.approx(full)
