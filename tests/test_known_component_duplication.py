from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from pathlib import Path

import pytest
import anyio

from pummelmcp.mcp_server import create_server
from pummelmcp.pmh import (
    UnsafeDuplicationError,
    build_action_template_catalog,
    build_prefab_reference_template_catalog,
    build_reference_graph,
    duplicate_leaf,
    parse_action_payload,
    plan_leaf_duplication,
    read_pmh,
)


BEFORE_SHA = "2897b7d6bdd19e8a304ce56c56d8224b6baaaec751a7d2c59fd455dc42ab06ea"
AFTER_SHA = "a80c068f4e61ddf566a7523b6eb681b0f7b3a7bbb060b9f7184b6d6d9331aa48"
SOURCE_GUID = "a08ec153-338d-4bc8-9196-0344654c98cf"
OFFICIAL_GUIDS = (
    uuid.UUID("0e11663f-cd1d-49b8-9123-4e99496e2798"),
    uuid.UUID("ab5cfaee-ff93-4914-8e7d-c601fc2076d5"),
    uuid.UUID("4c657968-104f-46e8-ab22-bf6a66ee3510"),
)
SHAPE = ("ModTransform", "ModBoxCollider", "ModTrigger")
EVENTS = ("OnHitActions", "OnEnterActions", "OnExitActions", "OnStayActions")


@pytest.fixture(scope="module")
def oracle_root() -> Path:
    configured = os.environ.get("PUMMELMCP_ORACLE_ROOT")
    if not configured:
        pytest.skip("PUMMELMCP_ORACLE_ROOT is not configured")
    return Path(configured)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_oracle07_is_hash_pinned_fully_parsed_and_read_only(oracle_root: Path) -> None:
    before = oracle_root / "07_button_empty_actions_before.scene"
    after = oracle_root / "07_button_empty_actions_after.scene"
    hashes = (_sha(before), _sha(after))
    assert hashes == (BEFORE_SHA, AFTER_SHA)
    before_scene = read_pmh(before)
    after_scene = read_pmh(after)
    assert before_scene.fully_consumed and after_scene.fully_consumed
    assert (before_scene.object_count, after_scene.object_count) == (50, 51)
    assert (before_scene.component_count, after_scene.component_count) == (93, 96)
    assert (_sha(before), _sha(after)) == hashes


def test_oracle07_proves_exact_shape_identity_payload_and_hierarchy(
    oracle_root: Path,
) -> None:
    before = read_pmh(oracle_root / "07_button_empty_actions_before.scene")
    after = read_pmh(oracle_root / "07_button_empty_actions_after.scene")
    source = before.find_game_object("ButtonLeaf")
    retained, duplicate = sorted(
        after.find_game_objects("ButtonLeaf"), key=lambda item: item.sibling_index
    )
    assert retained.guid == source.guid == SOURCE_GUID
    assert duplicate.guid == str(OFFICIAL_GUIDS[0])
    assert source.parent and retained.parent and duplicate.parent
    assert source.parent.guid == retained.parent.guid == duplicate.parent.guid
    assert [child.guid for child in duplicate.parent.children] == [source.guid, duplicate.guid]
    assert tuple(item.type_name for item in source.components) == SHAPE
    assert tuple(item.type_name for item in duplicate.components) == SHAPE
    assert tuple(item.guid for item in duplicate.components) == tuple(map(str, OFFICIAL_GUIDS))
    for source_component, duplicate_component in zip(
        source.components, duplicate.components, strict=True
    ):
        assert source_component.enabled == duplicate_component.enabled
        assert [(f.name, f.raw) for f in source_component.fields if f.name != "guid"] == [
            (f.name, f.raw) for f in duplicate_component.fields if f.name != "guid"
        ]
        assert duplicate_component.get_field("guid").value == duplicate_component.guid
    trigger = duplicate.get_component("ModTrigger")
    for event in EVENTS:
        parsed = parse_action_payload(trigger.get_field(event).raw, source_property=event)
        assert parsed.fully_consumed and not parsed.actions and not parsed.references
    owned = {duplicate.guid, *(component.guid for component in duplicate.components)}
    graph = build_reference_graph(after)
    assert not any(
        edge.classification.value == "UNKNOWN"
        and (edge.source.guid in owned or edge.target.guid in owned)
        for edge in graph.edges
    )


