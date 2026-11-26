"""A whole rocket: components placed along the axis, plus a motor.

Rockets are described in YAML. External parts (a nose cone, then body tubes)
are stacked nose to tail in the order written; internal parts and fins go in a
body tube's ``contents`` and are placed relative to it::

    name: Kestrel
    motor:
      file: motors/Cesaroni_M1670.eng   # relative to this YAML file
      overhang: 15mm
    components:
      - nose_cone: {shape: von_karman, length: 450mm, diameter: 102mm,
                    thickness: 3mm, material: fiberglass}
      - body_tube:
          length: 1.2m
          thickness: 2mm
          material: fiberglass
          contents:
            - fins: {count: 4, root_chord: 250mm, tip_chord: 80mm, span: 110mm,
                     sweep: 150mm, thickness: 4mm, material: plywood}

Every length and mass may carry a unit (``mm``, ``in``, ``g``, ``oz``...);
bare numbers are SI. A ``mass`` on any part overrides the computed value, for
parts you have weighed. See ``examples/rocket.yaml`` and ``docs/rocket.md``.

Positions are measured aft from the nose tip. A child's ``position`` is the
distance from its parent's front end to its own front end; ``aft_offset`` is
the distance from the parent's aft end to its own aft end instead (fins and
inner tubes default to ``aft_offset: 0``, flush with the tail).
"""

from __future__ import annotations

import difflib
import math
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from apogee.avionics import FlightComputerSettings
from apogee.components import (
    Airbrakes,
    BodyTube,
    Component,
    FinSet,
    InnerTube,
    MassComponent,
    MassProperties,
    NoseCone,
    NoseShape,
    Parachute,
    Ring,
)
from apogee.motor import Motor, MotorFileError, load_motor
from apogee.units import UnitError, parse_density, parse_length, parse_mass

__all__ = [
    "FINISHES",
    "MATERIALS",
    "PlacedComponent",
    "Rocket",
    "RocketFileError",
    "load_rocket",
]

#: Typical densities of common rocketry materials [kg/m³]. Use ``density:``
#: in the YAML for anything else, or ``mass:`` for parts you have weighed.
MATERIALS: dict[str, float] = {
    "cardboard": 680.0,
    "kraft phenolic": 950.0,
    "blue tube": 1300.0,
    "fiberglass": 1850.0,
    "carbon fiber": 1780.0,
    "balsa": 170.0,
    "basswood": 500.0,
    "plywood": 630.0,
    "polystyrene": 1050.0,
    "abs": 1050.0,
    "pla": 1250.0,
    "pvc": 1390.0,
    "aluminum": 2700.0,
}

_MATERIAL_ALIASES = {
    "fibreglass": "fiberglass",
    "g10": "fiberglass",
    "g12": "fiberglass",
    "carbon fibre": "carbon fiber",
    "birch plywood": "plywood",
    "aluminium": "aluminum",
}


#: Surface roughness height of common finishes [m], as used by OpenRocket
#: (Niskanen 2009). Set with ``finish:`` at the top of the YAML,
#: or give a length such as ``finish: 35um``.
FINISHES: dict[str, float] = {
    "polished": 2e-6,
    "smooth paint": 20e-6,
    "regular paint": 60e-6,
    "unfinished": 150e-6,
    "rough": 500e-6,
}

_DEFAULT_FINISH = "regular paint"


class RocketFileError(ValueError):
    """A rocket file has a problem; the message says where and how to fix it."""


@dataclass(frozen=True, slots=True)
class PlacedComponent:
    """A component at a position on the rocket.

    Attributes:
        component: The part.
        position: Distance from the nose tip to the part's front end [m].
        parent: Name of the body tube it sits in, or ``None`` if external.
    """

    component: Component
    position: float
    parent: str | None = None

    @property
    def aft(self) -> float:
        """Distance from the nose tip to the part's aft end [m]."""
        return self.position + self.component.length

    def mass_properties(self) -> MassProperties:
        """Mass properties with the CG measured from the nose tip."""
        return self.component.mass_properties().shifted(self.position)


