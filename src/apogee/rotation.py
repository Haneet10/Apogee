"""Unit quaternions for rocket attitude.

A quaternion ``q = [w, x, y, z]`` (scalar first) with ``|q| = 1`` encodes a
rotation. In Apogee it always maps **body axes to world axes**: a vector
``v_b`` written in the rocket's own axes becomes ``v_w = R(q) v_b`` on the
ground. Quaternions are used instead of Euler angles because they have no
singularity when the rocket points straight up, which is exactly where a
rocket spends its first seconds.

The attitude changes with the body rates ``ω`` (rad/s, body axes) as
``q̇ = ½ q ⊗ [0, ω]``.

Reference: J. Diebel, *Representing Attitude: Euler Angles, Unit Quaternions,
and Rotation Vectors*, Stanford University, 2006 (sections 6 and 7; the
Hamilton convention used here).

>>> import numpy as np
>>> q = from_axis_angle([0.0, 0.0, 1.0], np.pi / 2)    # 90° about world z
>>> np.round(rotate(q, [1.0, 0.0, 0.0]), 12) + 0.0
array([0., 1., 0.])
"""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import ArrayLike, NDArray

__all__ = [
    "from_axis_angle",
    "from_two_vectors",
    "multiply",
    "rate",
    "rotate",
    "rotate_inverse",
    "to_matrix",
]

Vector = NDArray[np.float64]


def multiply(p: ArrayLike, q: ArrayLike) -> Vector:
    """Hamilton product ``p ⊗ q`` (apply ``q`` first, then ``p``)."""
    pw, px, py, pz = np.asarray(p, dtype=np.float64)
    qw, qx, qy, qz = np.asarray(q, dtype=np.float64)
    return np.array(
        [
            pw * qw - px * qx - py * qy - pz * qz,
            pw * qx + px * qw + py * qz - pz * qy,
            pw * qy - px * qz + py * qw + pz * qx,
            pw * qz + px * qy - py * qx + pz * qw,
        ]
    )


def to_matrix(q: ArrayLike) -> Vector:
    """Rotation matrix ``R`` (body to world) of a unit quaternion."""
    w, x, y, z = np.asarray(q, dtype=np.float64)
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ]
    )


def rotate(q: ArrayLike, v: ArrayLike) -> Vector:
    """Express a body-axis vector in world axes."""
    return to_matrix(q) @ np.asarray(v, dtype=np.float64)


def rotate_inverse(q: ArrayLike, v: ArrayLike) -> Vector:
    """Express a world-axis vector in body axes."""
    return to_matrix(q).T @ np.asarray(v, dtype=np.float64)


def rate(q: ArrayLike, omega: ArrayLike) -> Vector:
    """Time derivative ``q̇ = ½ q ⊗ [0, ω]`` for body rates ``ω`` [rad/s]."""
    wx, wy, wz = np.asarray(omega, dtype=np.float64)
    return 0.5 * multiply(q, [0.0, wx, wy, wz])


def from_axis_angle(axis: ArrayLike, angle: float) -> Vector:
    """Rotation by ``angle`` [rad] about ``axis`` (right-hand rule)."""
    a = np.asarray(axis, dtype=np.float64)
    norm = float(np.linalg.norm(a))
    if norm == 0.0:
        raise ValueError("rotation axis must not be the zero vector")
    s = math.sin(0.5 * angle) / norm
    return np.array([math.cos(0.5 * angle), a[0] * s, a[1] * s, a[2] * s])


def from_two_vectors(a: ArrayLike, b: ArrayLike) -> Vector:
    """Shortest rotation that turns direction ``a`` into direction ``b``.

    >>> q = from_two_vectors([1.0, 0.0, 0.0], [0.0, 0.0, 1.0])
    >>> np.round(rotate(q, [1.0, 0.0, 0.0]), 12) + 0.0
    array([0., 0., 1.])
    """
    u = np.asarray(a, dtype=np.float64)
    v = np.asarray(b, dtype=np.float64)
    u = u / np.linalg.norm(u)
    v = v / np.linalg.norm(v)
    d = float(np.dot(u, v))
    if d < -1.0 + 1e-12:  # opposite: turn half a revolution about any perpendicular
        perp = np.cross(u, [1.0, 0.0, 0.0])
        if np.linalg.norm(perp) < 1e-6:
            perp = np.cross(u, [0.0, 1.0, 0.0])
        return from_axis_angle(perp, math.pi)
    q = np.concatenate(([1.0 + d], np.cross(u, v)))
    return np.asarray(q / np.linalg.norm(q), dtype=np.float64)
