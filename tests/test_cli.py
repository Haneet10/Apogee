import argparse
import csv
import io

import pytest

from apogee import __version__
from apogee.cli import main, parse_altitude


@pytest.mark.parametrize(
    ("text", "metres"),
    [
        ("1500", 1500.0),
        ("1.5km", 1500.0),
        ("10000ft", 3048.0),
        ("2 km", 2000.0),
        ("12,000", 12000.0),
    ],
)
def test_parse_altitude_units(text, metres):
    assert parse_altitude(text) == pytest.approx(metres)


def test_parse_altitude_rejects_garbage_with_examples():
    with pytest.raises(argparse.ArgumentTypeError, match=r"1\.5km"):
        parse_altitude("high")


def run(*argv):
    out = io.StringIO()
    code = main(list(argv), out=out)
    return code, out.getvalue()


def test_default_table_has_aligned_rows_with_units():
    code, text = run("atmosphere")
    assert code == 0
    lines = text.splitlines()
    assert "[kg/m³]" in lines[1]
    body = lines[3:14]
    assert len(body) == 11  # 0 to 20 km every 2 km
    assert len({len(line) for line in lines[:14]}) == 1  # every row the same width
    assert body[0].split()[:4] == ["0", "288.15", "15.00", "101,325"]


def test_table_for_requested_altitudes():
    _, text = run("atmosphere", "11km")
    row = text.splitlines()[3].split()
    assert row[0] == "11,000"
    assert row[1] == "216.77"


def test_csv_output_is_machine_readable():
    _, text = run("atmosphere", "0", "1km", "--csv")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert [float(r["altitude_m"]) for r in rows] == [0.0, 1000.0]
    assert float(rows[0]["density_kg_m3"]) == pytest.approx(1.225, rel=1e-6)


def test_out_of_range_exits_2_with_message(capsys):
    code, _ = run("atmosphere", "100km")
    assert code == 2
    assert "outside the 1976 US Standard Atmosphere" in capsys.readouterr().err


def test_no_command_prints_help():
    code, text = run()
    assert code == 0
    assert "atmosphere" in text


def test_version(capsys):
    with pytest.raises(SystemExit):
        main(["--version"])
    assert __version__ in capsys.readouterr().out
