from __future__ import annotations

import os
import shutil
import hashlib
import struct
import uuid
from pathlib import Path

import pytest
import anyio
from mcp.server.mcpserver.exceptions import ToolError

import pummelmcp.pmh.duplication_writer as duplication_writer
import pummelmcp.pmh.writer as pmh_writer
from pummelmcp.mcp_server import create_server
from pummelmcp.pmh import (
    AmbiguousObjectError,
    GraphEndpoint,
    GuidAllocationError,
    ObjectNotFoundError,
    ReferenceClassification,
    ReferenceEdge,
    SceneReferenceGraph,
    SourceSpan,
    UnsafeDuplicationError,
    WriterValidationError,
    ConcurrentModificationError,
    duplicate_transform_only_leaf,
    generate_unique_uuid4,
    plan_transform_only_leaf_duplication,
    read_pmh,
)


MIDDLE_SOURCE = "bd314d55-1423-478e-95f8-28209bfc025c"
MIDDLE_PARENT = "f75cc755-fa0d-48c7-9395-d36c548b1894"
FIXED_NEW_GUID = uuid.UUID("11111111-1111-4111-8111-111111111111")
OFFICIAL_DUPLICATE = uuid.UUID("9d2f64e2-7a97-4f94-ab51-e3428603db6a")


@pytest.fixture(scope="module")
def oracle_root() -> Path:
    configured = os.environ.get("PUMMELMCP_ORACLE_ROOT")
    if not configured:
        pytest.skip("PUMMELMCP_ORACLE_ROOT is not configured")
    root = Path(configured)
    if not root.is_dir():
        pytest.skip("configured Oracle directory is unavailable")
    return root


def test_safe_transform_leaf_clone_plan_matches_middle_oracle(
    oracle_root: Path,
) -> None:
    path = oracle_root / "04_middle_sibling before.scene"
    before = path.read_bytes()
    plan = plan_transform_only_leaf_duplication(
        path, "ChildLeaf", guid_factory=lambda: FIXED_NEW_GUID
    )
    assert path.read_bytes() == before
    assert plan.safety_class == "SAFE_TRANSFORM_ONLY_LEAF_DUPLICATION"
    assert plan.source.gameobject_guid == plan.source.transform_guid == MIDDLE_SOURCE
    assert plan.source.parent_guid == plan.destination.parent_guid == MIDDLE_PARENT
    assert (plan.source.sibling_index, plan.source.preorder_index) == (1, 40)
    assert (plan.destination.sibling_index, plan.destination.preorder_index) == (3, 42)
    assert plan.destination.gameobject_guid == plan.destination.transform_guid
    assert plan.destination.serialized_name == "ChildLeaf"
    assert plan.insert_policy == "APPEND_TO_PARENT_CHILDREN"
    assert plan.component_policy == "COPY_MODTRANSFORM"
    assert plan.reference_policy == "NONE_PRESENT"
    assert [item.kind for item in plan.mutations] == [
        "UPDATE_PARENT_CHILD_COUNT",
        "INSERT_HIERARCHY_RECORD",
        "INSERT_OBJECT_INDEX_RECORD",
        "INSERT_MODTRANSFORM_PAYLOAD",
    ]


def test_generated_identity_is_canonical_uuid4_and_shared(oracle_root: Path) -> None:
    plan = plan_transform_only_leaf_duplication(
        oracle_root / "04_middle_sibling before.scene", "ChildLeaf"
    )
    parsed = uuid.UUID(plan.destination.gameobject_guid)
    assert parsed.version == 4
    assert parsed.variant == "specified in RFC 4122"
    assert str(parsed) == plan.destination.gameobject_guid
    assert plan.destination.gameobject_guid == plan.destination.transform_guid


def test_clone_plan_records_exact_source_span_fingerprints(oracle_root: Path) -> None:
    path = oracle_root / "04_middle_sibling before.scene"
    raw = path.read_bytes()
    plan = plan_transform_only_leaf_duplication(
        path, MIDDLE_SOURCE, guid_factory=lambda: FIXED_NEW_GUID
    )
    spans = {
        "hierarchy": plan.source.hierarchy_span,
        "object_index": plan.source.object_index_span,
        "transform_payload": plan.source.transform_payload_span,
    }
    assert {
        name: hashlib.sha256(raw[span.start : span.end]).hexdigest()
        for name, span in spans.items()
    } == plan.source.serialized_span_sha256


