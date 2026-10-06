from __future__ import annotations

import hashlib
import math
import shutil
from pathlib import Path

import pytest

from pummelmcp.pmh import (
    COMPONENT_SCHEMAS,
    ComponentPropertyNotFoundError,
    ComponentPropertyReadOnlyError,
    ComponentWriteError,
    PMHScene,
    UnknownComponentSchemaError,
    Vector3,
    WriterValidationError,
    read_pmh,
)
from pummelmcp.service import get_component_property_details, get_components_details


ACTION_PROPERTIES = (
    "OnHitActions",
    "OnEnterActions",
    "OnExitActions",
    "OnStayActions",
)


@pytest.fixture
def trigger_scene(tmp_path: Path, main_scene_path: Path) -> Path:
    target = tmp_path / "trigger.scene"
    shutil.copyfile(main_scene_path, target)
    return target


def _trigger_target(path: Path) -> tuple[str, str]:
    scene = read_pmh(path)
    for obj in scene.walk():
        for component in obj.components:
            if component.type_name == "ModTrigger":
                return obj.guid, component.guid
    raise AssertionError("real fixture contains no ModTrigger")


def _component(path: Path, object_guid: str, component_guid: str):
    return PMHScene.load(path).get_component(object_guid, component_guid)


def _action_payloads(path: Path, object_guid: str, component_guid: str) -> dict[str, bytes]:
    component = _component(path, object_guid, component_guid)
    return {name: component.get_field(name).raw for name in ACTION_PROPERTIES}


def test_modtrigger_schema_registered() -> None:
    schema = COMPONENT_SCHEMAS["ModTrigger"]
    assert schema["TriggerShape"].schema_name == "Int32LE"
    assert schema["Size"].schema_name == "Vector3Float32"
    assert schema["Radius"].schema_name == "Float32LE"
    assert schema["TriggerOnHit"].schema_name == "Bool1"


def test_modtrigger_action_properties_read_only() -> None:
    schema = COMPONENT_SCHEMAS["ModTrigger"]
    for name in ACTION_PROPERTIES:
        assert schema[name].writable is False
        assert schema[name].schema_name == "ManagedReferencePayload"


def test_read_all_trigger_fixed_fields(main_scene_path: Path) -> None:
    object_guid, component_guid = _trigger_target(main_scene_path)
    expected_schemas = {
        "TriggerShape": "Int32LE",
        "Size": "Vector3Float32",
        "Center": "Vector3Float32",
        "Radius": "Float32LE",
        "Height": "Float32LE",
        "TriggerOnHit": "Bool1",
        "TriggerOnEnter": "Bool1",
        "TriggerOnExit": "Bool1",
        "TriggerOnStay": "Bool1",
        "StayTriggerInterval": "Float32LE",
        "DisableAfterTriggered": "Bool1",
        "OneUsePerPlayer": "Bool1",
    }
    for property_name, schema_name in expected_schemas.items():
        result = get_component_property_details(
            main_scene_path, object_guid, component_guid, property_name
        )
        assert result["schema"] == schema_name
        assert result["writable"] is True
        assert result["summarized"] is False
    shape = get_component_property_details(
        main_scene_path, object_guid, component_guid, "TriggerShape"
    )
    assert shape["value"] == {"raw_value": 1, "name": None}
    size = get_component_property_details(
        main_scene_path, object_guid, component_guid, "Size"
    )
    assert size["value"] == {"x": 1.0, "y": 1.0, "z": 1.0}


def test_action_payloads_are_summarized(main_scene_path: Path) -> None:
    object_guid, component_guid = _trigger_target(main_scene_path)
    details = get_components_details(main_scene_path, object_guid)
    trigger = next(item for item in details["components"] if item["guid"] == component_guid)
    for name in ACTION_PROPERTIES:
        action = next(item for item in trigger["properties"] if item["name"] == name)
        assert action["schema"] == "ManagedReferencePayload"
        assert action["writable"] is False
        assert action["summarized"] is True
        assert action["decoded"] is None
        assert "raw" not in action


