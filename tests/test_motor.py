from pathlib import Path

import numpy as np
import pytest

from apogee.motor import MotorFileError, impulse_class, load_motor, parse_eng

EXAMPLE = Path(__file__).parents[1] / "examples" / "motors" / "Cesaroni_M1670.eng"

# A hand-checkable motor: 0.1 s ramp to 10 N, 0.8 s flat, 0.1 s ramp down.
TRAPEZOID = """; comment line
C10-0 24 70 3-5-P 0.010 0.025 TST
0.1 10   ; inline comment
0.9 10
1.0 0
"""


def test_header_fields_are_read_in_si():
    (m,) = parse_eng(TRAPEZOID)
    assert m.designation == "C10-0"
    assert m.manufacturer == "TST"
    assert m.diameter == pytest.approx(0.024)
    assert m.length == pytest.approx(0.070)
    assert m.delays == (3.0, 5.0)
    assert m.propellant_mass == 0.010
    assert m.case_mass == pytest.approx(0.015)


def test_implicit_zero_start_and_trapezoid_impulse():
    (m,) = parse_eng(TRAPEZOID)
    assert m.time[0] == 0.0 and m.thrust[0] == 0.0
    # 0.5·0.1·10 + 0.8·10 + 0.5·0.1·10 = 9 N·s
    assert m.total_impulse == pytest.approx(9.0)
    assert m.average_thrust == pytest.approx(9.0)
    assert m.peak_thrust == 10.0
    assert m.impulse_class == "C"
    assert m.impulse_check() is None
    assert m.warnings == ()


def test_impulse_at_is_exact_on_piecewise_linear_thrust():
    (m,) = parse_eng(TRAPEZOID)
    assert m.impulse_at(0.05) == pytest.approx(0.5 * 0.05 * 5.0)  # area of ramp triangle
    assert m.impulse_at(0.5) == pytest.approx(0.5 + 4.0)
    assert m.impulse_at(-1.0) == 0.0
    assert m.impulse_at(2.0) == pytest.approx(9.0)
    t = np.linspace(0.0, 1.0, 20001)
    f = np.array([m.thrust_at(x) for x in t])
    numeric = np.sum(0.5 * (f[1:] + f[:-1]) * np.diff(t))
    assert numeric == pytest.approx(m.total_impulse, rel=1e-6)


def test_mass_falls_with_delivered_impulse():
    (m,) = parse_eng(TRAPEZOID)
    assert m.mass_at(0.0) == pytest.approx(0.025)
    assert m.mass_at(0.5) == pytest.approx(0.015 + 0.010 * (1 - 4.5 / 9.0))
    assert m.mass_at(5.0) == pytest.approx(0.015)


def test_inertia_matches_tube_plus_cylinder():
    (m,) = parse_eng(TRAPEZOID)
    r, length = 0.012, 0.070
    ixx, iyy = m.inertia_at(0.0)
    assert ixx == pytest.approx(0.015 * r**2 + 0.5 * 0.010 * r**2)
    assert iyy == pytest.approx(
        0.015 * (6 * r**2 + length**2) / 12 + 0.010 * (3 * r**2 + length**2) / 12
    )
    ixx_b, _ = m.inertia_at(10.0)
    assert ixx_b == pytest.approx(0.015 * r**2)


@pytest.mark.parametrize(
    ("impulse", "letter"),
    [
        (0.3, "1/8A"),
        (0.5, "1/4A"),
        (1.0, "1/2A"),
        (2.5, "A"),
        (2.51, "B"),
        (10.0, "C"),
        (40.0, "E"),
        (640.0, "I"),
        (6026.0, "M"),
        (40960.0, "O"),
    ],
)
def test_impulse_classes(impulse, letter):
    assert impulse_class(impulse) == letter


def test_example_motor_matches_its_published_impulse():
    m = load_motor(EXAMPLE)
    assert m.designation == "M1670-BS"
    # Cesaroni lists 6,026 N·s for the M1670; the curve should integrate to it.
    assert m.total_impulse == pytest.approx(6026.0, rel=1e-3)
    assert m.impulse_class == "M"
    assert m.warnings == ()
    assert m.burn_time == pytest.approx(3.9)


def test_wrong_class_is_flagged_but_still_loads():
    (m,) = parse_eng(TRAPEZOID.replace("C10-0", "F10-0"))
    assert "class C, not F" in m.warnings[0]


def test_missing_final_zero_is_added_with_warning():
    (m,) = parse_eng("A8 18 70 0 0.003 0.016 X\n0.1 8\n0.4 8\n")
    assert m.thrust[-1] == 0.0
    assert "did not end at zero" in m.warnings[0]


def test_several_motors_in_one_file(tmp_path):
    text = TRAPEZOID + "\n" + TRAPEZOID.replace("C10-0", "C11-0")
    path = tmp_path / "two.eng"
    path.write_text(text)
    assert len(parse_eng(text)) == 2
    assert load_motor(path, "c11").designation == "C11-0"
    with pytest.raises(MotorFileError, match="several motors"):
        load_motor(path)
    with pytest.raises(MotorFileError, match="it has: C10-0, C11-0"):
        load_motor(path, "D12")


def test_designations_starting_with_a_digit():
    (m,) = parse_eng("1/2A6 18 70 2 0.0016 0.015 X\n0.05 6\n0.2 4\n0.3 0\n")
    assert m.designation_class == "1/2A"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "no motor found"),
        ("C6 18 70 0 0.01\n0.1 5\n", "needs 7 fields"),
        ("C6 18 70 0 0.03 0.02 X\n0.1 5\n0.2 0\n", "may be swapped"),
        ("C6 18 70 0 0.01 0.02 X\n0.2 5\n0.1 0\n", "line 3: time 0.1 s is not after"),
        ("C6 18 70 0 0.01 0.02 X\n0.1 -5\n", "cannot be negative"),
        ("C6 18 70 0 0.01 0.02 X\n0.1 five\n", "line 2: expected 'time thrust'"),
        ("C6 18 70 0 0.01 0.02 X\n", "no thrust data"),
    ],
)
def test_malformed_files_say_what_is_wrong(text, message):
    with pytest.raises(MotorFileError, match=message):
        parse_eng(text)


def test_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="motor file not found"):
        load_motor(tmp_path / "nope.eng")