def test_known_component_clone_plan_has_independent_component_policies(
    oracle_root: Path,
) -> None:
    values = iter(OFFICIAL_GUIDS)
    plan = plan_leaf_duplication(
        oracle_root / "07_button_empty_actions_before.scene",
        SOURCE_GUID,
        guid_factory=lambda: next(values),
    )
    assert plan.safety_class == "SAFE_EMPTY_TRIGGER_BOXCOLLIDER_LEAF_DUPLICATION"
    assert tuple(item.type for item in plan.source.components) == SHAPE
    assert [item.identity_policy for item in plan.source.components] == [
        "GAMEOBJECT_IDENTITY", "INDEPENDENT_UUID", "INDEPENDENT_UUID"
    ]
    assert [item.payload_policy for item in plan.source.components] == [
        "COPY_MODTRANSFORM", "COPY_NON_IDENTITY_EXACTLY",
        "COPY_KNOWN_EMPTY_TRIGGER_PAYLOAD",
    ]
    assert plan.destination.gameobject_guid == plan.destination.transform_guid
    assert len(set(plan.destination.component_guids.values())) == 3
    assert plan.destination.sibling_index == 1
    assert plan.destination.preorder_index == 50
    assert plan.mutations[-1].kind == "INSERT_COMPONENT_PAYLOADS"


def test_known_component_writer_is_byte_exact_with_official_uuid_allocator(
    oracle_root: Path, tmp_path: Path
) -> None:
    target = tmp_path / "MainScene.scene"
    shutil.copyfile(oracle_root / "07_button_empty_actions_before.scene", target)
    values = iter(OFFICIAL_GUIDS)
    report = duplicate_leaf(
        target,
        SOURCE_GUID,
        expected_scene_hash=BEFORE_SHA,
        guid_factory=lambda: next(values),
    )
    assert report.validation.passed
    assert report.components == SHAPE
    assert target.read_bytes() == (
        oracle_root / "07_button_empty_actions_after.scene"
    ).read_bytes()
    assert report.backup_path and report.backup_path.read_bytes() == (
        oracle_root / "07_button_empty_actions_before.scene"
    ).read_bytes()


def test_known_component_writer_allocates_three_unique_uuid4s_and_preserves_source(
    oracle_root: Path, tmp_path: Path
) -> None:
    target = tmp_path / "random.scene"
    shutil.copyfile(oracle_root / "07_button_empty_actions_before.scene", target)
    before = read_pmh(target).find_game_object("ButtonLeaf")
    before_snapshot = [
        (component.guid, [(field.name, field.raw) for field in component.fields])
        for component in before.components
    ]
    report = duplicate_leaf(target, SOURCE_GUID)
    after = read_pmh(target)
    source = next(item for item in after.walk() if item.guid == SOURCE_GUID)
    duplicate = next(item for item in after.walk() if item.guid == report.duplicate_guid)
    assert [
        (component.guid, [(field.name, field.raw) for field in component.fields])
        for component in source.components
    ] == before_snapshot
    new_ids = [component.guid for component in duplicate.components]
    assert duplicate.guid == new_ids[0]
    assert len(set(new_ids)) == 3
    assert all(uuid.UUID(value).version == 4 for value in new_ids)
    assert all(report.validation.checks.values())


