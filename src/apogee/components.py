"""Rocket parts and their mass properties.

Every component knows its own shape and material and can report a
:class:`MassProperties`: mass, centre of gravity (CG) and moments of inertia
about that CG. Positions here are *local*: measured aft from the component's
own forward end. :mod:`apogee.rocket` places components along the rocket and
combines them.

Axes follow the usual rocketry convention: ``x`` runs along the rocket's axis
of symmetry, and every part is symmetric about it, so the inertia tensor is
``diag(Ixx, Iyy, Iyy)``: one axial (roll) moment and one transverse
(pitch/yaw) moment.

Method:

* Tubes, rings and packed parachutes are hollow cylinders, using the
  closed-form moments ``Ixx = m (R² + r²) / 2`` and
  ``Iyy = m (3 (R² + r²) + L²) / 12``.
* Nose cones are thin-walled shells of revolution. The radius profile ``r(x)``
  of each shape is integrated numerically in 2,000 annular slices (inner radius
  ``r − t``, wall thickness measured radially), each slice a thin hollow disc.
  A thickness larger than the base radius gives a solid nose.
* Fin sets are flat trapezoidal plates, integrated in span-wise strips. For two
  or more evenly spaced fins the transverse moment is the same about every
  axis, ``Iyy = Σ m (x − x̄)² + ½ Σ m ρ²`` with ``ρ`` the distance from the
  rocket's axis.
* If a ``mass`` is given it replaces the computed mass and the moments are
  scaled with it, so a part you have weighed keeps its geometric distribution.

References: nose profile equations from G. A. Crowell, *The Descriptive
Geometry of Nose Cones* (1996); hollow-cylinder moments from any statics text
(e.g. Meriam & Kraige, *Engineering Mechanics: Dynamics*, Appendix D).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

import numpy as np
from numpy.typing import NDArray

__all__ = [
    "Airbrakes",
    "BodyTube",
    "Component",
    "FinSet",
    "InnerTube",
    "MassComponent",
    "MassProperties",
    "NoseCone",
    "NoseShape",
    "Parachute",
    "Ring",
]

_SLICES = 2000


@dataclass(frozen=True, slots=True)
class MassProperties:
    """Mass, CG and principal moments of an axisymmetric body.

    Attributes:
        mass: Mass [kg].
        cg: Centre of gravity along the axis [m], measured aft from a reference
            (a component's forward end, or the nose tip for a whole rocket).
        ixx: Axial (roll) moment of inertia about the CG [kg·m²].
        iyy: Transverse (pitch and yaw) moment of inertia about the CG [kg·m²].
    """

    mass: float
    cg: float
    ixx: float
    iyy: float

    @property
    def inertia_tensor(self) -> NDArray[np.float64]:
        """The 3×3 inertia tensor about the CG in body axes [kg·m²]."""
        return np.diag([self.ixx, self.iyy, self.iyy])

    def shifted(self, offset: float) -> MassProperties:
        """The same body measured from a reference ``offset`` metres further forward."""
        return MassProperties(self.mass, self.cg + offset, self.ixx, self.iyy)

    def scaled_to(self, mass: float) -> MassProperties:
        """The same geometry with a different total mass."""
        if self.mass == 0.0:
            return MassProperties(mass, self.cg, 0.0, 0.0)
        k = mass / self.mass
        return MassProperties(mass, self.cg, self.ixx * k, self.iyy * k)

    @staticmethod
    def combine(parts: list[MassProperties]) -> MassProperties:
        """Combine bodies on a common axis using the parallel-axis theorem."""
        mass = sum(p.mass for p in parts)
        if mass <= 0.0:
            raise ValueError("cannot combine parts with no mass")
        cg = sum(p.mass * p.cg for p in parts) / mass
        ixx = sum(p.ixx for p in parts)
        iyy = sum(p.iyy + p.mass * (p.cg - cg) ** 2 for p in parts)
        return MassProperties(mass, cg, ixx, iyy)


def hollow_cylinder(mass: float, outer: float, inner: float, length: float) -> MassProperties:
    """Mass properties of a uniform hollow cylinder (radii and length in m)."""
    r2 = outer**2 + inner**2
    return MassProperties(
        mass=mass,
        cg=0.5 * length,
        ixx=0.5 * mass * r2,
        iyy=mass * (3.0 * r2 + length**2) / 12.0,
    )


@dataclass(frozen=True, kw_only=True)
class Component:
    """Base class: a named part with an optional measured mass."""

    name: str
    length: float
    mass_override: float | None = None

    def computed(self) -> MassProperties:
        """Mass properties from geometry and material."""
        raise NotImplementedError

    def mass_properties(self) -> MassProperties:
        """Mass properties, using the measured mass if one was given."""
        props = self.computed()
        if self.mass_override is not None:
            return props.scaled_to(self.mass_override)
        return props


# --- External components ----------------------------------------------------


class NoseShape(Enum):
    """Nose cone profiles (Crowell 1996)."""

    CONICAL = "conical"
    OGIVE = "ogive"  # tangent ogive
    ELLIPSOID = "ellipsoid"
    PARABOLIC = "parabolic"
    VON_KARMAN = "von_karman"  # Haack series, C = 0
    LV_HAACK = "lv_haack"  # Haack series, C = 1/3

    def radius(self, x: NDArray[np.float64], length: float, base: float) -> NDArray[np.float64]:
        """Radius [m] at distance ``x`` aft of the tip for a nose of given size."""
        xi = np.clip(x / length, 0.0, 1.0)
        if self is NoseShape.CONICAL:
            r = base * xi
        elif self is NoseShape.OGIVE:
            rho = (base**2 + length**2) / (2.0 * base)
            r = np.sqrt(rho**2 - (length - xi * length) ** 2) + base - rho
        elif self is NoseShape.ELLIPSOID:
            r = base * np.sqrt(1.0 - (1.0 - xi) ** 2)
        elif self is NoseShape.PARABOLIC:
            r = base * (2.0 * xi - xi**2)
        else:
            c = 0.0 if self is NoseShape.VON_KARMAN else 1.0 / 3.0
            theta = np.arccos(1.0 - 2.0 * xi)
            r = (
                base
                / math.sqrt(math.pi)
                * np.sqrt(theta - 0.5 * np.sin(2.0 * theta) + c * np.sin(theta) ** 3)
            )
        return np.maximum(r, 0.0)


@dataclass(frozen=True, kw_only=True)
class NoseCone(Component):
    """A hollow nose cone of revolution, tip forward.

    Attributes:
        shape: Profile of the cone.
        diameter: Base outer diameter [m].
        thickness: Wall thickness [m], measured radially.
        density: Material density [kg/m³].
    """

    shape: NoseShape
    diameter: float
    thickness: float
    density: float

    def computed(self) -> MassProperties:
        """Integrate the shell in thin annular slices."""
        dx = self.length / _SLICES
        x = np.asarray((np.arange(_SLICES) + 0.5) * dx, dtype=np.float64)
        r_out = self.shape.radius(x, self.length, 0.5 * self.diameter)
        r_in = np.maximum(r_out - self.thickness, 0.0)
        dm = self.density * math.pi * (r_out**2 - r_in**2) * dx
        mass = float(dm.sum())
        cg = float((dm * x).sum() / mass)
        r2 = r_out**2 + r_in**2
        ixx = float((0.5 * dm * r2).sum())
        iyy = float((dm * (0.25 * r2 + (x - cg) ** 2)).sum())
        return MassProperties(mass, cg, ixx, iyy)


@dataclass(frozen=True, kw_only=True)
class BodyTube(Component):
    """A cylindrical airframe section.

    Attributes:
        diameter: Outer diameter [m].
        thickness: Wall thickness [m].
        density: Material density [kg/m³].
    """

    diameter: float
    thickness: float
    density: float

    @property
    def inner_diameter(self) -> float:
        """Inner diameter [m]."""
        return self.diameter - 2.0 * self.thickness

    def computed(self) -> MassProperties:
        """Hollow cylinder."""
        ro, ri = 0.5 * self.diameter, 0.5 * self.inner_diameter
        mass = self.density * math.pi * (ro**2 - ri**2) * self.length
        return hollow_cylinder(mass, ro, ri, self.length)


@dataclass(frozen=True, kw_only=True)
class FinSet(Component):
    """Identical flat trapezoidal fins spaced evenly around a body tube.

    ``length`` is the root chord. Attributes:
        count: Number of fins (2 to 8).
        tip_chord: Chord at the tip [m].
        span: Height from the body surface to the tip [m].
        sweep: How far aft the tip's leading edge sits from the root's [m].
        thickness: Plate thickness [m].
        density: Material density [kg/m³].
        body_radius: Outer radius of the tube the fins are mounted on [m].
    """

    count: int
    tip_chord: float
    span: float
    sweep: float
    thickness: float
    density: float
    body_radius: float

    @property
    def root_chord(self) -> float:
        """Chord at the body surface [m]."""
        return self.length

    @property
    def area(self) -> float:
        """Planform area of one fin [m²]."""
        return 0.5 * (self.root_chord + self.tip_chord) * self.span

    def computed(self) -> MassProperties:
        """Integrate each fin in thin span-wise strips."""
        dy = self.span / _SLICES
        y = np.asarray((np.arange(_SLICES) + 0.5) * dy, dtype=np.float64)
        frac = y / self.span
        chord = self.root_chord + (self.tip_chord - self.root_chord) * frac
        x_le = self.sweep * frac
        dm = self.density * self.thickness * chord * dy  # one fin
        mass_one = float(dm.sum())
        mass = self.count * mass_one
        cg = float((dm * (x_le + 0.5 * chord)).sum() / mass_one)
        rho = self.body_radius + y
        ixx = self.count * float((dm * rho**2).sum())
        # Second moment of each chord-wise strip about the set's CG.
        x_mid = x_le + 0.5 * chord - cg
        axial = self.count * float((dm * (x_mid**2 + chord**2 / 12.0)).sum())
        return MassProperties(mass, cg, ixx, axial + 0.5 * ixx)


# --- Internal components ----------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class InnerTube(Component):
    """A tube inside the airframe, such as a motor mount.

    Attributes:
        diameter: Outer diameter [m].
        thickness: Wall thickness [m].
        density: Material density [kg/m³].
        motor_mount: Whether the motor goes in this tube.
    """

    diameter: float
    thickness: float
    density: float
    motor_mount: bool = False

    @property
    def inner_diameter(self) -> float:
        """Inner diameter [m]."""
        return self.diameter - 2.0 * self.thickness

    def computed(self) -> MassProperties:
        """Hollow cylinder."""
        ro, ri = 0.5 * self.diameter, 0.5 * self.inner_diameter
        mass = self.density * math.pi * (ro**2 - ri**2) * self.length
        return hollow_cylinder(mass, ro, ri, self.length)


@dataclass(frozen=True, kw_only=True)
class Ring(Component):
    """A centering ring or bulkhead (a bulkhead has no hole).

    ``length`` is the ring's thickness. Attributes:
        diameter: Outer diameter [m].
        inner_diameter: Hole diameter [m]; 0 for a bulkhead.
        density: Material density [kg/m³].
    """

    diameter: float
    inner_diameter: float
    density: float

    def computed(self) -> MassProperties:
        """Hollow cylinder (or solid disc)."""
        ro, ri = 0.5 * self.diameter, 0.5 * self.inner_diameter
        mass = self.density * math.pi * (ro**2 - ri**2) * self.length
        return hollow_cylinder(mass, ro, ri, self.length)


@dataclass(frozen=True, kw_only=True)
class Parachute(Component):
    """A packed parachute. Its canopy size and drag matter once it opens.

    ``length`` and ``packed_diameter`` describe the packed bundle, treated as a
    solid cylinder. Attributes:
        mass: Packed mass including shroud lines [kg].
        diameter: Canopy diameter when open [m].
        cd: Drag coefficient referenced to the canopy's flat area.
        deploy_altitude: Altitude to open at [m], or ``None`` for apogee.
    """

    mass: float
    packed_diameter: float
    diameter: float
    cd: float
    deploy_altitude: float | None = None

    @property
    def canopy_area(self) -> float:
        """Reference area of the open canopy [m²]."""
        return 0.25 * math.pi * self.diameter**2

    def computed(self) -> MassProperties:
        """Solid cylinder of the packed bundle."""
        return hollow_cylinder(self.mass, 0.5 * self.packed_diameter, 0.0, self.length)


@dataclass(frozen=True, kw_only=True)
class MassComponent(Component):
    """Anything else with a known mass: avionics, payload, ballast, shock cord.

    Treated as a solid cylinder of the given length and diameter (both may be
    zero for a point mass). Attributes:
        mass: Mass [kg].
        diameter: Diameter [m].
    """

    mass: float
    diameter: float = 0.0

    def computed(self) -> MassProperties:
        """Solid cylinder (or point mass)."""
        return hollow_cylinder(self.mass, 0.5 * self.diameter, 0.0, self.length)


@dataclass(frozen=True, kw_only=True)
class Airbrakes(Component):
    """Deployable drag panels that a flight computer opens to shave off apogee.

    The module is a ring of ``panels`` flat plates that swing or slide out of
    the airframe into the airflow. Fully out, each presents ``panel_width ×
    panel_height`` of frontal area; part-way out the exposed area grows in
    proportion to the extension, so the drag added at deployment ``δ`` (0
    stowed, 1 fully out) is ``½ ρ V² · cd · A_max · δ``.

    For its mass the module is a solid cylinder of the airframe's inner
    diameter (servo, linkage, panels, structure). Attributes:
        mass: The whole module [kg].
        diameter: Diameter used for the module's inertia [m].
        panels: Number of panels.
        panel_width: Width of one panel [m].
        panel_height: How far one panel sticks out of the airframe when fully
            deployed [m].
        cd: Drag coefficient of the panels, on their exposed frontal area.
            Flat plates normal to the flow sit near 1.1-1.3; 1.2 is typical
            for airbrake panels behind a body (wind tunnel data compiled by
            S. F. Hoerner, *Fluid-Dynamic Drag*, 1965, ch. 3).
        travel_time: Time to go from stowed to fully deployed [s]; the
            actuator moves no faster than this.
        max_mach: The panels are only opened below this Mach number; above it
            the loads and shock interactions are too uncertain (and most
            competition rules ask for it).
    """

    mass: float
    diameter: float
    panels: int
    panel_width: float
    panel_height: float
    cd: float = 1.2
    travel_time: float = 0.5
    max_mach: float = 0.8

    def __post_init__(self) -> None:
        if not 1 <= self.panels <= 12:
            raise ValueError(f"use 1 to 12 airbrake panels, got {self.panels}")
        for label, value in (
            ("panel_width", self.panel_width),
            ("panel_height", self.panel_height),
            ("cd", self.cd),
            ("travel_time", self.travel_time),
            ("max_mach", self.max_mach),
        ):
            if not (value > 0.0 and math.isfinite(value)):
                raise ValueError(f"airbrake {label} must be positive, got {value}")

    @property
    def max_area(self) -> float:
        """Frontal area of all panels fully deployed [m²]."""
        return self.panels * self.panel_width * self.panel_height

    @property
    def drag_area(self) -> float:
        """``cd · A`` with every panel fully out [m²]."""
        return self.cd * self.max_area

    def computed(self) -> MassProperties:
        """Solid cylinder filling the airframe over the module's length."""
        return hollow_cylinder(self.mass, 0.5 * self.diameter, 0.0, self.length)
