"""The airbrake commands and the airbrake part of the playback file."""

import csv
import io
import json

from apogee.cli import example_path, main
from apogee.environment import LaunchSite
from apogee.flight import simulate
from apogee.playback import playback, write_playback
from apogee.rocket import load_rocket

AB = str(example_path("kestrel-airbrakes.yaml"))


def run(*argv):
    out = io.StringIO()
    code = main(list(argv), out=out)
    return code, out.getvalue()


def test_simulate_reports_the_airbrakes(tmp_path):
    flight_json = tmp_path / "ab.json"
    code, text = run("simulate", AB, "--dt", "0.02", "-o", "none", "--json", str(flight_json))
    assert code == 0
    assert "Airbrakes open" in text
    assert "Target apogee      4,400 m" in text
    assert "Without airbrakes" in text
    assert "Drag learned" in text
    doc = json.loads(flight_json.read_text())
    assert doc["airbrakes"]["target"] == 4400.0
    assert doc["airbrakes"]["passive_apogee"] > 4500.0


def test_simulate_target_and_no_airbrakes_flags():
    code, text = run("simulate", AB, "--dt", "0.02", "-o", "none", "--target", "4200")
    assert code == 0
    assert "Target apogee      4,200 m" in text
    code, text = run("simulate", AB, "--dt", "0.02", "-o", "none", "--no-airbrakes")
    assert code == 0
    assert "Airbrakes open" not in text and "Target apogee" not in text


def test_target_on_a_rocket_without_airbrakes_is_an_error(capsys):
    code, _ = run("simulate", "--target", "3000", "-o", "none")
    assert code != 0
    assert "no airbrakes" in capsys.readouterr().err


def test_airbrakes_study_compares_passive_and_active(tmp_path):
    out_csv = tmp_path / "study.csv"
    code, text = run(
        "airbrakes", "--runs", "3", "--workers", "1", "--dt", "0.05", "-o", str(out_csv)
    )
    assert code == 0
    assert "Passive" in text and "Airbrakes" in text
    assert "airbrake Cd ±10 %" in text
    assert "cut the 1σ apogee scatter" in text
    rows = list(csv.DictReader(out_csv.open()))
    assert len(rows) == 3
    for r in rows:
        passive = abs(float(r["passive_apogee_m"]) - 4400.0)
        active = abs(float(r["airbrakes_apogee_m"]) - 4400.0)
        assert active < passive


def test_airbrakes_study_needs_airbrakes(capsys):
    code, _ = run("airbrakes", str(example_path("rocket.yaml")), "--runs", "2")
    assert code != 0
    assert "no airbrakes" in capsys.readouterr().err


def test_playback_carries_the_flight_computer(tmp_path):
    rocket = load_rocket(AB)
    flight = simulate(rocket, LaunchSite(), dt=0.05, descent_dt=0.5)
    doc = playback(flight)
    n = len(doc["series"]["t"])
    assert len(doc["series"]["airbrakes"]) == n
    assert max(doc["series"]["airbrakes"]) > 0.0
    outline = doc["rocket"]["outline"]["airbrakes"]
    assert outline["panels"] == 4
    log = doc["airbrakes"]["log"]
    assert len(log["t"]) == len(log["predicted_apogee"]) == len(log["deployment"])
    assert log["predicted_apogee"][0] is None  # nothing predicted on the pad
    assert doc["airbrakes"]["passive_apogee"] is None
    write_playback(flight, tmp_path / "f.json")  # NaNs became nulls: valid JSON
    json.loads((tmp_path / "f.json").read_text())


def test_passive_playback_has_no_airbrake_keys():
    flight = simulate(load_rocket(example_path("rocket.yaml")), dt=0.05, descent_dt=0.5)
    doc = playback(flight)
    assert doc["airbrakes"] is None
    assert "airbrakes" not in doc["series"]
    assert doc["rocket"]["outline"]["airbrakes"] is None
