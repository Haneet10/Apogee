"""Apogee: a 6-DOF rocket flight simulator you can read.

The package is built up in layers. Available today:

* :mod:`apogee.atmosphere`: the 1976 US Standard Atmosphere, 0 to 86 km.
* :mod:`apogee.earth`: gravity and geometric/geopotential altitude conversion.
"""

from apogee.atmosphere import AtmosphereState, OutOfAtmosphereError, atmosphere
from apogee.earth import gravity

__version__ = "0.1.0"

__all__ = ["AtmosphereState", "OutOfAtmosphereError", "__version__", "atmosphere", "gravity"]
