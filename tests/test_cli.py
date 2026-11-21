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


ROCKET = "examples/rocket.yaml"
MOTOR = "examples/motors/Cesaroni_M1670.eng"


@pytest.fixture
def in_repo(monkeypatch):
    from pathlib import Path

    monkeypatch.chdir(Path(__file__).parents[1])


def test_rocket_command_prints_breakdown_and_states(in_repo):
    code, text = run("rocket", ROCKET)
    assert code == 0
    assert text.startswith("Kestrel\n")
    assert "Nose cone *" in text and "* measured mass" in text
    for label in ("Dry, no motor", "Liftoff", "Burnout"):
        assert label in text
    assert "class M" in text
    assert "moves" in text and "forward" in text
    assert "Main parachute: 1.80 m, Cd 1.60, opens at 300 m on descent" in text


def test_rocket_command_reports_file_errors(tmp_path, capsys):
    bad = tmp_path / "r.yaml"
    bad.write_text("name: x\ncomponents:\n  - body_tube: {length: 1m}\n")
    code, _ = run("rocket", str(bad))
    assert code == 2
    assert "start the components list with a nose_cone" in capsys.readouterr().err


def test_motor_command(in_repo):
    code, text = run("motor", MOTOR)
    assert code == 0
    assert "CTI M1670-BS" in text
    assert "6,026.4 N·s  (class M, matches the designation)" in text
    assert "█" in text


def test_motor_command_missing_file(capsys):
    code, _ = run("motor", "nope.eng")
    assert code == 2
    assert "motor file not found" in capsys.readouterr().err


def test_sparkline_peaks_at_full_block(in_repo):
    from apogee.cli import thrust_sparkline
    from apogee.motor import load_motor

    line = thrust_sparkline(load_motor(MOTOR), width=40)
    assert len(line) == 40
    assert "█" in line


def test_rocket_without_file_shows_bundled_example(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # works from anywhere, not just the repo
    code, text = run("rocket")
    assert code == 0
    assert text.startswith("Kestrel")
    assert "built-in example" in text
    code, text = run("motor")
    assert code == 0 and "M1670-BS" in text
