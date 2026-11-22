"""Aerodynamics: where the air pushes, how hard, and how much it slows you down.

Three questions decide how a rocket flies:

1. **Is it stable?** The air's sideways push acts at the *centre of pressure*
   (CP). If the CP is behind the centre of gravity (CG), a gust that tips the
   nose turns it back into the wind. The gap, in body diameters ("calibres"),
   is the *static margin*; 1 to 2 calibres is the usual target.
2. **How much drag?** The drag coefficient ``Cd`` sets how much of the motor's
   impulse becomes altitude. It changes with speed (Mach number) and with the
   size of the rocket relative to its boundary layer (Reynolds number).
3. **How fast do oscillations die out?** Air resists the rocket rotating, which
   damps the weathercock wobble after rail exit.

Quick use::

    >>> from apogee import load_rocket
    >>> from apogee.aero import Aerodynamics
    >>> from apogee.cli import example_path
    >>> aero = Aerodynamics(load_rocket(example_path("rocket.yaml")))
    >>> round(aero.stability_margin(t=0.0), 2)         # calibres, at liftoff
    2.28
    >>> round(aero.drag(mach=0.5).total, 3)             # Cd at Mach 0.5, sea level, coasting
    0.513

Method
------
*Normal force and CP* follow J. S. Barrowman, *The Practical Calculation of the
Aerodynamic Characteristics of Slender Finned Vehicles* (M.S. thesis, Catholic
University of America, 1967):

* Nose: ``CNα = 2 A_base / A_ref`` per radian, acting at
  ``X = L − V / A_base`` (``V`` the nose volume). This general form gives the
  textbook 2/3 L for a cone and L/2 for a von Kármán nose.
* Body tubes carry no normal force at small angles. At larger angles of attack
  the cross-flow adds ``CN = K (A_plan / A_ref) sin²α`` with ``K = 1.1`` at the
  planform centroid (R. Galejs, *Wind Instability: What Barrowman Left Out*,
  1999), which moves the CP forward as α grows.
* Fins: Barrowman's fin formula with body interference ``1 + R / (s + R)``.
  Written in Diederich's form it carries the compressibility factor
  ``β = sqrt(1 − M²)`` and reduces exactly to Barrowman's at ``M = 0``.
  Above Mach 1.5 each fin follows linear supersonic theory,
  ``CNα = 4 / β · (1 − 1 / (2 A β))`` on its planform with ``β = sqrt(M² − 1)``
  and Bonney's tip-loss term for aspect ratio ``A`` (E. A. Bonney,
  *Engineering Supersonic Aerodynamics*, 1950), valid for ``A β ≥ 1``. Stubbier
  fins blend linearly towards slender-wing theory's ``π A / 2`` (R. T. Jones,
  NACA Report 835, 1946) as ``A β → 0``. Between Mach 0.9
  and 1.5 the slope is interpolated linearly, as OpenRocket does. ``N`` fins give ``N / 2`` times
  one fin's slope (exact when averaged over roll for any ``N``); five to eight
  fins lose a little to fin-fin interference (factors from Niskanen 2009).

*Drag* is a component build-up after S. Niskanen, *Development of an Open Source
Model Rocket Simulation Software* (M.Sc. thesis, Helsinki University of
Technology, 2009), the method behind OpenRocket, with coefficients from
S. F. Hoerner, *Fluid-Dynamic Drag* (1965):

* Skin friction: turbulent ``Cf = 1 / (1.50 ln Re − 5.6)²``, or the
  roughness-limited ``Cf = 0.032 (ε / L)^0.2`` once the finish dominates, with
  compressibility corrections; scaled by wetted area with corrections for body
  fineness and fin thickness.
* Fin leading edges (rounded) from stagnation pressure, trailing edges (square)
  as base drag.
* Base drag ``0.12 + 0.13 M²`` below Mach 1 and ``0.25 / M`` above, on the
  base area not filled by the motor's exhaust while it burns.
* Nose pressure drag ``0.8 sin²φ`` for a cone of half-angle ``φ``, rising to
  Hoerner's supersonic cone value; smooth noses (ogive, von Kármán, Haack,
  parabolic, ellipsoid) have none below Mach 0.8.

*Pitch damping* uses the classic coefficient
``C = ½ ρ v A_ref Σ CNα_i (x_i − x_cg)²`` (G. H. Stine, *Handbook of Model
Rocketry*, 7th ed., ch. 10): each component sees an extra angle of attack
``ω (x_i − x_cg) / v`` while the rocket rotates at ``ω``.

Positions are measured aft from the nose tip, in metres. Angles are radians.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from apogee.atmosphere import atmosphere
from apogee.components import BodyTube, FinSet, NoseCone, NoseShape
from apogee.rocket import Rocket

__all__ = [
    "Aerodynamics",
    "ComponentLift",
    "DragBreakdown",
    "base_drag_coefficient",
    "fin_leading_edge_drag",
    "skin_friction_coefficient",
]

#: Galejs's cross-flow factor for body lift at angle of attack.
BODY_LIFT_K = 1.1

#: Fin-fin interference for 5 to 8 fins (Niskanen 2009, after Barrowman).
_FIN_INTERFERENCE = {5: 0.948, 6: 0.913, 7: 0.854, 8: 0.810}

#: Mach range over which fin lift is interpolated between subsonic and supersonic theory.
_TRANSONIC = (0.9, 1.5)

#: Above this Mach number smooth noses start to carry wave drag (not yet modelled).
SMOOTH_NOSE_MACH_LIMIT = 0.8

_SLICES = 2000


# --- Coefficient building blocks ------------------------------------------------


def skin_friction_coefficient(reynolds: float, mach: float, relative_roughness: float) -> float:
    """Skin-friction coefficient of a flat plate, as used for rocket bodies.

    Args:
        reynolds: Reynolds number based on the rocket's length.
        mach: Free-stream Mach number.
        relative_roughness: Roughness height divided by the rocket's length.

    The larger of the smooth turbulent and the roughness-limited value is used,
    each with its compressibility correction (Niskanen 2009, eqs. 3.81 to 3.85).

    >>> round(skin_friction_coefficient(1e6, 0.0, 0.0), 6)
    0.004372
    """
    # Below Re = 10⁴ the turbulent formula is out of range; hold its value there.
    turbulent = 1.0 / (1.50 * math.log(max(reynolds, 1e4)) - 5.6) ** 2
    if mach < 1.0:
        turbulent *= 1.0 - 0.1 * mach**2
    else:
        turbulent /= (1.0 + 0.15 * mach**2) ** 0.58
    if relative_roughness <= 0.0:
        return turbulent
    rough = 0.032 * relative_roughness**0.2
    rough *= (1.0 - 0.1 * mach**2) if mach < 1.0 else 1.0 / (1.0 + 0.18 * mach**2)
    return float(max(turbulent, rough))


def base_drag_coefficient(mach: float) -> float:
    """Base drag referenced to the base area (Hoerner via Niskanen 2009, eq. 3.94).

    >>> base_drag_coefficient(0.0), base_drag_coefficient(1.0), base_drag_coefficient(2.0)
    (0.12, 0.25, 0.125)
    """
    return 0.12 + 0.13 * mach**2 if mach < 1.0 else 0.25 / mach


def fin_leading_edge_drag(mach: float) -> float:
    """Pressure drag of a rounded leading edge, referenced to its frontal area.

    From the stagnation pressure behind a normal shock (Hoerner; Niskanen 2009,
    eqs. 3.89 and 3.90). Continuous at Mach 0.9 and 1.
    """
    if mach < 0.9:
        return float((1.0 - mach**2) ** -0.417 - 1.0)
    if mach < 1.0:
        return 1.0 - 1.785 * (mach - 0.9)
    return 1.214 - 0.502 / mach**2 + 0.1095 / mach**4


def _cone_pressure_drag(mach: float, half_angle: float) -> float:
    """Pressure drag of a conical nose referenced to its base area (Hoerner)."""
    s = math.sin(half_angle)
    subsonic = 0.8 * s**2
    if mach <= 0.8:
        return subsonic
    supersonic_at = 1.3
    if mach >= supersonic_at:
        return 2.1 * s**2 + 0.5 * s / math.sqrt(mach**2 - 1.0)
    edge = 2.1 * s**2 + 0.5 * s / math.sqrt(supersonic_at**2 - 1.0)
    return subsonic + (edge - subsonic) * (mach - 0.8) / (supersonic_at - 0.8)


# --- Results --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ComponentLift:
    """One component's normal-force slope and where it acts.

    Attributes:
        name: Component name.
        cn_alpha: Normal-force coefficient slope [1/rad], on the rocket's reference area.
        cp: Centre of pressure, aft of the nose tip [m].
    """

    name: str
    cn_alpha: float
    cp: float


@dataclass(frozen=True, slots=True)
class DragBreakdown:
    """Zero-lift drag coefficient by source, all on the rocket's reference area.

    Attributes:
        friction: Skin friction over the body and fins.
        nose: Nose pressure (wave) drag.
        fin_pressure: Fin leading- and trailing-edge pressure drag.
        base: Base drag of the tail.
        mach: Mach number these values are for.
        reynolds: Reynolds number based on the rocket's length.
    """

    friction: float
    nose: float
    fin_pressure: float
    base: float
    mach: float
    reynolds: float

    @property
    def total(self) -> float:
        """Total zero-lift drag coefficient."""
        return self.friction + self.nose + self.fin_pressure + self.base


@dataclass(frozen=True)
class _Fins:
    name: str
    count: int
    root: float
    tip: float
    span: float
    sweep: float
    thickness: float
    position: float  # nose tip to the root leading edge
    body_radius: float

    @property
    def area(self) -> float:
        return 0.5 * (self.root + self.tip) * self.span

    @property
    def mid_chord_sweep(self) -> float:
        return math.atan2(self.sweep + 0.5 * self.tip - 0.5 * self.root, self.span)

    @property
    def leading_edge_sweep(self) -> float:
        return math.atan2(self.sweep, self.span)

    @property
    def mean_aerodynamic_chord(self) -> float:
        c_r, c_t = self.root, self.tip
        return 2.0 / 3.0 * (c_r + c_t - c_r * c_t / (c_r + c_t))

    @property
    def cp(self) -> float:
        """Barrowman's fin CP: the quarter-chord of the mean aerodynamic chord."""
        c_r, c_t, x_t = self.root, self.tip, self.sweep
        return (
            self.position
            + x_t / 3.0 * (c_r + 2.0 * c_t) / (c_r + c_t)
            + (c_r + c_t - c_r * c_t / (c_r + c_t)) / 6.0
        )


