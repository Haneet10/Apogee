import argparse
import csv
import dataclasses
import io

import pytest

from apogee import __version__
from apogee.cli import example_path, main, parse_altitude


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


def test_aero_shows_margin_verdict_and_drag_table():
    code, text = run("aero")
    assert code == 0
    assert "Whole rocket" in text
    assert "Off the rail: stable." in text
    liftoff = next(line for line in text.splitlines() if line.startswith("Liftoff"))
    assert 1.5 < float(liftoff.split()[-1]) < 3.0
    assert "Cd coast" in text
    assert "lower bound" not in text  # von Kármán wave drag is modelled at every Mach


def test_aero_custom_mach_list_and_altitude():
    code, text = run("aero", "--mach", "0.3", "0.6", "--altitude", "3km")
    assert code == 0
    rows = [line for line in text.splitlines() if line[:4] in ("0.30", "0.60")]
    assert len(rows) == 2
    assert "at 3,000 m" in text
    assert "lower bound" not in text


def test_aero_rejects_bad_mach(capsys):
    with pytest.raises(SystemExit):
        main(["aero", "--mach", "fast"])
    assert "between 0 and 5" in capsys.readouterr().err


# --- simulate --------------------------------------------------------------------


def test_simulate_prints_events_and_writes_csv(tmp_path):
    path = tmp_path / "flight.csv"
    code, text = run("simulate", "--wind", "4", "--dt", "0.02", "-o", str(path))
    assert code == 0
    assert "wind 4 m/s from W (270°)" in text
    for word in ("Rail exit", "Apogee", "Main parachute opens", "Landing", "Max speed"):
        assert word in text
    landing = next(line for line in text.splitlines() if "of the pad" in line)
    assert " E of the pad" in landing  # a west wind blows it east
    assert "-0.0" not in text
    rows = list(csv.DictReader(path.open()))
    assert f"Wrote {len(rows):,} time steps" in text
    assert rows[-1]["phase"] == "under Main parachute"


def test_simulate_default_csv_name_and_skip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, text = run("simulate", "--dt", "0.02", "-o", "none")
    assert code == 0 and "Wrote" not in text and not list(tmp_path.iterdir())
    from apogee.cli import default_csv_path
    from apogee.rocket import load_rocket

    rocket = load_rocket(example_path("rocket.yaml"))
    assert str(default_csv_path("examples/rocket.yaml", rocket)) == "kestrel-flight.csv"
    unnamed = dataclasses.replace(rocket, name="Unnamed rocket")
    assert str(default_csv_path("rockets/My Rocket v2.yaml", unnamed)) == "my-rocket-v2-flight.csv"


def test_simulate_reports_bad_input(capsys):
    assert run("simulate", "missing.yaml")[0] == 2
    assert "missing.yaml" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        run("simulate", "--wind", "-3")
