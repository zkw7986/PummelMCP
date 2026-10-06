from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from pummelmcp.pmh.action_graph import (
    ActionGraph,
    validate_observed_rid_allocation,
)
from pummelmcp.pmh.action_graph_writer import (
    ActionGraphWriter,
    build_action_template_catalog,
)
from pummelmcp.pmh.actions import ACTION_EVENT_PROPERTIES, parse_action_payload
from pummelmcp.pmh.errors import (
    ActionDependencyError,
    ActionPayloadChangedError,
    ActionTemplateError,
)
from pummelmcp.pmh.actions import encode_action_varuint7
from pummelmcp.pmh.reader import read_pmh
from pummelmcp.pmh.writer import PMHScene


def _spawn_target(path: Path) -> tuple[str, str, str, int]:
    for obj in read_pmh(path).walk():
        for component in obj.components:
            if component.type_name != "ModTrigger":
                continue
            for field in component.fields:
                if field.name not in ACTION_EVENT_PROPERTIES:
                    continue
                parsed = parse_action_payload(field.raw)
                for action in parsed.actions:
                    if action.type_info and action.type_info.class_name == "SpawnPrefabAction":
                        return obj.guid, component.guid, field.name, action.rid
    raise AssertionError("fixture has no SpawnPrefabAction")


def _copy(main_scene_path: Path, tmp_path: Path) -> Path:
    target = tmp_path / "MainScene.scene"
    target.write_bytes(main_scene_path.read_bytes())
    return target


def _state(path: Path, target: tuple[str, str, str, int]):
    loaded = PMHScene.load(path)
    obj, component, event, _ = target
    field = loaded.get_component(obj, component).get_field(event)
    return field, parse_action_payload(field.raw, source_property=event)


def test_action_graph_model(main_scene_path: Path) -> None:
    target = _spawn_target(main_scene_path)
    _, parsed = _state(main_scene_path, target)
    graph = ActionGraph.from_action_list(parsed)
    assert graph.ordered_rids == graph.reference_order == (1000,)
    assert graph.node(1000).class_name == "SpawnPrefabAction"


def test_rid_scope_and_allocator(main_scene_path: Path) -> None:
    seen = 0
    for obj in read_pmh(main_scene_path).walk():
        for component in obj.components:
            if component.type_name != "ModTrigger":
                continue
            for field in component.fields:
                if field.name in ACTION_EVENT_PROPERTIES:
                    parsed = parse_action_payload(field.raw)
                    assert validate_observed_rid_allocation(parsed) == 1000 + len(parsed.actions)
                    seen += 1
    assert seen > 10


def test_action_template_catalog(main_scene_path: Path) -> None:
    catalog = build_action_template_catalog(read_pmh(main_scene_path))
    templates = catalog.matching("ModSystem.Logic", "SpawnPrefabAction")
    assert templates
    assert all(item.type_tag == 576 for item in templates)
    assert all(item.managed_reference_sha256 for item in templates)


@pytest.fixture
def added(main_scene_path: Path, tmp_path: Path):
    scene = _copy(main_scene_path, tmp_path)
    target = _spawn_target(scene)
    before_field, before = _state(scene, target)
    report = ActionGraphWriter(scene).add_action(
        target[0], target[1], target[2], "SpawnPrefabAction",
        expected_payload_sha256=hashlib.sha256(before_field.raw).hexdigest(),
    )
    after_field, after = _state(scene, target)
    return scene, target, before_field, before, after_field, after, report


def test_add_spawn_prefab_action(added) -> None:
    assert added[6].affected_rid == 1001
    assert added[6].validation.passed
    assert [item.rid for item in added[5].actions] == [1000, 1001]


def test_add_updates_refids_and_count(added) -> None:
    assert [item.rid for item in added[5].references] == [1000, 1001]
    assert added[6].reference_count_before == 1
    assert added[6].reference_count_after == 2


def test_add_clones_validated_template(added) -> None:
    first, second = added[5].actions
    assert first.fields == second.fields
    assert first.type_info == second.type_info
    assert added[6].template["evidence_status"] == "CONFIRMED"