def test_clone_plan_identity_mapping_has_both_typed_identities(oracle_root: Path) -> None:
    plan = plan_transform_only_leaf_duplication(
        oracle_root / "04_middle_sibling before.scene",
        MIDDLE_SOURCE,
        guid_factory=lambda: FIXED_NEW_GUID,
    )
    assert plan.identity_mapping == {
        "gameobject": {"source": MIDDLE_SOURCE, "duplicate": str(FIXED_NEW_GUID)},
        "mod_transform": {"source": MIDDLE_SOURCE, "duplicate": str(FIXED_NEW_GUID)},
    }


def test_clone_plan_parent_count_patch_targets_existing_count(oracle_root: Path) -> None:
    path = oracle_root / "04_middle_sibling before.scene"
    plan = plan_transform_only_leaf_duplication(path, MIDDLE_SOURCE)
    mutation = next(
        item for item in plan.mutations if item.kind == "UPDATE_PARENT_CHILD_COUNT"
    )
    assert mutation.source_length == mutation.output_length == 2
    assert struct.unpack(
        "<H", path.read_bytes()[mutation.source_offset : mutation.source_offset + 2]
    )[0] == 3


def test_clone_plan_insertions_are_length_accounted(oracle_root: Path) -> None:
    plan = plan_transform_only_leaf_duplication(
        oracle_root / "04_middle_sibling before.scene", MIDDLE_SOURCE
    )
    inserted = sum(
        item.output_length - item.source_length for item in plan.mutations
    )
    assert inserted == 238
    assert all(
        item.source_length == 0
        for item in plan.mutations
        if item.kind.startswith("INSERT_")
    )


def test_guid_allocator_retries_collisions_and_rejects_exhaustion(
    oracle_root: Path,
) -> None:
    scene = read_pmh(oracle_root / "04_middle_sibling before.scene")
    values = iter((uuid.UUID(MIDDLE_SOURCE), FIXED_NEW_GUID))
    assert generate_unique_uuid4(scene, guid_factory=lambda: next(values)) == str(
        FIXED_NEW_GUID
    )
    with pytest.raises(GuidAllocationError):
        generate_unique_uuid4(
            scene,
            guid_factory=lambda: uuid.UUID(MIDDLE_SOURCE),
            max_attempts=2,
        )


def test_guid_allocator_rejects_non_uuid4_candidates(oracle_root: Path) -> None:
    scene = read_pmh(oracle_root / "04_middle_sibling before.scene")
    values = iter(("not-a-guid", uuid.UUID("11111111-1111-1111-8111-111111111111")))
    with pytest.raises(GuidAllocationError):
        generate_unique_uuid4(scene, guid_factory=lambda: next(values), max_attempts=2)


@pytest.mark.parametrize(
    ("filename", "identifier", "code"),
    [
        ("01_empty_before.scene", "EmptyLeaf", "UNSAFE_ROOT_OBJECT"),
        ("04_middle_sibling before.scene", "Parent", "UNSAFE_HAS_CHILDREN"),
        ("06_external_component_before.scene", "SourceTrigger", "UNSAFE_UNSUPPORTED_COMPONENT"),
    ],
)
def test_unsafe_shapes_are_rejected(
    oracle_root: Path, filename: str, identifier: str, code: str
) -> None:
    with pytest.raises(UnsafeDuplicationError, match=code):
        plan_transform_only_leaf_duplication(oracle_root / filename, identifier)


def test_ambiguous_and_missing_objects_are_rejected(oracle_root: Path) -> None:
    with pytest.raises(AmbiguousObjectError):
        plan_transform_only_leaf_duplication(
            oracle_root / "04_middle_sibling after.scene", "ChildLeaf"
        )


def test_parented_modprop_is_rejected(oracle_root: Path) -> None:
    with pytest.raises(UnsafeDuplicationError, match="UNSAFE_UNSUPPORTED_COMPONENT"):
        plan_transform_only_leaf_duplication(
            oracle_root / "04_middle_sibling before.scene", "/World/LowPolySphere"
        )