@dataclass(frozen=True)
class Rocket:
    """A single-stage rocket with its motor.

    Attributes:
        name: Display name.
        description: One line about the design.
        parts: Every component, placed, in file order.
        motor: The motor, or ``None`` for a bare airframe.
        motor_position: Nose tip to the motor's front end [m].
        warnings: Non-fatal issues found while loading.
        finish: Name of the surface finish, for display.
        roughness: Surface roughness height [m], for skin-friction drag.
        dry_mass_scale: Multiplier on the airframe's mass and inertia (CG kept),
            for Monte Carlo scatter; 1 for the rocket as built.
        drag_scale: Multiplier on the zero-lift drag coefficient; 1 as modelled.
    """

    name: str
    description: str
    parts: tuple[PlacedComponent, ...]
    motor: Motor | None = None
    motor_position: float = 0.0
    warnings: tuple[str, ...] = ()
    finish: str = _DEFAULT_FINISH
    roughness: float = FINISHES[_DEFAULT_FINISH]
    dry_mass_scale: float = 1.0
    drag_scale: float = 1.0
    flight_computer: FlightComputerSettings | None = None

    @property
    def length(self) -> float:
        """Nose tip to the aft end of the last body tube [m]."""
        return max(p.aft for p in self.parts if p.parent is None)

    @property
    def diameter(self) -> float:
        """Largest airframe diameter, the aerodynamic reference [m]."""
        return max(
            p.component.diameter for p in self.parts if isinstance(p.component, NoseCone | BodyTube)
        )

    @property
    def reference_area(self) -> float:
        """Frontal area of the largest airframe section [m²]."""
        return 0.25 * math.pi * self.diameter**2

    @property
    def parachutes(self) -> list[Parachute]:
        """The recovery parachutes, in file order."""
        return [p.component for p in self.parts if isinstance(p.component, Parachute)]

    @property
    def airbrakes(self) -> Airbrakes | None:
        """The airbrake module, if the rocket has one."""
        found = [p.component for p in self.parts if isinstance(p.component, Airbrakes)]
        return found[0] if found else None

    @property
    def airbrake_position(self) -> float | None:
        """Nose tip to the airbrake module's front end [m], or ``None``."""
        found = [p.position for p in self.parts if isinstance(p.component, Airbrakes)]
        return found[0] if found else None

    @property
    def target_apogee(self) -> float | None:
        """Apogee the flight computer aims for [m], if it has airbrakes and a target."""
        if self.airbrakes is None or self.flight_computer is None:
            return None
        return self.flight_computer.target_apogee

    def with_target(self, target_apogee: float | None) -> Rocket:
        """A copy whose flight computer aims for ``target_apogee`` (``None`` stows the brakes).

        >>> from apogee.cli import example_path
        >>> ab = load_rocket(example_path("kestrel-airbrakes.yaml"))
        >>> ab.with_target(4200.0).target_apogee
        4200.0
        """
        if target_apogee is not None and self.airbrakes is None:
            raise ValueError(f"{self.name} has no airbrakes to aim for an apogee with")
        settings = self.flight_computer or FlightComputerSettings()
        return replace(self, flight_computer=replace(settings, target_apogee=target_apogee))

    def dry_mass_properties(self) -> MassProperties:
        """The rocket without its motor."""
        dry = MassProperties.combine([p.mass_properties() for p in self.parts])
        return dry if self.dry_mass_scale == 1.0 else dry.scaled_to(dry.mass * self.dry_mass_scale)

    def motor_mass_properties(self, t: float = 0.0) -> MassProperties | None:
        """The motor alone at time ``t`` after ignition, CG from the nose tip."""
        if self.motor is None:
            return None
        ixx, iyy = self.motor.inertia_at(t)
        cg = self.motor_position + 0.5 * self.motor.length
        return MassProperties(self.motor.mass_at(t), cg, ixx, iyy)

    def mass_properties(self, t: float = 0.0) -> MassProperties:
        """The complete rocket at time ``t`` [s] after ignition.

        Before ignition (or for ``t = 0``) the motor is fully loaded; after
        burnout only its casing remains.
        """
        dry = self.dry_mass_properties()
        motor = self.motor_mass_properties(t)
        return dry if motor is None else MassProperties.combine([dry, motor])

    @property
    def burnout_time(self) -> float:
        """Motor burn time [s] (0 without a motor)."""
        return self.motor.burn_time if self.motor else 0.0

    def perturbed(
        self,
        *,
        dry_mass: float = 1.0,
        thrust: float = 1.0,
        drag: float = 1.0,
        parachute_cd: float = 1.0,
        airbrake_cd: float = 1.0,
    ) -> Rocket:
        """A copy with scaled properties, as built rockets differ from the drawing.

        Each argument is a multiplier (1 leaves it unchanged): ``dry_mass`` on
        the airframe's mass and inertia, ``thrust`` on the motor's thrust curve
        (and so its total impulse, at the same burn time and propellant mass),
        ``drag`` on the zero-lift drag coefficient and ``parachute_cd`` on every
        canopy's drag coefficient and ``airbrake_cd`` on the airbrake panels'.

        >>> from apogee.cli import example_path
        >>> kestrel = load_rocket(example_path("rocket.yaml"))
        >>> heavy = kestrel.perturbed(dry_mass=1.1)
        >>> round(heavy.dry_mass_properties().mass / kestrel.dry_mass_properties().mass, 6)
        1.1
        """
        for label, k in (
            ("dry_mass", dry_mass),
            ("thrust", thrust),
            ("drag", drag),
            ("parachute_cd", parachute_cd),
            ("airbrake_cd", airbrake_cd),
        ):
            if not k > 0.0:
                raise ValueError(f"{label} multiplier must be positive, got {k}")
        motor = self.motor
        if motor is not None and thrust != 1.0:
            motor = replace(motor, thrust=motor.thrust * thrust)
        parts = self.parts
        if parachute_cd != 1.0:
            parts = tuple(
                replace(p, component=replace(p.component, cd=p.component.cd * parachute_cd))
                if isinstance(p.component, Parachute)
                else p
                for p in parts
            )
        if airbrake_cd != 1.0:
            parts = tuple(
                replace(p, component=replace(p.component, cd=p.component.cd * airbrake_cd))
                if isinstance(p.component, Airbrakes)
                else p
                for p in parts
            )
        return replace(
            self,
            parts=parts,
            motor=motor,
            dry_mass_scale=self.dry_mass_scale * dry_mass,
            drag_scale=self.drag_scale * drag,
        )


