from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from pummelmcp.pmh import (
    REFERENCE_SCHEMAS,
    ReferenceFieldSchema,
    ReferenceClassification,
    analyze_duplication_oracle,
    build_reference_graph,
    inspect_action_entry_references,
    parse_action_payload,
    read_pmh,
)
from pummelmcp.service import analyze_duplication_safety_details


SELF_SOURCE = "095b954f-863d-4514-8561-3ec94e8c9bd2"
SELF_DUPLICATE = "9e6c5c5a-c364-4b69-8c7e-7038568b252d"
EXTERNAL_SOURCE = "2024921f-5822-4c24-a73d-a44188ef4863"
EXTERNAL_DUPLICATE = "200e7643-195d-4e38-9618-3aff31b984f1"
EXTERNAL_TARGET = "b8aa72c2-32b6-481f-bcd7-559415dbb9e0"


@pytest.fixture(scope="module")
def oracle_root() -> Path:
    configured = os.environ.get("PUMMELMCP_ORACLE_ROOT")
    if not configured:
        pytest.skip("PUMMELMCP_ORACLE_ROOT is not configured")
    root = Path(configured)
    if not root.is_dir():
        pytest.skip("configured Oracle directory is unavailable")
    return root


def _action(scene, object_guid: str):
    obj = next(item for item in scene.walk() if item.guid == object_guid)
    trigger = obj.get_component("ModTrigger")
    field = trigger.get_field("OnEnterActions")
    parsed = parse_action_payload(
        field.raw, source_property=field.name, source_span=field.source_span
    )
    assert parsed.fully_consumed
    assert len(parsed.actions) == 1
    return obj, trigger, field, parsed, parsed.actions[0]


def _target(action):
    inspection = inspect_action_entry_references(action)
    reference = next(item for item in inspection.references if item.source_field == "m_targets")
    assert reference.kind == "TransformReferenceList"
    assert reference.semantic_status == "CONFIRMED"
    assert len(reference.items) == 1
    return reference, reference.items[0]


def test_transform_reference_oracles_are_valid_single_duplicates(
    oracle_root: Path,
) -> None:
    self_report = analyze_duplication_oracle(
        oracle_root / "05_self_component_before.scene",
        oracle_root / "05_self_component_after.scene",
        source_name="TriggerLeaf",
        source_guid=SELF_SOURCE,
    )
    external_report = analyze_duplication_oracle(
        oracle_root / "06_external_component_before.scene",
        oracle_root / "06_external_component_after.scene",
        source_name="SourceTrigger",
        source_guid=EXTERNAL_SOURCE,
    )
    assert self_report.single_object_duplicate_candidate
    assert self_report.added_object_guids == (SELF_DUPLICATE,)
    assert external_report.single_object_duplicate_candidate
    assert external_report.added_object_guids == (EXTERNAL_DUPLICATE,)
    assert not self_report.removed_object_guids
    assert not external_report.removed_object_guids


def test_parser_locates_scene_transform_reference_and_exact_span(
    oracle_root: Path,
) -> None:
    path = oracle_root / "05_self_component_before.scene"
    scene = read_pmh(path)
    _, _, field, _, action = _action(scene, SELF_SOURCE)
    reference, item = _target(action)
    assert item.kind == "TransformReference"
    assert item.normalized_value == {
        "guid": SELF_SOURCE,
        "file_id": -71472,
        "path_id": 0,
        "component_type": "ModTransform",
        "shape": "scene_component_reference",
    }
    token_span = item.value_spans["$.m_assetGUID.serializedGuid"]
    raw_segment = action.resolved_reference.raw_segment
    assert raw_segment[token_span.start : token_span.end] == f'"{SELF_SOURCE}"'.encode()
    graph_edge = next(
        edge
        for edge in build_reference_graph(scene).edges
        if edge.reference_kind == "TransformReference"
        and edge.source.guid == scene.find_game_objects("TriggerLeaf")[0].get_component("ModTrigger").guid
    )
    assert graph_edge.source_span is not None
    assert SELF_SOURCE.encode() in path.read_bytes()[graph_edge.source_span.start : graph_edge.source_span.end]
    assert reference.normalized_value["serialization"] == "m_assetGUID + m_asset.m_FileID/m_PathID"


def test_self_transform_reference_is_preserved_to_original_not_remapped(
    oracle_root: Path,
) -> None:
    before = read_pmh(oracle_root / "05_self_component_before.scene")
    after = read_pmh(oracle_root / "05_self_component_after.scene")
    _, _, _, _, before_action = _action(before, SELF_SOURCE)
    _, _, _, _, source_after_action = _action(after, SELF_SOURCE)
    duplicate, _, _, _, duplicate_action = _action(after, SELF_DUPLICATE)
    assert duplicate.get_component("ModTransform").guid == SELF_DUPLICATE
    assert _target(before_action)[1].normalized_value["guid"] == SELF_SOURCE
    assert _target(source_after_action)[1].normalized_value["guid"] == SELF_SOURCE
    assert _target(duplicate_action)[1].normalized_value["guid"] == SELF_SOURCE


