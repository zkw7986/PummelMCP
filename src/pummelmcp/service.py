"""Application-facing services shared by CLI and MCP front ends."""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from .pmh import (
    ACTION_EVENT_PROPERTIES,
    MAX_ACTION_PARSE_DEPTH,
    ActionInspectionError,
    ActionReferenceError,
    ActionFieldWriter,
    ActionGraphWriter,
    ActionList,
    AmbiguousObjectError,
    KNOWN_REFERENCE_POLICY_KINDS,
    PMHScene,
    ObjectNotFoundError,
    PrefabReferenceWriter,
    Vector2,
    Vector3,
    Vector4,
    PROP_ASSET_TYPE,
    build_asset_catalog,
    build_verified_prop_reference,
    describe_editor_asset,
    get_builtin_asset_by_guid,
    load_builtin_asset_catalog,
    load_editor_asset_catalog,
    resolve_editor_asset_entry,
    search_builtin_asset_catalog,
    search_editor_assets,
    spawn_builtin_prop,
    plan_blender_scene_import,
    build_blender_scene_import,
    EditorAssetNotFoundError,
    EditorAssetNotInternalError,
    build_reference_graph,
    find_mod_root,
    inspect_action_entry_references,
    parse_action_payload,
    read_pmh,
    duplicate_leaf,
    plan_leaf_duplication,
    plan_gameobject_reparent,
    reparent_gameobject,
    plan_subtree_duplication,
    duplicate_subtree,
    register_object_template,
    list_object_templates,
    plan_create_from_template,
    create_from_template,
    GuidAllocationError,
    UnsafeDuplicationError,
    validate_scene,
)
from .pmh.action_schemas import get_action_schema
from .pmh.models import Color4
from .pmh.models import DecodeConfidence, Field, GameObject
from .pmh.asset_reference_writer import replace_prop_reference_from_template
from .pmh.gameplay_composer import compose_gameplay, list_gameplay_recipes, plan_gameplay_composition
from .pmh.runtime import (
    collect_playtest_evidence as collect_runtime_evidence,
    evaluate_playtest as evaluate_runtime_playtest,
    inspect_runtime_capabilities as inspect_runtime_surface,
    plan_playtest_session as plan_runtime_session,
    start_playtest_session as start_runtime_session,
)
from .pmh.minigame_planner import list_minigame_archetypes,plan_minigame,build_minigame
from .pmh.minigame_v2 import plan_minigame_v2, build_minigame_v2
from .pmh.prefab_assets import plan_prefab_pack, build_prefab_pack
from .minigame_config import (
    get_minigame_config,
    plan_minigame_config_update,
    apply_minigame_config_update,
)


def get_minigame_config_details(scene_path, *, allowed_root):
    return get_minigame_config(scene_path, allowed_root)


def plan_minigame_config_update_details(scene_path, kind, updates, *, allowed_root):
    return plan_minigame_config_update(scene_path, allowed_root, kind, updates)


def apply_minigame_config_update_details(scene_path, kind, updates, expected_plan_sha256, *, allowed_root):
    return apply_minigame_config_update(scene_path, allowed_root, kind, updates, expected_plan_sha256)


def plan_minigame_v2_details(scene_path, spec, *, allowed_root):
    return plan_minigame_v2(scene_path, allowed_root, spec)


def build_minigame_v2_details(scene_path, spec, expected_plan_sha256, *, allowed_root):
    return build_minigame_v2(scene_path, allowed_root, spec, expected_plan_sha256)


def plan_prefab_pack_details(scene_path, spec, *, allowed_root):
    return plan_prefab_pack(scene_path, allowed_root, spec)


def build_prefab_pack_details(scene_path, spec, expected_plan_sha256, *, allowed_root):
    return build_prefab_pack(scene_path, allowed_root, spec, expected_plan_sha256)


def get_builtin_asset_catalog_summary_details(asset_root: str | Path) -> dict[str, object]:
    return load_builtin_asset_catalog(asset_root).summary()