def test_add_preserves_existing_action_bytes(added) -> None:
    assert added[3].references[0].raw_segment == added[5].references[0].raw_segment


def test_add_at_index(main_scene_path: Path, tmp_path: Path) -> None:
    scene = _copy(main_scene_path, tmp_path)
    target = _spawn_target(scene)
    report = ActionGraphWriter(scene).add_action(*target[:3], "SpawnPrefabAction", insert_index=0)
    _, parsed = _state(scene, target)
    assert report.new_index == 0
    assert [item.rid for item in parsed.actions] == [1001, 1000]
    assert [item.rid for item in parsed.references] == [1000, 1001]


def test_add_first_to_empty_list(main_scene_path: Path, tmp_path: Path) -> None:
    scene = _copy(main_scene_path, tmp_path)
    source = _spawn_target(scene)
    loaded = PMHScene.load(scene)
    template = next(
        item for item in build_action_template_catalog(loaded.scene).templates
        if item.source.component_guid == source[1] and item.source.event_property == source[2]
        and item.source.source_rid == source[3]
    )
    obj = loaded.get_object(source[0])
    component = loaded.get_component(source[0], source[1])
    empty_event = next(
        name for name in ACTION_EVENT_PROPERTIES
        if not parse_action_payload(component.get_field(name).raw).actions
    )
    before_parsed = parse_action_payload(component.get_field(empty_event).raw)
    report = ActionGraphWriter(scene).add_action(
        obj.guid, component.guid, empty_event, "SpawnPrefabAction",
        template_sha256=template.managed_reference_sha256,
    )
    field = PMHScene.load(scene).get_component(obj.guid, component.guid).get_field(empty_event)
    parsed = parse_action_payload(field.raw)
    assert report.affected_rid == 1000
    assert [item.rid for item in parsed.actions] == [1000]
    assert parsed.actions[0].fields == template.data
    before_prefix = next(s.raw for s in before_parsed.segments if s.kind == "root_json_length")
    after_prefix = next(s.raw for s in parsed.segments if s.kind == "root_json_length")
    assert len(before_prefix) == 1
    assert len(after_prefix) == 3  # crosses both 127/128 and 16383/16384


def test_empty_add_requires_unambiguous_template(main_scene_path: Path, tmp_path: Path) -> None:
    scene = _copy(main_scene_path, tmp_path)
    source = _spawn_target(scene)
    loaded = PMHScene.load(scene)
    component = loaded.get_component(source[0], source[1])
    empty_event = next(name for name in ACTION_EVENT_PROPERTIES if not parse_action_payload(component.get_field(name).raw).actions)
    with pytest.raises(ActionTemplateError):
        ActionGraphWriter(scene).add_action(source[0], source[1], empty_event, "SpawnPrefabAction")


def test_add_stale_hash_rejected(main_scene_path: Path, tmp_path: Path) -> None:
    scene = _copy(main_scene_path, tmp_path)
    target = _spawn_target(scene)
    with pytest.raises(ActionPayloadChangedError):
        ActionGraphWriter(scene).add_action(
            *target[:3], "SpawnPrefabAction", expected_payload_sha256="0" * 64
        )


@pytest.mark.parametrize("operation", ["delete", "move"])
def test_all_graph_mutations_reject_stale_hash(main_scene_path: Path, tmp_path: Path, operation: str) -> None:
    scene = _copy(main_scene_path, tmp_path)
    target = _spawn_target(scene)
    writer = ActionGraphWriter(scene)
    with pytest.raises(ActionPayloadChangedError):
        if operation == "delete":
            writer.delete_action(*target[:3], target[3], expected_payload_sha256="f" * 64)
        else:
            writer.move_action(*target[:3], target[3], 0, expected_payload_sha256="f" * 64)


