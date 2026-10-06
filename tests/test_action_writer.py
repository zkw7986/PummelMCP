from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from pummelmcp.pmh import (
    ActionClassMismatchError,
    ActionPayloadChangedError,
    ActionRidNotFoundError,
    ActionFieldWriter,
    ReadOnlyActionFieldError,
    UnknownActionFieldError,
    UnknownActionSchemaError,
    encode_action_varuint7,
    parse_action_payload,
    parse_json_spans,
    read_pmh,
)
from pummelmcp.pmh.action_writer import _reframe_reference_json


ACTION_PROPERTIES = (
    "OnHitActions",
    "OnEnterActions",
    "OnExitActions",
    "OnStayActions",
)


@pytest.fixture
def action_scene(tmp_path: Path, main_scene_path: Path) -> Path:
    target = tmp_path / "action-writer.scene"
    shutil.copyfile(main_scene_path, target)
    return target


def _spawn_target(path: Path) -> tuple[str, str]:
    for obj in read_pmh(path).walk():
        for component in obj.components:
            if component.type_name != "ModTrigger":
                continue
            parsed = parse_action_payload(component.get_field("OnHitActions").raw)
            if any(
                action.type_info is not None
                and action.type_info.class_name == "SpawnPrefabAction"
                for action in parsed.actions
            ):
                return obj.guid, component.guid
    raise AssertionError("fixture has no SpawnPrefabAction")


def _action_state(path: Path):
    object_guid, component_guid = _spawn_target(path)
    scene = read_pmh(path)
    obj = next(item for item in scene.walk() if item.guid == object_guid)
    component = next(item for item in obj.components if item.guid == component_guid)
    field = component.get_field("OnHitActions")
    parsed = parse_action_payload(field.raw)
    action = next(item for item in parsed.actions if item.type_info.class_name == "SpawnPrefabAction")
    return obj, component, field, parsed, action


def _write(path: Path, field_name: str, value, **kwargs):
    object_guid, component_guid = _spawn_target(path)
    return ActionFieldWriter(path).set_action_field(
        object_guid,
        component_guid,
        "OnHitActions",
        1000,
        "SpawnPrefabAction",
        field_name,
        value,
        **kwargs,
    )


def test_set_spawn_at_position_false_to_true(action_scene: Path) -> None:
    report = _write(action_scene, "m_spawnAtPosition", True)
    assert report.changes["value"].before is False
    assert report.changes["value"].after is True
    assert report.changes["value"].token_length_before == 5
    assert report.changes["value"].token_length_after == 4
    assert _action_state(action_scene)[4].fields["m_spawnAtPosition"] is True


def test_set_spawn_at_position_true_to_false(action_scene: Path) -> None:
    _write(action_scene, "m_spawnAtPosition", True)
    report = _write(action_scene, "m_spawnAtPosition", False)
    assert report.changes["value"].token_length_before == 4
    assert report.changes["value"].token_length_after == 5
    assert _action_state(action_scene)[4].fields["m_spawnAtPosition"] is False


def test_set_parent_to_target(action_scene: Path) -> None:
    report = _write(action_scene, "m_parentToTarget", True)
    assert report.validation.passed
    assert _action_state(action_scene)[4].fields["m_parentToTarget"] is True


def test_set_position_x(action_scene: Path) -> None:
    report = _write(action_scene, "m_position", {"x": 5.0})
    assert report.changes["x"].before == 0.0
    assert _action_state(action_scene)[4].fields["m_position"]["x"] == 5.0


def test_set_position_partial_preserves_yz_bytes(action_scene: Path) -> None:
    before_ref = _action_state(action_scene)[4].resolved_reference.raw_segment
    before_doc = parse_json_spans(before_ref)
    before_y = before_doc.node_at(("m_position", "y"))
    before_z = before_doc.node_at(("m_position", "z"))
    y_raw = before_ref[before_y.span.start : before_y.span.end]
    z_raw = before_ref[before_z.span.start : before_z.span.end]
    _write(action_scene, "m_position", {"x": 5.0})
    after_ref = _action_state(action_scene)[4].resolved_reference.raw_segment
    after_doc = parse_json_spans(after_ref)
    after_y = after_doc.node_at(("m_position", "y"))
    after_z = after_doc.node_at(("m_position", "z"))
    assert after_ref[after_y.span.start : after_y.span.end] == y_raw
    assert after_ref[after_z.span.start : after_z.span.end] == z_raw


def test_set_rotation_z(action_scene: Path) -> None:
    _write(action_scene, "m_rotation", {"z": 45.0})
    assert _action_state(action_scene)[4].fields["m_rotation"]["z"] == 45.0