def search_builtin_assets_details(
    asset_root: str | Path,
    *,
    query: str | None = None,
    asset_type: str | None = None,
    source: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> dict[str, object]:
    catalog = load_builtin_asset_catalog(asset_root)
    return search_builtin_asset_catalog(
        catalog,
        query=query,
        asset_type=asset_type,
        source=source,
        offset=offset,
        limit=limit,
    )


def get_builtin_asset_details(asset_root: str | Path, guid: str) -> dict[str, object]:
    catalog = load_builtin_asset_catalog(asset_root)
    return get_builtin_asset_by_guid(catalog, guid)


def list_editor_assets_details(
    asset_root: str | Path,
    *,
    folder: str | None = None,
    folder_prefix: str | None = None,
    query: str | None = None,
    tag: str | None = None,
    asset_type: str | None = PROP_ASSET_TYPE,
    enabled_only: bool = True,
    offset: int = 0,
    limit: int = 50,
) -> dict[str, object]:
    catalog = load_editor_asset_catalog(asset_root)
    return search_editor_assets(
        catalog,
        folder=folder,
        folder_prefix=folder_prefix,
        query=query,
        tag=tag,
        asset_type=asset_type,
        enabled_only=enabled_only,
        offset=offset,
        limit=limit,
    )


def get_editor_asset_details(
    asset_root: str | Path, asset: str, *, folder: str | None = None
) -> dict[str, object]:
    catalog = load_editor_asset_catalog(asset_root)
    return describe_editor_asset(catalog, asset, folder=folder)


def spawn_builtin_prop_details(
    scene_path: str | Path,
    asset_root: str | Path,
    *,
    asset: str,
    mod_root: str | Path | None = None,
    folder: str | None = None,
    parent: str | None = None,
    name: str | None = None,
    position: Mapping[str, float | None] | None = None,
    rotation: Mapping[str, float | None] | None = None,
    scale: Mapping[str, float | None] | None = None,
    tint_color: Mapping[str, float | None] | None = None,
    collision_type: str | None = None,
    shadow_casting_mode: str | None = None,
    layer: int | None = None,
    tag: str | None = None,
    active: bool = True,
    expected_scene_hash: str | None = None,
    dry_run: bool = False,
) -> dict[str, object]:
    """Resolve a built-in asset from the game registry and spawn its ModProp.

    The caller supplies only a name or a contained relative path. The reference
    handed to the writer is built here, from a catalog this process scanned and
    validated, so no raw GUID, payload, or serialized reference can be injected
    from outside.
    """
    catalog = load_editor_asset_catalog(asset_root)
    try:
        entry = resolve_editor_asset_entry(
            catalog, asset, folder=folder, require_spawnable=True
        )
    except EditorAssetNotFoundError:
        _reject_mod_local_asset(mod_root, asset)
        raise
    reference = build_verified_prop_reference(catalog, entry)
    return spawn_builtin_prop(
        scene_path,
        reference,
        parent=parent,
        name=name,
        position=position,
        rotation=rotation,
        scale=scale,
        tint_color=tint_color,
        collision_type=collision_type,
        shadow_casting_mode=shadow_casting_mode,
        layer=layer,
        tag=tag,
        active=active,
        expected_scene_hash=expected_scene_hash,
        dry_run=dry_run,
    )


def plan_blender_scene_import_details(
    scene_path, manifest_path, *, allowed_root, import_root, template_root
):
    return plan_blender_scene_import(
        scene_path,
        manifest_path,
        allowed_root=allowed_root,
        import_root=import_root,
        template_root=template_root,
    )


def build_blender_scene_import_details(
    scene_path,
    manifest_path,
    expected_plan_sha256,
    *,
    allowed_root,
    import_root,
    template_root,
):
    return build_blender_scene_import(
        scene_path,
        manifest_path,
        expected_plan_sha256,
        allowed_root=allowed_root,
        import_root=import_root,
        template_root=template_root,
    )


def _reject_mod_local_asset(mod_root: str | Path | None, asset: str) -> None:
    """Report a Mod-local asset request as cross-library rather than missing.

    ``ModProp.prop`` is loaded with ``Addressables.LoadAssetAsync<Prop>(guid)``,
    so only Internal assets can back it. A mod's own ``.pmeta`` sidecars describe
    External assets, which are a different library entirely.
    """
    if mod_root is None:
        return
    root = Path(mod_root)
    try:
        local, _warnings = build_asset_catalog(root, root)
    except (OSError, ValueError):
        return
    wanted = Path(asset.replace("\\", "/")).name.casefold()
    for records in local.values():
        for record in records:
            if record.name is None or record.name.casefold() != wanted:
                continue
            raise EditorAssetNotInternalError(
                "EDITOR_ASSET_NOT_INTERNAL: "
                f"{record.name!r} is a Mod-local asset of this mod "
                f"({record.metadata_relative_path}), not a built-in Prop. Only "
                "assets registered in the game's Asset Browser library can back "
                "a ModProp reference"
            )


def list_minigame_archetype_details(): return list_minigame_archetypes()
def plan_minigame_details(scene_path,spec,*,mod_root): return plan_minigame(scene_path,mod_root,spec).to_dict()
def build_minigame_details(scene_path,spec,expected_plan_hash,*,mod_root): return build_minigame(scene_path,mod_root,spec,expected_plan_hash=expected_plan_hash)

def inspect_runtime_capability_details(scene_path, *, mod_root):
    return inspect_runtime_surface(scene_path, mod_root)

def plan_playtest_session_details(scene_path, *, mod_root, composition_id=None, gameplay_spec_hash=None, logical_mappings=None, expected_assertions=None, timeout_seconds=600):
    return plan_runtime_session(scene_path, mod_root, composition_id=composition_id, gameplay_spec_hash=gameplay_spec_hash, logical_mappings=logical_mappings, expected_assertions=expected_assertions, timeout_seconds=timeout_seconds)

def start_playtest_session_details(session_id, *, mod_root):
    return start_runtime_session(mod_root, session_id)

def collect_playtest_evidence_details(session_id, *, mod_root):
    return collect_runtime_evidence(mod_root, session_id)

def evaluate_playtest_details(session_id, *, mod_root):
    return evaluate_runtime_playtest(mod_root, session_id)

def list_gameplay_recipe_details():
    return list_gameplay_recipes()

def plan_gameplay_composition_details(scene_path, spec, *, catalog_root):
    return plan_gameplay_composition(scene_path, catalog_root, spec).to_dict()

def compose_gameplay_scene(scene_path, spec, *, catalog_root, expected_plan_sha256):
    return compose_gameplay(scene_path, catalog_root, spec, expected_plan_sha256=expected_plan_sha256)
from .pmh.schemas import SchemaValueError, get_property_schema
from .pmh.writer import ChangeReport


def get_scene_summary(scene_path: str | Path) -> dict[str, object]:
    scene = read_pmh(scene_path)
    validation = validate_scene(scene)
    return {
        "magic": scene.magic,
        "version": scene.version,
        "roots": scene.root_count,
        "game_objects": scene.object_count,
        "components": scene.component_count,
        "file_size": scene.byte_length,
        "fully_consumed": scene.fully_consumed,
        "valid": validation.passed,
    }


def plan_gameobject_reparent_details(scene_path, source, destination):
    return plan_gameobject_reparent(scene_path, source, destination).to_dict()


def reparent_scene_gameobject(scene_path, source, destination, *, expected_scene_hash=None):
    return reparent_gameobject(scene_path, source, destination, expected_scene_hash=expected_scene_hash)


def plan_subtree_duplication_details(scene_path, source):
    return plan_subtree_duplication(scene_path, source).to_dict()


def duplicate_scene_subtree(scene_path, source, *, expected_scene_hash=None):
    return duplicate_subtree(scene_path, source, expected_scene_hash=expected_scene_hash)


def register_scene_object_template(scene_path, source, template_id, *, catalog_root, expected_scene_hash=None, display_name=None):
    return register_object_template(scene_path, source, template_id, catalog_root=catalog_root, expected_scene_hash=expected_scene_hash, display_name=display_name)


def list_scene_object_templates(scene_path, *, catalog_root):
    return list_object_templates(scene_path, catalog_root=catalog_root)


def plan_scene_create_from_template(scene_path, template_id, *, catalog_root, destination_parent=None, position=None, rotation=None, scale=None, expected_scene_hash=None):
    return plan_create_from_template(scene_path, template_id, catalog_root=catalog_root, destination_parent=destination_parent, position=position, rotation=rotation, scale=scale, expected_scene_hash=expected_scene_hash).to_dict()


def create_scene_from_template(scene_path, template_id, *, catalog_root, destination_parent=None, position=None, rotation=None, scale=None, expected_scene_hash=None):
    return create_from_template(scene_path, template_id, catalog_root=catalog_root, destination_parent=destination_parent, position=position, rotation=rotation, scale=scale, expected_scene_hash=expected_scene_hash)


def inspect_reference_graph_details(
    scene_path: str | Path, *, offset: int = 0, limit: int = 100
) -> dict[str, object]:
    """Return a bounded, read-only view of the structure-backed scene graph."""
    if offset < 0:
        raise ValueError("offset must be zero or greater")
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    graph = build_reference_graph(scene_path)
    selected = graph.edges[offset : offset + limit]
    return {
        "read_only": True,
        "object_count": len(graph.objects),
        "component_count": len(graph.components),
        "edge_count": len(graph.edges),
        "offset": offset,
        "limit": limit,
        "returned": len(selected),
        "has_more": offset + len(selected) < len(graph.edges),
        "objects": [_graph_object(item) for item in graph.objects],
        "components": [_graph_component(item) for item in graph.components],
        "edges": [_graph_edge(item) for item in selected],
        "warnings": list(graph.warnings),
    }


def inspect_object_references_details(
    scene_path: str | Path, identifier: str
) -> dict[str, object]:
    loaded = PMHScene.load(scene_path)
    obj = loaded.get_object(identifier)
    graph = build_reference_graph(loaded.scene)
    component_ids = {item.guid for item in obj.components}
    owned_ids = component_ids | {obj.guid}
    outgoing = [edge for edge in graph.edges if edge.source.guid in owned_ids]
    incoming = [
        edge
        for edge in graph.edges
        if edge.target.guid in owned_ids and edge.source.guid not in owned_ids
    ]
    internal_classes = {
        "IDENTITY",
        "INTERNAL_OBJECT_REFERENCE",
        "INTERNAL_COMPONENT_REFERENCE",
        "HIERARCHY_REFERENCE",
    }
    external_classes = {
        "EXTERNAL_OBJECT_REFERENCE",
        "EXTERNAL_COMPONENT_REFERENCE",
        "ASSET_REFERENCE",
    }
    return {
        "read_only": True,
        "object": {"name": obj.name, "guid": obj.guid, "path": obj.hierarchy_path},
        "components": [
            {"type": item.type_name, "guid": item.guid} for item in obj.components
        ],
        "outgoing_references": [_graph_edge(item) for item in outgoing],
        "incoming_references": [_graph_edge(item) for item in incoming],
        "internal_references": [
            _graph_edge(item)
            for item in outgoing
            if item.classification.value in internal_classes
        ],
        "external_outgoing_references": [
            _graph_edge(item)
            for item in outgoing
            if item.classification.value in external_classes
        ],
        "external_incoming_references": [
            _graph_edge(item)
            for item in incoming
            if item.classification.value in external_classes
        ],
        "unknown_references": [
            _graph_edge(item)
            for item in outgoing + incoming
            if item.classification.value == "UNKNOWN"
        ],
        "warnings": list(graph.warnings),
    }


def analyze_duplication_safety_details(
    scene_path: str | Path, identifier: str
) -> dict[str, object]:
    details = inspect_object_references_details(scene_path, identifier)
    loaded = PMHScene.load(scene_path)
    obj = loaded.get_object(identifier)
    reasons: list[str] = []
    unknown_reference_types = sorted(
        {
            item["reference_kind"]
            for item in details["unknown_references"]
            if item["reference_kind"] not in KNOWN_REFERENCE_POLICY_KINDS
        }
    )
    if unknown_reference_types:
        reasons.append(
            "unknown reference serialization type(s): "
            + ", ".join(unknown_reference_types)
        )
    if obj.parent is None:
        reasons.append("root object duplication is outside the Stage 10B v0.6b scope")
    if obj.children:
        reasons.append("object has children; recursive subtree duplication is forbidden")
    known_types = {
        "ModTransform",
        "ModPlayerSpawn",
        "ModBoxCollider",
        "ModProp",
        "ModLight",
        "ModText",
        "ModTrigger",
    }
    unknown_types = sorted({item.type_name for item in obj.components} - known_types)
    if unknown_types:
        reasons.append(f"unknown component type(s): {', '.join(unknown_types)}")
    if details["unknown_references"]:
        reasons.append("unknown reference semantics are present")
    standard_transform_only = (
        len(obj.components) == 1
        and obj.components[0].type_name == "ModTransform"
        and obj.components[0].guid == obj.guid
        and tuple(item.name for item in obj.components[0].fields)
        == ("position", "rotation", "scale", "guid")
    )
    empty_trigger_boxcollider = False
    if [item.type_name for item in obj.components] == [
        "ModTransform", "ModBoxCollider", "ModTrigger"
    ]:
        transform, box, trigger = obj.components
        empty_trigger_boxcollider = (
            transform.guid == obj.guid
            and tuple(item.name for item in transform.fields)
            == ("position", "rotation", "scale", "guid")
            and tuple(item.name for item in box.fields) == ("center", "size", "guid")
            and tuple(item.name for item in trigger.fields)
            == (
                "TriggerShape", "Size", "Center", "Radius", "Height",
                "TriggerOnHit", "TriggerOnEnter", "TriggerOnExit", "TriggerOnStay",
                "OnHitActions", "OnEnterActions", "OnExitActions", "OnStayActions",
                "StayTriggerInterval", "DisableAfterTriggered", "OneUsePerPlayer", "guid",
            )
            and all(
                (lambda parsed: parsed.fully_consumed and not parsed.actions and not parsed.references)(
                    parse_action_payload(trigger.get_field(name).raw, source_property=name)
                )
                for name in ("OnHitActions", "OnEnterActions", "OnExitActions", "OnStayActions")
            )
        )
    safe_transform_leaf = (
        not reasons
        and standard_transform_only
        and obj.parent is not None
        and not obj.children
    )
    safe_known_leaf = (
        not reasons
        and empty_trigger_boxcollider
        and obj.parent is not None
        and not obj.children
    )
    status = (
        "UNSAFE_UNKNOWN_REFERENCE_TYPE"
        if unknown_reference_types
        else (
            "SAFE_TRANSFORM_ONLY_LEAF_DUPLICATION"
            if safe_transform_leaf
            else (
                "SAFE_EMPTY_TRIGGER_BOXCOLLIDER_LEAF_DUPLICATION"
                if safe_known_leaf
                else "UNSAFE"
            )
        )
    )
    if not safe_transform_leaf and not safe_known_leaf and not reasons:
        reasons.append(
            "object is outside the Oracle-approved exact leaf subsets"
        )
    return {
        **details,
        "duplication_status": status,
        "reasons": reasons,
        "approved_subset": (
            "parented leaf with exactly one standard ModTransform and no UNKNOWN references"
            if safe_transform_leaf
            else (
                "parented leaf with exact ModTransform + ModBoxCollider + ModTrigger order, "
                "four empty Action graphs, and no UNKNOWN references"
                if safe_known_leaf
                else None
            )
        ),
        "stage_10a_hard_gate_passed": True,
        "writer_available": safe_transform_leaf or safe_known_leaf,
    }


def plan_gameobject_duplication_details(
    scene_path: str | Path, identifier: str
) -> dict[str, object]:
    """Return a read-only ClonePlan or a structured fail-closed rejection."""
    try:
        return plan_leaf_duplication(scene_path, identifier).to_dict()
    except (
        UnsafeDuplicationError,
        AmbiguousObjectError,
        ObjectNotFoundError,
        GuidAllocationError,
    ) as exc:
        if isinstance(exc, AmbiguousObjectError):
            code = "UNSAFE_AMBIGUOUS_OBJECT"
        elif isinstance(exc, ObjectNotFoundError):
            code = "UNSAFE_OBJECT_NOT_FOUND"
        elif isinstance(exc, GuidAllocationError):
            code = "UNSAFE_GUID_ALLOCATION"
        else:
            code = str(exc).split(":", 1)[0]
        return {
            "safety_class": "UNSAFE",
            "reason_code": code,
            "reasons": [str(exc)],
            "dry_run": True,
            "writer_available": False,
        }


def duplicate_scene_gameobject(
    scene_path: str | Path,
    identifier: str,
    *,
    expected_scene_hash: str | None = None,
):
    """Execute the Oracle-approved Stage 10 leaf duplication writer."""
    return duplicate_leaf(
        scene_path,
        identifier,
        expected_scene_hash=expected_scene_hash,
    )


def _graph_object(item: Any) -> dict[str, object]:
    return {
        "guid": item.guid,
        "name": item.name,
        "path": item.hierarchy_path,
        "parent_guid": item.parent_guid,
        "children": list(item.child_guids),
        "components": list(item.component_guids),
        "preorder_index": item.preorder_index,
        "sibling_index": item.sibling_index,
        "hierarchy_span": _span_dict(item.hierarchy_span) if item.hierarchy_span else None,
        "identity_span": _span_dict(item.identity_span) if item.identity_span else None,
    }


def _graph_component(item: Any) -> dict[str, object]:
    return {
        "guid": item.guid,
        "type": item.type_name,
        "owner_gameobject_guid": item.owner_gameobject_guid,
        "identity_span": _span_dict(item.identity_span) if item.identity_span else None,
    }


def _graph_edge(item: Any) -> dict[str, object]:
    return {
        "source": {"kind": item.source.kind, "guid": item.source.guid, "label": item.source.label},
        "target": {"kind": item.target.kind, "guid": item.target.guid, "label": item.target.label},
        "property_path": item.property_path,
        "source_span": _span_dict(item.source_span) if item.source_span else None,
        "reference_kind": item.reference_kind,
        "classification": item.classification.value,
    }


def list_scene_objects(
    scene_path: str | Path,
    *,
    query: str | None = None,
    offset: int = 0,
    limit: int | None = 100,
) -> dict[str, object]:
    if offset < 0:
        raise ValueError("offset must be zero or greater")
    if limit is not None and limit < 1:
        raise ValueError("limit must be at least 1")
    scene = read_pmh(scene_path)
    folded_query = query.casefold() if query else None
    matches = [
        obj
        for obj in scene.walk()
        if folded_query is None
        or folded_query in obj.name.casefold()
        or folded_query in obj.hierarchy_path.casefold()
    ]
    selected = matches[offset:] if limit is None else matches[offset : offset + limit]
    return {
        "total": len(matches),
        "offset": offset,
        "limit": limit,
        "objects": [
            {"name": obj.name, "guid": obj.guid, "path": obj.hierarchy_path}
            for obj in selected
        ],
    }


def get_object_details(scene_path: str | Path, identifier: str) -> dict[str, object]:
    loaded = PMHScene.load(scene_path)
    obj = loaded.get_object(identifier)
    return _object_details(obj)


def get_components_details(
    scene_path: str | Path, identifier: str
) -> dict[str, object]:
    loaded = PMHScene.load(scene_path)
    obj = loaded.get_object(identifier)
    return {
        "object": {"name": obj.name, "guid": obj.guid, "path": obj.hierarchy_path},
        "components": [
            {
                "type": component.type_name,
                "guid": component.guid,
                "enabled": component.enabled,
                "properties": [
                    _field_summary(component.type_name, item)
                    for item in component.fields
                ],
            }
            for component in obj.components
        ],
    }


def get_transform_details(scene_path: str | Path, identifier: str) -> dict[str, object]:
    loaded = PMHScene.load(scene_path)
    obj = loaded.get_object(identifier)
    transform = loaded.get_transform(identifier)
    position = transform.get_field("position").value
    rotation = transform.get_field("rotation").value
    scale = transform.get_field("scale").value
    if not all(isinstance(value, Vector3) for value in (position, rotation, scale)):
        raise ValueError("ModTransform does not contain decoded Vector3 fields")
    return {
        "object": {"name": obj.name, "guid": obj.guid, "path": obj.hierarchy_path},
        "transform_guid": transform.guid,
        "position": asdict(position),
        "rotation": asdict(rotation),
        "scale": asdict(scale),
    }


def get_component_property_details(
    scene_path: str | Path,
    object_identifier: str,
    component_identifier: str,
    property_name: str,
) -> dict[str, object]:
    loaded = PMHScene.load(scene_path)
    obj = loaded.get_object(object_identifier)
    component = loaded.get_component(object_identifier, component_identifier)
    field = loaded.get_component_property(
        object_identifier, component_identifier, property_name
    )
    schema = get_property_schema(component.type_name, property_name)
    result: dict[str, object] = {
        "object": {
            "name": obj.name,
            "guid": obj.guid,
            "path": obj.hierarchy_path,
        },
        "component": {
            "type": component.type_name,
            "guid": component.guid,
        },
        "property": property_name,
        "raw_length": len(field.raw),
        "writable": bool(schema and schema.writable),
        "schema": schema.schema_name if schema else None,
    }
    if schema is not None and schema.codec is not None:
        try:
            decoded = schema.api_value(field.raw)
        except SchemaValueError:
            result["value"] = None
            result["confidence"] = DecodeConfidence.UNKNOWN.value
            result["writable"] = False
            result["summarized"] = True
        else:
            result["value"] = _bounded_value(decoded)
            result["confidence"] = schema.confidence.value
            result["summarized"] = False
    else:
        result["value"] = None
        result["confidence"] = DecodeConfidence.UNKNOWN.value
        result["summarized"] = True
    if schema is not None and schema.note:
        result["note"] = schema.note
    if component.type_name == "ModTrigger" and property_name in ACTION_EVENT_PROPERTIES:
        result["sha256"] = hashlib.sha256(field.raw).hexdigest()
        result["suggestion"] = "Use inspect_action_list for structured inspection."
    return result


def inspect_action_list_details(
    scene_path: str | Path,
    object_identifier: str,
    component_identifier: str,
    event_property: str,
    *,
    offset: int = 0,
    limit: int = 20,
    max_depth: int = MAX_ACTION_PARSE_DEPTH,
) -> dict[str, object]:
    """Inspect one existing ModTrigger Action list without exposing raw bytes."""
    if event_property not in ACTION_EVENT_PROPERTIES:
        allowed = ", ".join(sorted(ACTION_EVENT_PROPERTIES))
        raise ActionInspectionError(f"event_property must be one of: {allowed}")
    if offset < 0:
        raise ActionInspectionError("offset must be zero or greater")
    if not 1 <= limit <= 100:
        raise ActionInspectionError("limit must be between 1 and 100")
    if not 0 <= max_depth <= MAX_ACTION_PARSE_DEPTH:
        raise ActionInspectionError(
            f"max_depth must be between 0 and {MAX_ACTION_PARSE_DEPTH}"
        )

    loaded = PMHScene.load(scene_path)
    obj = loaded.get_object(object_identifier)
    component = loaded.get_component(object_identifier, component_identifier)
    if component.type_name != "ModTrigger":
        raise ActionInspectionError(
            f"component must be ModTrigger, got {component.type_name!r}"
        )
    field = component.get_field(event_property)
    parsed = parse_action_payload(
        field.raw,
        source_property=event_property,
        source_span=field.source_span,
        max_depth=max_depth,
    )
    selected = parsed.actions[offset : offset + limit]
    return {
        "object": {"name": obj.name, "guid": obj.guid, "path": obj.hierarchy_path},
        "component": {"type": component.type_name, "guid": component.guid},
        "event_property": event_property,
        "raw_length": parsed.raw_length,
        "sha256": parsed.sha256,
        "fully_consumed": parsed.fully_consumed,
        "parsed_ranges": [_span_dict(item) for item in parsed.parsed_ranges],
        "unparsed_ranges": [_span_dict(item) for item in parsed.unparsed_ranges],
        "prefix_u16": parsed.prefix_u16,
        "m_type": _bounded_value(parsed.m_type),
        "action_count": len(parsed.actions),
        "offset": offset,
        "limit": limit,
        "returned": len(selected),
        "actions": [_action_summary(item) for item in selected],
        "reference_summary": {
            "version": _bounded_value(parsed.references_version),
            "count": len(parsed.references),
            "duplicate_rids": list(parsed.duplicate_rids),
            "dangling_rids": list(parsed.dangling_rids),
            "unreferenced_rids": list(parsed.unreferenced_rids),
            "cycles": [list(item) for item in parsed.reference_cycles],
        },
        "warnings": list(parsed.warnings),
    }


def inspect_action_references_details(
    scene_path: str | Path,
    object_identifier: str,
    component_identifier: str,
    event_property: str,
    action_rid: int,
    *,
    offset: int = 0,
    limit: int = 10,
    allowed_root: str | Path | None = None,
) -> dict[str, object]:
    """Inspect reference-like fields on one existing Action without exposing bytes."""
    if event_property not in ACTION_EVENT_PROPERTIES:
        allowed = ", ".join(sorted(ACTION_EVENT_PROPERTIES))
        raise ActionReferenceError(f"event_property must be one of: {allowed}")
    if isinstance(action_rid, bool) or not isinstance(action_rid, int):
        raise ActionReferenceError("action_rid must be an integer")
    if offset < 0:
        raise ActionReferenceError("offset must be zero or greater")
    if not 1 <= limit <= 100:
        raise ActionReferenceError("limit must be between 1 and 100")

    loaded = PMHScene.load(scene_path)
    obj = loaded.get_object(object_identifier)
    component = loaded.get_component(object_identifier, component_identifier)
    if component.type_name != "ModTrigger":
        raise ActionReferenceError(
            f"component must be ModTrigger, got {component.type_name!r}"
        )
    field = component.get_field(event_property)
    parsed = parse_action_payload(
        field.raw,
        source_property=event_property,
        source_span=field.source_span,
    )
    matches = [item for item in parsed.actions if item.rid == action_rid]
    if not matches:
        raise ActionReferenceError(f"Action rid {action_rid} was not found")
    if len(matches) != 1:
        raise ActionReferenceError(
            f"Action rid {action_rid} identifies {len(matches)} entries"
        )
    action = matches[0]
    if action.resolved_reference is None:
        raise ActionReferenceError(
            f"Action rid {action_rid} is dangling, duplicated, or unresolved"
        )

    catalog = None
    catalog_warnings: list[str] = []
    mod_root_relative: str | None = None
    if allowed_root is not None:
        mod_root = find_mod_root(scene_path, allowed_root)
        if mod_root is None:
            catalog_warnings.append("no contained Mod root with an Assets directory was found")
        else:
            catalog, scan_warnings = build_asset_catalog(mod_root, allowed_root)
            catalog_warnings.extend(scan_warnings)
            root = Path(allowed_root).resolve(strict=True)
            mod_root_relative = mod_root.relative_to(root).as_posix() or "."

    inspection = inspect_action_entry_references(action, asset_catalog=catalog)
    segment_base = (
        inspection.reference_segment_span.start
        if inspection.reference_segment_span is not None
        else None
    )
    references = [
        _reference_summary(
            item,
            offset=offset,
            limit=limit,
            payload_base=segment_base,
            scene_base=field.source_span.start,
        )
        for item in inspection.references
    ]
    return {
        "object": {"name": obj.name, "guid": obj.guid, "path": obj.hierarchy_path},
        "component": {"type": component.type_name, "guid": component.guid},
        "event_property": event_property,
        "payload_sha256": parsed.sha256,
        "action": {
            "index": inspection.action_index,
            "rid": inspection.action_rid,
            "namespace": inspection.namespace,
            "class": inspection.class_name,
            "assembly": inspection.assembly,
        },
        "coordinate_space": inspection.coordinate_space,
        "reference_segment_span": (
            _span_dict(inspection.reference_segment_span)
            if inspection.reference_segment_span is not None
            else None
        ),
        "mod_root_relative_to_allowed_root": mod_root_relative,
        "reference_count": len(references),
        "references": references,
        "warnings": _bounded_warnings(catalog_warnings + list(inspection.warnings)),
        "writable": False,
    }


def set_scene_action_field(
    scene_path: str | Path,
    object_identifier: str,
    component_identifier: str,
    event_property: str,
    action_rid: int,
    expected_class: str,
    field_name: str,
    value: Any,
    *,
    dry_run: bool = False,
    expected_payload_sha256: str | None = None,
):
    return ActionFieldWriter(scene_path).set_action_field(
        object_identifier,
        component_identifier,
        event_property,
        action_rid,
        expected_class,
        field_name,
        value,
        dry_run=dry_run,
        expected_payload_sha256=expected_payload_sha256,
    )


def replace_scene_prefab_reference(
    scene_path: str | Path,
    object_identifier: str,
    component_identifier: str,
    event_property: str,
    action_rid: int,
    prefab_index: int,
    *,
    allowed_root: str | Path,
    target_prefab_guid: str | None = None,
    target_prefab_relative_path: str | None = None,
    expected_current_prefab_guid: str | None = None,
    template_reference_sha256: str | None = None,
    template_scene_path: str | Path | None = None,
    expected_payload_sha256: str | None = None,
    dry_run: bool = False,
):
    return PrefabReferenceWriter(scene_path, allowed_root).replace_prefab_reference(
        object_identifier,
        component_identifier,
        event_property,
        action_rid,
        prefab_index,
        target_prefab_guid=target_prefab_guid,
        target_prefab_relative_path=target_prefab_relative_path,
        expected_current_prefab_guid=expected_current_prefab_guid,
        template_reference_sha256=template_reference_sha256,
        template_scene_path=template_scene_path,
        expected_payload_sha256=expected_payload_sha256,
        dry_run=dry_run,
    )


def add_scene_action(
    scene_path: str | Path, object_identifier: str, component_identifier: str,
    event_property: str, action_class: str, *, insert_index: int | None = None,
    template_sha256: str | None = None,
    template_scene_path: str | Path | None = None,
    expected_payload_sha256: str | None = None, dry_run: bool = False,
):
    return ActionGraphWriter(scene_path).add_action(
        object_identifier, component_identifier, event_property, action_class,
        insert_index=insert_index, template_sha256=template_sha256,
        template_scene_path=template_scene_path,
        expected_payload_sha256=expected_payload_sha256, dry_run=dry_run,
    )


def delete_scene_action(
    scene_path: str | Path, object_identifier: str, component_identifier: str,
    event_property: str, action_rid: int, *,
    expected_payload_sha256: str | None = None, dry_run: bool = False,
):
    return ActionGraphWriter(scene_path).delete_action(
        object_identifier, component_identifier, event_property, action_rid,
        expected_payload_sha256=expected_payload_sha256, dry_run=dry_run,
    )


def move_scene_action(
    scene_path: str | Path, object_identifier: str, component_identifier: str,
    event_property: str, action_rid: int, new_index: int, *,
    expected_payload_sha256: str | None = None, dry_run: bool = False,
):
    return ActionGraphWriter(scene_path).move_action(
        object_identifier, component_identifier, event_property, action_rid, new_index,
        expected_payload_sha256=expected_payload_sha256, dry_run=dry_run,
    )


def set_scene_transform(
    scene_path: str | Path,
    identifier: str,
    *,
    position: Mapping[str, float | None] | None = None,
    rotation: Mapping[str, float | None] | None = None,
    scale: Mapping[str, float | None] | None = None,
    dry_run: bool = False,
) -> ChangeReport:
    return PMHScene.load(scene_path).set_transform(
        identifier,
        position=position,
        rotation=rotation,
        scale=scale,
        dry_run=dry_run,
    )


def set_scene_component_property(
    scene_path: str | Path,
    object_identifier: str,
    component_identifier: str,
    property_name: str,
    value: Any,
    *,
    dry_run: bool = False,
) -> ChangeReport:
    if property_name == "prop" and isinstance(value, Mapping) and "template_object" in value:
        required={"template_object","expected_scene_hash","template_reference_sha256"}
        if set(value) != required:
            raise ValueError("typed Prop replacement requires exactly template_object, expected_scene_hash, and template_reference_sha256")
        return replace_prop_reference_from_template(scene_path, object_identifier, value["template_object"], expected_scene_hash=value["expected_scene_hash"], template_reference_sha256=value["template_reference_sha256"])
    return PMHScene.load(scene_path).set_component_property(
        object_identifier,
        component_identifier,
        property_name,
        value,
        dry_run=dry_run,
    )


def validate_scene_file(scene_path: str | Path) -> dict[str, object]:
    scene = read_pmh(scene_path)
    validation = validate_scene(scene)
    return {
        "valid": validation.passed,
        "magic": scene.magic,
        "version": scene.version,
        "roots": scene.root_count,
        "game_objects": scene.object_count,
        "components": scene.component_count,
        "fully_consumed": scene.fully_consumed,
        "checks": dict(validation.checks),
        "errors": [name for name, passed in validation.checks.items() if not passed],
    }


def _object_details(obj: GameObject) -> dict[str, object]:
    return {
        "name": obj.name,
        "guid": obj.guid,
        "path": obj.hierarchy_path,
        "active": obj.active,
        "layer": obj.layer,
        "tag": obj.tag,
        "parent": (
            {"name": obj.parent.name, "guid": obj.parent.guid, "path": obj.parent.hierarchy_path}
            if obj.parent
            else None
        ),
        "children": [
            {"name": child.name, "guid": child.guid, "path": child.hierarchy_path}
            for child in obj.children
        ],
        "components": [
            {
                "type": component.type_name,
                "guid": component.guid,
                "enabled": component.enabled,
            }
            for component in obj.components
        ],
    }


def _field_summary(component_type: str, field: Field) -> dict[str, object]:
    schema = get_property_schema(component_type, field.name)
    if schema is not None and schema.codec is not None:
        try:
            decoded = schema.api_value(field.raw)
        except SchemaValueError:
            pass
        else:
            return {
                "name": field.name,
                "confidence": schema.confidence.value,
                "decoded": _bounded_value(decoded),
                "raw_length": len(field.raw),
                "schema": schema.schema_name,
                "writable": schema.writable,
                "summarized": False,
            }
    if (
        component_type == "ModTransform"
        and field.name in {"position", "rotation", "scale"}
        and isinstance(field.value, Vector3)
    ):
        return {
            "name": field.name,
            "confidence": field.confidence.value,
            "decoded": asdict(field.value),
            "raw_length": len(field.raw),
            "schema": "Vector3Float32",
            "writable": True,
            "summarized": False,
        }
    return {
        "name": field.name,
        "confidence": (
            schema.confidence.value if schema is not None else field.confidence.value
        ),
        "decoded": None,
        "raw_length": len(field.raw),
        "schema": schema.schema_name if schema is not None else None,
        "writable": False,
        "summarized": True,
    }


def _span_dict(span: Any) -> dict[str, int]:
    return {"start": span.start, "end": span.end, "length": span.length}


def _translated_span(span: Any, base: int | None) -> dict[str, int] | None:
    if base is None:
        return None
    return _span_dict(type(span)(base + span.start, span.length))


def _span_map(spans: Mapping[str, Any], *, limit: int = 100) -> dict[str, dict[str, int]]:
    return {
        (path if len(path) <= 256 else path[:256] + "…"): _span_dict(span)
        for path, span in list(spans.items())[:limit]
    }


def _bounded_warnings(warnings: list[str], *, limit: int = 100) -> list[str]:
    if len(warnings) <= limit:
        return warnings
    return warnings[:limit] + [f"{len(warnings) - limit} additional warning(s) omitted"]


def _reference_summary(
    reference: Any,
    *,
    offset: int,
    limit: int,
    payload_base: int | None,
    scene_base: int,
) -> dict[str, object]:
    selected = reference.items[offset : offset + limit]
    total = len(reference.items)
    field_payload_span = _translated_span(reference.source_span, payload_base)
    field_scene_span = None
    if field_payload_span is not None:
        field_scene_span = {
            "start": scene_base + field_payload_span["start"],
            "end": scene_base + field_payload_span["end"],
            "length": field_payload_span["length"],
        }
    return {
        "field": reference.source_field,
        "kind": reference.kind,
        "raw_shape": _json_shape(reference.raw_value),
        "normalized_value": _bounded_value(reference.normalized_value),
        "source_span": _span_dict(reference.source_span),
        "payload_source_span": field_payload_span,
        "scene_source_span": field_scene_span,
        "value_spans": (
            _span_map(reference.value_spans)
            if total == 0
            else {"$": _span_dict(reference.source_span)}
        ),
        "value_span_count": len(reference.value_spans),
        "value_spans_truncated": len(reference.value_spans) > 100,
        "fingerprint": {
            "namespace": reference.fingerprint.namespace,
            "class": reference.fingerprint.class_name,
            "field": reference.fingerprint.field_name,
            "source_span": _span_dict(reference.fingerprint.source_span),
            "raw_sha256": reference.fingerprint.raw_sha256,
            "normalized_sha256": reference.fingerprint.normalized_sha256,
        },
        "writable": False,
        "evidence_status": reference.evidence_status,
        "semantic_status": reference.semantic_status,
        "count": total,
        "offset": offset,
        "limit": limit,
        "returned": len(selected),
        "has_more": offset + len(selected) < total,
        "items": [
            _reference_item_summary(
                item,
                payload_base=payload_base,
                scene_base=scene_base,
            )
            for item in selected
        ],
        "warnings": list(reference.warnings),
    }


def _reference_item_summary(
    item: Any, *, payload_base: int | None, scene_base: int
) -> dict[str, object]:
    item_payload_span = _translated_span(item.source_span, payload_base)
    item_scene_span = None
    if item_payload_span is not None:
        item_scene_span = {
            "start": scene_base + item_payload_span["start"],
            "end": scene_base + item_payload_span["end"],
            "length": item_payload_span["length"],
        }
    resolution = None
    if item.resolution is not None:
        resolution = _bounded_value({
            "resolved": item.resolution.resolved,
            "guid": item.resolution.guid,
            "asset_type": item.resolution.asset_type,
            "asset_name": item.resolution.asset_name,
            "relative_path": item.resolution.relative_path,
            "metadata_relative_path": item.resolution.metadata_relative_path,
            "sha256": item.resolution.sha256,
            "status": item.resolution.status,
            "warnings": list(item.resolution.warnings),
        })
    return {
        "index": item.index,
        "kind": item.kind,
        "reference_sha256": hashlib.sha256(item.raw_bytes).hexdigest(),
        "raw_shape": _json_shape(item.raw_value),
        "normalized_value": _bounded_value(item.normalized_value),
        "source_span": _span_dict(item.source_span),
        "payload_source_span": item_payload_span,
        "scene_source_span": item_scene_span,
        "value_spans": _span_map(item.value_spans),
        "value_span_count": len(item.value_spans),
        "value_spans_truncated": len(item.value_spans) > 100,
        "unknown_fields": _bounded_value(item.unknown_fields),
        "resolution": resolution,
        "writable": False,
        "evidence_status": item.evidence_status,
        "warnings": list(item.warnings),
    }


def _json_shape(value: Any) -> dict[str, object]:
    if isinstance(value, Mapping):
        return {
            "type": "object",
            "keys_preview": _bounded_value(list(value)[:50]),
            "count": len(value),
            "truncated": len(value) > 50,
        }
    if isinstance(value, (list, tuple)):
        return {"type": "list", "count": len(value)}
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, (int, float)):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string", "length": len(value)}
    return {"type": type(value).__name__}


