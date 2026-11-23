import math
from itertools import pairwise

import numpy as np
import pytest

from apogee import rotation
from apogee.cli import example_path
from apogee.dynamics import ATTITUDE, RATES, STATE_SIZE, Dynamics, integrate, rk4_step
from apogee.earth import G0
from apogee.environment import LaunchSite, Wind
from apogee.rocket import load_rocket, rocket_from_dict

FREE = LaunchSite(rail_length=0.0)


def airframe(ballast=None):
    """A 100 mm, 1.3 m finned airframe with no motor; optional ballast at the CG region."""
    contents = [
        {
            "fins": {
                "count": 4,
                "root_chord": 0.15,
                "tip_chord": 0.05,
                "span": 0.1,
                "sweep": 0.1,
                "thickness": 0.003,
                "density": 1850,
            }
        }
    ]
    if ballast is not None:
        contents.append(
            {"mass": {"mass": ballast, "length": 0.1, "diameter": 0.08, "position": 0.3}}
        )
    return rocket_from_dict(
        {
            "components": [
                {
                    "nose_cone": {
                        "shape": "conical",
                        "length": 0.3,
                        "diameter": 0.1,
                        "thickness": 0.002,
                        "density": 1850,
                    }
                },
                {
                    "body_tube": {
                        "length": 1.0,
                        "thickness": 0.002,
                        "density": 1850,
                        "contents": contents,
                    }
                },
            ]
        }
    )


@pytest.fixture(scope="module")
def kestrel():
    return load_rocket(example_path("rocket.yaml"))


def state(velocity=(0.0, 0.0, 0.0), omega=(0.0, 0.0, 0.0), axis=(1.0, 0.0, 0.0)):
    y = np.zeros(STATE_SIZE)
    y[3:6] = velocity
    y[ATTITUDE] = rotation.from_two_vectors([1.0, 0.0, 0.0], axis)
    y[RATES] = omega
    return y


# --- Integrator --------------------------------------------------------------------


def test_rk4_is_fourth_order():
    """Halving the step on y' = y cuts the error by 2⁴."""

    def f(t, y):
        return y

    def error(dt):
        y = np.array([1.0])
        for i in range(round(1.0 / dt)):
            y = rk4_step(f, i * dt, y, dt)
        return abs(y[0] - math.e)

    assert error(0.1) / error(0.05) == pytest.approx(16.0, rel=0.05)


def test_integrate_validates_arguments(kestrel):
    with pytest.raises(ValueError, match="time step"):
        integrate(Dynamics(kestrel), 1.0, dt=0.0)
    with pytest.raises(ValueError, match="after t0"):
        integrate(Dynamics(kestrel), 0.0)
    with pytest.raises(ValueError, match="y0"):
        integrate(lambda t, y: y, 1.0)


def test_integrate_lands_exactly_on_t_end():
    traj = integrate(lambda t, y: np.zeros_like(y), 0.25, dt=0.1, y0=state())
    assert traj.t.tolist() == pytest.approx([0.0, 0.1, 0.2, 0.25])


# --- Closed-form checks ------------------------------------------------------------


def test_vacuum_ballistic_flight_matches_closed_form():
    """No air, uniform gravity: x = v0 t, z = v0z t − ½ g t² (RK4 is exact for this)."""
    dyn = Dynamics(airframe(), FREE, aerodynamics=False, uniform_gravity=True)
    v0 = np.array([30.0, -10.0, 50.0])
    traj = integrate(dyn, 8.0, dt=0.05, y0=state(v0, axis=v0))
    t = traj.t[:, None]
    expected = v0 * t + 0.5 * np.array([0.0, 0.0, -G0]) * t**2
    np.testing.assert_allclose(traj.position, expected, atol=1e-9)
    # With no torque the attitude never changes.
    np.testing.assert_allclose(traj.attitude - traj.attitude[0], 0.0, atol=1e-12)


