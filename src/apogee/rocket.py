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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from apogee.components import (
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

__all__ = ["MATERIALS", "PlacedComponent", "Rocket", "RocketFileError", "load_rocket"]

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
    """

    name: str
    description: str
    parts: tuple[PlacedComponent, ...]
    motor: Motor | None = None
    motor_position: float = 0.0
    warnings: tuple[str, ...] = ()

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

    def dry_mass_properties(self) -> MassProperties:
        """The rocket without its motor."""
        return MassProperties.combine([p.mass_properties() for p in self.parts])

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
    elif kind == "mass":
        s = _Spec(data, where, _COMMON | _PLACE | {"length", "diameter"})
        part = MassComponent(
            name=s.text("name", "Mass"),
            length=s.length("length", 0.0, zero=True),
            diameter=s.length("diameter", 0.0, zero=True),
            mass=s.mass("mass"),
        )
    else:
        kinds = "fins, inner_tube, ring, bulkhead, parachute, mass"
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
    top = _Spec(data, "rocket", {"name", "description", "motor", "components"})
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
    return Rocket(
        name=top.text("name", "Unnamed rocket"),
        description=top.text("description", ""),
        parts=tuple(parts),
        motor=motor,
        motor_position=motor_pos,
        warnings=tuple(warnings),
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