def test_parented_modmeshcollider_trigger_is_rejected(oracle_root: Path) -> None:
    with pytest.raises(UnsafeDuplicationError, match="UNSAFE_UNSUPPORTED_COMPONENT"):
        plan_transform_only_leaf_duplication(
            oracle_root / "06_external_component_before.scene",
            "/ReferenceTests/SourceTrigger",
        )
    with pytest.raises(ObjectNotFoundError):
        plan_transform_only_leaf_duplication(
            oracle_root / "04_middle_sibling before.scene", "MissingLeaf"
        )


def test_invalid_gameobject_transform_identity_is_rejected(
    oracle_root: Path, tmp_path: Path
) -> None:
    target = tmp_path / "invalid-identity.scene"
    shutil.copyfile(oracle_root / "04_middle_sibling before.scene", target)
    scene = read_pmh(target)
    source = scene.find_game_object("ChildLeaf")
    transform = source.get_component("ModTransform")
    assert transform.identity_span is not None
    raw = bytearray(target.read_bytes())
    replacement = b"22222222-2222-4222-8222-222222222222"
    raw[transform.identity_span.start + 1 : transform.identity_span.end] = replacement
    target.write_bytes(raw)
    with pytest.raises(UnsafeDuplicationError, match="UNSAFE_INVALID_IDENTITY"):
        plan_transform_only_leaf_duplication(target, source.guid)