def test_powered_vertical_flight_matches_rocket_equation(kestrel):
    """Vacuum, uniform g: v_burnout = c ln(m_lift / m_burnout) − g (t_b − t_lift).

    The propellant burns in proportion to delivered impulse, so the effective
    exhaust velocity is exactly c = I_total / m_propellant. The rocket sits on
    the launch stop until thrust exceeds weight at t_lift.
    """
    motor = kestrel.motor
    dyn = Dynamics(kestrel, LaunchSite(rail_length=1e6), aerodynamics=False, uniform_gravity=True)
    traj = integrate(dyn, motor.burn_time, dt=0.001)

    def excess(t):
        return motor.thrust_at(t) - kestrel.mass_properties(t).mass * G0

    lo, hi = 0.0, 0.2
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if excess(mid) < 0.0 else (lo, mid)
    t_lift = lo
    c = motor.total_impulse / motor.propellant_mass
    m_lift = kestrel.mass_properties(t_lift).mass
    m_burnout = kestrel.mass_properties(motor.burn_time).mass
    v_burnout = c * math.log(m_lift / m_burnout) - G0 * (motor.burn_time - t_lift)

    assert traj.velocity[-1, 2] == pytest.approx(v_burnout, rel=1e-6)
    np.testing.assert_allclose(traj.velocity[-1, :2], 0.0, atol=1e-9)
    assert np.all(traj.velocity[traj.t < t_lift - 0.002, 2] == 0.0)  # waits on the stop


def test_torque_free_spin_precesses_and_conserves_momentum():
    """Axisymmetric body: ω_x is constant, (ω_y + i ω_z) turns at Ω = (Ixx − Iyy) ω_x / Iyy."""
    rocket = airframe()
    dyn = Dynamics(rocket, FREE, aerodynamics=False, gravity=False)
    _, _, ixx, iyy = dyn.mass_properties(0.0)
    w0 = np.array([20.0, 0.5, -0.3])
    traj = integrate(dyn, 4.0, dt=0.001, y0=state(omega=w0), normalize=False)

    big_omega = (ixx - iyy) / iyy * w0[0]
    transverse = (w0[1] + 1j * w0[2]) * np.exp(1j * big_omega * traj.t)
    np.testing.assert_allclose(traj.rates[:, 0], w0[0], rtol=1e-12)
    np.testing.assert_allclose(traj.rates[:, 1], transverse.real, atol=1e-7)
    np.testing.assert_allclose(traj.rates[:, 2], transverse.imag, atol=1e-7)

    inertia = np.array([ixx, iyy, iyy])
    momentum = np.array(
        [rotation.rotate(q, inertia * w) for q, w in zip(traj.attitude, traj.rates, strict=True)]
    )
    drift = np.linalg.norm(momentum - momentum[0], axis=1)
    assert drift.max() < 1e-7 * np.linalg.norm(momentum[0])
    energy = 0.5 * np.sum(inertia * traj.rates**2, axis=1)
    np.testing.assert_allclose(energy, energy[0], rtol=1e-9)
    # Without renormalisation RK4 still keeps |q| = 1 to round-off level.
    assert np.max(np.abs(np.linalg.norm(traj.attitude, axis=1) - 1.0)) < 1e-10


def test_pitch_oscillation_matches_linear_theory():
    """Released at a small pitch rate, the rocket weathercocks at the damped natural frequency.

    Linearised pitch: I θ'' + C θ' + k θ = 0 with k = q A CNα (x_cp − x_cg) and C the
    aerodynamic damping coefficient, so ω_d = √(k/I) √(1 − ζ²), ζ = C / (2√(k I)).
    Heavy ballast keeps the airspeed nearly constant over the measurement.
    """
    rocket = airframe(ballast=40.0)
    dyn = Dynamics(rocket, FREE, gravity=False)
    v = 150.0
    traj = integrate(dyn, 1.5, dt=0.001, y0=state((v, 0.0, 0.0), omega=(0.0, 0.3, 0.0)))

    wy = traj.rates[:, 1]
    crossings = traj.t[1:][np.signbit(wy[1:]) != np.signbit(wy[:-1])]
    assert len(crossings) >= 5
    period = 2.0 * np.mean(np.diff(crossings[:5]))

    mid = np.searchsorted(traj.t, 0.5 * (crossings[0] + crossings[4]))
    speed = traj.speed[mid]
    _, cg, _, iyy = dyn.mass_properties(0.0)
    aero = dyn.aero
    air_density = 1.225
    mach = speed / 340.294
    k = 0.5 * air_density * speed**2 * aero.reference_area * aero.cn_alpha(mach)
    k *= aero.center_of_pressure(mach) - cg
    c = aero.damping_coefficient(air_density, speed, cg, mach)
    zeta = c / (2.0 * math.sqrt(k * iyy))
    omega_d = math.sqrt(k / iyy) * math.sqrt(1.0 - zeta**2)
    assert period == pytest.approx(2.0 * math.pi / omega_d, rel=0.005)
    # And the swing dies away: each peak smaller than the last.
    peaks = [np.max(np.abs(wy[(traj.t >= a) & (traj.t < b)])) for a, b in pairwise(crossings)]
    assert all(later < earlier for earlier, later in pairwise(peaks))