def test_populated_trigger_is_rejected_before_clone_plan(
    oracle_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pummelmcp.pmh.duplication_writer as writer

    original = writer.parse_action_payload

    def populated(raw: bytes, *, source_property: str | None = None):
        parsed = original(raw, source_property=source_property)
        if source_property == "OnEnterActions":
            return type("Populated", (), {
                "fully_consumed": True, "actions": (object(),), "references": ()
            })()
        return parsed

    monkeypatch.setattr(writer, "parse_action_payload", populated)
    with pytest.raises(UnsafeDuplicationError, match="UNSAFE_POPULATED_ACTION_GRAPH"):
        plan_leaf_duplication(
            oracle_root / "07_button_empty_actions_before.scene", SOURCE_GUID
        )


def test_malformed_component_payload_identity_is_rejected(
    oracle_root: Path, tmp_path: Path
) -> None:
    target = tmp_path / "invalid-component-identity.scene"
    shutil.copyfile(oracle_root / "07_button_empty_actions_before.scene", target)
    scene = read_pmh(target)
    box = scene.find_game_object("ButtonLeaf").get_component("ModBoxCollider")
    span = box.get_field("guid").source_span
    assert span is not None
    raw = bytearray(target.read_bytes())
    raw[span.start + 1 : span.end] = b"22222222-2222-4222-8222-222222222222"
    target.write_bytes(raw)
    with pytest.raises(UnsafeDuplicationError, match="UNSAFE_INVALID_IDENTITY"):
        plan_leaf_duplication(target, SOURCE_GUID)


def _mcp_call(server, name: str, arguments: dict):
    async def invoke():
        return await server.call_tool(name, arguments)

    return anyio.run(invoke)


def test_mcp_known_leaf_duplicate_move_trigger_edit_and_validate_are_isolated(
    oracle_root: Path, tmp_path: Path
) -> None:
    target = tmp_path / "MainScene.scene"
    shutil.copyfile(oracle_root / "07_button_empty_actions_before.scene", target)
    server = create_server(tmp_path)
    planned = _mcp_call(
        server,
        "plan_gameobject_duplication",
        {"scene_path": str(target), "object": SOURCE_GUID},
    )
    assert not planned.is_error
    assert planned.structured_content["safety_class"] == (
        "SAFE_EMPTY_TRIGGER_BOXCOLLIDER_LEAF_DUPLICATION"
    )
    duplicated = _mcp_call(
        server,
        "duplicate_gameobject",
        {
            "scene_path": str(target),
            "object": SOURCE_GUID,
            "expected_scene_hash": BEFORE_SHA,
        },
    )
    assert not duplicated.is_error
    duplicate_guid = duplicated.structured_content["duplicate"]["guid"]
    duplicate = next(item for item in read_pmh(target).walk() if item.guid == duplicate_guid)
    duplicate_trigger = duplicate.get_component("ModTrigger")
    moved = _mcp_call(
        server,
        "set_transform",
        {"scene_path": str(target), "identifier": duplicate_guid, "position": {"x": 2.0}},
    )
    assert not moved.is_error
    edited = _mcp_call(
        server,
        "set_component_property",
        {
            "scene_path": str(target),
            "object_identifier": duplicate_guid,
            "component_identifier": duplicate_trigger.guid,
            "property_name": "DisableAfterTriggered",
            "value": True,
        },
    )
    assert not edited.is_error
    validated = _mcp_call(server, "validate_scene", {"scene_path": str(target)})
    assert not validated.is_error and validated.structured_content["valid"] is True
    after = read_pmh(target)
    source = next(item for item in after.walk() if item.guid == SOURCE_GUID)
    duplicate = next(item for item in after.walk() if item.guid == duplicate_guid)
    assert source.get_component("ModTransform").get_field("position").value.x != 2.0
    assert duplicate.get_component("ModTransform").get_field("position").value.x == 2.0
    assert source.get_component("ModTrigger").get_field("DisableAfterTriggered").value is False
    assert duplicate.get_component("ModTrigger").get_field("DisableAfterTriggered").value is True
    assert all(
        not parse_action_payload(
            source.get_component("ModTrigger").get_field(event).raw
        ).actions
        for event in EVENTS
    )


def test_mcp_full_button_workflow_uses_read_only_sample_template_in_temp_copy(
    oracle_root: Path, tmp_path: Path
) -> None:
    configured = os.environ.get("PUMMELMCP_SAMPLE_MOD_ROOT")
    if not configured:
        pytest.skip("PUMMELMCP_SAMPLE_MOD_ROOT is not configured")
    sample_mod = Path(configured)
    writable_mod = tmp_path / "sample-mod-copy"
    shutil.copytree(sample_mod, writable_mod)
    template_scene = writable_mod / "Data" / "Template.scene"
    shutil.copyfile(writable_mod / "Data" / "MainScene.scene", template_scene)
    target = writable_mod / "Data" / "MainScene.scene"
    shutil.copyfile(oracle_root / "07_button_empty_actions_before.scene", target)

    templates = [
        item
        for item in build_action_template_catalog(read_pmh(template_scene)).templates
        if item.class_name == "SpawnPrefabAction"
        and item.data.get("m_prefabs")
        and item.data["m_prefabs"][0]["m_asset"].get("assetFolder") == "/Prefabs/"
    ]
    assert len(templates) >= 2
    template = templates[0]
    original_prefab = template.data["m_prefabs"][0]["m_assetGUID"]["serializedGuid"]
    replacement_prefab = next(
        item.data["m_prefabs"][0]["m_assetGUID"]["serializedGuid"]
        for item in templates[1:]
        if item.data["m_prefabs"][0]["m_assetGUID"]["serializedGuid"] != original_prefab
    )

    server = create_server(writable_mod)
    duplicated = _mcp_call(
        server,
        "duplicate_gameobject",
        {
            "scene_path": str(target),
            "object": SOURCE_GUID,
            "expected_scene_hash": BEFORE_SHA,
        },
    )
    assert not duplicated.is_error
    duplicate_guid = duplicated.structured_content["duplicate"]["guid"]
    duplicate = next(item for item in read_pmh(target).walk() if item.guid == duplicate_guid)
    trigger_guid = duplicate.get_component("ModTrigger").guid

    assert not _mcp_call(
        server,
        "set_transform",
        {"scene_path": str(target), "identifier": duplicate_guid, "position": {"x": 2.0}},
    ).is_error
    assert not _mcp_call(
        server,
        "set_component_property",
        {
            "scene_path": str(target),
            "object_identifier": duplicate_guid,
            "component_identifier": trigger_guid,
            "property_name": "DisableAfterTriggered",
            "value": True,
        },
    ).is_error
    added = _mcp_call(
        server,
        "add_action",
        {
            "scene_path": str(target),
            "object_identifier": duplicate_guid,
            "component_identifier": trigger_guid,
            "event_property": "OnEnterActions",
            "action_class": "SpawnPrefabAction",
            "template_sha256": template.managed_reference_sha256,
            "template_scene_path": str(template_scene),
        },
    )
    assert not added.is_error
    rid = added.structured_content["affected_rid"]
    for field_name, value in (
        ("m_spawnAtPosition", True),
        ("m_parentToTarget", False),
        ("m_position", {"x": 1.0, "y": 0.0, "z": 0.0}),
        ("m_rotation", {"x": 0.0, "y": 45.0, "z": 0.0}),
    ):
        result = _mcp_call(
            server,
            "set_action_field",
            {
                "scene_path": str(target),
                "object_identifier": duplicate_guid,
                "component_identifier": trigger_guid,
                "event_property": "OnEnterActions",
                "action_rid": rid,
                "expected_class": "SpawnPrefabAction",
                "field_name": field_name,
                "value": value,
            },
        )
        assert not result.is_error
    replaced = _mcp_call(
        server,
        "replace_prefab_reference",
        {
            "scene_path": str(target),
            "object_identifier": duplicate_guid,
            "component_identifier": trigger_guid,
            "event_property": "OnEnterActions",
            "action_rid": rid,
            "prefab_index": 0,
            "target_prefab_guid": replacement_prefab,
            "expected_current_prefab_guid": original_prefab,
            "template_reference_sha256": next(
                variant.reference_sha256
                for variant in build_prefab_reference_template_catalog(
                    template_scene, writable_mod
                ).get(replacement_prefab).variants
            ),
            "template_scene_path": str(template_scene),
        },
    )
    assert not replaced.is_error
    validated = _mcp_call(server, "validate_scene", {"scene_path": str(target)})
    assert not validated.is_error and validated.structured_content["valid"] is True

    final_scene = read_pmh(target)
    source = next(item for item in final_scene.walk() if item.guid == SOURCE_GUID)
    duplicate = next(item for item in final_scene.walk() if item.guid == duplicate_guid)
    source_trigger = source.get_component("ModTrigger")
    duplicate_trigger = duplicate.get_component("ModTrigger")
    assert all(not parse_action_payload(source_trigger.get_field(event).raw).actions for event in EVENTS)
    parsed = parse_action_payload(duplicate_trigger.get_field("OnEnterActions").raw)
    assert len(parsed.actions) == 1
    action = parsed.actions[0]
    assert action.fields["m_spawnAtPosition"] is True
    assert action.fields["m_parentToTarget"] is False
    assert action.fields["m_position"] == {"x": 1.0, "y": 0.0, "z": 0.0}
    assert action.fields["m_rotation"] == {"x": 0.0, "y": 45.0, "z": 0.0}
    assert action.fields["m_prefabs"][0]["m_assetGUID"]["serializedGuid"] == replacement_prefab
    assert source.get_component("ModTrigger").get_field("DisableAfterTriggered").value is False
    assert duplicate_trigger.get_field("DisableAfterTriggered").value is True
