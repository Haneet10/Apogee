from pathlib import Path

import pytest
import yaml

from apogee.components import FinSet, InnerTube, MassProperties
from apogee.rocket import RocketFileError, load_rocket, rocket_from_dict

EXAMPLES = Path(__file__).parents[1] / "examples"


def minimal(**extra):
    doc = {
        "name": "Test",
        "components": [
            {
                "nose_cone": {
                    "length": "300mm",
                    "diameter": "100mm",
                    "thickness": "2mm",
                    "material": "polystyrene",
                }
            },
            {
                "body_tube": {
                    "length": "1m",
                    "thickness": "2mm",
                    "material": "cardboard",
                    "contents": [],
                }
            },
        ],
    }
    doc.update(extra)
    return doc


def tube_contents(doc):
    return doc["components"][1]["body_tube"]["contents"]


def test_external_parts_are_stacked_nose_to_tail():
    r = rocket_from_dict(minimal())
    nose, tube = r.parts
    assert nose.position == 0.0
    assert tube.position == pytest.approx(0.3)
    assert r.length == pytest.approx(1.3)
    assert r.diameter == pytest.approx(0.1)  # inherited from the nose
    assert r.motor is None


def test_rocket_mass_properties_combine_parts():
    r = rocket_from_dict(minimal())
    whole = r.mass_properties()
    parts = [p.mass_properties() for p in r.parts]
    assert whole.mass == pytest.approx(sum(p.mass for p in parts))
    assert whole == MassProperties.combine(parts)
    assert 0.3 < whole.cg < 1.3


def test_children_positions_front_and_aft():
    doc = minimal()
    tube_contents(doc).extend(
        [
            {"mass": {"name": "Payload", "mass": "100g", "position": "200mm"}},
            {
                "mass": {
                    "name": "Tail weight",
                    "mass": "50g",
                    "length": "20mm",
                    "aft_offset": "10mm",
                }
            },
            {
                "fins": {
                    "root_chord": "150mm",
                    "span": "80mm",
                    "thickness": "3mm",
                    "material": "balsa",
                }
            },
        ]
    )
    r = rocket_from_dict(doc)
    by_name = {p.component.name: p for p in r.parts}
    assert by_name["Payload"].position == pytest.approx(0.5)
    assert by_name["Tail weight"].aft == pytest.approx(1.29)
    fins = by_name["Fins"]
    assert fins.aft == pytest.approx(1.3)  # flush with the tail by default
    assert isinstance(fins.component, FinSet)
    assert fins.component.count == 3
    assert fins.component.body_radius == pytest.approx(0.05)


def test_motor_in_mount_with_overhang(tmp_path):
    (tmp_path / "m.eng").write_text("C10 24 70 0 0.010 0.025 X\n0.1 10\n0.9 10\n1.0 0\n")
    doc = minimal(motor={"file": "m.eng", "overhang": "5mm"})
    tube_contents(doc).append(
        {
            "inner_tube": {
                "length": "200mm",
                "diameter": "26mm",
                "thickness": "0.5mm",
                "material": "cardboard",
            }
        }
    )
    path = tmp_path / "r.yaml"
    path.write_text(yaml.safe_dump(doc))
    r = load_rocket(path)
    mount = next(p for p in r.parts if isinstance(p.component, InnerTube))
    assert mount.aft == pytest.approx(1.3)  # the only inner tube is the mount
    assert r.motor_position == pytest.approx(1.3 + 0.005 - 0.070)
    lift, burnout = r.mass_properties(0.0), r.mass_properties(r.burnout_time)
    assert lift.mass - burnout.mass == pytest.approx(0.010)
    assert burnout.mass - r.dry_mass_properties().mass == pytest.approx(0.015)
    assert burnout.cg < lift.cg  # propellant was aft of the CG, so the CG moves forward
    mid = r.mass_properties(0.5)
    assert burnout.mass < mid.mass < lift.mass