@pytest.mark.parametrize(
    ("property_name", "value", "member", "expected"),
    [
        ("TriggerShape", 2, "value", 2),
        ("Radius", 2.5, "value", 2.5),
        ("Height", 3.5, "value", 3.5),
        ("Size", {"x": 10.0}, "x", 10.0),
        ("Center", {"z": 1.25}, "z", 1.25),
        ("TriggerOnHit", False, "value", False),
        ("TriggerOnEnter", False, "value", False),
        ("TriggerOnExit", True, "value", True),
        ("TriggerOnStay", True, "value", True),
        ("StayTriggerInterval", 1.25, "value", 1.25),
        ("DisableAfterTriggered", True, "value", True),
        ("OneUsePerPlayer", True, "value", True),
    ],
)
def test_write_trigger_property_is_minimal_and_preserves_actions(
    trigger_scene: Path,
    property_name: str,
    value,
    member: str,
    expected,
) -> None:
    object_guid, component_guid = _trigger_target(trigger_scene)
    before_scene = read_pmh(trigger_scene)
    before_component = _component(trigger_scene, object_guid, component_guid)
    before_field = before_component.get_field(property_name)
    before_actions = _action_payloads(trigger_scene, object_guid, component_guid)
    before_bytes = trigger_scene.read_bytes()

    report = PMHScene.load(trigger_scene).set_component_property(
        object_guid, component_guid, property_name, value, backup=False
    )

    after_scene = read_pmh(trigger_scene)
    after_component = _component(trigger_scene, object_guid, component_guid)
    after_field = after_component.get_field(property_name)
    change = report.changes[member]
    differences = {
        index
        for index, (before, after) in enumerate(
            zip(before_bytes, trigger_scene.read_bytes(), strict=True)
        )
        if before != after
    }
    allowed = set(range(change.source_start, change.source_start + change.source_length))

    assert differences
    assert differences <= allowed
    assert before_field.source_span is not None
    assert before_field.source_span.start <= change.source_start < before_field.source_span.end
    if isinstance(after_field.value, Vector3):
        assert getattr(after_field.value, member) == expected
    else:
        assert after_field.value == expected
    assert _action_payloads(trigger_scene, object_guid, component_guid) == before_actions
    assert report.guid == object_guid
    assert report.component_guid == component_guid
    assert report.validation.passed
    assert all(
        report.validation.checks[f"{name}_unchanged"] for name in ACTION_PROPERTIES
    )
    assert after_scene.root_count == before_scene.root_count
    assert after_scene.object_count == before_scene.object_count
    assert after_scene.component_count == before_scene.component_count
    assert after_scene.fully_consumed


def test_trigger_dry_run_preserves_file_hash_and_creates_no_backup(
    trigger_scene: Path,
) -> None:
    object_guid, component_guid = _trigger_target(trigger_scene)
    before = hashlib.sha256(trigger_scene.read_bytes()).hexdigest()
    report = PMHScene.load(trigger_scene).set_component_property(
        object_guid, component_guid, "Radius", 2.5, dry_run=True
    )
    after = hashlib.sha256(trigger_scene.read_bytes()).hexdigest()
    assert before == after
    assert report.dry_run is True
    assert report.backup_path is None
    assert report.validation.passed


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_trigger_invalid_float_rejected(trigger_scene: Path, value: float) -> None:
    object_guid, component_guid = _trigger_target(trigger_scene)
    with pytest.raises(ComponentWriteError, match="finite number"):
        PMHScene.load(trigger_scene).set_component_property(
            object_guid, component_guid, "Radius", value, dry_run=True
        )


def test_trigger_bool_is_strict_and_vector_member_is_validated(
    trigger_scene: Path,
) -> None:
    object_guid, component_guid = _trigger_target(trigger_scene)
    loaded = PMHScene.load(trigger_scene)
    with pytest.raises(ComponentWriteError, match="requires a boolean"):
        loaded.set_component_property(
            object_guid, component_guid, "TriggerOnHit", 1, dry_run=True
        )
    with pytest.raises(ComponentWriteError, match="unknown members"):
        loaded.set_component_property(
            object_guid, component_guid, "Size", {"w": 2.0}, dry_run=True
        )


@pytest.mark.parametrize("property_name", ACTION_PROPERTIES)
def test_trigger_readonly_action_rejected(
    trigger_scene: Path, property_name: str
) -> None:
    object_guid, component_guid = _trigger_target(trigger_scene)
    with pytest.raises(
        ComponentPropertyReadOnlyError,
        match="Action mutation is not supported in Trigger Writer v0.3",
    ):
        PMHScene.load(trigger_scene).set_component_property(
            object_guid, component_guid, property_name, {}, dry_run=True
        )


def test_modtrigger_unknown_property_rejected(trigger_scene: Path) -> None:
    object_guid, component_guid = _trigger_target(trigger_scene)
    with pytest.raises(ComponentPropertyNotFoundError):
        PMHScene.load(trigger_scene).set_component_property(
            object_guid, component_guid, "UnknownProperty", 1, dry_run=True
        )


def test_unknown_component_schema_has_specific_error(trigger_scene: Path) -> None:
    object_guid, _ = _trigger_target(trigger_scene)
    transform = PMHScene.load(trigger_scene).get_component(object_guid, "ModTransform")
    with pytest.raises(UnknownComponentSchemaError):
        PMHScene.load(trigger_scene).set_component_property(
            object_guid, transform.guid, "position", {"x": 1.0}, dry_run=True
        )


def test_trigger_writer_validation_failure_keeps_source(
    trigger_scene: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    object_guid, component_guid = _trigger_target(trigger_scene)
    before = trigger_scene.read_bytes()
    loaded = PMHScene.load(trigger_scene)

    def reject(*_args, **_kwargs):
        raise WriterValidationError("simulated Trigger validation failure")

    monkeypatch.setattr(loaded, "_validate_component", reject)
    with pytest.raises(WriterValidationError, match="Trigger validation failure"):
        loaded.set_component_property(
            object_guid, component_guid, "Radius", 2.5
        )
    assert trigger_scene.read_bytes() == before
    assert not list(trigger_scene.parent.glob("trigger.scene.bak.*"))