# --- Loading ----------------------------------------------------------------


class _Spec:
    """A YAML mapping being read, which reports unknown and missing keys."""

    def __init__(self, data: object, where: str, allowed: set[str]) -> None:
        if not isinstance(data, dict):
            raise RocketFileError(f"{where}: expected a mapping of settings, got {data!r}")
        self.data: dict[str, Any] = data
        self.where = where
        for key in data:
            if key not in allowed:
                hint = difflib.get_close_matches(str(key), sorted(allowed), n=1)
                tip = f"; did you mean {hint[0]!r}?" if hint else f"; allowed: {sorted(allowed)}"
                raise RocketFileError(f"{where}: unknown setting {key!r}{tip}")

    def has(self, key: str) -> bool:
        return key in self.data and self.data[key] is not None

    def _get(self, key: str, parse: Callable[[object], float], default: float | None) -> float:
        if not self.has(key):
            if default is None:
                raise RocketFileError(f"{self.where}: missing required setting {key!r}")
            return default
        try:
            return parse(self.data[key])
        except UnitError as exc:
            raise RocketFileError(f"{self.where}.{key}: {exc}") from None

    def length(self, key: str, default: float | None = None, *, zero: bool = False) -> float:
        return self._get(key, lambda v: parse_length(v, allow_zero=zero), default)

    def mass(self, key: str, default: float | None = None) -> float:
        return self._get(key, parse_mass, default)

    def number(self, key: str, default: float | None = None) -> float:
        def parse(v: object) -> float:
            if isinstance(v, bool) or not isinstance(v, int | float) or v <= 0:
                raise UnitError(f"expected a positive number, got {v!r}")
            return float(v)

        return self._get(key, parse, default)

    def text(self, key: str, default: str) -> str:
        return str(self.data.get(key) or default)

    def mass_override(self) -> float | None:
        return self.mass("mass") if self.has("mass") else None

    def density(self) -> float:
        if self.has("density"):
            if self.has("material"):
                raise RocketFileError(f"{self.where}: give 'material' or 'density', not both")
            return self._get("density", parse_density, None)
        material = str(self.data.get("material", "")).strip().lower().replace("_", " ")
        material = _MATERIAL_ALIASES.get(material, material)
        if not material:
            raise RocketFileError(
                f"{self.where}: missing 'material' (one of {', '.join(MATERIALS)}) "
                "or 'density' (e.g. 1200kg/m3)"
            )
        if material not in MATERIALS:
            hint = difflib.get_close_matches(material, list(MATERIALS), n=1)
            tip = f"did you mean {hint[0]!r}? " if hint else ""
            raise RocketFileError(
                f"{self.where}: unknown material {material!r}; {tip}known materials: "
                f"{', '.join(MATERIALS)}, or give 'density' instead"
            )
        return MATERIALS[material]


