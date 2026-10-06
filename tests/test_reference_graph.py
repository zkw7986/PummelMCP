from __future__ import annotations

import hashlib
from pathlib import Path

import anyio

from pummelmcp.mcp_server import create_server
from pummelmcp.pmh import ReferenceClassification, build_reference_graph, read_pmh
from pummelmcp.service import (
    analyze_duplication_safety_details,
    inspect_object_references_details,
    inspect_reference_graph_details,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _structured(server, name: str, arguments: dict) -> dict:
    async def invoke():
        return await server.call_tool(name, arguments)

    result = anyio.run(invoke)
    assert not result.is_error
    assert isinstance(result.structured_content, dict)
    return result.structured_content


def test_reader_exposes_structural_identity_and_hierarchy_spans(main_scene_path: Path) -> None:
    raw = main_scene_path.read_bytes()
    scene = read_pmh(main_scene_path)
    objects = list(scene.walk())
    assert [item.preorder_index for item in objects] == list(range(len(objects)))
    for obj in objects:
        assert obj.identity_span is not None
        assert raw[obj.identity_span.start + 1 : obj.identity_span.end].decode() == obj.guid
        assert obj.hierarchy_span is not None
        for index, child in enumerate(obj.children):
            assert child.parent is obj
            assert child.sibling_index == index
        for component in obj.components:
            assert component.identity_span is not None
            assert raw[component.identity_span.start + 1 : component.identity_span.end].decode() == component.guid


def test_reference_graph_recovers_identity_ownership_and_hierarchy(main_scene_path: Path) -> None:
    graph = build_reference_graph(main_scene_path)
    assert len(graph.objects) == 142
    assert len(graph.components) == 267
    assert all(item.owner_gameobject_guid for item in graph.components)
    classifications = {item.classification for item in graph.edges}
    assert ReferenceClassification.IDENTITY in classifications
    assert ReferenceClassification.INTERNAL_COMPONENT_REFERENCE in classifications
    assert ReferenceClassification.HIERARCHY_REFERENCE in classifications


def test_gameobject_identity_matches_unique_transform_identity(main_scene_path: Path) -> None:
    scene = read_pmh(main_scene_path)
    for obj in scene.walk():
        transforms = [item for item in obj.components if item.type_name == "ModTransform"]
        assert len(transforms) == 1
        assert transforms[0].guid == obj.guid


def test_all_scene_identity_guids_are_lowercase_uuid4(main_scene_path: Path) -> None:
    from uuid import UUID

    scene = read_pmh(main_scene_path)
    values = [item.guid for item in scene.walk()]
    values += [component.guid for item in scene.walk() for component in item.components]
    for value in values:
        parsed = UUID(value)
        assert len(value) == 36
        assert value == value.lower()
        assert parsed.version == 4
        assert parsed.variant == "specified in RFC 4122"


def test_action_asset_and_unknown_references_are_classified(main_scene_path: Path) -> None:
    graph = build_reference_graph(main_scene_path)
    assets = [item for item in graph.edges if item.classification is ReferenceClassification.ASSET_REFERENCE]
    unknown = [item for item in graph.edges if item.classification is ReferenceClassification.UNKNOWN]
    assert any(item.reference_kind == "PrefabReference" and item.target.guid for item in assets)
    assert any(item.reference_kind == "AudioReference" and item.target.guid for item in assets)
    assert any(item.reference_kind in {"TargetFlags", "TargetSelectorList"} for item in unknown)


def test_observed_prop_component_references_are_structurally_classified(main_scene_path: Path) -> None:
    graph = build_reference_graph(main_scene_path)
    assert any(
        item.property_path.endswith(".customMaterials")
        and item.classification is ReferenceClassification.MATERIAL_REFERENCE_LIST
        for item in graph.edges
    )


def test_object_inspector_reports_incoming_hierarchy(main_scene_path: Path) -> None:
    scene = read_pmh(main_scene_path)
    obj = scene.find_game_object("PlayerSpawn_0")
    result = inspect_object_references_details(main_scene_path, obj.guid)
    assert result["read_only"] is True
    assert any(item["classification"] == "HIERARCHY_REFERENCE" for item in result["incoming_references"])


def test_duplication_analysis_is_fail_closed_without_oracle(main_scene_path: Path) -> None:
    obj = read_pmh(main_scene_path).find_game_object("PlayerSpawn_0")
    result = analyze_duplication_safety_details(main_scene_path, obj.guid)
    assert result["duplication_status"] == "UNSAFE"
    assert result["writer_available"] is False
    assert any("outside" in reason for reason in result["reasons"])


def test_graph_inspection_is_byte_for_byte_read_only(main_scene_path: Path) -> None:
    before = _sha(main_scene_path)
    result = inspect_reference_graph_details(main_scene_path, offset=0, limit=5)
    assert result["read_only"] is True
    assert result["returned"] == 5
    assert _sha(main_scene_path) == before


def test_stage_10a_mcp_tools(main_scene_path: Path) -> None:
    scene = read_pmh(main_scene_path)
    obj = scene.find_game_object("PlayerSpawn_0")
    server = create_server(main_scene_path.parent)
    graph = _structured(server, "inspect_reference_graph", {"scene_path": str(main_scene_path), "limit": 3})
    refs = _structured(server, "inspect_object_references", {"scene_path": str(main_scene_path), "object": obj.guid})
    safety = _structured(server, "analyze_duplication_safety", {"scene_path": str(main_scene_path), "object": obj.guid})
    assert graph["returned"] == 3
    assert refs["object"]["guid"] == obj.guid
    assert safety["stage_10a_hard_gate_passed"] is True
