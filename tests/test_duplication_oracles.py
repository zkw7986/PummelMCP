from __future__ import annotations

import hashlib
import os
from pathlib import Path
from uuid import UUID

import pytest

from pummelmcp.pmh import (
    ReferenceClassification,
    analyze_duplication_oracle,
    build_reference_graph,
    parse_action_payload,
    read_pmh,
)
from pummelmcp.service import analyze_duplication_safety_details


EXPECTED_SHA256 = {
    "01_empty_before.scene": "a7f47e3741400be48ab85c2f74caacd6188cbb9381bc030ac200db7c3ad64b24",
    "01_empty_  after.scene": "f4aebadf56b7e91c8ec641ea41612063a548fa249cf140fa5b0fe31a248b116c",
    "03_trigger_before.scene": "3c0223bc35b3c6a158732be55b758beb0d7a12ddf083e9a9e83b22df9e893802",
    "03_trigger_after.scene": "bee51a74c6654b485cbc97607ecb09342c8084142cdf7e8413f6a7971eb99aeb",
    "04_middle_sibling before.scene": "8212c182f7e8f1a3f97d86ba0cc2ca6185eecbdfcfd7f7d6dad38c360b1d9cc4",
    "04_middle_sibling after.scene": "8649704b376c43d6f4df35951000c138bf586d922902ea1915599f07d4fef05a",
    "05_self_component_before.scene": "a394b6b00434778f0a98941cd7000b7b820b85b8127917a530263b7c1fe5628a",
    "05_self_component_after.scene": "04a7cced260cf77d7550a5d08066ef322f692925799ff490090ff301f8ff105d",
    "06_external_component_before.scene": "7af1b95b7941bedf3dfdff94b05ba7113be3ad21e8191de7b49ef3ad8cfeaead",
    "06_external_component_after.scene": "148dcd559764c626008791f43161577081e7006f6105ca4e945178dd1c3f4427",
}


@pytest.fixture(scope="module")
def oracle_root() -> Path:
    configured = os.environ.get("PUMMELMCP_ORACLE_ROOT")
    if not configured:
        pytest.skip("PUMMELMCP_ORACLE_ROOT is not configured")
    root = Path(configured)
    if not root.is_dir():
        pytest.skip("configured Oracle directory is unavailable")
    return root


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pair(oracle_root: Path, before: str, after: str, source: str):
    return analyze_duplication_oracle(
        oracle_root / before, oracle_root / after, source_name=source
    )


def _duplicate(before, after, name: str):
    source = before.find_game_object(name)
    return source, next(
        item for item in after.find_game_objects(name) if item.guid != source.guid
    )


def _assert_uuid4(value: str) -> None:
    parsed = UUID(value)
    assert len(value) == 36
    assert value == value.lower()
    assert parsed.version == 4
    assert parsed.variant == "specified in RFC 4122"


def test_replacement_oracles_are_hash_pinned_fully_parsed_and_read_only(
    oracle_root: Path,
) -> None:
    before_hashes = {name: _sha(oracle_root / name) for name in EXPECTED_SHA256}
    assert before_hashes == EXPECTED_SHA256
    for name in EXPECTED_SHA256:
        assert read_pmh(oracle_root / name).fully_consumed
    assert {name: _sha(oracle_root / name) for name in EXPECTED_SHA256} == before_hashes


def test_empty_oracle_has_exactly_one_complete_duplicate(oracle_root: Path) -> None:
    report = _pair(
        oracle_root, "01_empty_before.scene", "01_empty_  after.scene", "EmptyLeaf"
    )
    assert report.before_counts == (4, 35, 66)
    assert report.after_counts == (5, 36, 67)
    assert report.single_object_duplicate_candidate
    assert len(report.added_object_guids) == 1
    assert len(report.added_component_guids) == 1
    assert not report.removed_object_guids
    assert not report.removed_component_guids