def test_move_action(added) -> None:
    scene, target = added[0], added[1]
    before_refs = [item.raw_segment for item in added[5].references]
    field = added[4]
    report = ActionGraphWriter(scene).move_action(
        *target[:3], 1001, 0,
        expected_payload_sha256=hashlib.sha256(field.raw).hexdigest(),
    )
    _, after = _state(scene, target)
    assert [item.rid for item in after.actions] == [1001, 1000]
    assert [item.rid for item in after.references] == [1000, 1001]
    assert [item.raw_segment for item in after.references] == before_refs
    assert report.validation.checks["managed_reference_bytes_unchanged"]


def test_delete_action(added) -> None:
    scene, target = added[0], added[1]
    field = added[4]
    report = ActionGraphWriter(scene).delete_action(
        *target[:3], 1001,
        expected_payload_sha256=hashlib.sha256(field.raw).hexdigest(),
    )
    _, after = _state(scene, target)
    assert [item.rid for item in after.actions] == [1000]
    assert [item.rid for item in after.references] == [1000]
    assert report.validation.checks["deleted_rid_absent"]


def test_round_trip_add_move_delete_is_byte_identical(main_scene_path: Path, tmp_path: Path) -> None:
    scene = _copy(main_scene_path, tmp_path)
    original = scene.read_bytes()
    target = _spawn_target(scene)
    ActionGraphWriter(scene).add_action(*target[:3], "SpawnPrefabAction", backup=False)
    field, _ = _state(scene, target)
    ActionGraphWriter(scene).move_action(*target[:3], 1001, 0, expected_payload_sha256=hashlib.sha256(field.raw).hexdigest(), backup=False)
    field, _ = _state(scene, target)
    ActionGraphWriter(scene).delete_action(*target[:3], 1001, expected_payload_sha256=hashlib.sha256(field.raw).hexdigest(), backup=False)
    assert scene.read_bytes() == original


def test_graph_dry_run_does_not_write(main_scene_path: Path, tmp_path: Path) -> None:
    scene = _copy(main_scene_path, tmp_path)
    before = scene.read_bytes()
    target = _spawn_target(scene)
    report = ActionGraphWriter(scene).add_action(*target[:3], "SpawnPrefabAction", dry_run=True)
    assert report.validation.passed
    assert report.backup_path is None
    assert scene.read_bytes() == before


def test_delete_single_action_to_empty(main_scene_path: Path, tmp_path: Path) -> None:
    scene = _copy(main_scene_path, tmp_path)
    target = _spawn_target(scene)
    report = ActionGraphWriter(scene).delete_action(*target[:3], target[3])
    _, parsed = _state(scene, target)
    assert parsed.actions == ()
    assert parsed.references == ()
    assert report.action_count_after == report.reference_count_after == 0
    assert len(next(s.raw for s in parsed.segments if s.kind == "root_json_length")) == 1


def test_creation_stops_for_unconfirmed_rid_pattern(main_scene_path: Path, tmp_path: Path) -> None:
    scene = _copy(main_scene_path, tmp_path)
    target = _spawn_target(scene)
    raw = scene.read_bytes()
    field, parsed = _state(scene, target)
    root = parsed.root_segment.raw.replace(b'"rid": 1000', b'"rid": 2000')
    payload = (
        parsed.raw_payload[:2]
        + bytes([len(root) & 0x7f | 0x80, len(root) >> 7 & 0x7f | 0x80, len(root) >> 14])
        + root
        + parsed.raw_payload[parsed.root_segment.source_span.end:]
    )
    start = field.source_span.start
    scene.write_bytes(raw[:start-4] + len(payload).to_bytes(4, "little") + payload + raw[field.source_span.end:])
    with pytest.raises(Exception, match="confirmed property-local contiguous"):
        ActionGraphWriter(scene).add_action(*target[:3], "SpawnPrefabAction")


def _three_actions(main_scene_path: Path, tmp_path: Path):
    scene = _copy(main_scene_path, tmp_path)
    target = _spawn_target(scene)
    ActionGraphWriter(scene).add_action(*target[:3], "SpawnPrefabAction", backup=False)
    ActionGraphWriter(scene).add_action(*target[:3], "SpawnPrefabAction", backup=False)
    return scene, target