def test_motor_too_big_for_mount(tmp_path):
    (tmp_path / "m.eng").write_text("C10 29 70 0 0.010 0.025 X\n0.1 10\n1.0 0\n")
    doc = minimal(motor={"file": "m.eng"})
    tube_contents(doc).append(
        {
            "inner_tube": {
                "length": "200mm",
                "diameter": "26mm",
                "thickness": "0.5mm",
                "material": "cardboard",
            }
        }
    )
    with pytest.raises(RocketFileError, match=r"29 mm across but 'Inner tube' is only 25\.0 mm"):
        rocket_from_dict(doc, tmp_path)


def test_example_rocket_loads_with_sensible_numbers():
    r = load_rocket(EXAMPLES / "rocket.yaml")
    assert r.name == "Kestrel"
    assert r.warnings == ()
    assert r.motor is not None and r.motor.designation == "M1670-BS"
    lift = r.mass_properties(0.0)
    burnout = r.mass_properties(r.burnout_time)
    dry = r.dry_mass_properties()
    assert lift.mass == pytest.approx(dry.mass + r.motor.loaded_mass)
    assert burnout.mass == pytest.approx(dry.mass + r.motor.case_mass)
    assert 0.5 * r.length < lift.cg < r.length
    assert burnout.cg < lift.cg
    assert lift.iyy > burnout.iyy > dry.iyy
    assert [c.name for c in r.parachutes] == ["Main parachute", "Drogue parachute"]
    assert r.parachutes[0].deploy_altitude == pytest.approx(300.0)
    assert r.parachutes[1].deploy_altitude is None


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda d: d["components"][0]["nose_cone"].update(lenght="1m"),
            "unknown setting 'lenght'; did you mean 'length'",
        ),
        (
            lambda d: d["components"][0]["nose_cone"].update(material="fiberglas"),
            "did you mean 'fiberglass'",
        ),
        (
            lambda d: d["components"][0]["nose_cone"].update(shape="pointy"),
            "unknown shape 'pointy'",
        ),
        (
            lambda d: d["components"][1]["body_tube"].pop("thickness"),
            "missing required setting 'thickness'",
        ),
        (
            lambda d: d["components"][1]["body_tube"].update(length="1 parsec"),
            r"components\[1\].body_tube.length: unknown length unit",
        ),
        (lambda d: d["components"].reverse(), "start the components list with a nose_cone"),
        (lambda d: d["components"].append({"transition": {}}), "unknown component 'transition'"),
        (
            lambda d: tube_contents(d).append({"mass": {"mass": "1g", "position": "2m"}}),
            "runs outside 'Body tube'",
        ),
        (
            lambda d: tube_contents(d).append(
                {
                    "fins": {
                        "root_chord": "0.1m",
                        "span": "0.1m",
                        "thickness": "3mm",
                        "material": "balsa",
                        "count": 1,
                    }
                }
            ),
            "use 2 to 8 fins",
        ),
        (lambda d: tube_contents(d).append({"wing": {}}), "unknown part 'wing'"),
        (lambda d: d.update(motor={"file": "missing.eng"}), "motor file not found"),
        (lambda d: d.pop("components"), "add a 'components' list"),
    ],
)
def test_errors_point_at_the_problem(mutate, message, tmp_path):
    doc = minimal()
    mutate(doc)
    with pytest.raises(RocketFileError, match=message):
        rocket_from_dict(doc, tmp_path)


def test_invalid_yaml_and_missing_file(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: [unclosed\n")
    with pytest.raises(RocketFileError, match="not valid YAML"):
        load_rocket(bad)
    with pytest.raises(FileNotFoundError, match="rocket file not found"):
        load_rocket(tmp_path / "none.yaml")


def test_british_spellings_are_accepted():
    doc = minimal()
    doc["components"][0]["nose_cone"]["material"] = "Fibreglass"
    doc["components"][1]["body_tube"]["material"] = "aluminium"
    r = rocket_from_dict(doc)
    assert r.parts[1].component.density == 2700.0