_COMMON = {"name", "mass"}
_PLACE = {"position", "aft_offset"}


def _nose(spec_data: object, where: str) -> NoseCone:
    s = _Spec(
        spec_data,
        where,
        _COMMON | {"shape", "length", "diameter", "thickness", "material", "density"},
    )
    shape_name = s.text("shape", "ogive").strip().lower().replace(" ", "_").replace("-", "_")
    try:
        shape = NoseShape(shape_name)
    except ValueError:
        shapes = ", ".join(sh.value for sh in NoseShape)
        raise RocketFileError(
            f"{where}.shape: unknown shape {shape_name!r}; use {shapes}"
        ) from None
    diameter = s.length("diameter")
    return NoseCone(
        name=s.text("name", "Nose cone"),
        length=s.length("length"),
        diameter=diameter,
        thickness=s.length("thickness"),  # a value >= the base radius gives a solid nose
        density=s.density(),
        shape=shape,
        mass_override=s.mass_override(),
    )


def _body(spec_data: object, where: str, diameter: float | None) -> tuple[BodyTube, list[Any]]:
    s = _Spec(
        spec_data,
        where,
        _COMMON | {"length", "diameter", "thickness", "material", "density", "contents"},
    )
    d = s.length("diameter", diameter)
    tube = BodyTube(
        name=s.text("name", "Body tube"),
        length=s.length("length"),
        diameter=d,
        thickness=s.length("thickness"),
        density=s.density(),
        mass_override=s.mass_override(),
    )
    if tube.inner_diameter <= 0.0:
        raise RocketFileError(f"{where}: wall thickness is at least half the diameter")
    contents = s.data.get("contents") or []
    if not isinstance(contents, list):
        raise RocketFileError(f"{where}.contents: expected a list of parts")
    return tube, contents