def _action_summary(entry: Any) -> dict[str, object]:
    reference = entry.resolved_reference
    type_info = entry.type_info
    warnings = list(entry.warnings)
    if reference is not None:
        warnings.extend(reference.warnings)
    schema = get_action_schema(
        type_info.namespace if type_info else None,
        type_info.class_name if type_info else None,
    )
    return {
        "index": entry.index,
        "rid": entry.rid,
        "resolved": reference is not None,
        "namespace": type_info.namespace if type_info else None,
        "class": type_info.class_name if type_info else None,
        "assembly": type_info.assembly if type_info else None,
        "type_tag": reference.type_tag if reference else None,
        "managed_reference_sha256": (
            hashlib.sha256(reference.raw_segment).hexdigest()
            if reference is not None and reference.raw_segment is not None
            else None
        ),
        "fields": _bounded_value(entry.fields),
        "field_schema": (
            {
                name: {
                    "logical_type": item.logical_type,
                    "writable": item.writable,
                    "evidence": item.evidence,
                    "note": item.note,
                }
                for name, item in schema.fields.items()
            }
            if schema is not None
            else None
        ),
        "creation_supported": bool(schema and schema.creation_supported),
        "template_type_tag": schema.type_tag if schema and schema.creation_supported else None,
        "nested_action_lists": [
            _nested_action_list_summary(item) for item in entry.nested_action_lists
        ],
        "warnings": warnings,
    }


