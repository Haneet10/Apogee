import pytest

from apogee.units import UnitError, parse_density, parse_length, parse_mass


@pytest.mark.parametrize(
    ("value", "metres"),
    [(0.5, 0.5), (2, 2.0), ("102mm", 0.102), ("4 in", 0.1016), ("1.5ft", 0.4572), ("3 cm", 0.03)],
)
def test_lengths(value, metres):
    assert parse_length(value) == pytest.approx(metres)


@pytest.mark.parametrize(
    ("value", "kg"),
    [("450g", 0.45), ("1.2 oz", 0.0340194), ("2lb", 0.90718474), (1.5, 1.5), ("1,250 g", 1.25)],
)
def test_masses(value, kg):
    assert parse_mass(value) == pytest.approx(kg)


def test_density_units():
    assert parse_density("1.85 g/cm3") == pytest.approx(1850.0)
    assert parse_density(630) == 630.0


def test_unknown_unit_lists_the_accepted_ones():
    with pytest.raises(UnitError, match=r"use one of: m, cm, mm, in, ft"):
        parse_length("3 furlongs")


def test_mass_unit_on_a_length_is_rejected():
    with pytest.raises(UnitError, match="unknown length unit"):
        parse_length("40g")


@pytest.mark.parametrize("bad", ["", "long", True, None, [1]])
def test_non_numbers_are_rejected(bad):
    with pytest.raises(UnitError):
        parse_length(bad)


def test_zero_and_negative():
    with pytest.raises(UnitError, match="more than zero"):
        parse_length(0)
    assert parse_length("0mm", allow_zero=True) == 0.0
    with pytest.raises(UnitError, match="zero or more"):
        parse_length("-1mm", allow_zero=True)