def _child(kind: str, data: object, where: str, parent: BodyTube) -> tuple[Component, _Spec]:
    """Build one part that lives on or inside ``parent``."""
    inner = parent.inner_diameter
    if kind == "fins":
        s = _Spec(
            data,
            where,
            _COMMON
            | _PLACE
            | {"count", "root_chord", "tip_chord", "span", "sweep", "thickness"}
            | {"material", "density"},
        )
        count = int(s.number("count", 3))
        if not 2 <= count <= 8:
            raise RocketFileError(f"{where}.count: use 2 to 8 fins, got {count}")
        part: Component = FinSet(
            name=s.text("name", "Fins"),
            length=s.length("root_chord"),
            count=count,
            tip_chord=s.length("tip_chord", 0.0, zero=True),
            span=s.length("span"),
            sweep=s.length("sweep", 0.0, zero=True),
            thickness=s.length("thickness"),
            density=s.density(),
            body_radius=0.5 * parent.diameter,
            mass_override=s.mass_override(),
        )
    elif kind == "inner_tube":
        s = _Spec(
            data,
            where,
            _COMMON
            | _PLACE
            | {"length", "diameter", "thickness", "material", "density", "motor_mount"},
        )
        part = InnerTube(
            name=s.text("name", "Inner tube"),
            length=s.length("length"),
            diameter=s.length("diameter"),
            thickness=s.length("thickness"),
            density=s.density(),
            motor_mount=bool(s.data.get("motor_mount", False)),
            mass_override=s.mass_override(),
        )
    elif kind in ("ring", "bulkhead"):
        s = _Spec(
            data,
            where,
            _COMMON | _PLACE | {"thickness", "diameter", "inner_diameter", "material", "density"},
        )
        hole = 0.0 if kind == "bulkhead" else s.length("inner_diameter")
        part = Ring(
            name=s.text("name", kind.capitalize()),
            length=s.length("thickness"),
            diameter=s.length("diameter", inner),
            inner_diameter=hole,
            density=s.density(),
            mass_override=s.mass_override(),
        )
    elif kind == "parachute":
        s = _Spec(
            data,
            where,
            _COMMON | _PLACE | {"diameter", "cd", "packed_length", "packed_diameter", "deploy"},
        )
        packed_d = s.length("packed_diameter", inner)
        deploy = s.data.get("deploy", "apogee")
        if isinstance(deploy, str) and deploy.strip().lower() == "apogee":
            altitude = None
        else:
            try:
                altitude = parse_length(deploy)
            except UnitError as exc:
                raise RocketFileError(
                    f"{where}.deploy: use 'apogee' or an altitude such as 300m ({exc})"
                ) from None
        part = Parachute(
            name=s.text("name", "Parachute"),
            length=s.length("packed_length", packed_d),
            mass=s.mass("mass"),
            packed_diameter=packed_d,
            diameter=s.length("diameter"),
            cd=s.number("cd", 0.8),
            deploy_altitude=altitude,
        )
    elif kind == "airbrakes":
        s = _Spec(
            data,
            where,
            _COMMON
            | _PLACE
            | {"length", "panels", "panel_width", "panel_height", "cd", "travel_time"}
            | {"max_mach"},
        )
        try:
            part = Airbrakes(
                name=s.text("name", "Airbrakes"),
                length=s.length("length"),
                mass=s.mass("mass"),
                diameter=inner,
                panels=int(s.number("panels", 3)),
                panel_width=s.length("panel_width"),
                panel_height=s.length("panel_height"),
                cd=s.number("cd", 1.2),
                travel_time=_seconds(s, "travel_time", 0.5),
                max_mach=s.number("max_mach", 0.8),
            )
        except ValueError as exc:
            if isinstance(exc, RocketFileError):
                raise
            raise RocketFileError(f"{where}: {exc}") from None
    elif kind == "mass":
        s = _Spec(data, where, _COMMON | _PLACE | {"length", "diameter"})
        part = MassComponent(
            name=s.text("name", "Mass"),
            length=s.length("length", 0.0, zero=True),
            diameter=s.length("diameter", 0.0, zero=True),
            mass=s.mass("mass"),
        )
    else:
        kinds = "fins, inner_tube, ring, bulkhead, parachute, mass, airbrakes"
        raise RocketFileError(f"{where}: unknown part {kind!r}; body tubes can hold {kinds}")
    return part, s


def _single_key(item: object, where: str, kinds: str) -> tuple[str, object]:
    if not isinstance(item, dict) or len(item) != 1:
        raise RocketFileError(f"{where}: each entry is one part, e.g. '- {kinds.split(',')[0]}:'")
    ((kind, data),) = item.items()
    return str(kind), data


