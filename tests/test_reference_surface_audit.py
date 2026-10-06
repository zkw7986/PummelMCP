from __future__ import annotations

import hashlib
import os
from collections import Counter
from pathlib import Path

import pytest

from pummelmcp.pmh import (
    ReferenceClassification,
    build_reference_graph,
    inspect_action_entry_references,
    parse_action_payload,
    read_pmh,
)
from pummelmcp.pmh.actions import ACTION_EVENT_PROPERTIES


@pytest.fixture(scope="module")
def oracle_root() -> Path:
    configured = os.environ.get("PUMMELMCP_ORACLE_ROOT")
    if not configured:
        pytest.skip("PUMMELMCP_ORACLE_ROOT is not configured")
    root = Path(configured)
    if not root.is_dir():
        pytest.skip("configured Oracle directory is unavailable")
    return root


def _transform_file_ids(path: Path) -> Counter[tuple[int | None, int | None]]:
    result: Counter[tuple[int | None, int | None]] = Counter()
    scene = read_pmh(path)
    for obj in scene.walk():
        for component in obj.components:
            if component.type_name != "ModTrigger":
                continue
            for field in component.fields:
                if field.name not in ACTION_EVENT_PROPERTIES:
                    continue
                parsed = parse_action_payload(
                    field.raw,
                    source_property=field.name,
                    source_span=field.source_span,
                )
                for action in parsed.actions:
                    if action.resolved_reference is None:
                        continue
                    inspection = inspect_action_entry_references(action)
                    for reference in inspection.references:
                        if reference.kind != "TransformReferenceList":
                            continue
                        for item in reference.items:
                            result[
                                (
                                    item.normalized_value.get("file_id"),
                                    item.normalized_value.get("path_id"),
                                )
                            ] += 1
    return result


def test_repo_fixture_reference_surface_inventory(main_scene_path: Path) -> None:
    graph = build_reference_graph(main_scene_path)
    property_edges = [
        edge
        for edge in graph.edges
        if edge.classification
        in {ReferenceClassification.ASSET_REFERENCE, ReferenceClassification.PROP_ASSET_REFERENCE, ReferenceClassification.MATERIAL_REFERENCE_LIST, ReferenceClassification.UNKNOWN}
    ]
    assert len(property_edges) == 369
    assert Counter(edge.reference_kind for edge in property_edges) == {
        "PrefabReference": 183,
        "AudioReference": 1,
        "PropAssetReference": 85,
        "MaterialReferenceList": 85,
        "TargetFlags": 11,
        "TargetSelectorList": 3,
        "EffectIndexReference": 1,
    }
    assert not any(
        edge.classification
        in {
            ReferenceClassification.INTERNAL_OBJECT_REFERENCE,
            ReferenceClassification.EXTERNAL_OBJECT_REFERENCE,
        }
        for edge in graph.edges
    )
    assert not any(edge.reference_kind == "TransformReference" for edge in graph.edges)


def test_transform_reference_file_id_inventory(oracle_root: Path) -> None:
    total: Counter[tuple[int | None, int | None]] = Counter()
    for name in (
        "05_self_component_before.scene",
        "05_self_component_after.scene",
        "06_external_component_before.scene",
        "06_external_component_after.scene",
    ):
        total.update(_transform_file_ids(oracle_root / name))
    assert total == {(-71472, 0): 7, (-81578, 0): 3}


def test_no_independent_gameobject_reference_in_active_surfaces(
    main_scene_path: Path, oracle_root: Path
) -> None:
    paths = [main_scene_path, *sorted(oracle_root.glob("*.scene"))]
    for path in paths:
        graph = build_reference_graph(path)
        assert not any(
            edge.reference_kind == "GameObjectReference" for edge in graph.edges
        )
        assert not any(
            edge.classification
            in {
                ReferenceClassification.INTERNAL_OBJECT_REFERENCE,
                ReferenceClassification.EXTERNAL_OBJECT_REFERENCE,
            }
            for edge in graph.edges
        )


def test_live_joker21_reference_audit_is_read_only(main_scene_path: Path) -> None:
    configured = os.environ.get("PUMMELMCP_JOKER21_SCENE")
    if not configured:
        pytest.skip("PUMMELMCP_JOKER21_SCENE is not configured")
    path = Path(configured)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    live = build_reference_graph(path)
    fixture = build_reference_graph(main_scene_path)
    assert Counter(edge.reference_kind for edge in live.edges) == Counter(
        edge.reference_kind for edge in fixture.edges
    )
    assert not any(
        edge.classification
        in {
            ReferenceClassification.INTERNAL_OBJECT_REFERENCE,
            ReferenceClassification.EXTERNAL_OBJECT_REFERENCE,
        }
        for edge in live.edges
    )
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert before == "080a693bddc60e3fb29b5a5bafb09d3eb972af13a8bab53edb2af0403b9acd5b"