@pytest.mark.parametrize("rid,index", [(1000, 0), (1001, 1), (1002, 2)])
def test_delete_first_middle_last(main_scene_path: Path, tmp_path: Path, rid: int, index: int) -> None:
    scene, target = _three_actions(main_scene_path, tmp_path)
    before_field, before = _state(scene, target)
    survivor_bytes = {ref.rid: ref.raw_segment for ref in before.references if ref.rid != rid}
    report = ActionGraphWriter(scene).delete_action(*target[:3], rid, backup=False)
    _, after = _state(scene, target)
    assert report.old_index == index
    assert rid not in [item.rid for item in after.actions]
    assert {ref.rid: ref.raw_segment for ref in after.references} == survivor_bytes


def test_move_first_to_last(main_scene_path: Path, tmp_path: Path) -> None:
    scene, target = _three_actions(main_scene_path, tmp_path)
    _, before = _state(scene, target)
    before_refs = [ref.raw_segment for ref in before.references]
    ActionGraphWriter(scene).move_action(*target[:3], 1000, 2, backup=False)
    _, after = _state(scene, target)
    assert [item.rid for item in after.actions] == [1001, 1002, 1000]
    assert [ref.raw_segment for ref in after.references] == before_refs


def test_add_after_reorder_keeps_sequential_refids(main_scene_path: Path, tmp_path: Path) -> None:
    scene = _copy(main_scene_path, tmp_path)
    target = _spawn_target(scene)
    ActionGraphWriter(scene).add_action(*target[:3], "SpawnPrefabAction", backup=False)
    ActionGraphWriter(scene).move_action(*target[:3], 1001, 0, backup=False)
    report = ActionGraphWriter(scene).add_action(*target[:3], "SpawnPrefabAction", backup=False)
    _, after = _state(scene, target)
    assert report.affected_rid == 1002
    assert [item.rid for item in after.actions] == [1001, 1000, 1002]
    assert [item.rid for item in after.references] == [1000, 1001, 1002]


def test_graph_writer_validates_unrelated_root_bytes(added) -> None:
    assert added[6].validation.checks["root_unrelated_bytes_unchanged"] is True


def test_unknown_action_creation_rejected(main_scene_path: Path, tmp_path: Path) -> None:
    scene = _copy(main_scene_path, tmp_path)
    target = _spawn_target(scene)
    with pytest.raises(ActionTemplateError, match="no Phase 3 creation schema"):
        ActionGraphWriter(scene).add_action(*target[:3], "WaitAction")


def test_delete_rejects_known_incoming_rid(main_scene_path: Path, tmp_path: Path) -> None:
    scene = _copy(main_scene_path, tmp_path)
    target = _spawn_target(scene)
    field, _ = _state(scene, target)
    data = [
        {"m_type": 64, "m_version": 0, "m_targets": [], "m_targetFlags": 1},
        {"m_type": 900, "m_version": 0, "target": {"rid": 1000}},
    ]
    refids = [
        {
            "rid": 1000 + index,
            "type": {"class": f"Test{index}Action", "ns": "ModSystem.Logic", "asm": "Assembly-CSharp"},
            "data": item,
        }
        for index, item in enumerate(data)
    ]
    root = {
        "m_type": 0,
        "m_actions": [{"rid": 1000}, {"rid": 1001}],
        "references": {"version": 2, "RefIds": refids},
    }
    root_raw = json.dumps(root, indent=4).encode()
    tails = []
    for item in data:
        raw_item = json.dumps(item, indent=4).encode()
        tails.append(int(item["m_type"]).to_bytes(2, "little") + encode_action_varuint7(len(raw_item)) + raw_item)
    payload = b"\0\0" + encode_action_varuint7(len(root_raw)) + root_raw + (2).to_bytes(2, "little") + b"".join(tails)
    original = scene.read_bytes()
    start = field.source_span.start
    scene.write_bytes(original[:start-4] + len(payload).to_bytes(4, "little") + payload + original[field.source_span.end:])
    with pytest.raises(ActionDependencyError, match="incoming"):
        ActionGraphWriter(scene).delete_action(*target[:3], 1000)