def rocket_from_dict(data: object, base_dir: Path | None = None) -> Rocket:
    """Build a :class:`Rocket` from parsed YAML data.

    Args:
        data: The YAML document as Python objects.
        base_dir: Directory that relative motor paths are resolved against.

    Raises:
        RocketFileError: Describing the first problem found and how to fix it.
    """
    top = _Spec(
        data,
        "rocket",
        {"name", "description", "finish", "motor", "components", "flight_computer"},
    )
    components = top.data.get("components")
    if not isinstance(components, list) or not components:
        raise RocketFileError("rocket: add a 'components' list, starting with a nose_cone")

    parts: list[PlacedComponent] = []
    warnings: list[str] = []
    x = 0.0
    diameter: float | None = None
    for i, item in enumerate(components):
        where = f"components[{i}]"
        kind, body = _single_key(item, where, "nose_cone, body_tube")
        where = f"{where}.{kind}"
        if kind == "nose_cone":
            if i != 0:
                raise RocketFileError(f"{where}: the nose cone must be the first component")
            nose = _nose(body, where)
            parts.append(PlacedComponent(nose, 0.0))
            x, diameter = nose.length, nose.diameter
        elif kind == "body_tube":
            if i == 0:
                raise RocketFileError(f"{where}: start the components list with a nose_cone")
            tube, contents = _body(body, where, diameter)
            parts.append(PlacedComponent(tube, x))
            for j, child_item in enumerate(contents):
                cwhere = f"{where}.contents[{j}]"
                ckind, cdata = _single_key(child_item, cwhere, "fins, inner_tube")
                cwhere = f"{cwhere}.{ckind}"
                part, s = _child(ckind, cdata, cwhere, tube)
                parts.append(PlacedComponent(part, _place(s, part, tube, x, ckind), tube.name))
            x += tube.length
            diameter = tube.diameter
        else:
            raise RocketFileError(
                f"{where}: unknown component {kind!r}; the outside of the rocket is a "
                "nose_cone followed by body_tube sections (fins and internal parts go in "
                "a body tube's 'contents')"
            )

    motor, motor_pos = _motor(top, parts, base_dir, warnings)
    finish, roughness = _finish(top)
    computer = _flight_computer(top)
    brakes = [p for p in parts if isinstance(p.component, Airbrakes)]
    if len(brakes) > 1:
        raise RocketFileError("rocket: only one airbrake module is supported")
    if computer is not None and computer.target_apogee is not None and not brakes:
        warnings.append("flight_computer has a target apogee but the rocket has no airbrakes")
    return Rocket(
        name=top.text("name", "Unnamed rocket"),
        description=top.text("description", ""),
        parts=tuple(parts),
        motor=motor,
        motor_position=motor_pos,
        warnings=tuple(warnings),
        finish=finish,
        roughness=roughness,
        flight_computer=computer,
    )


def _seconds(s: _Spec, key: str, default: float) -> float:
    """A duration such as ``0.5`` or ``0.5s`` or ``500ms``."""
    if not s.has(key):
        return default
    raw = s.data[key]
    text = str(raw).strip().lower()
    scale = 1.0
    if text.endswith("ms"):
        text, scale = text[:-2], 1e-3
    elif text.endswith("s"):
        text = text[:-1]
    try:
        value = float(text) * scale
    except ValueError:
        value = math.nan
    if not (value >= 0.0 and math.isfinite(value)) or isinstance(raw, bool):
        raise RocketFileError(f"{s.where}.{key}: expected a time such as 0.5s, got {raw!r}")
    return value


def _rate(s: _Spec, key: str, default: float) -> float:
    """A frequency such as ``50`` or ``50Hz``."""
    if not s.has(key):
        return default
    raw = s.data[key]
    text = str(raw).strip().lower().removesuffix("hz").strip()
    try:
        value = float(text)
    except ValueError:
        value = math.nan
    if not (value > 0.0 and math.isfinite(value)) or isinstance(raw, bool):
        raise RocketFileError(f"{s.where}.{key}: expected a rate such as 50Hz, got {raw!r}")
    return value


def _flight_computer(top: _Spec) -> FlightComputerSettings | None:
    """The optional ``flight_computer:`` section that drives the airbrakes."""
    if not top.has("flight_computer"):
        return None
    s = _Spec(
        top.data["flight_computer"],
        "flight_computer",
        {
            "target_apogee",
            "rate",
            "control_rate",
            "barometer_noise",
            "accelerometer_noise",
            "jerk_noise",
            "lockout",
        },
    )
    target = s.length("target_apogee") if s.has("target_apogee") else None
    accel = 0.3
    if s.has("accelerometer_noise"):
        raw = s.data["accelerometer_noise"]
        text = str(raw).strip().lower()
        try:
            if text.endswith("g"):
                accel = float(text[:-1]) * 9.80665
            else:
                accel = float(text.removesuffix("m/s2").removesuffix("m/s²"))
        except ValueError:
            accel = math.nan
        if not (accel >= 0.0 and math.isfinite(accel)):
            raise RocketFileError(
                "flight_computer.accelerometer_noise: expected e.g. 0.3 (m/s²) or 0.03g, "
                f"got {raw!r}"
            )
    try:
        return FlightComputerSettings(
            target_apogee=target,
            rate=_rate(s, "rate", 50.0),
            control_rate=_rate(s, "control_rate", 10.0),
            barometer_noise=s.length("barometer_noise", 1.5, zero=True),
            accelerometer_noise=accel,
            jerk_noise=s.number("jerk_noise", 20.0),
            lockout=_seconds(s, "lockout", 1.0),
        )
    except ValueError as exc:
        raise RocketFileError(f"flight_computer: {exc}") from None