def test_empty_duplicate_preserves_source_and_copies_fields(oracle_root: Path) -> None:
    before = read_pmh(oracle_root / "01_empty_before.scene")
    after = read_pmh(oracle_root / "01_empty_  after.scene")
    source, duplicate = _duplicate(before, after, "EmptyLeaf")
    retained = next(
        item for item in after.find_game_objects("EmptyLeaf") if item.guid == source.guid
    )
    assert (retained.name, retained.active, retained.layer, retained.tag) == (
        source.name, source.active, source.layer, source.tag
    )
    assert [item.raw for item in retained.components[0].fields] == [
        item.raw for item in source.components[0].fields
    ]
    assert duplicate.guid != source.guid
    assert duplicate.guid == duplicate.get_component("ModTransform").guid
    _assert_uuid4(duplicate.guid)
    assert (duplicate.name, duplicate.active, duplicate.layer, duplicate.tag) == (
        source.name, source.active, source.layer, source.tag
    )
    assert [item.raw for item in duplicate.components[0].fields[:-1]] == [
        item.raw for item in source.components[0].fields[:-1]
    ]


def test_trigger_oracle_maps_every_component_to_fresh_identity(
    oracle_root: Path,
) -> None:
    report = _pair(
        oracle_root, "03_trigger_before.scene", "03_trigger_after.scene", "TriggerLeaf"
    )
    assert report.before_counts == (6, 37, 70)
    assert report.after_counts == (7, 38, 73)
    assert report.single_object_duplicate_candidate
    assert len(report.added_component_guids) == 3
    source, duplicate = _duplicate(
        read_pmh(report.before_path), read_pmh(report.after_path), "TriggerLeaf"
    )
    expected_types = ["ModTransform", "ModTrigger", "ModMeshCollider"]
    assert [item.type_name for item in source.components] == expected_types
    assert [item.type_name for item in duplicate.components] == expected_types
    for source_component, duplicate_component in zip(
        source.components, duplicate.components, strict=True
    ):
        assert duplicate_component.guid != source_component.guid
        _assert_uuid4(duplicate_component.guid)
        if duplicate_component.type_name == "ModTransform":
            assert duplicate_component.guid == duplicate.guid
        else:
            assert duplicate_component.guid != duplicate.guid


def test_trigger_properties_and_empty_action_graphs_are_byte_equivalent(
    oracle_root: Path,
) -> None:
    source, duplicate = _duplicate(
        read_pmh(oracle_root / "03_trigger_before.scene"),
        read_pmh(oracle_root / "03_trigger_after.scene"),
        "TriggerLeaf",
    )
    for source_component, duplicate_component in zip(
        source.components, duplicate.components, strict=True
    ):
        assert source_component.enabled == duplicate_component.enabled
        assert [(f.name, f.raw) for f in source_component.fields if f.name != "guid"] == [
            (f.name, f.raw) for f in duplicate_component.fields if f.name != "guid"
        ]
    source_trigger = source.get_component("ModTrigger")
    duplicate_trigger = duplicate.get_component("ModTrigger")
    for name in ("OnHitActions", "OnEnterActions", "OnExitActions", "OnStayActions"):
        source_raw = source_trigger.get_field(name).raw
        duplicate_raw = duplicate_trigger.get_field(name).raw
        assert source_raw == duplicate_raw
        parsed = parse_action_payload(duplicate_raw, source_property=name)
        assert parsed.fully_consumed and not parsed.actions and not parsed.references