def test_numeric_length_change(action_scene: Path) -> None:
    report = _write(action_scene, "m_position", {"x": 10.0})
    assert report.changes["x"].token_length_before == 3
    assert report.changes["x"].token_length_after == 4
    assert report.bytes_added_or_removed == 1


def test_segment_length_updated(action_scene: Path) -> None:
    before = _action_state(action_scene)[3]
    report = _write(action_scene, "m_spawnAtPosition", True)
    after = _action_state(action_scene)[3]
    before_length = next(item for item in before.segments if item.kind == "reference_json_length")
    after_length = next(item for item in after.segments if item.kind == "reference_json_length")
    assert after_length.value == before_length.value - 1
    assert report.validation.checks["target_reference_exact_patch"]


def test_property_length_updated(action_scene: Path) -> None:
    report = _write(action_scene, "m_spawnAtPosition", True)
    assert report.new_property_length == report.old_property_length - 1
    assert report.validation.checks["property_length_updated"]


def _synthetic_payload(reference_data: dict, extra_reference: dict | None = None) -> bytes:
    refs = [
        {
            "rid": 1,
            "type": {"class": "SpawnPrefabAction", "ns": "ModSystem.Logic", "asm": "Tests"},
            "data": reference_data,
        }
    ]
    if extra_reference is not None:
        refs.append(
            {
                "rid": 2,
                "type": {"class": "OtherAction", "ns": "Tests", "asm": "Tests"},
                "data": extra_reference,
            }
        )
    root = {"m_type": 0, "m_actions": [{"rid": 1}], "references": {"version": 2, "RefIds": refs}}
    root_raw = json.dumps(root, separators=(",", ":")).encode()
    payload = bytearray(b"\x00\x00" + encode_action_varuint7(len(root_raw)) + root_raw)
    payload.extend(len(refs).to_bytes(2, "little"))
    for ref in refs:
        raw = json.dumps(ref["data"], separators=(",", ":")).encode()
        payload.extend(int(ref["data"]["m_type"]).to_bytes(2, "little"))
        payload.extend(encode_action_varuint7(len(raw)))
        payload.extend(raw)
    return bytes(payload)


def test_varint_length_boundary() -> None:
    base = {"m_type": 576, "m_spawnAtPosition": True, "padding": ""}
    base_length = len(json.dumps(base, separators=(",", ":")).encode())
    base["padding"] = "x" * (127 - base_length)
    payload = _synthetic_payload(base)
    parsed = parse_action_payload(payload)
    before = parsed.references[0].raw_segment
    assert len(before) == 127
    node = parse_json_spans(before).root.member("m_spawnAtPosition")
    after = before[: node.span.start] + b"false" + before[node.span.end :]
    reframed = _reframe_reference_json(parsed, 0, after)
    reparsed = parse_action_payload(reframed)
    prefix = next(item for item in reparsed.segments if item.kind == "reference_json_length")
    assert len(after) == prefix.value == 128
    assert prefix.raw == b"\x80\x01"
    assert reparsed.fully_consumed
    assert encode_action_varuint7(16_383) == b"\xff\x7f"
    assert encode_action_varuint7(16_384) == b"\x80\x80\x01"


def test_root_json_bytes_unchanged(action_scene: Path) -> None:
    report = _write(action_scene, "m_spawnAtPosition", True)
    assert report.root_json_sha256_before == report.root_json_sha256_after
    assert report.validation.checks["root_json_bytes_unchanged"]


def test_m_actions_bytes_unchanged(action_scene: Path) -> None:
    report = _write(action_scene, "m_position", {"x": 5.0})
    assert report.validation.checks["m_actions_bytes_unchanged"]


def test_action_order_unchanged(action_scene: Path) -> None:
    report = _write(action_scene, "m_rotation", {"z": 45.0})
    assert report.validation.checks["action_order_unchanged"]


def test_rids_unchanged(action_scene: Path) -> None:
    report = _write(action_scene, "m_parentToTarget", True)
    assert report.validation.checks["rids_unchanged"]


def test_refids_unchanged(action_scene: Path) -> None:
    report = _write(action_scene, "m_parentToTarget", True)
    assert report.validation.checks["refids_unchanged"]


def test_namespace_class_assembly_unchanged(action_scene: Path) -> None:
    report = _write(action_scene, "m_parentToTarget", True)
    assert report.validation.checks["namespace_class_assembly_unchanged"]


def test_type_tags_unchanged(action_scene: Path) -> None:
    report = _write(action_scene, "m_parentToTarget", True)
    assert report.validation.checks["type_tags_unchanged"]


