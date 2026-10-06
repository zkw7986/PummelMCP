"""Read-only, structure-backed object reference graph for PMH v1 scenes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterator

from .action_references import ActionReferenceError, inspect_action_entry_references
from .actions import ACTION_EVENT_PROPERTIES, ActionPayloadError, parse_action_payload
from .models import Component, GameObject, Scene, SourceSpan
from .reader import read_pmh
from .schemas import get_component_schema
from .asset_references import AssetReferenceError, parse_compact_asset_reference, parse_material_reference_list


class ReferenceClassification(str, Enum):
    IDENTITY = "IDENTITY"
    INTERNAL_OBJECT_REFERENCE = "INTERNAL_OBJECT_REFERENCE"
    INTERNAL_COMPONENT_REFERENCE = "INTERNAL_COMPONENT_REFERENCE"
    EXTERNAL_OBJECT_REFERENCE = "EXTERNAL_OBJECT_REFERENCE"
    EXTERNAL_COMPONENT_REFERENCE = "EXTERNAL_COMPONENT_REFERENCE"
    ASSET_REFERENCE = "ASSET_REFERENCE"
    PROP_ASSET_REFERENCE = "PROP_ASSET_REFERENCE"
    MATERIAL_REFERENCE_LIST = "MATERIAL_REFERENCE_LIST"
    HIERARCHY_REFERENCE = "HIERARCHY_REFERENCE"
    UNKNOWN = "UNKNOWN"


# Every reference kind that the duplication safety layer deliberately knows how
# to preserve, reject, or otherwise account for. A newly decoded kind must not be
# silently omitted merely because its field schema claims confirmed semantics.
KNOWN_REFERENCE_POLICY_KINDS = frozenset(
    {
        "ActionGraph",
        "ActionManagedReference",
        "AudioReference",
        "ComponentPropertyReference",
        "PropAssetReference",
        "MaterialReferenceList",
        "EffectIndexReference",
        "PrefabReference",
        "TargetFlags",
        "TargetSelectorList",
        "TransformReferenceList",
    }
)


@dataclass(frozen=True, slots=True)
class GraphEndpoint:
    kind: str
    guid: str | None
    label: str | None = None


@dataclass(frozen=True, slots=True)
class ReferenceEdge:
    source: GraphEndpoint
    target: GraphEndpoint
    property_path: str
    source_span: SourceSpan | None
    reference_kind: str
    classification: ReferenceClassification


@dataclass(frozen=True, slots=True)
class GameObjectNode:
    guid: str
    name: str
    hierarchy_path: str
    parent_guid: str | None
    child_guids: tuple[str, ...]
    component_guids: tuple[str, ...]
    preorder_index: int
    sibling_index: int
    hierarchy_span: SourceSpan | None
    identity_span: SourceSpan | None


@dataclass(frozen=True, slots=True)
class ComponentNode:
    guid: str
    type_name: str
    owner_gameobject_guid: str
    identity_span: SourceSpan | None


@dataclass(frozen=True, slots=True)
class SceneReferenceGraph:
    objects: tuple[GameObjectNode, ...]
    components: tuple[ComponentNode, ...]
    edges: tuple[ReferenceEdge, ...]
    warnings: tuple[str, ...]

    def edges_for_object(self, guid: str) -> tuple[ReferenceEdge, ...]:
        component_ids = {
            item.guid for item in self.components if item.owner_gameobject_guid == guid
        }
        ids = component_ids | {guid}
        return tuple(
            edge
            for edge in self.edges
            if edge.source.guid in ids or edge.target.guid in ids
        )


def build_reference_graph(scene_or_path: Scene | str | Path) -> SceneReferenceGraph:
    """Build a graph without scanning the file for GUID-shaped strings."""
    scene = read_pmh(scene_or_path) if isinstance(scene_or_path, (str, Path)) else scene_or_path
    objects = tuple(
        GameObjectNode(
            guid=obj.guid,
            name=obj.name,
            hierarchy_path=obj.hierarchy_path,
            parent_guid=obj.parent.guid if obj.parent else None,
            child_guids=tuple(child.guid for child in obj.children),
            component_guids=tuple(component.guid for component in obj.components),
            preorder_index=obj.preorder_index,
            sibling_index=obj.sibling_index,
            hierarchy_span=obj.hierarchy_span,
            identity_span=obj.identity_span,
        )
        for obj in scene.walk()
    )
    components = tuple(
        ComponentNode(
            guid=component.guid,
            type_name=component.type_name,
            owner_gameobject_guid=obj.guid,
            identity_span=component.identity_span,
        )
        for obj in scene.walk()
        for component in obj.components
    )
    components_by_guid = {item.guid: item for item in components}
    edges: list[ReferenceEdge] = []
    warnings: list[str] = []
    for obj in scene.walk():
        object_endpoint = GraphEndpoint("GAMEOBJECT", obj.guid, obj.name)
        edges.append(
            ReferenceEdge(
                object_endpoint,
                object_endpoint,
                "$.identity.guid",
                obj.identity_span,
                "GameObjectIdentity",
                ReferenceClassification.IDENTITY,
            )
        )
        if obj.parent is not None:
            edges.append(
                ReferenceEdge(
                    GraphEndpoint("GAMEOBJECT", obj.parent.guid, obj.parent.name),
                    object_endpoint,
                    f"$.children[{obj.sibling_index}]",
                    obj.hierarchy_span,
                    "SerializedHierarchyChild",
                    ReferenceClassification.HIERARCHY_REFERENCE,
                )
            )
        for component_index, component in enumerate(obj.components):
            component_endpoint = GraphEndpoint(
                "COMPONENT", component.guid, component.type_name
            )
            edges.append(
                ReferenceEdge(
                    object_endpoint,
                    component_endpoint,
                    f"$.components[{component_index}]",
                    component.index_span,
                    "SerializedComponentMembership",
                    ReferenceClassification.INTERNAL_COMPONENT_REFERENCE,
                )
            )
            edges.extend(
                _component_edges(component, obj.guid, components_by_guid)
            )
            if (
                component.type_name != "ModTransform"
                and get_component_schema(component.type_name) is None
            ):
                warnings.append(
                    f"unknown component type {component.type_name!r} on {obj.hierarchy_path}"
                )
    return SceneReferenceGraph(objects, components, tuple(edges), tuple(warnings))


def _component_edges(
    component: Component,
    owner_guid: str,
    components_by_guid: dict[str, ComponentNode],
) -> Iterator[ReferenceEdge]:
    source = GraphEndpoint("COMPONENT", component.guid, component.type_name)
    for field in component.fields:
        path = f"$.properties.{field.name}"
        if field.name == "guid":
            classification = (
                ReferenceClassification.IDENTITY
                if isinstance(field.value, str) and field.value == component.guid
                else ReferenceClassification.UNKNOWN
            )
            target = GraphEndpoint(
                "COMPONENT", field.value if isinstance(field.value, str) else None
            )
            yield ReferenceEdge(
                source,
                target,
                path,
                field.source_span,
                "ComponentIdentityProperty",
                classification,
            )
            continue
        if component.type_name == "ModProp" and field.name == "prop":
            try:
                ref = parse_compact_asset_reference(field.raw)
            except AssetReferenceError:
                ref = None
            yield ReferenceEdge(
                source,
                GraphEndpoint("ASSET" if ref else "UNKNOWN", ref.guid if ref else None, "compact Prop asset reference" if ref else "malformed Prop asset reference"),
                path,
                field.source_span,
                "PropAssetReference",
                ReferenceClassification.PROP_ASSET_REFERENCE if ref else ReferenceClassification.UNKNOWN,
            )
            continue
        if component.type_name == "ModProp" and field.name == "customMaterials":
            try:
                refs = parse_material_reference_list(field.raw)
            except AssetReferenceError:
                refs = None
            yield ReferenceEdge(source, GraphEndpoint("ASSET_LIST" if refs is not None else "UNKNOWN", None, f"{len(refs.elements)} compact material reference(s)" if refs is not None else "malformed material reference list"), path, field.source_span, "MaterialReferenceList", ReferenceClassification.MATERIAL_REFERENCE_LIST if refs is not None else ReferenceClassification.UNKNOWN)
            continue
        if component.type_name == "ModTrigger" and field.name in ACTION_EVENT_PROPERTIES:
            yield from _action_edges(
                source,
                owner_guid,
                components_by_guid,
                field.name,
                field.raw,
                field.source_span,
            )


def _action_edges(
    source: GraphEndpoint,
    source_owner_guid: str,
    components_by_guid: dict[str, ComponentNode],
    field_name: str,
    raw: bytes,
    field_span: SourceSpan | None,
) -> Iterator[ReferenceEdge]:
    try:
        parsed = parse_action_payload(raw, source_property=field_name, source_span=field_span)
    except (ActionPayloadError, ActionReferenceError, ValueError):
        yield ReferenceEdge(
            source,
            GraphEndpoint("UNKNOWN", None, "corrupted Action graph"),
            f"$.properties.{field_name}",
            field_span,
            "ActionGraph",
            ReferenceClassification.UNKNOWN,
        )
        return
    for action in parsed.actions:
        if action.resolved_reference is None:
            yield ReferenceEdge(
                source,
                GraphEndpoint("UNKNOWN", None, f"Action rid {action.rid}"),
                f"$.properties.{field_name}.actions[rid={action.rid}]",
                field_span,
                "ActionManagedReference",
                ReferenceClassification.UNKNOWN,
            )
            continue
        inspection = inspect_action_entry_references(action)
        segment = inspection.reference_segment_span
        for reference in inspection.references:
            base_path = (
                f"$.properties.{field_name}.actions[rid={action.rid}].{reference.source_field}"
            )
            if reference.kind in {"PrefabReferenceList", "AudioReference"}:
                for item in reference.items:
                    guid = item.normalized_value.get("guid")
                    yield ReferenceEdge(
                        source,
                        GraphEndpoint(
                            "ASSET",
                            guid if isinstance(guid, str) else None,
                            item.kind,
                        ),
                        f"{base_path}[{item.index}]",
                        _scene_action_span(field_span, segment, item.source_span),
                        item.kind,
                        (
                            ReferenceClassification.ASSET_REFERENCE
                            if isinstance(guid, str)
                            else ReferenceClassification.UNKNOWN
                        ),
                    )
            elif reference.kind == "TransformReferenceList":
                for item in reference.items:
                    guid = item.normalized_value.get("guid")
                    target = (
                        components_by_guid.get(guid)
                        if isinstance(guid, str)
                        else None
                    )
                    valid_target = target is not None and target.type_name == "ModTransform"
                    classification = ReferenceClassification.UNKNOWN
                    if valid_target:
                        classification = (
                            ReferenceClassification.INTERNAL_COMPONENT_REFERENCE
                            if target.owner_gameobject_guid == source_owner_guid
                            else ReferenceClassification.EXTERNAL_COMPONENT_REFERENCE
                        )
                    yield ReferenceEdge(
                        source,
                        GraphEndpoint(
                            "COMPONENT" if valid_target else "UNKNOWN",
                            guid if isinstance(guid, str) else None,
                            "ModTransform" if valid_target else "unresolved Transform",
                        ),
                        f"{base_path}[{item.index}]",
                        _scene_action_span(field_span, segment, item.source_span),
                        item.kind,
                        classification,
                    )
            else:
                yield ReferenceEdge(
                    source,
                    GraphEndpoint("UNKNOWN", None, reference.kind),
                    base_path,
                    _scene_action_span(field_span, segment, reference.source_span),
                    reference.kind,
                    ReferenceClassification.UNKNOWN,
                )


def _scene_action_span(
    field_span: SourceSpan | None,
    segment_span: SourceSpan | None,
    local_span: SourceSpan,
) -> SourceSpan | None:
    if field_span is None or segment_span is None:
        return None
    return SourceSpan(field_span.start + segment_span.start + local_span.start, local_span.length)