def test_middle_sibling_oracle_proves_append_without_shift(oracle_root: Path) -> None:
    report = _pair(
        oracle_root,
        "04_middle_sibling before.scene",
        "04_middle_sibling after.scene",
        "ChildLeaf",
    )
    assert report.before_counts == (8, 42, 77)
    assert report.after_counts == (8, 43, 78)
    assert report.single_object_duplicate_candidate
    before = read_pmh(report.before_path)
    after = read_pmh(report.after_path)
    source, duplicate = _duplicate(before, after, "ChildLeaf")
    before_following = before.find_game_object("AfterSibling")
    after_following = after.find_game_object("AfterSibling")
    assert source.parent is not None and duplicate.parent is not None
    assert duplicate.parent.guid == source.parent.guid
    assert (source.sibling_index, source.preorder_index) == (1, 40)
    assert (duplicate.sibling_index, duplicate.preorder_index) == (3, 42)
    assert (before_following.sibling_index, before_following.preorder_index) == (2, 41)
    assert (after_following.sibling_index, after_following.preorder_index) == (2, 41)
    assert [item.name for item in duplicate.parent.children] == [
        "BeforeSibling", "ChildLeaf", "AfterSibling", "ChildLeaf"
    ]


def test_middle_sibling_source_bytes_are_preserved_and_parent_grows(
    oracle_root: Path,
) -> None:
    before_path = oracle_root / "04_middle_sibling before.scene"
    after_path = oracle_root / "04_middle_sibling after.scene"
    before_scene = read_pmh(before_path)
    after_scene = read_pmh(after_path)
    source = before_scene.find_game_object("ChildLeaf")
    retained = next(
        item for item in after_scene.find_game_objects("ChildLeaf") if item.guid == source.guid
    )
    assert source.hierarchy_span is not None and retained.hierarchy_span is not None
    assert before_path.read_bytes()[source.hierarchy_span.start:source.hierarchy_span.end] == after_path.read_bytes()[retained.hierarchy_span.start:retained.hierarchy_span.end]
    assert source.parent is not None and retained.parent is not None
    assert len(source.parent.children) == 3
    assert len(retained.parent.children) == 4


def test_editor_save_noise_is_classified_and_filtered(oracle_root: Path) -> None:
    for before_name, after_name, source, expected_noise in (
        ("01_empty_before.scene", "01_empty_  after.scene", "EmptyLeaf", 8),
        ("03_trigger_before.scene", "03_trigger_after.scene", "TriggerLeaf", 8),
        ("04_middle_sibling before.scene", "04_middle_sibling after.scene", "ChildLeaf", 0),
    ):
        report = _pair(oracle_root, before_name, after_name, source)
        assert len(report.known_editor_save_noise) == expected_noise
        assert all(
            item.classification == "KNOWN_EDITOR_SAVE_NOISE"
            for item in report.known_editor_save_noise
        )
        assert all(
            item.classification == "SEMANTIC_CHANGE"
            for item in report.semantic_existing_object_changes
        )


def test_replacement_oracles_contain_no_reference_remapping_sample(
    oracle_root: Path,
) -> None:
    scene = read_pmh(oracle_root / "03_trigger_after.scene")
    obj = next(
        item for item in scene.find_game_objects("TriggerLeaf") if item.preorder_index == 37
    )
    graph = build_reference_graph(scene)
    owned = {obj.guid} | {item.guid for item in obj.components}
    edges = [item for item in graph.edges if item.source.guid in owned]
    assert {item.classification for item in edges} <= {
        ReferenceClassification.IDENTITY,
        ReferenceClassification.INTERNAL_COMPONENT_REFERENCE,
    }
    assert any("ModMeshCollider" in warning for warning in graph.warnings)


def test_duplication_safety_approves_only_transform_leaf_subset(
    oracle_root: Path,
) -> None:
    for filename, name, expected in (
        ("01_empty_before.scene", "EmptyLeaf", "UNSAFE"),
        ("03_trigger_before.scene", "TriggerLeaf", "UNSAFE"),
        (
            "04_middle_sibling before.scene",
            "ChildLeaf",
            "SAFE_TRANSFORM_ONLY_LEAF_DUPLICATION",
        ),
    ):
        path = oracle_root / filename
        result = analyze_duplication_safety_details(
            path, read_pmh(path).find_game_object(name).guid
        )
        assert result["duplication_status"] == expected
        assert result["writer_available"] is (
            expected == "SAFE_TRANSFORM_ONLY_LEAF_DUPLICATION"
        )