def test_non_target_reference_hashes_unchanged() -> None:
    first = {"m_type": 576, "m_spawnAtPosition": True}
    second = {"m_type": 99, "value": "untouched"}
    parsed = parse_action_payload(_synthetic_payload(first, second))
    before_second = parsed.references[1].raw_segment
    first_raw = parsed.references[0].raw_segment
    node = parse_json_spans(first_raw).root.member("m_spawnAtPosition")
    patched_first = first_raw[: node.span.start] + b"false" + first_raw[node.span.end :]
    after = parse_action_payload(_reframe_reference_json(parsed, 0, patched_first))
    assert after.references[1].raw_segment == before_second


def test_other_event_payloads_unchanged(action_scene: Path) -> None:
    _, component, _, _, _ = _action_state(action_scene)
    before = {name: component.get_field(name).raw for name in ACTION_PROPERTIES[1:]}
    report = _write(action_scene, "m_position", {"x": 5.0})
    _, component_after, _, _, _ = _action_state(action_scene)
    assert {name: component_after.get_field(name).raw for name in ACTION_PROPERTIES[1:]} == before
    assert report.validation.checks["other_event_payloads_unchanged"]


def test_graph_fingerprint_unchanged(action_scene: Path) -> None:
    report = _write(action_scene, "m_position", {"x": 5.0})
    assert report.graph_fingerprint_before == report.graph_fingerprint_after
    assert report.validation.checks["graph_fingerprint_unchanged"]


def test_dry_run_scene_hash_unchanged(action_scene: Path) -> None:
    before = hashlib.sha256(action_scene.read_bytes()).hexdigest()
    report = _write(action_scene, "m_spawnAtPosition", True, dry_run=True)
    assert report.validation.passed
    assert report.backup_path is None
    assert hashlib.sha256(action_scene.read_bytes()).hexdigest() == before


def test_action_write_creates_recoverable_backup(action_scene: Path) -> None:
    before = action_scene.read_bytes()
    report = _write(action_scene, "m_spawnAtPosition", True)
    assert report.backup_path is not None
    assert report.backup_path.read_bytes() == before
    assert action_scene.read_bytes() != before


def test_stale_payload_hash_rejected(action_scene: Path) -> None:
    hash_a = _action_state(action_scene)[3].sha256
    _write(action_scene, "m_position", {"x": 5.0})
    hash_b = hashlib.sha256(action_scene.read_bytes()).hexdigest()
    with pytest.raises(ActionPayloadChangedError, match="changed after inspection"):
        _write(
            action_scene,
            "m_rotation",
            {"z": 45.0},
            expected_payload_sha256=hash_a,
        )
    assert hashlib.sha256(action_scene.read_bytes()).hexdigest() == hash_b


def test_readonly_m_prefabs_rejected(action_scene: Path) -> None:
    with pytest.raises(ReadOnlyActionFieldError, match="Prefab/reference mutation"):
        _write(action_scene, "m_prefabs", [])


def test_confirmed_enum_field_write_still_rejected(action_scene: Path) -> None:
    scene = read_pmh(action_scene)
    obj = next(item for item in scene.walk() if item.name == "Base Wall Window Low 01 Glass")
    component = obj.get_component("ModTrigger")
    with pytest.raises(
        ReadOnlyActionFieldError,
        match="Enum values are assembly-confirmed; field mutation is not enabled",
    ):
        ActionFieldWriter(action_scene).set_action_field(
            obj.guid,
            component.guid,
            "OnHitActions",
            1000,
            "SpawnEffectAction",
            "m_effectType",
            1,
        )


def test_missing_action_field_rejected(action_scene: Path) -> None:
    with pytest.raises(UnknownActionFieldError, match="not registered"):
        _write(action_scene, "missing", True)


def test_missing_rid_rejected(action_scene: Path) -> None:
    object_guid, component_guid = _spawn_target(action_scene)
    with pytest.raises(ActionRidNotFoundError, match="was not found"):
        ActionFieldWriter(action_scene).set_action_field(
            object_guid,
            component_guid,
            "OnHitActions",
            9999,
            "SpawnPrefabAction",
            "m_spawnAtPosition",
            True,
        )


def test_class_mismatch_rejected(action_scene: Path) -> None:
    with pytest.raises(ActionClassMismatchError, match="not expected"):
        object_guid, component_guid = _spawn_target(action_scene)
        ActionFieldWriter(action_scene).set_action_field(
            object_guid,
            component_guid,
            "OnHitActions",
            1000,
            "KillAction",
            "m_spawnAtPosition",
            True,
        )
