"""Flight playback files for the browser viewer.

:func:`playback` turns a :class:`~apogee.flight.Flight` into a small,
self-describing JSON document: the rocket's outline, the launch site, the
events, the headline numbers and a thinned copy of the trajectory. The viewer
in ``viewer/`` (live at https://haneet10.github.io/Apogee/) plays it back;
``apogee simulate --json flight.json`` writes one::

    >>> from apogee import load_rocket, simulate
    >>> from apogee.cli import example_path
    >>> doc = playback(simulate(load_rocket(example_path("rocket.yaml")), dt=0.02))
    >>> doc["format"], doc["version"]
    ('apogee-flight', 1)
    >>> [e["name"] for e in doc["events"]][:3]
    ['Rail exit', 'Burnout', 'Apogee']

Thinning
--------
A full flight holds thousands of integration steps, most of them under the
parachute where nothing changes quickly. Samples are kept at most every
``0.02 s`` on the rail and under thrust, ``0.1 s`` while coasting and
``0.5 s`` under a canopy, and the step at (or nearest) every event is always
kept, so the apogee and landing in the file are exactly the simulated ones. A Kestrel
flight shrinks from about 8,500 steps to about 1,000 (roughly 100 kB).
Between samples the viewer interpolates linearly, which at these spacings
is within a few centimetres of the RK4 solution on the way up.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from apogee import __version__
from apogee.aero import Aerodynamics
from apogee.components import Airbrakes, BodyTube, FinSet, NoseCone
from apogee.earth import G0
from apogee.flight import Flight

__all__ = ["FORMAT", "VERSION", "playback", "sample_indices", "write_playback"]

#: Identifies a playback document.
FORMAT = "apogee-flight"
#: Bumped when the layout changes in a way old viewers cannot read.
VERSION = 1

#: Longest gap between kept samples in each kind of phase [s].
_SPACING = {"rail": 0.02, "boost": 0.02, "coast": 0.1}
_DESCENT_SPACING = 0.5


def _spacing(phase: str) -> float:
    return _SPACING.get(phase, _DESCENT_SPACING)


def sample_indices(flight: Flight) -> list[int]:
    """Indices of the steps kept for playback, in time order.

    The first and last steps and the step nearest each event are always included;
    otherwise a step is kept once the phase's spacing has passed since the
    last kept one.
    """
    t = flight.t
    must = {0, len(t) - 1}
    for e in flight.events:
        must.add(int(np.argmin(np.abs(t - e.time))))
    kept = [0]
    for i in range(1, len(t)):
        if i in must or t[i] - t[kept[-1]] >= _spacing(flight.phase[i]) - 1e-9:
            kept.append(i)
    return kept


def _round(values: Any, digits: int) -> list[float]:
    return [round(float(v), digits) + 0.0 for v in values]


def _outline(flight: Flight) -> dict[str, Any]:
    """The rocket's side profile, positions from the nose tip [m]."""
    rocket = flight.rocket
    nose: dict[str, Any] | None = None
    bodies: list[dict[str, float | str]] = []
    fins: list[dict[str, float | int]] = []
    brakes: dict[str, float | int] | None = None
    for placed in rocket.parts:
        part = placed.component
        if isinstance(part, Airbrakes):
            brakes = {
                "start": round(placed.position, 4),
                "length": round(part.length, 4),
                "panels": part.panels,
                "panel_width": round(part.panel_width, 4),
                "panel_height": round(part.panel_height, 4),
            }
        elif isinstance(part, NoseCone):
            x = np.linspace(0.0, part.length, 25)
            r = part.shape.radius(x, part.length, part.diameter / 2.0)
            nose = {
                "shape": part.shape.value,
                "length": round(part.length, 4),
                "diameter": round(part.diameter, 4),
                "profile": [
                    [round(float(a), 4), round(float(b), 4)] for a, b in zip(x, r, strict=True)
                ],
            }
        elif isinstance(part, BodyTube):
            bodies.append(
                {
                    "name": part.name,
                    "start": round(placed.position, 4),
                    "length": round(part.length, 4),
                    "diameter": round(part.diameter, 4),
                }
            )
        elif isinstance(part, FinSet):
            fins.append(
                {
                    "count": part.count,
                    "start": round(placed.position, 4),
                    "root_chord": round(part.root_chord, 4),
                    "tip_chord": round(part.tip_chord, 4),
                    "span": round(part.span, 4),
                    "sweep": round(part.sweep, 4),
                }
            )
    aero = Aerodynamics(rocket)
    cp = aero.center_of_pressure()
    cg = rocket.mass_properties(0.0).cg
    return {
        "length": round(rocket.length, 4),
        "diameter": round(rocket.diameter, 4),
        "nose": nose,
        "bodies": bodies,
        "fins": fins,
        "airbrakes": brakes,
        "cg_liftoff": round(cg, 4),
        "cp": round(cp, 4),
        "margin_liftoff": round((cp - cg) / aero.reference_diameter, 3),
    }


def _phases(flight: Flight, kept: list[int]) -> list[dict[str, Any]]:
    """Run-length phases over the kept samples, as ``{name, start, end}`` times.

    A boundary sits on the event that caused it (burnout, a canopy opening)
    when the last sample of the old phase is that event's instant.
    """
    t = flight.t
    event_times = np.array([e.time for e in flight.events])
    runs: list[dict[str, Any]] = []
    previous = kept[0]
    for i in kept:
        name = flight.phase[i]
        now = round(float(t[i]), 4)
        if runs and runs[-1]["name"] == name:
            runs[-1]["end"] = now
        else:
            if runs:
                at_event = bool(np.any(np.abs(event_times - t[previous]) < 1e-9))
                boundary = round(float(t[previous]), 4) if at_event else now
                runs[-1]["end"] = boundary
                now = boundary
            runs.append({"name": name, "start": now, "end": now})
        previous = i
    return runs