def test_external_transform_reference_is_preserved(oracle_root: Path) -> None:
    before = read_pmh(oracle_root / "06_external_component_before.scene")
    after = read_pmh(oracle_root / "06_external_component_after.scene")
    external = next(item for item in before.walk() if item.guid == EXTERNAL_TARGET)
    assert external.get_component("ModTransform").guid == EXTERNAL_TARGET
    for scene, guid in (
        (before, EXTERNAL_SOURCE),
        (after, EXTERNAL_SOURCE),
        (after, EXTERNAL_DUPLICATE),
    ):
        assert _target(_action(scene, guid)[4])[1].normalized_value["guid"] == EXTERNAL_TARGET


def test_action_graph_is_copied_byte_for_byte_in_both_oracles(
    oracle_root: Path,
) -> None:
    for after_name, source_guid, duplicate_guid in (
        ("05_self_component_after.scene", SELF_SOURCE, SELF_DUPLICATE),
        ("06_external_component_after.scene", EXTERNAL_SOURCE, EXTERNAL_DUPLICATE),
    ):
        scene = read_pmh(oracle_root / after_name)
        source = _action(scene, source_guid)
        duplicate = _action(scene, duplicate_guid)
        assert source[2].raw == duplicate[2].raw
        assert [item.rid for item in source[3].actions] == [1000]
        assert [item.rid for item in duplicate[3].actions] == [1000]
        assert [item.rid for item in source[3].references] == [1000]
        assert [item.rid for item in duplicate[3].references] == [1000]
        assert source[4].resolved_reference.raw_segment == duplicate[4].resolved_reference.raw_segment
        assert source[4].fields["m_prefabs"] == duplicate[4].fields["m_prefabs"] == []


def test_reference_graph_classifies_clone_membership_from_resolved_owner(
    oracle_root: Path,
) -> None:
    before = build_reference_graph(oracle_root / "05_self_component_before.scene")
    before_edge = next(
        item
        for item in before.edges
        if item.reference_kind == "TransformReference" and item.target.guid == SELF_SOURCE
    )
    assert before_edge.classification is ReferenceClassification.INTERNAL_COMPONENT_REFERENCE
    after = build_reference_graph(oracle_root / "05_self_component_after.scene")
    duplicate_trigger = next(
        item
        for item in after.components
        if item.owner_gameobject_guid == SELF_DUPLICATE and item.type_name == "ModTrigger"
    )
    duplicate_edge = next(
        item
        for item in after.edges
        if item.reference_kind == "TransformReference"
        and item.source.guid == duplicate_trigger.guid
    )
    assert duplicate_edge.target.guid == SELF_SOURCE
    assert duplicate_edge.classification is ReferenceClassification.EXTERNAL_COMPONENT_REFERENCE
    external_after = build_reference_graph(oracle_root / "06_external_component_after.scene")
    matching = [
        item
        for item in external_after.edges
        if item.reference_kind == "TransformReference"
        and item.target.guid == EXTERNAL_TARGET
    ]
    assert len(matching) == 2
    assert all(
        item.classification is ReferenceClassification.EXTERNAL_COMPONENT_REFERENCE
        for item in matching
    )


def test_reference_oracle_files_remain_unchanged_and_unsafe(oracle_root: Path) -> None:
    names = (
        "05_self_component_before.scene",
        "05_self_component_after.scene",
        "06_external_component_before.scene",
        "06_external_component_after.scene",
    )
    paths = [oracle_root / name for name in names]
    before_hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
    for path, guid in ((paths[0], SELF_SOURCE), (paths[2], EXTERNAL_SOURCE)):
        result = analyze_duplication_safety_details(path, guid)
        assert result["duplication_status"] == "UNSAFE"
        assert result["writer_available"] is False
    assert [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths] == before_hashes


def test_future_scene_reference_kind_fails_closed(
    oracle_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = ("ModSystem.Logic", "SpawnPrefabAction", "m_parentToTarget")
    monkeypatch.setitem(
        REFERENCE_SCHEMAS,
        key,
        ReferenceFieldSchema(
            namespace=key[0],
            class_name=key[1],
            field_name=key[2],
            kind="FutureSceneReference",
            observed_shape="boolean",
            evidence_status="CONFIRMED",
            semantic_status="CONFIRMED",
            writable=False,
        ),
    )
    result = analyze_duplication_safety_details(
        oracle_root / "05_self_component_before.scene", SELF_SOURCE
    )
    assert result["duplication_status"] == "UNSAFE_UNKNOWN_REFERENCE_TYPE"
    assert any(
        "FutureSceneReference" in reason for reason in result["reasons"]
    )
