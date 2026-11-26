import json
from itertools import pairwise

import numpy as np
import pytest

from apogee import Aerodynamics, LaunchSite, Wind, load_rocket, simulate
from apogee.cli import VIEWER_URL, example_path, main
from apogee.playback import FORMAT, VERSION, playback, sample_indices, write_playback


@pytest.fixture(scope="module")
def flight():
    rocket = load_rocket(example_path("rocket.yaml"))
    return simulate(rocket, LaunchSite(rail_length=5.2, wind=Wind(4.0, 270.0)), dt=0.02)


@pytest.fixture(scope="module")
def doc(flight):
    return playback(flight)


def test_document_identifies_itself(doc):
    assert doc["format"] == FORMAT
    assert doc["version"] == VERSION
    assert doc["generator"].startswith("apogee ")
    assert doc["rocket"]["name"] == "Kestrel"
    assert doc["rocket"]["motor"]["designation"] == "M1670-BS"


def test_series_are_aligned_and_time_ordered(doc):
    series = doc["series"]
    n = len(series["t"])
    assert all(len(column) == n for column in series.values())
    assert np.all(np.diff(series["t"]) > 0)
    assert all(len(q) == 4 for q in series["attitude"])
    norms = np.linalg.norm(np.array(series["attitude"]), axis=1)
    assert np.allclose(norms, 1.0, atol=1e-4)


def test_thinning_keeps_the_flight_small(flight, doc):
    n = len(doc["series"]["t"])
    assert n < len(flight.t) / 3
    assert 500 < n < 2000


def test_spacing_by_phase(flight):
    kept = sample_indices(flight)
    t = flight.t[kept]
    gaps = np.diff(t)
    phases = [flight.phase[i] for i in kept[1:]]
    boost = [g for g, p in zip(gaps, phases, strict=True) if p == "boost"]
    coast = [g for g, p in zip(gaps, phases, strict=True) if p == "coast"]
    chute = [g for g, p in zip(gaps, phases, strict=True) if p.startswith("under")]
    # dt = 0.02 here, so boost keeps every step; the event instants add short gaps
    assert max(boost) <= 0.02 + 1e-9
    assert max(coast) <= 0.1 + 0.02 + 1e-9
    assert max(chute) <= 0.5 + 0.05 + 1e-9


def test_event_instants_are_kept_exactly(flight, doc):
    t = np.array(doc["series"]["t"])
    for e in flight.events:  # rail exit falls between steps; the others are steps
        assert np.min(np.abs(t - e.time)) <= 0.02 / 2 + 1e-9, e.name
    assert max(doc["series"]["up"]) == pytest.approx(flight.apogee, abs=0.01)
    last = flight.trajectory.position[-1]
    assert doc["series"]["east"][-1] == pytest.approx(last[0], abs=0.01)
    assert doc["series"]["up"][-1] == pytest.approx(0.0, abs=0.01)


def test_linear_playback_tracks_the_solution(flight, doc):
    """Interpolating the thinned samples stays close to every RK4 step."""
    s = doc["series"]
    up = np.interp(flight.t, s["t"], s["up"])
    ascent = flight.t <= flight.time_to_apogee
    assert np.max(np.abs(up[ascent] - flight.trajectory.altitude[ascent])) < 0.5
    assert np.max(np.abs(up - flight.trajectory.altitude)) < 1.0


def test_phases_cover_the_flight_and_meet_at_events(flight, doc):
    phases = doc["phases"]
    assert [p["name"] for p in phases] == [
        "rail",
        "boost",
        "coast",
        "under Drogue parachute",
        "under Main parachute",
    ]
    assert phases[0]["start"] == 0.0
    assert phases[-1]["end"] == pytest.approx(flight.flight_time, abs=1e-3)
    for a, b in pairwise(phases):
        assert a["end"] == b["start"]
    assert phases[1]["end"] == pytest.approx(flight.event("Burnout").time, abs=1e-3)
    assert phases[3]["end"] == pytest.approx(flight.event("Main parachute opens").time, abs=1e-3)


def test_summary_matches_the_flight(flight, doc):
    s = doc["summary"]
    assert s["apogee"] == pytest.approx(flight.apogee, abs=0.01)
    assert s["max_mach"] == pytest.approx(flight.max_mach, abs=1e-3)
    assert s["landing_distance"] == pytest.approx(flight.landing_distance, abs=0.1)
    assert s["landing_bearing"] == pytest.approx(90.0, abs=1.0)
    assert set(s["descent_rates"]) == {"Drogue parachute", "Main parachute"}
    assert doc["site"]["wind"] == {"speed": 4.0, "direction": 270.0}


def test_outline_describes_the_airframe(flight, doc):
    o = doc["rocket"]["outline"]
    assert o["length"] == pytest.approx(flight.rocket.length, abs=1e-4)
    nose = o["nose"]
    assert nose["shape"] == "von_karman"
    assert nose["profile"][0] == [0.0, 0.0]
    assert nose["profile"][-1][1] == pytest.approx(nose["diameter"] / 2, abs=1e-4)
    # body tubes run on from the nose without gaps
    edge = nose["length"]
    for body in o["bodies"]:
        assert body["start"] == pytest.approx(edge, abs=1e-4)
        edge = body["start"] + body["length"]
    assert edge == pytest.approx(o["length"], abs=1e-4)
    fin = o["fins"][0]
    assert fin["count"] == 3
    assert fin["start"] + fin["root_chord"] == pytest.approx(o["length"], abs=1e-4)
    margin = Aerodynamics(flight.rocket).stability_margin(0.0)
    assert o["margin_liftoff"] == pytest.approx(margin, abs=1e-3)
    assert o["cg_liftoff"] < o["cp"]


def test_write_playback_is_compact_json(flight, tmp_path):
    path = tmp_path / "flight.json"
    n = write_playback(flight, path)
    text = path.read_text(encoding="utf-8")
    assert "\n" not in text and '": ' not in text
    assert len(text) < 200_000
    assert len(json.loads(text)["series"]["t"]) == n


def test_cli_writes_playback_and_points_to_viewer(tmp_path, capsys):
    path = tmp_path / "kestrel.json"
    code = main(["simulate", "--dt", "0.05", "-o", "none", "--json", str(path)])
    out = capsys.readouterr().out
    assert code == 0
    assert VIEWER_URL in out
    assert json.loads(path.read_text(encoding="utf-8"))["format"] == FORMAT


def test_cli_reports_unwritable_playback(tmp_path, capsys):
    target = tmp_path / "missing" / "flight.json"
    code = main(["simulate", "--dt", "0.05", "-o", "none", "--json", str(target)])
    assert code != 0
    assert "cannot write" in capsys.readouterr().err