def _finite(values: Any, digits: int) -> list[float | None]:
    return [None if not math.isfinite(float(v)) else round(float(v), digits) + 0.0 for v in values]


def _airbrakes(flight: Flight, passive: Flight | None) -> dict[str, Any] | None:
    """The flight computer's side of an airbrake flight."""
    log = flight.airbrake_log
    brakes = flight.rocket.airbrakes
    if log is None or flight.target_apogee is None or brakes is None:
        return None
    cols = log.arrays()
    return {
        "target": round(flight.target_apogee, 2),
        "error": round(flight.apogee - flight.target_apogee, 2),
        "passive_apogee": None if passive is None else round(passive.apogee, 2),
        "max_mach": brakes.max_mach,
        "travel_time": brakes.travel_time,
        "log": {
            "t": _round(cols["t"], 3),
            "altitude": _round(cols["altitude"], 2),
            "velocity": _round(cols["velocity"], 2),
            "predicted_apogee": _finite(cols["predicted_apogee"], 1),
            "command": _round(cols["command"], 4),
            "deployment": _round(cols["deployment"], 4),
            "drag_factor": _round(cols["drag_factor"], 4),
        },
    }


def playback(flight: Flight, passive: Flight | None = None) -> dict[str, Any]:
    """The flight as a JSON-ready playback document (see the module docs).

    For an airbrake flight, ``passive`` (the same rocket flown with the brakes
    stowed) adds the apogee it would have reached.
    """
    rocket = flight.rocket
    motor = rocket.motor
    site = flight.site
    traj = flight.trajectory
    kept = sample_indices(flight)
    idx = np.asarray(kept)

    speed, t_speed = flight.max_speed
    accel, t_accel = flight.max_acceleration
    descent = {}
    for e in flight.events:
        if e.name.endswith(" opens"):
            chute = e.name.removesuffix(" opens")
            rate = flight.descent_rate(f"under {chute}")
            if rate is not None:
                descent[chute] = round(rate, 2)
    landing = flight.landing

    pos = traj.position[idx]
    q = traj.attitude[idx]
    return {
        "format": FORMAT,
        "version": VERSION,
        "generator": f"apogee {__version__}",
        "rocket": {
            "name": rocket.name,
            "description": rocket.description,
            "motor": None
            if motor is None
            else {
                "manufacturer": motor.manufacturer,
                "designation": motor.designation,
                "total_impulse": round(motor.total_impulse, 1),
                "burn_time": round(motor.burn_time, 3),
            },
            "liftoff_mass": round(rocket.mass_properties(0.0).mass, 3),
            "outline": _outline(flight),
        },
        "site": {
            "rail_length": site.rail_length,
            "tilt": site.tilt,
            "heading": site.heading,
            "elevation": site.altitude,
            "wind": {"speed": site.wind.speed, "direction": site.wind.direction},
        },
        "summary": {
            "apogee": round(flight.apogee, 2),
            "time_to_apogee": round(flight.time_to_apogee, 3),
            "max_speed": round(speed, 2),
            "max_speed_time": round(t_speed, 3),
            "max_mach": round(flight.max_mach, 3),
            "max_acceleration_g": round(accel / G0, 2),
            "max_acceleration_time": round(t_accel, 3),
            "rail_exit_speed": round(flight.rail_exit_speed, 2),
            "descent_rates": descent,
            "landing_distance": round(flight.landing_distance, 1),
            "landing_bearing": round(flight.landing_bearing, 1),
            "landing_speed": None if landing is None else round(-float(landing.velocity[2]), 2),
            "flight_time": round(flight.flight_time, 3),
        },
        "events": [
            {
                "name": e.name,
                "time": round(e.time, 4),
                "altitude": round(e.altitude, 2),
                "speed": round(e.speed, 2),
                "note": e.note,
            }
            for e in flight.events
        ],
        "phases": _phases(flight, kept),
        "warnings": [*rocket.warnings, *flight.warnings],
        "series": {
            "t": _round(traj.t[idx], 4),
            "east": _round(pos[:, 0], 2),
            "north": _round(pos[:, 1], 2),
            "up": _round(pos[:, 2], 2),
            "speed": _round(traj.speed[idx], 2),
            "mach": _round(flight.mach[idx], 4),
            "acceleration_g": _round(np.linalg.norm(flight.acceleration[idx], axis=1) / G0, 3),
            "pitch": _round(traj.pitch_from_vertical[idx], 2),
            "attitude": [_round(row, 5) for row in q],
            **(
                {"airbrakes": _round(flight.brake_deployment[idx], 4)}
                if flight.brake_deployment is not None
                else {}
            ),
        },
        "airbrakes": _airbrakes(flight, passive),
    }


def write_playback(flight: Flight, path: str | Path, passive: Flight | None = None) -> int:
    """Write :func:`playback` to ``path`` as compact JSON; returns the sample count."""
    doc = playback(flight, passive)
    for key, column in doc["series"].items():
        if key != "attitude" and not all(math.isfinite(v) for v in column):
            raise ValueError(f"non-finite value in the {key!r} series; the flight did not solve")
    Path(path).write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
    return len(doc["series"]["t"])