# --- Rail, wind and damping -------------------------------------------------------


def test_rail_holds_attitude_and_line(kestrel):
    site = LaunchSite(rail_length=5.2, tilt=10.0, heading=45.0, wind=Wind(8.0, direction=0.0))
    dyn = Dynamics(kestrel, site)
    traj = integrate(dyn, 1.0, dt=0.005)
    rail = site.rail_direction
    guided = traj.position @ rail < site.rail_length
    assert dyn.released
    assert guided[0] and not guided[-1]
    on = traj.position[guided]
    off_line = on - np.outer(on @ rail, rail)
    np.testing.assert_allclose(off_line, 0.0, atol=1e-9)
    np.testing.assert_allclose(traj.attitude[guided] - traj.attitude[0], 0.0, atol=1e-15)
    np.testing.assert_allclose(traj.axis[0], rail, atol=1e-12)


def test_rocket_turns_into_the_wind(kestrel):
    """Wind from the east: after the rail the nose swings east and the rocket drifts upwind."""
    site = LaunchSite(rail_length=5.2, wind=Wind(8.0, direction=90.0))
    traj = integrate(Dynamics(kestrel, site), 6.0, dt=0.01)
    assert traj.axis[-1, 0] > 0.05
    assert traj.position[-1, 0] > 5.0
    assert abs(traj.position[-1, 1]) < 1e-6  # nothing pushes it north or south


def test_jet_damping_moment(kestrel):
    """Off the rail during the burn: M = −ṁ (x_nozzle − x_cg)² ω, in pitch and yaw only."""
    dyn = Dynamics(kestrel, FREE, aerodynamics=False)
    t = 1.0
    f = dyn.forces(t, state(omega=(3.0, 1.0, -2.0)))
    _, cg, _, _ = dyn.mass_properties(t)
    arm = kestrel.motor_position + kestrel.motor.length - cg
    jet = dyn.mass_flow(t) * arm**2
    assert jet > 0.0
    np.testing.assert_allclose(f.moment, [0.0, -jet, 2.0 * jet])


def test_mass_flow_integrates_to_propellant_mass(kestrel):
    dyn = Dynamics(kestrel)
    t = np.linspace(0.0, kestrel.motor.burn_time, 20001)
    flow = np.array([dyn.mass_flow(x) for x in t])
    burnt = float(np.sum(0.5 * (flow[1:] + flow[:-1]) * np.diff(t)))
    assert burnt == pytest.approx(kestrel.motor.propellant_mass, rel=1e-4)


def test_mass_table_matches_rocket(kestrel):
    dyn = Dynamics(kestrel)
    for t in (0.0, 0.7, 2.31, 3.9, 10.0):
        exact = kestrel.mass_properties(t)
        m, cg, ixx, iyy = dyn.mass_properties(t)
        assert m == pytest.approx(exact.mass, rel=1e-5)
        assert cg == pytest.approx(exact.cg, rel=1e-5)
        assert ixx == pytest.approx(exact.ixx, rel=1e-4)
        assert iyy == pytest.approx(exact.iyy, rel=1e-4)


def test_step_size_convergence(kestrel):
    """Halving the step from 0.02 s changes the 6 s state by well under a metre."""
    site = LaunchSite(rail_length=5.2, wind=Wind(5.0, direction=200.0))
    coarse = integrate(Dynamics(kestrel, site), 6.0, dt=0.02).position[-1]
    fine = integrate(Dynamics(kestrel, site), 6.0, dt=0.01).position[-1]
    assert np.linalg.norm(coarse - fine) < 0.5