# --- The model ------------------------------------------------------------------


@dataclass
class Aerodynamics:
    """Aerodynamic model of a rocket.

    Args:
        rocket: The rocket, as loaded by :func:`apogee.load_rocket`. Its
            ``roughness`` (from ``finish:`` in the YAML) sets skin friction.

    Raises:
        ValueError: If the rocket has no nose cone.
    """

    rocket: Rocket
    warnings: list[str] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        noses = [p for p in self.rocket.parts if isinstance(p.component, NoseCone)]
        if not noses:
            raise ValueError("aerodynamics needs a nose cone at the front of the rocket")
        nose = noses[0].component
        assert isinstance(nose, NoseCone)
        self._nose = nose
        self._tubes = [
            (p.position, p.component)
            for p in self.rocket.parts
            if isinstance(p.component, BodyTube) and p.parent is None
        ]
        self._fins = [
            _Fins(
                name=p.component.name,
                count=p.component.count,
                root=p.component.root_chord,
                tip=p.component.tip_chord,
                span=p.component.span,
                sweep=p.component.sweep,
                thickness=p.component.thickness,
                position=p.position,
                body_radius=p.component.body_radius,
            )
            for p in self.rocket.parts
            if isinstance(p.component, FinSet)
        ]
        self.reference_diameter = self.rocket.diameter
        self.reference_area = self.rocket.reference_area
        self.length = self.rocket.length

        steps = [
            t.name
            for _, t in self._tubes
            if not math.isclose(t.diameter, self._nose.diameter, rel_tol=1e-3)
        ]
        if steps:
            self.warnings.append(
                f"{', '.join(steps)} differ in diameter from the nose; shoulders and boat "
                "tails are not modelled yet, so the step adds no lift or drag"
            )
        if not self._fins:
            self.warnings.append("no fins: the rocket is aerodynamically unstable")

        self._nose_geometry()

    # Geometry --------------------------------------------------------------------

    def _nose_geometry(self) -> None:
        n = self._nose
        dx = n.length / _SLICES
        x = np.asarray((np.arange(_SLICES) + 0.5) * dx, dtype=np.float64)
        r = n.shape.radius(x, n.length, 0.5 * n.diameter)
        base_area = 0.25 * math.pi * n.diameter**2
        self._nose_volume = float(np.sum(math.pi * r**2) * dx)
        self._nose_base_area = base_area
        edges = n.shape.radius(np.linspace(0.0, n.length, _SLICES + 1), n.length, 0.5 * n.diameter)
        ds = np.hypot(np.diff(edges), dx)
        self._nose_wetted = float(np.sum(math.pi * (edges[:-1] + edges[1:]) * ds))
        self._nose_plan_area = float(np.sum(2.0 * r) * dx)
        self._nose_plan_centroid = float(np.sum(2.0 * r * x) * dx) / self._nose_plan_area

    @property
    def body_wetted_area(self) -> float:
        """Wetted area of the nose and body tubes [m²]."""
        return self._nose_wetted + sum(math.pi * t.diameter * t.length for _, t in self._tubes)

    @property
    def fin_wetted_area(self) -> float:
        """Wetted area of every fin, both sides [m²]."""
        return sum(2.0 * f.count * f.area for f in self._fins)

    @property
    def planform_area(self) -> float:
        """Side-on projected area of the nose and body [m²]."""
        return self._nose_plan_area + sum(t.diameter * t.length for _, t in self._tubes)

    @property
    def planform_centroid(self) -> float:
        """Centroid of the side-on projected area, aft of the nose tip [m]."""
        moment = self._nose_plan_area * self._nose_plan_centroid + sum(
            t.diameter * t.length * (x + 0.5 * t.length) for x, t in self._tubes
        )
        return moment / self.planform_area

    # Normal force ----------------------------------------------------------------

    def _fin_cn_alpha(self, fins: _Fins, mach: float) -> float:
        a_ref = self.reference_area

        def subsonic(m: float) -> float:
            beta = math.sqrt(1.0 - m**2)
            ratio = beta * fins.span**2 / (fins.area * math.cos(fins.mid_chord_sweep))
            return 2.0 * math.pi * fins.span**2 / a_ref / (1.0 + math.sqrt(1.0 + ratio**2))

        def supersonic(m: float) -> float:
            beta = math.sqrt(m**2 - 1.0)
            aspect = 2.0 * fins.span**2 / fins.area  # the fin and its mirror image
            u = aspect * beta
            if u >= 1.0:
                per_area = 4.0 / beta * (1.0 - 1.0 / (2.0 * u))
            else:  # stubby fins: blend towards slender-wing theory, continuous at u = 1
                per_area = aspect * (0.5 * math.pi * (1.0 - u) + 2.0 * u)
            return per_area * fins.area / a_ref

        low, high = _TRANSONIC
        if mach <= low:
            one = subsonic(mach)
        elif mach >= high:
            one = supersonic(mach)
        else:
            a, b = subsonic(low), supersonic(high)
            one = a + (b - a) * (mach - low) / (high - low)
        interference = 1.0 + fins.body_radius / (fins.span + fins.body_radius)
        return one * 0.5 * fins.count * _FIN_INTERFERENCE.get(fins.count, 1.0) * interference

    def components(self, mach: float = 0.0) -> list[ComponentLift]:
        """Normal-force slope and CP of the nose and each fin set at small angles.

        >>> from apogee.rocket import rocket_from_dict
        >>> cone = rocket_from_dict({"components": [{"nose_cone": {"shape": "conical",
        ...     "length": 0.3, "diameter": 0.1, "thickness": 0.002, "density": 1000}}]})
        >>> nose, = Aerodynamics(cone).components()
        >>> round(nose.cn_alpha, 3), round(nose.cp, 4)     # 2 per radian at 2/3 of the length
        (2.0, 0.2)
        """
        n = self._nose
        out = [
            ComponentLift(
                n.name,
                2.0 * self._nose_base_area / self.reference_area,
                n.length - self._nose_volume / self._nose_base_area,
            )
        ]
        out += [
            ComponentLift(
                f"{f.name} ({f.count})",
                self._fin_cn_alpha(f, mach),
                f.cp,
            )
            for f in self._fins
        ]
        return out

    def cn_alpha(self, mach: float = 0.0) -> float:
        """Total normal-force slope at small angles of attack [1/rad]."""
        return sum(c.cn_alpha for c in self.components(mach))

    def center_of_pressure(self, mach: float = 0.0, alpha: float = 0.0) -> float:
        """Centre of pressure, aft of the nose tip [m].

        At ``alpha = 0`` this is Barrowman's CP; at larger angles the body's
        cross-flow lift pulls it forward.
        """
        if alpha == 0.0:
            parts = self.components(mach)
            total = sum(c.cn_alpha for c in parts)
            return sum(c.cn_alpha * c.cp for c in parts) / total
        return self.normal_force(alpha, mach)[1]

    def normal_force(self, alpha: float, mach: float = 0.0) -> tuple[float, float]:
        """Normal-force coefficient and its point of action at angle of attack ``alpha``.

        Returns:
            ``(CN, x_cp)``: the coefficient on the reference area, and the
            centre of pressure aft of the nose tip [m].
        """
        sin_a = math.sin(alpha)
        parts = [(c.cn_alpha * sin_a, c.cp) for c in self.components(mach)]
        body = BODY_LIFT_K * self.planform_area / self.reference_area * sin_a * abs(sin_a)
        parts.append((body, self.planform_centroid))
        cn = sum(p for p, _ in parts)
        if cn == 0.0:
            return 0.0, self.components(mach)[0].cp
        return cn, sum(p * x for p, x in parts) / cn

    def stability_margin(
        self, t: float = 0.0, mach: float = 0.0, alpha: float = 0.0, cg: float | None = None
    ) -> float:
        """Static margin in calibres: ``(x_cp − x_cg) / d``. Positive is stable.

        Args:
            t: Time after ignition [s], which sets the CG as propellant burns.
            mach: Mach number.
            alpha: Angle of attack [rad].
            cg: Override the CG position [m from the nose tip].
        """
        x_cg = self.rocket.mass_properties(t).cg if cg is None else cg
        return (self.center_of_pressure(mach, alpha) - x_cg) / self.reference_diameter

    # Drag ------------------------------------------------------------------------

    def drag(
        self,
        mach: float,
        altitude: float = 0.0,
        *,
        thrusting: bool = False,
        speed_of_sound: float | None = None,
        kinematic_viscosity: float | None = None,
    ) -> DragBreakdown:
        """Zero-lift drag coefficient at a flight condition.

        Args:
            mach: Mach number.
            altitude: Altitude above sea level [m]; sets the air's viscosity and
                speed of sound via the 1976 Standard Atmosphere.
            thrusting: While the motor burns, its exhaust fills part of the base
                and removes that part's base drag.
            speed_of_sound: Override the atmosphere [m/s].
            kinematic_viscosity: Override the atmosphere [m²/s].
        """
        if mach < 0.0:
            raise ValueError(f"Mach number must be zero or positive, got {mach}")
        if speed_of_sound is None or kinematic_viscosity is None:
            air = atmosphere(altitude)
            speed_of_sound = speed_of_sound or air.speed_of_sound
            kinematic_viscosity = kinematic_viscosity or air.kinematic_viscosity
        a_ref = self.reference_area
        reynolds = mach * speed_of_sound * self.length / kinematic_viscosity

        cf = skin_friction_coefficient(reynolds, mach, self.rocket.roughness / self.length)
        fineness = self.length / self.reference_diameter
        fin_wet = sum(
            2.0 * f.count * f.area * (1.0 + 2.0 * f.thickness / f.mean_aerodynamic_chord)
            for f in self._fins
        )
        friction = cf * ((1.0 + 0.5 / fineness) * self.body_wetted_area + fin_wet) / a_ref

        le = fin_leading_edge_drag(mach)
        base = base_drag_coefficient(mach)
        fin_pressure = sum(
            f.count
            * f.thickness
            * f.span
            * (le * math.cos(f.leading_edge_sweep) ** 2 + base)
            / a_ref
            for f in self._fins
        )

        tail = self._tubes[-1][1] if self._tubes else self._nose
        base_area = 0.25 * math.pi * tail.diameter**2
        if thrusting and self.rocket.motor is not None:
            base_area = max(0.0, base_area - 0.25 * math.pi * self.rocket.motor.diameter**2)

        nose = 0.0
        if self._nose.shape is NoseShape.CONICAL:
            half_angle = math.atan2(0.5 * self._nose.diameter, self._nose.length)
            nose = _cone_pressure_drag(mach, half_angle) * self._nose_base_area / a_ref

        return DragBreakdown(
            friction=friction,
            nose=nose,
            fin_pressure=fin_pressure,
            base=base * base_area / a_ref,
            mach=mach,
            reynolds=reynolds,
        )

    # Damping ---------------------------------------------------------------------

    def damping_coefficient(
        self, density: float, speed: float, cg: float, mach: float = 0.0
    ) -> float:
        """Aerodynamic pitch-damping coefficient ``C`` [N·m·s/rad].

        The damping moment is ``−C ω`` for a pitch rate ``ω``:
        ``C = ½ ρ v A_ref Σ CNα_i (x_i − x_cg)²``.

        Args:
            density: Air density [kg/m³].
            speed: Airspeed [m/s].
            cg: Centre of gravity, aft of the nose tip [m].
            mach: Mach number, for the fin lift slope.
        """
        arms = sum(c.cn_alpha * (c.cp - cg) ** 2 for c in self.components(mach))
        return 0.5 * density * speed * self.reference_area * arms

    def max_reliable_mach(self) -> float:
        """Highest Mach number at which every drag term is modelled."""
        if self._nose.shape is NoseShape.CONICAL:
            return math.inf
        return SMOOTH_NOSE_MACH_LIMIT