def _nested_action_list_summary(action_list: ActionList) -> dict[str, object]:
    return {
        "source": action_list.source_property,
        "depth": action_list.depth,
        "m_type": _bounded_value(action_list.m_type),
        "action_count": len(action_list.actions),
        "actions": [_action_summary(item) for item in action_list.actions[:20]],
        "truncated": len(action_list.actions) > 20,
        "warnings": list(action_list.warnings),
    }


def _bounded_value(value: Any, *, _depth: int = 0) -> Any:
    if isinstance(value, (Vector2, Vector3, Vector4, Color4)):
        return asdict(value)
    if isinstance(value, dict):
        if _depth >= 6 or len(value) > 50:
            return {
                "type": "object",
                "count": len(value),
                "keys_preview": list(value)[:20],
                "truncated": True,
            }
        return {
            key: _bounded_value(item, _depth=_depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        if _depth >= 6 or len(value) > 20:
            return {
                "type": "list",
                "count": len(value),
                "items_preview": [
                    _bounded_value(item, _depth=_depth + 1) for item in value[:5]
                ],
                "truncated": len(value) > 5,
            }
        return [_bounded_value(item, _depth=_depth + 1) for item in value]
    if isinstance(value, str):
        if len(value) <= 256:
            return value
        return {
            "type": "string",
            "length": len(value),
            "preview": value[:256],
            "truncated": True,
        }
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    if isinstance(value, bytes):
        return {"type": "bytes", "length": len(value), "omitted": True}
    return {"type": type(value).__name__, "omitted": True}