def _finish(top: _Spec) -> tuple[str, float]:
    """Surface finish: a named finish or a roughness height such as ``35um``."""
    raw = top.data.get("finish")
    if raw is None:
        return _DEFAULT_FINISH, FINISHES[_DEFAULT_FINISH]
    name = str(raw).strip().lower().replace("_", " ").replace("-", " ")
    if name in FINISHES:
        return name, FINISHES[name]
    text = str(raw).strip().lower()
    for micro in ("µm", "um"):
        if text.endswith(micro):
            try:
                value = float(text[: -len(micro)]) * 1e-6
            except ValueError:
                break
            if value >= 0.0:
                return f"{value * 1e6:g} µm roughness", value
    options = ", ".join(n.replace(" ", "_") for n in FINISHES)
    raise RocketFileError(
        f"finish: unknown finish {raw!r}; use one of {options}, or a roughness height like 35um"
    )


def _place(s: _Spec, part: Component, parent: BodyTube, parent_x: float, kind: str) -> float:
    """Position of a child's front end from the nose tip."""
    if s.has("position") and s.has("aft_offset"):
        raise RocketFileError(f"{s.where}: give 'position' or 'aft_offset', not both")
    flush_aft = kind in ("fins", "inner_tube")
    if s.has("aft_offset") or (flush_aft and not s.has("position")):
        offset = s.length("aft_offset", 0.0, zero=True)
        local = parent.length - offset - part.length
    else:
        local = s.length("position", 0.0, zero=True)
    tolerance = 1e-6
    if local < -tolerance or local + part.length > parent.length + tolerance:
        raise RocketFileError(
            f"{s.where}: part runs outside {parent.name!r} "
            f"(it spans {local * 1000:.0f} to {(local + part.length) * 1000:.0f} mm "
            f"of a {parent.length * 1000:.0f} mm tube); check its position and length"
        )
    return parent_x + local


def _motor(
    top: _Spec, parts: list[PlacedComponent], base_dir: Path | None, warnings: list[str]
) -> tuple[Motor | None, float]:
    if not top.has("motor"):
        return None, 0.0
    s = _Spec(top.data["motor"], "motor", {"file", "designation", "overhang"})
    if not s.has("file"):
        raise RocketFileError("motor: missing 'file' (a RASP .eng thrust curve)")
    path = Path(str(s.data["file"]))
    if not path.is_absolute() and base_dir is not None:
        path = base_dir / path
    try:
        motor = load_motor(path, s.data.get("designation"))
    except (FileNotFoundError, MotorFileError) as exc:
        raise RocketFileError(f"motor: {exc}") from None
    warnings.extend(motor.warnings)

    mounts = [p for p in parts if isinstance(p.component, InnerTube) and p.component.motor_mount]
    if not mounts:
        mounts = [p for p in parts if isinstance(p.component, InnerTube)]
        if len(mounts) > 1:
            raise RocketFileError("motor: several inner tubes; mark one with 'motor_mount: true'")
    overhang = s.length("overhang", 0.0, zero=True)
    if mounts:
        mount = mounts[0]
        tube = mount.component
        assert isinstance(tube, InnerTube)
        if motor.diameter > tube.inner_diameter + 5e-4:
            raise RocketFileError(
                f"motor: {motor.designation} is {motor.diameter * 1000:.0f} mm across but "
                f"{tube.name!r} is only {tube.inner_diameter * 1000:.1f} mm inside"
            )
        aft = mount.aft
    else:
        aft = max(p.aft for p in parts if p.parent is None)
        warnings.append("no motor mount found; the motor sits flush with the tail")
    return motor, aft + overhang - motor.length


def load_rocket(path: str | Path) -> Rocket:
    """Load a rocket from a YAML file.

    Raises:
        FileNotFoundError: If the file does not exist.
        RocketFileError: If the file is not valid YAML or describes an invalid
            rocket; the message names the setting and how to fix it.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise FileNotFoundError(f"rocket file not found: {path}") from None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RocketFileError(f"{path.name} is not valid YAML: {exc}") from None
    try:
        return rocket_from_dict(data, path.parent)
    except RocketFileError as exc:
        raise RocketFileError(f"{path.name}: {exc}") from None
