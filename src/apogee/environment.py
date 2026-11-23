"""Where the rocket flies from: the launch rail, the site and the wind.

World axes are local east-north-up (ENU) with the origin at the rocket's
centre of gravity as it sits on the rail: ``x`` east, ``y`` north, ``z`` up.
The Earth is flat and does not rotate, which is accurate to well under 0.1 %
for flights of a few kilometres (the Coriolis drift of a 3 km flight is a few
metres).

Quick use::

    >>> from apogee.environment import LaunchSite, Wind
    >>> site = LaunchSite(rail_length=5.2, tilt=5.0, heading=270.0,
    ...                   wind=Wind(speed=6.0, direction=270.0))
    >>> site.rail_direction.round(3) + 0.0             # leaning 5° to the west
    array([-0.087,  0.   ,  0.996])
    >>> site.wind.velocity(10.0).round(2) + 0.0        # a west wind blows east
    array([6., 0., 0.])
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

__all__ = ["LaunchSite", "Wind"]


@dataclass(frozen=True)
class Wind:
    """A steady horizontal wind that strengthens with height.

    Speed follows the power law used for the atmospheric boundary layer over
    open ground, ``v(h) = v_ref (h / h_ref)^(1/7)`` (Hellmann exponent 1/7;
    see e.g. J. F. Manwell et al., *Wind Energy Explained*, 2nd ed., §2.3.3).
    Set ``exponent=0`` for the same wind at every height.

    Attributes:
        speed: Wind speed at the reference height [m/s].
        direction: Where the wind blows *from*, as a compass bearing in
            degrees (0 north, 90 east), the way weather reports give it.
        reference_height: Height above the pad at which ``speed`` was
            measured [m]; 10 m is the meteorological standard.
        exponent: Power-law exponent.
    """

    speed: float = 0.0
    direction: float = 0.0
    reference_height: float = 10.0
    exponent: float = 1.0 / 7.0

    def __post_init__(self) -> None:
        if self.speed < 0.0:
            raise ValueError(
                f"wind speed must be zero or positive, got {self.speed}; "
                "use `direction` to say where it comes from"
            )
        if self.reference_height <= 0.0:
            raise ValueError("wind reference height must be positive")

    def speed_at(self, height: float) -> float:
        """Wind speed [m/s] at ``height`` metres above the pad."""
        if self.exponent == 0.0:
            return self.speed
        if height <= 0.0:
            return 0.0
        return float(self.speed * (height / self.reference_height) ** self.exponent)

    def velocity(self, height: float) -> NDArray[np.float64]:
        """Velocity of the air [m/s] in world axes (east, north, up)."""
        v = self.speed_at(height)
        bearing = math.radians(self.direction)
        # Air moves *towards* the opposite bearing.
        return np.array([-v * math.sin(bearing), -v * math.cos(bearing), 0.0])


@dataclass(frozen=True)
class LaunchSite:
    """The launch rail and the ground around it.

    Attributes:
        rail_length: Distance the rocket is guided before it flies free [m]
            (rail length minus the gap between the aft rail button and the
            rail's lower end).
        tilt: Rail angle from vertical [degrees]. Ranges allow up to about 20°.
        heading: Compass bearing the rail leans towards [degrees]; matters only
            when ``tilt`` is not zero. Leaning slightly *into* the wind keeps
            the rocket closer to the pad.
        altitude: Height of the pad above sea level [m].
        wind: The wind.
    """

    rail_length: float = 5.0
    tilt: float = 0.0
    heading: float = 0.0
    altitude: float = 0.0
    wind: Wind = field(default_factory=Wind)

    def __post_init__(self) -> None:
        if self.rail_length < 0.0:
            raise ValueError(f"rail length must be zero or positive, got {self.rail_length}")
        if not 0.0 <= self.tilt < 90.0:
            raise ValueError(f"rail tilt must be between 0° and 90° from vertical, got {self.tilt}")

    @property
    def rail_direction(self) -> NDArray[np.float64]:
        """Unit vector along the rail, pointing up it (world axes)."""
        tilt = math.radians(self.tilt)
        bearing = math.radians(self.heading)
        return np.array(
            [
                math.sin(tilt) * math.sin(bearing),
                math.sin(tilt) * math.cos(bearing),
                math.cos(tilt),
            ]
        )
