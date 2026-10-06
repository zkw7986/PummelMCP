from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from pummelmcp.pmh import (
    COMPONENT_SCHEMAS,
    DecodeConfidence,
    analyze_duplication_oracle,
    parse_action_payload,
    read_pmh,
)


ACTIVE_HASHES = {
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


def test_common_component_identity_envelope_is_structural(
    main_scene_path: Path, oracle_root: Path
) -> None:
    for path in (main_scene_path, *(oracle_root / name for name in ACTIVE_HASHES)):
        scene = read_pmh(path)
        assert scene.fully_consumed
        for obj in scene.walk():
            for component in obj.components:
                assert component.identity_span is not None
                assert component.index_span is not None
                guid = component.get_field("guid")
                assert guid.value == component.guid
                assert guid.source_span is not None


def test_modboxcollider_is_value_only_but_has_no_direct_duplication_oracle(
    oracle_root: Path,
) -> None:
    schema = COMPONENT_SCHEMAS["ModBoxCollider"]
    assert set(schema) == {"center", "size", "guid"}
    assert all(item.confidence is DecodeConfidence.CONFIRMED for item in schema.values())
    report = analyze_duplication_oracle(
        oracle_root / "03_trigger_before.scene",
        oracle_root / "03_trigger_after.scene",
        source_name="TriggerLeaf",
    )
    after = read_pmh(report.after_path)
    added = next(obj for obj in after.walk() if obj.guid in report.added_object_guids)
    assert "ModBoxCollider" not in {component.type_name for component in added.components}


def test_empty_trigger_duplication_evidence_is_blocked_by_mesh_component(
    oracle_root: Path,
) -> None:
    report = analyze_duplication_oracle(
        oracle_root / "03_trigger_before.scene",
        oracle_root / "03_trigger_after.scene",
        source_name="TriggerLeaf",
    )
    before = read_pmh(report.before_path)
    after = read_pmh(report.after_path)
    source = before.find_game_object("TriggerLeaf")
    duplicate = next(obj for obj in after.walk() if obj.guid in report.added_object_guids)
    assert [component.type_name for component in source.components] == [
        "ModTransform", "ModTrigger", "ModMeshCollider"
    ]
    assert [component.type_name for component in duplicate.components] == [
        "ModTransform", "ModTrigger", "ModMeshCollider"
    ]
    source_trigger = source.get_component("ModTrigger")
    duplicate_trigger = duplicate.get_component("ModTrigger")
    for name in ("OnHitActions", "OnEnterActions", "OnExitActions", "OnStayActions"):
        source_raw = source_trigger.get_field(name).raw
        assert duplicate_trigger.get_field(name).raw == source_raw
        parsed = parse_action_payload(source_raw, source_property=name)
        assert parsed.fully_consumed and not parsed.actions and not parsed.references
    assert "ModMeshCollider" not in COMPONENT_SCHEMAS


def test_no_clean_known_component_duplicate_exists_in_active_oracles(
    oracle_root: Path,
) -> None:
    pairs = (
        ("01_empty_before.scene", "01_empty_  after.scene", "EmptyLeaf", None),
        ("03_trigger_before.scene", "03_trigger_after.scene", "TriggerLeaf", None),
        ("04_middle_sibling before.scene", "04_middle_sibling after.scene", "ChildLeaf", None),
        (
            "05_self_component_before.scene",
            "05_self_component_after.scene",
            "TriggerLeaf",
            "095b954f-863d-4514-8561-3ec94e8c9bd2",
        ),
        (
            "06_external_component_before.scene",
            "06_external_component_after.scene",
            "SourceTrigger",
            "2024921f-5822-4c24-a73d-a44188ef4863",
        ),
    )
    multi_component_shapes = []
    for before_name, after_name, source_name, source_guid in pairs:
        report = analyze_duplication_oracle(
            oracle_root / before_name,
            oracle_root / after_name,
            source_name=source_name,
            source_guid=source_guid,
        )
        after = read_pmh(report.after_path)
        duplicate = next(obj for obj in after.walk() if obj.guid in report.added_object_guids)
        shape = tuple(component.type_name for component in duplicate.components)
        if len(shape) > 1:
            multi_component_shapes.append(shape)
    assert multi_component_shapes
    assert all("ModMeshCollider" in shape for shape in multi_component_shapes)


def test_stage10c_audit_is_read_only(oracle_root: Path) -> None:
    before = {
        name: hashlib.sha256((oracle_root / name).read_bytes()).hexdigest()
        for name in ACTIVE_HASHES
    }
    assert before == ACTIVE_HASHES
    for name in ACTIVE_HASHES:
        read_pmh(oracle_root / name)
    after = {
        name: hashlib.sha256((oracle_root / name).read_bytes()).hexdigest()
        for name in ACTIVE_HASHES
    }
    assert after == before