def test_future_outgoing_reference_is_rejected(
    oracle_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = duplication_writer.build_reference_graph

    def with_unknown(scene):
        graph = original(scene)
        edge = ReferenceEdge(
            source=GraphEndpoint("COMPONENT", MIDDLE_SOURCE, "ModTransform"),
            target=GraphEndpoint("UNKNOWN", None, "future"),
            property_path="$.properties.futureReference",
            source_span=SourceSpan(0, 0),
            reference_kind="FutureSceneReference",
            classification=ReferenceClassification.UNKNOWN,
        )


def test_future_incoming_reference_is_rejected(
    oracle_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = duplication_writer.build_reference_graph

    def with_unknown(scene):
        graph = original(scene)
        edge = ReferenceEdge(
            source=GraphEndpoint("COMPONENT", "future-source", "Future"),
            target=GraphEndpoint("COMPONENT", MIDDLE_SOURCE, "ModTransform"),
            property_path="$.properties.futureReference",
            source_span=SourceSpan(0, 0),
            reference_kind="FutureSceneReference",
            classification=ReferenceClassification.UNKNOWN,
        )
        return SceneReferenceGraph(
            graph.objects, graph.components, (*graph.edges, edge), graph.warnings
        )

    monkeypatch.setattr(duplication_writer, "build_reference_graph", with_unknown)
    with pytest.raises(UnsafeDuplicationError, match="UNSAFE_UNKNOWN_REFERENCE_TYPE"):
        plan_transform_only_leaf_duplication(
            oracle_root / "04_middle_sibling before.scene", MIDDLE_SOURCE
        )
        return SceneReferenceGraph(
            graph.objects, graph.components, (*graph.edges, edge), graph.warnings
        )

    monkeypatch.setattr(duplication_writer, "build_reference_graph", with_unknown)
    with pytest.raises(UnsafeDuplicationError, match="UNSAFE_UNKNOWN_REFERENCE_TYPE"):
        plan_transform_only_leaf_duplication(
            oracle_root / "04_middle_sibling before.scene", "ChildLeaf"
        )


def test_writer_is_byte_equivalent_to_official_middle_oracle_after_guid_alignment(
    oracle_root: Path, tmp_path: Path
) -> None:
    before_path = oracle_root / "04_middle_sibling before.scene"
    official_after = oracle_root / "04_middle_sibling after.scene"
    target = tmp_path / "generated.scene"
    shutil.copyfile(before_path, target)
    report = duplicate_transform_only_leaf(
        target,
        "ChildLeaf",
        expected_scene_hash="8212c182f7e8f1a3f97d86ba0cc2ca6185eecbdfcfd7f7d6dad38c360b1d9cc4",
        guid_factory=lambda: OFFICIAL_DUPLICATE,
    )
    assert report.validation.passed
    assert report.bytes_added == 238
    assert target.read_bytes() == official_after.read_bytes()
    assert report.backup_path is not None
    assert report.backup_path.read_bytes() == before_path.read_bytes()


def test_writer_preserves_source_and_existing_siblings(
    oracle_root: Path, tmp_path: Path
) -> None:
    target = tmp_path / "preservation.scene"
    shutil.copyfile(oracle_root / "04_middle_sibling before.scene", target)
    before = read_pmh(target)
    source_before = before.find_game_object("ChildLeaf")
    source_fields = [
        (field.name, field.raw)
        for field in source_before.get_component("ModTransform").fields
    ]
    report = duplicate_transform_only_leaf(target, MIDDLE_SOURCE)
    after = read_pmh(target)
    source_after = next(obj for obj in after.walk() if obj.guid == MIDDLE_SOURCE)
    duplicate = next(obj for obj in after.walk() if obj.guid == report.duplicate_guid)
    parent = duplicate.parent
    assert parent is not None
    assert [child.name for child in parent.children] == [
        "BeforeSibling", "ChildLeaf", "AfterSibling", "ChildLeaf"
    ]
    assert [(child.sibling_index, child.preorder_index) for child in parent.children] == [
        (0, 39), (1, 40), (2, 41), (3, 42)
    ]
    assert [
        (field.name, field.raw)
        for field in source_after.get_component("ModTransform").fields
    ] == source_fields
    assert duplicate.get_component("ModTransform").guid == duplicate.guid


def test_writer_report_hashes_match_written_bytes(
    oracle_root: Path, tmp_path: Path
) -> None:
    target = tmp_path / "hashes.scene"
    shutil.copyfile(oracle_root / "04_middle_sibling before.scene", target)
    before_hash = hashlib.sha256(target.read_bytes()).hexdigest()
    report = duplicate_transform_only_leaf(target, MIDDLE_SOURCE)
    assert report.before_sha256 == before_hash
    assert report.after_sha256 == hashlib.sha256(target.read_bytes()).hexdigest()


def test_writer_copies_name_active_layer_tag_and_transform_values(
    oracle_root: Path, tmp_path: Path
) -> None:
    target = tmp_path / "copied-values.scene"
    shutil.copyfile(oracle_root / "04_middle_sibling before.scene", target)
    before = read_pmh(target).find_game_object("ChildLeaf")
    report = duplicate_transform_only_leaf(target, MIDDLE_SOURCE)
    duplicate = next(
        obj for obj in read_pmh(target).walk() if obj.guid == report.duplicate_guid
    )
    assert (duplicate.name, duplicate.active, duplicate.layer, duplicate.tag) == (
        before.name, before.active, before.layer, before.tag
    )
    assert [
        (field.name, field.raw)
        for field in duplicate.get_component("ModTransform").fields
        if field.name != "guid"
    ] == [
        (field.name, field.raw)
        for field in before.get_component("ModTransform").fields
        if field.name != "guid"
    ]


def test_writer_can_disable_backup_for_internal_api(
    oracle_root: Path, tmp_path: Path
) -> None:
    target = tmp_path / "no-backup.scene"
    shutil.copyfile(oracle_root / "04_middle_sibling before.scene", target)
    report = duplicate_transform_only_leaf(target, MIDDLE_SOURCE, backup=False)
    assert report.backup_path is None
    assert not list(tmp_path.glob("no-backup.scene.bak.*"))


def test_writer_accepts_case_insensitive_expected_hash(
    oracle_root: Path, tmp_path: Path
) -> None:
    target = tmp_path / "uppercase-hash.scene"
    shutil.copyfile(oracle_root / "04_middle_sibling before.scene", target)
    expected = hashlib.sha256(target.read_bytes()).hexdigest().upper()
    report = duplicate_transform_only_leaf(
        target, MIDDLE_SOURCE, expected_scene_hash=expected
    )
    assert report.validation.passed


def test_written_reference_graph_has_no_duplicate_unknowns(
    oracle_root: Path, tmp_path: Path
) -> None:
    target = tmp_path / "graph.scene"
    shutil.copyfile(oracle_root / "04_middle_sibling before.scene", target)
    report = duplicate_transform_only_leaf(target, MIDDLE_SOURCE)
    graph = duplication_writer.build_reference_graph(target)
    assert not any(
        edge.source.guid == report.duplicate_guid
        and edge.classification is ReferenceClassification.UNKNOWN
        for edge in graph.edges
    )


def test_stale_expected_hash_rejected_without_write(
    oracle_root: Path, tmp_path: Path
) -> None:
    target = tmp_path / "stale.scene"
    shutil.copyfile(oracle_root / "04_middle_sibling before.scene", target)
    before = target.read_bytes()
    with pytest.raises(ConcurrentModificationError, match="UNSAFE_STALE_SCENE"):
        duplicate_transform_only_leaf(target, "ChildLeaf", expected_scene_hash="0" * 64)
    assert target.read_bytes() == before


def test_temp_parse_failure_leaves_source_unchanged(
    oracle_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "temp-parse.scene"
    shutil.copyfile(oracle_root / "04_middle_sibling before.scene", target)
    before = target.read_bytes()

    def fail_read_path(self, path):
        raise WriterValidationError("forced temp parse failure")

    monkeypatch.setattr(pmh_writer.PMHReader, "read_path", fail_read_path)
    with pytest.raises(WriterValidationError, match="forced temp parse failure"):
        duplicate_transform_only_leaf(target, "ChildLeaf")
    assert target.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))


def test_validation_failure_leaves_source_unchanged(
    oracle_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "validation.scene"
    shutil.copyfile(oracle_root / "04_middle_sibling before.scene", target)
    before = target.read_bytes()

    def fail_validation(*args, **kwargs):
        raise WriterValidationError("forced validation failure")

    monkeypatch.setattr(duplication_writer, "_validate_duplicate", fail_validation)
    with pytest.raises(WriterValidationError, match="forced validation failure"):
        duplicate_transform_only_leaf(target, "ChildLeaf")
    assert target.read_bytes() == before


def test_writer_uses_atomic_replace(
    oracle_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "atomic.scene"
    shutil.copyfile(oracle_root / "04_middle_sibling before.scene", target)
    calls: list[tuple[Path, Path]] = []
    original_replace = pmh_writer.os.replace

    def tracked_replace(source, destination):
        assert len(list(tmp_path.glob("atomic.scene.bak.*"))) == 1
        calls.append((Path(source), Path(destination)))
        return original_replace(source, destination)

    monkeypatch.setattr(pmh_writer.os, "replace", tracked_replace)
    duplicate_transform_only_leaf(target, "ChildLeaf")
    assert len(calls) == 1
    assert calls[0][0].suffix == ".tmp"
    assert calls[0][1] == target


def _mcp_call(server, name: str, arguments: dict):
    async def invoke():
        return await server.call_tool(name, arguments)

    return anyio.run(invoke)


def test_mcp_plan_is_read_only_and_duplicate_is_available(
    oracle_root: Path, tmp_path: Path
) -> None:
    target = tmp_path / "mcp.scene"
    shutil.copyfile(oracle_root / "04_middle_sibling before.scene", target)
    before = target.read_bytes()
    server = create_server(tmp_path)
    planned = _mcp_call(
        server,
        "plan_gameobject_duplication",
        {"scene_path": str(target), "object": "ChildLeaf"},
    )
    assert not planned.is_error
    assert planned.structured_content["safety_class"] == "SAFE_TRANSFORM_ONLY_LEAF_DUPLICATION"
    assert target.read_bytes() == before
    written = _mcp_call(
        server,
        "duplicate_gameobject",
        {
            "scene_path": str(target),
            "object": MIDDLE_SOURCE,
            "expected_scene_hash": "8212c182f7e8f1a3f97d86ba0cc2ca6185eecbdfcfd7f7d6dad38c360b1d9cc4",
        },
    )
    assert not written.is_error
    assert written.structured_content["validation"]["passed"] is True
    assert read_pmh(target).object_count == 43


def test_mcp_plan_and_writer_reject_unsafe_root(
    oracle_root: Path, tmp_path: Path
) -> None:
    target = tmp_path / "unsafe.scene"
    shutil.copyfile(oracle_root / "01_empty_before.scene", target)
    server = create_server(tmp_path)
    planned = _mcp_call(
        server,
        "plan_gameobject_duplication",
        {"scene_path": str(target), "object": "EmptyLeaf"},
    )
    assert planned.structured_content["safety_class"] == "UNSAFE"
    assert planned.structured_content["reason_code"] == "UNSAFE_ROOT_OBJECT"
    with pytest.raises(ToolError, match="UNSAFE_ROOT_OBJECT"):
        _mcp_call(
            server,
            "duplicate_gameobject",
            {"scene_path": str(target), "object": "EmptyLeaf"},
        )
