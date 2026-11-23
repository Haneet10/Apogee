"""Apogee: a 6-DOF rocket flight simulator you can read.

The package is built up in layers. Available today:

* :mod:`apogee.atmosphere`: the 1976 US Standard Atmosphere, 0 to 86 km.
* :mod:`apogee.earth`: gravity and geometric/geopotential altitude conversion.
* :mod:`apogee.motor`: RASP ``.eng`` thrust curves, impulse and burning mass.
* :mod:`apogee.components` and :mod:`apogee.rocket`: rocket parts loaded from
  YAML, with mass, centre of gravity and inertia through the burn.
* :mod:`apogee.aero`: Barrowman centre of pressure and stability margin, drag
  build-up against Mach and Reynolds number, and pitch damping.
* :mod:`apogee.environment` and :mod:`apogee.rotation`: launch rail, wind
  profile and quaternion attitude.
* :mod:`apogee.dynamics`: 6-DOF equations of motion and the RK4 integrator.
"""

from apogee.aero import Aerodynamics, DragBreakdown
from apogee.atmosphere import AtmosphereState, OutOfAtmosphereError, atmosphere
from apogee.components import MassProperties
from apogee.dynamics import Dynamics, Trajectory, integrate
from apogee.earth import gravity
from apogee.environment import LaunchSite, Wind
from apogee.motor import Motor, MotorFileError, load_motor
from apogee.rocket import Rocket, RocketFileError, load_rocket

__version__ = "0.1.0"

__all__ = [
    "Aerodynamics",
    "AtmosphereState",
    "DragBreakdown",
    "Dynamics",
    "LaunchSite",
    "MassProperties",
    "Motor",
    "MotorFileError",
    "OutOfAtmosphereError",
    "Rocket",
    "RocketFileError",
    "Trajectory",
    "Wind",
    "__version__",
    "atmosphere",
    "gravity",
    "integrate",
    "load_motor",
    "load_rocket",
]
