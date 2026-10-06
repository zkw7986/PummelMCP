"""Oracle-constrained planning for approved leaf duplication shapes."""

from __future__ import annotations

import hashlib
import struct
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from .errors import (
    ConcurrentModificationError,
    GuidAllocationError,
    UnsafeDuplicationError,
    WriterValidationError,
)
from .models import Component, GameObject, Scene, SourceSpan
from .actions import parse_action_payload
from .reference_graph import ReferenceClassification, build_reference_graph
from .asset_references import AssetReferenceError, parse_compact_asset_reference, parse_material_reference_list
from .writer import ObjectIdentifier, PMHScene


MAX_GUID_ATTEMPTS = 32
STANDARD_TRANSFORM_FIELDS = ("position", "rotation", "scale", "guid")
STANDARD_BOX_COLLIDER_FIELDS = ("center", "size", "guid")
STANDARD_PLAYER_SPAWN_FIELDS = (
    "SharedSpawn", "SpawnUsageType", "AllowedPlayers", "SpawnShape",
    "PlayerCountMask", "SpawnDistribution", "LineLength", "Radius", "Advanced", "guid",
)
STANDARD_LIGHT_FIELDS = ("type", "color", "range", "intensity", "spotAngle", "shadows", "guid")
STANDARD_TEXT_FIELDS = (
    "Text", "Font", "Color", "FontSize", "HorizontalAlignment", "Size",
    "FontStyles", "Outline", "OutlineThickness", "OutlineColor", "Shadow",
    "ShadowColor", "ShadowOffsetX", "ShadowOffsetY", "ShadowSoftness", "guid",
)
STANDARD_TRIGGER_FIELDS = (
    "TriggerShape", "Size", "Center", "Radius", "Height",
    "TriggerOnHit", "TriggerOnEnter", "TriggerOnExit", "TriggerOnStay",
    "OnHitActions", "OnEnterActions", "OnExitActions", "OnStayActions",
    "StayTriggerInterval", "DisableAfterTriggered", "OneUsePerPlayer", "guid",
)
EMPTY_TRIGGER_FIELDS = (
    "OnHitActions", "OnEnterActions", "OnExitActions", "OnStayActions"
)
TRANSFORM_ONLY_SHAPE = ("ModTransform",)
EMPTY_TRIGGER_BOXCOLLIDER_SHAPE = (
    "ModTransform", "ModBoxCollider", "ModTrigger"
)
PLAYER_SPAWN_SHAPE = ("ModTransform", "ModPlayerSpawn")
LIGHT_SHAPE = ("ModTransform", "ModLight")
TEXT_SHAPE = ("ModTransform", "ModText")
PROP_SHAPE = ("ModTransform", "ModProp")
STANDARD_PROP_FIELDS = ("prop", "tintColor", "collisionType", "shadowCastingMode", "customMaterials", "guid")

KNOWN_VALUE_SHAPES = {
    PLAYER_SPAWN_SHAPE: (STANDARD_PLAYER_SPAWN_FIELDS, "SAFE_PLAYERSPAWN_LEAF_DUPLICATION"),
    LIGHT_SHAPE: (STANDARD_LIGHT_FIELDS, "SAFE_LIGHT_LEAF_DUPLICATION"),
    TEXT_SHAPE: (STANDARD_TEXT_FIELDS, "SAFE_TEXT_LEAF_DUPLICATION"),
}


@dataclass(frozen=True, slots=True)
class CloneComponent:
    type: str
    source_guid: str
    new_guid: str
    component_index_span: SourceSpan
    payload_span: SourceSpan
    payload_hash: str
    identity_policy: str
    payload_policy: str
    reference_policy: str
    unknown_fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CloneSource:
    name: str
    gameobject_guid: str
    transform_guid: str
    parent_guid: str
    sibling_index: int
    preorder_index: int
    hierarchy_span: SourceSpan
    object_index_span: SourceSpan
    transform_payload_span: SourceSpan
    components: tuple[CloneComponent, ...]
    serialized_span_sha256: dict[str, str]


@dataclass(frozen=True, slots=True)
class CloneDestination:
    gameobject_guid: str
    transform_guid: str
    parent_guid: str
    sibling_index: int
    preorder_index: int
    serialized_name: str
    component_guids: dict[str, str]


@dataclass(frozen=True, slots=True)
class PlannedMutation:
    kind: str
    source_offset: int
    source_length: int
    output_length: int


@dataclass(frozen=True, slots=True)
class ClonePlan:
    safety_class: str
    source_file: Path
    source_sha256: str
    source: CloneSource
    destination: CloneDestination
    identity_mapping: dict[str, dict[str, str]]
    insert_policy: str
    component_policy: str
    reference_policy: str
    mutations: tuple[PlannedMutation, ...]

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["source_file"] = str(self.source_file)
        result["dry_run"] = True
        result["writer_available"] = True
        return result


@dataclass(frozen=True, slots=True)
class DuplicationValidation:
    passed: bool
    checks: dict[str, bool]


@dataclass(frozen=True, slots=True)
class DuplicationReport:
    source_file: Path
    source_name: str
    source_guid: str
    duplicate_name: str
    duplicate_guid: str
    parent_name: str
    parent_guid: str
    sibling_index: int
    preorder_index: int
    components: tuple[str, ...]
    safety_class: str
    backup_path: Path | None
    before_sha256: str
    after_sha256: str
    bytes_added: int
    validation: DuplicationValidation

    def to_dict(self) -> dict[str, object]:
        return {
            "source": {"name": self.source_name, "guid": self.source_guid},
            "duplicate": {"name": self.duplicate_name, "guid": self.duplicate_guid},
            "parent": {"name": self.parent_name, "guid": self.parent_guid},
            "sibling_index": self.sibling_index,
            "preorder_index": self.preorder_index,
            "components": list(self.components),
            "safety_class": self.safety_class,
            "source_file": str(self.source_file),
            "backup_path": str(self.backup_path) if self.backup_path else None,
            "before_sha256": self.before_sha256,
            "after_sha256": self.after_sha256,
            "bytes_added": self.bytes_added,
            "validation": {
                "passed": self.validation.passed,
                "checks": dict(self.validation.checks),
            },
        }


def _span_bytes(raw: bytes, span: SourceSpan) -> bytes:
    return raw[span.start : span.end]


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _component_payload_length(component: Component) -> int:
    return 2 + sum(
        1 + len(field.name.encode("utf-8")) + 4 + len(field.raw)
        for field in component.fields
    )


def _layout(scene: Scene) -> tuple[int, dict[str, SourceSpan], dict[str, int]]:
    objects = list(scene.walk())
    if not objects or any(obj.index_span is None for obj in objects):
        raise UnsafeDuplicationError("UNSAFE_HIERARCHY: object index spans are incomplete")
    index_end = max(obj.index_span.end for obj in objects if obj.index_span is not None)
    cursor = index_end
    component_spans: dict[str, SourceSpan] = {}
    object_payload_starts: dict[str, int] = {}
    for obj in objects:
        object_payload_starts[obj.guid] = cursor
        for component in obj.components:
            length = _component_payload_length(component)
            component_spans[component.guid] = SourceSpan(cursor, length)
            cursor += length
    if cursor != scene.byte_length:
        raise UnsafeDuplicationError(
            "UNSAFE_HIERARCHY: computed Component payload layout does not reach EOF"
        )
    return index_end, component_spans, object_payload_starts


def _parent_child_count_offset(parent: GameObject) -> int:
    if parent.hierarchy_span is None:
        raise UnsafeDuplicationError("UNSAFE_HIERARCHY: parent hierarchy span is absent")
    return (
        parent.hierarchy_span.start
        + 1
        + len(parent.name.encode("utf-8"))
        + 1
        + 4
        + 1
        + len(parent.tag.encode("utf-8"))
    )


def _subtree_size(obj: GameObject) -> int:
    return sum(1 for _ in obj.walk())


def _canonical_uuid4(value: object) -> str | None:
    try:
        parsed = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None
    text = str(parsed)
    if parsed.version != 4 or parsed.variant != "specified in RFC 4122":
        return None
    return text if text == text.lower() and len(text) == 36 else None


def generate_unique_uuid4(
    scene: Scene,
    *,
    guid_factory: Callable[[], object] = uuid.uuid4,
    max_attempts: int = MAX_GUID_ATTEMPTS,
) -> str:
    occupied = {obj.guid.casefold() for obj in scene.walk()}
    occupied.update(
        component.guid.casefold()
        for obj in scene.walk()
        for component in obj.components
    )
    for _ in range(max_attempts):
        candidate = _canonical_uuid4(guid_factory())
        if candidate is not None and candidate.casefold() not in occupied:
            return candidate
    raise GuidAllocationError(
        f"could not allocate a collision-free canonical UUID v4 in {max_attempts} attempts"
    )


def _validate_source_surface(
    loaded: PMHScene, source: GameObject, *, allow_known_components: bool
) -> tuple[tuple[Component, ...], GameObject, str]:
    if source.children:
        raise UnsafeDuplicationError("UNSAFE_HAS_CHILDREN: subtree duplication is forbidden")
    if source.parent is None:
        raise UnsafeDuplicationError("UNSAFE_ROOT_OBJECT: root duplication is forbidden")
    shape = tuple(component.type_name for component in source.components)
    allowed_shapes = {TRANSFORM_ONLY_SHAPE}
    if allow_known_components:
        allowed_shapes.add(EMPTY_TRIGGER_BOXCOLLIDER_SHAPE)
        allowed_shapes.update(KNOWN_VALUE_SHAPES)
        allowed_shapes.add(PROP_SHAPE)
    if shape not in allowed_shapes:
        kinds = ", ".join(shape)
        raise UnsafeDuplicationError(
            "UNSAFE_UNSUPPORTED_COMPONENT: expected an approved exact Component "
            f"sequence, found {kinds or 'none'}"
        )
    transform = source.components[0]
    if source.guid != transform.guid:
        raise UnsafeDuplicationError(
            "UNSAFE_INVALID_IDENTITY: GameObject and ModTransform GUIDs differ"
        )
    if tuple(field.name for field in transform.fields) != STANDARD_TRANSFORM_FIELDS:
        raise UnsafeDuplicationError(
            "UNSAFE_UNSUPPORTED_COMPONENT: ModTransform does not have the standard field sequence"
        )
    for component in source.components:
        guid_field = component.get_field("guid")
        if (
            component.identity_span is None
            or component.index_span is None
            or guid_field.source_span is None
            or guid_field.value != component.guid
        ):
            raise UnsafeDuplicationError(
                f"UNSAFE_INVALID_IDENTITY: {component.type_name} index/payload GUIDs differ"
            )
    if shape == EMPTY_TRIGGER_BOXCOLLIDER_SHAPE:
        box = source.components[1]
        trigger = source.components[2]
        if tuple(field.name for field in box.fields) != STANDARD_BOX_COLLIDER_FIELDS:
            raise UnsafeDuplicationError(
                "UNSAFE_UNKNOWN_FIELD: ModBoxCollider does not have the Oracle-approved field sequence"
            )
        if tuple(field.name for field in trigger.fields) != STANDARD_TRIGGER_FIELDS:
            raise UnsafeDuplicationError(
                "UNSAFE_UNKNOWN_FIELD: ModTrigger does not have the Oracle-approved field sequence"
            )
        for field_name in EMPTY_TRIGGER_FIELDS:
            parsed = parse_action_payload(
                trigger.get_field(field_name).raw, source_property=field_name
            )
            if not parsed.fully_consumed or parsed.actions or parsed.references:
                raise UnsafeDuplicationError(
                    "UNSAFE_POPULATED_ACTION_GRAPH: all four ModTrigger Action graphs must be empty"
                )
    elif shape == PROP_SHAPE:
        prop = source.components[1]
        if tuple(field.name for field in prop.fields) != STANDARD_PROP_FIELDS:
            raise UnsafeDuplicationError("UNSAFE_UNKNOWN_FIELD: ModProp does not have the audited field sequence")
        try:
            parse_compact_asset_reference(prop.get_field("prop").raw)
            materials = parse_material_reference_list(prop.get_field("customMaterials").raw)
        except AssetReferenceError as exc:
            raise UnsafeDuplicationError(f"UNSAFE_UNKNOWN_REFERENCE_TYPE: {exc}") from exc
        if materials.elements:
            raise UnsafeDuplicationError("UNSUPPORTED_MATERIAL_OVERRIDE: Prop duplication requires empty customMaterials")
    elif shape in KNOWN_VALUE_SHAPES:
        expected_fields, _ = KNOWN_VALUE_SHAPES[shape]
        component = source.components[1]
        if tuple(field.name for field in component.fields) != expected_fields:
            raise UnsafeDuplicationError(
                f"UNSAFE_UNKNOWN_FIELD: {component.type_name} does not have the audited field sequence"
            )
    if any(
        span is None
        for span in (
            source.hierarchy_span,
            source.identity_span,
            source.index_span,
            *(component.identity_span for component in source.components),
            *(component.index_span for component in source.components),
        )
    ):
        raise UnsafeDuplicationError("UNSAFE_HIERARCHY: required source spans are absent")
    parent = source.parent
    if (
        source.sibling_index < 0
        or source.sibling_index >= len(parent.children)
        or parent.children[source.sibling_index] is not source
    ):
        raise UnsafeDuplicationError("UNSAFE_HIERARCHY: sibling membership is inconsistent")
    graph = build_reference_graph(loaded.scene)
    owned = {source.guid, *(component.guid for component in source.components)}
    related = [
        edge
        for edge in graph.edges
        if edge.source.guid in owned or edge.target.guid in owned
    ]
    if any(edge.classification is ReferenceClassification.UNKNOWN for edge in related):
        raise UnsafeDuplicationError(
            "UNSAFE_UNKNOWN_REFERENCE_TYPE: unknown incoming or outgoing reference is present"
        )
    outgoing = [
        edge
        for edge in graph.edges
        if edge.source.guid in owned
        and edge.reference_kind
        not in {"GameObjectIdentity", "ComponentIdentityProperty", "SerializedComponentMembership"}
    ]
    allowed_outgoing = {"PropAssetReference", "MaterialReferenceList"} if shape == PROP_SHAPE else set()
    if any(edge.reference_kind not in allowed_outgoing for edge in outgoing):
        raise UnsafeDuplicationError(
            "UNSAFE_UNKNOWN_REFERENCE_TYPE: approved leaf source must have no unsupported outgoing references"
        )
    if shape == TRANSFORM_ONLY_SHAPE:
        safety_class = "SAFE_TRANSFORM_ONLY_LEAF_DUPLICATION"
    elif shape == EMPTY_TRIGGER_BOXCOLLIDER_SHAPE:
        safety_class = "SAFE_EMPTY_TRIGGER_BOXCOLLIDER_LEAF_DUPLICATION"
    elif shape == PROP_SHAPE:
        safety_class = "SAFE_PROP_LEAF_DUPLICATION_EMPTY_MATERIALS_CANDIDATE"
    else:
        safety_class = KNOWN_VALUE_SHAPES[shape][1]
    return tuple(source.components), parent, safety_class


def plan_transform_only_leaf_duplication(
    scene_path: str | Path,
    identifier: ObjectIdentifier | str,
    *,
    guid_factory: Callable[[], object] = uuid.uuid4,
    max_guid_attempts: int = MAX_GUID_ATTEMPTS,
) -> ClonePlan:
    """Create a complete, read-only plan for the approved Stage 10B subset."""
    return _plan_leaf_duplication(
        scene_path,
        identifier,
        guid_factory=guid_factory,
        max_guid_attempts=max_guid_attempts,
        allow_known_components=False,
    )


def plan_leaf_duplication(
    scene_path: str | Path,
    identifier: ObjectIdentifier | str,
    *,
    guid_factory: Callable[[], object] = uuid.uuid4,
    max_guid_attempts: int = MAX_GUID_ATTEMPTS,
) -> ClonePlan:
    """Plan either Oracle-approved transform-only or empty-trigger leaf duplication."""
    return _plan_leaf_duplication(
        scene_path,
        identifier,
        guid_factory=guid_factory,
        max_guid_attempts=max_guid_attempts,
        allow_known_components=True,
    )


def _plan_leaf_duplication(
    scene_path: str | Path,
    identifier: ObjectIdentifier | str,
    *,
    guid_factory: Callable[[], object],
    max_guid_attempts: int,
    allow_known_components: bool,
) -> ClonePlan:
    loaded = PMHScene.load(scene_path)
    source = loaded.get_object(identifier)
    components, parent, safety_class = _validate_source_surface(
        loaded, source, allow_known_components=allow_known_components
    )
    transform = components[0]
    raw = loaded.source.read_bytes()
    if raw != loaded._original_bytes:
        raise UnsafeDuplicationError("UNSAFE_STALE_SCENE: source changed while planning")
    index_end, component_spans, object_payload_starts = _layout(loaded.scene)
    transform_payload_span = component_spans[transform.guid]
    occupied = {
        *(obj.guid.casefold() for obj in loaded.scene.walk()),
        *(component.guid.casefold() for obj in loaded.scene.walk() for component in obj.components),
    }
    new_guids: list[str] = []
    for _component in components:
        allocated = None
        for _ in range(max_guid_attempts):
            candidate = _canonical_uuid4(guid_factory())
            if candidate is not None and candidate.casefold() not in occupied:
                allocated = candidate
                occupied.add(candidate.casefold())
                break
        if allocated is None:
            raise GuidAllocationError(
                f"could not allocate a collision-free canonical UUID v4 in {max_guid_attempts} attempts"
            )
        new_guids.append(allocated)
    new_guid = new_guids[0]
    expected_preorder = parent.preorder_index + _subtree_size(parent)
    objects = list(loaded.scene.walk())
    if expected_preorder < len(objects):
        next_index = objects[expected_preorder].index_span
        if next_index is None:
            raise UnsafeDuplicationError("UNSAFE_HIERARCHY: destination index span is absent")
        index_insert = next_index.start
        payload_insert = object_payload_starts[objects[expected_preorder].guid]
    else:
        index_insert = index_end
        payload_insert = loaded.scene.byte_length
    count_offset = _parent_child_count_offset(parent)
    if struct.unpack("<H", raw[count_offset : count_offset + 2])[0] != len(parent.children):
        raise UnsafeDuplicationError("UNSAFE_HIERARCHY: parent child count bytes disagree")
    assert source.hierarchy_span is not None
    assert source.index_span is not None
    source_spans = {
        "hierarchy": _sha(_span_bytes(raw, source.hierarchy_span)),
        "object_index": _sha(_span_bytes(raw, source.index_span)),
        "transform_payload": _sha(_span_bytes(raw, transform_payload_span)),
    }
    component_plans = tuple(
        CloneComponent(
            type=component.type_name,
            source_guid=component.guid,
            new_guid=new_component_guid,
            component_index_span=component.index_span,
            payload_span=component_spans[component.guid],
            payload_hash=_sha(_span_bytes(raw, component_spans[component.guid])),
            identity_policy=(
                "GAMEOBJECT_IDENTITY"
                if component.type_name == "ModTransform"
                else "INDEPENDENT_UUID"
            ),
            payload_policy=(
                "COPY_MODTRANSFORM"
                if component.type_name == "ModTransform"
                else "COPY_KNOWN_EMPTY_TRIGGER_PAYLOAD"
                if component.type_name == "ModTrigger"
                else "COPY_NON_IDENTITY_EXACTLY"
            ),
            reference_policy=(
                "EMPTY_ONLY" if component.type_name == "ModTrigger" else "VALUE_ONLY"
            ),
        )
        for component, new_component_guid in zip(components, new_guids, strict=True)
    )
    if len(component_plans) > 1:
        for item in component_plans:
            source_spans[f"{item.type}_payload"] = item.payload_hash
    payload_start = component_plans[0].payload_span.start
    payload_end = component_plans[-1].payload_span.end
    payload_kind = (
        "INSERT_MODTRANSFORM_PAYLOAD"
        if len(component_plans) == 1
        else "INSERT_COMPONENT_PAYLOADS"
    )
    return ClonePlan(
        safety_class=safety_class,
        source_file=loaded.source,
        source_sha256=_sha(raw),
        source=CloneSource(
            name=source.name,
            gameobject_guid=source.guid,
            transform_guid=transform.guid,
            parent_guid=parent.guid,
            sibling_index=source.sibling_index,
            preorder_index=source.preorder_index,
            hierarchy_span=source.hierarchy_span,
            object_index_span=source.index_span,
            transform_payload_span=transform_payload_span,
            components=component_plans,
            serialized_span_sha256=source_spans,
        ),
        destination=CloneDestination(
            gameobject_guid=new_guid,
            transform_guid=new_guid,
            parent_guid=parent.guid,
            sibling_index=len(parent.children),
            preorder_index=expected_preorder,
            serialized_name=source.name,
            component_guids={item.type: item.new_guid for item in component_plans},
        ),
        identity_mapping={
            "gameobject": {"source": source.guid, "duplicate": new_guid},
            **{
                item.type.replace("Mod", "mod_").casefold(): {
                    "source": item.source_guid,
                    "duplicate": item.new_guid,
                }
                for item in component_plans
            },
        },
        insert_policy="APPEND_TO_PARENT_CHILDREN",
        component_policy=(
            "COPY_MODTRANSFORM"
            if len(component_plans) == 1
            else "COPY_APPROVED_COMPONENT_SEQUENCE"
        ),
        reference_policy=(
            "NONE_PRESENT"
            if len(component_plans) == 1
            else "IDENTITY_REGENERATE_HIERARCHY_REBUILD_VALUE_COPY_EMPTY_ACTION_ONLY"
        ),
        mutations=(
            PlannedMutation("UPDATE_PARENT_CHILD_COUNT", count_offset, 2, 2),
            PlannedMutation(
                "INSERT_HIERARCHY_RECORD",
                parent.hierarchy_span.end,
                0,
                source.hierarchy_span.length,
            ),
            PlannedMutation(
                "INSERT_OBJECT_INDEX_RECORD",
                index_insert,
                0,
                source.index_span.length,
            ),
            PlannedMutation(
                payload_kind,
                payload_insert,
                0,
                payload_end - payload_start,
            ),
        ),
    )


def _mutation(plan: ClonePlan, kind: str) -> PlannedMutation:
    matches = [item for item in plan.mutations if item.kind == kind]
    if len(matches) != 1:
        raise WriterValidationError(f"ClonePlan has no unique {kind} mutation")
    return matches[0]


def _patch_uuid_slot(
    record: bytearray,
    *,
    record_start: int,
    identity_span: SourceSpan,
    new_guid: str,
) -> None:
    relative = identity_span.start - record_start
    if identity_span.length != 37 or record[relative] != 36:
        raise WriterValidationError("identity slot is not a canonical str8 UUID")
    record[relative + 1 : relative + 37] = new_guid.encode("ascii")


def _construct_duplicate_bytes(loaded: PMHScene, plan: ClonePlan) -> bytes:
    raw = loaded._original_bytes
    source = loaded.get_object(plan.source.gameobject_guid)
    assert source.hierarchy_span is not None
    assert source.index_span is not None

    hierarchy = _span_bytes(raw, source.hierarchy_span)
    index_record = bytearray(_span_bytes(raw, source.index_span))
    _patch_uuid_slot(
        index_record,
        record_start=source.index_span.start,
        identity_span=source.identity_span,
        new_guid=plan.destination.gameobject_guid,
    )
    for component, component_plan in zip(
        source.components, plan.source.components, strict=True
    ):
        if component.identity_span is None:
            raise WriterValidationError(f"{component.type_name} index identity span is absent")
        _patch_uuid_slot(
            index_record,
            record_start=source.index_span.start,
            identity_span=component.identity_span,
            new_guid=component_plan.new_guid,
        )

    payload_start = plan.source.components[0].payload_span.start
    payload_end = plan.source.components[-1].payload_span.end
    payload = bytearray(raw[payload_start:payload_end])
    for component, component_plan in zip(
        source.components, plan.source.components, strict=True
    ):
        guid_field = component.get_field("guid")
        if guid_field.source_span is None:
            raise WriterValidationError(f"{component.type_name}.guid payload span is absent")
        _patch_uuid_slot(
            payload,
            record_start=payload_start,
            identity_span=guid_field.source_span,
            new_guid=component_plan.new_guid,
        )

    count = _mutation(plan, "UPDATE_PARENT_CHILD_COUNT")
    hierarchy_insert = _mutation(plan, "INSERT_HIERARCHY_RECORD")
    index_insert = _mutation(plan, "INSERT_OBJECT_INDEX_RECORD")
    payload_insert = _mutation(
        plan,
        "INSERT_MODTRANSFORM_PAYLOAD"
        if len(plan.source.components) == 1
        else "INSERT_COMPONENT_PAYLOADS",
    )
    parent = source.parent
    if parent is None:
        raise WriterValidationError("planned source unexpectedly became a root")
    operations = [
        (count.source_offset, count.source_length, struct.pack("<H", len(parent.children) + 1)),
        (hierarchy_insert.source_offset, 0, hierarchy),
        (index_insert.source_offset, 0, bytes(index_record)),
        (payload_insert.source_offset, 0, bytes(payload)),
    ]
    patched = bytearray(raw)
    for offset, old_length, replacement in sorted(operations, reverse=True):
        patched[offset : offset + old_length] = replacement
    return bytes(patched)


def _component_snapshot(component: Component) -> tuple[object, ...]:
    return (
        component.type_name,
        component.guid,
        component.enabled,
        tuple((field.name, field.raw) for field in component.fields),
    )


def _source_snapshot(obj: GameObject) -> tuple[object, ...]:
    return (
        obj.name,
        obj.active,
        obj.layer,
        obj.tag,
        obj.parent.guid if obj.parent else None,
        obj.sibling_index,
        obj.preorder_index,
        tuple(_component_snapshot(component) for component in obj.components),
    )


def _stable_object_snapshot(obj: GameObject) -> tuple[object, ...]:
    """Snapshot serialized object values while allowing necessary preorder shifts."""
    return (
        obj.name,
        obj.active,
        obj.layer,
        obj.tag,
        obj.parent.guid if obj.parent else None,
        tuple(_component_snapshot(component) for component in obj.components),
    )


def _validate_duplicate(
    original: Scene,
    original_raw: bytes,
    reparsed: Scene,
    patched: bytes,
    plan: ClonePlan,
) -> DuplicationValidation:
    source_before = next(obj for obj in original.walk() if obj.guid == plan.source.gameobject_guid)
    source_after = [obj for obj in reparsed.walk() if obj.guid == plan.source.gameobject_guid]
    duplicates = [obj for obj in reparsed.walk() if obj.guid == plan.destination.gameobject_guid]
    old_object_guids = [obj.guid for obj in original.walk()]
    new_object_guids = [obj.guid for obj in reparsed.walk()]
    old_component_guids = [c.guid for obj in original.walk() for c in obj.components]
    new_component_guids = [c.guid for obj in reparsed.walk() for c in obj.components]
    checks: dict[str, bool] = {
        "parse_to_exact_eof": reparsed.fully_consumed,
        "magic_and_version_preserved": (reparsed.magic, reparsed.version)
        == (original.magic, original.version),
        "root_count_preserved": reparsed.root_count == original.root_count,
        "gameobject_count_incremented_once": reparsed.object_count == original.object_count + 1,
        "component_count_incremented_by_source_shape": (
            reparsed.component_count
            == original.component_count + len(source_before.components)
        ),
        "gameobject_guids_unique": len(new_object_guids) == len(set(new_object_guids)),
        "component_guids_unique": len(new_component_guids) == len(set(new_component_guids)),
        "old_gameobject_identities_preserved": set(old_object_guids) < set(new_object_guids),
        "old_component_identities_preserved": set(old_component_guids) < set(new_component_guids),
        "source_resolves_once": len(source_after) == 1,
        "duplicate_resolves_once": len(duplicates) == 1,
    }
    duplicate = duplicates[0] if len(duplicates) == 1 else None
    retained = source_after[0] if len(source_after) == 1 else None
    checks["source_semantic_preservation"] = (
        retained is not None and _source_snapshot(retained) == _source_snapshot(source_before)
    )
    original_snapshots = {
        obj.guid: _stable_object_snapshot(obj) for obj in original.walk()
    }
    reparsed_by_guid = {obj.guid: obj for obj in reparsed.walk()}
    checks["all_existing_objects_preserved"] = all(
        guid in reparsed_by_guid
        and _stable_object_snapshot(reparsed_by_guid[guid]) == snapshot
        for guid, snapshot in original_snapshots.items()
    )
    if retained is not None and source_before.hierarchy_span and retained.hierarchy_span:
        checks["source_hierarchy_bytes_preserved"] = (
            _span_bytes(original_raw, source_before.hierarchy_span)
            == _span_bytes(patched, retained.hierarchy_span)
        )
    else:
        checks["source_hierarchy_bytes_preserved"] = False
    if duplicate is None or duplicate.parent is None:
        checks.update(
            {
                "same_parent": False,
                "append_to_parent_children": False,
                "duplicate_position": False,
                "duplicate_identity_relation": False,
                "duplicate_fields_copied": False,
                "component_order_preserved": False,
                "component_ownership_valid": False,
                "empty_action_graphs_preserved": False,
                "existing_siblings_preserved": False,
                "reference_graph_valid": False,
            }
        )
    else:
        original_parent = source_before.parent
        new_parent = duplicate.parent
        checks["same_parent"] = duplicate.parent.guid == plan.destination.parent_guid
        checks["append_to_parent_children"] = (
            original_parent is not None
            and [child.guid for child in new_parent.children]
            == [child.guid for child in original_parent.children]
            + [plan.destination.gameobject_guid]
        )
        checks["duplicate_position"] = (
            duplicate.sibling_index == plan.destination.sibling_index
            and duplicate.preorder_index == plan.destination.preorder_index
        )
        transforms = [c for c in duplicate.components if c.type_name == "ModTransform"]
        checks["duplicate_identity_relation"] = (
            len(transforms) == 1
            and duplicate.guid == transforms[0].guid == plan.destination.gameobject_guid
        )
        source_transform = source_before.get_component("ModTransform")
        expected_types = [item.type for item in plan.source.components]
        checks["component_order_preserved"] = (
            [component.type_name for component in duplicate.components] == expected_types
        )
        expected_component_guids = [item.new_guid for item in plan.source.components]
        checks["component_ownership_valid"] = (
            [component.guid for component in duplicate.components]
            == expected_component_guids
            and all(
                component.get_field("guid").value == component.guid
                for component in duplicate.components
            )
        )
        checks["duplicate_fields_copied"] = (
            len(transforms) == 1
            and duplicate.name == source_before.name
            and duplicate.active == source_before.active
            and duplicate.layer == source_before.layer
            and duplicate.tag == source_before.tag
            and transforms[0].enabled == source_transform.enabled
            and [
                (
                    component.type_name,
                    component.enabled,
                    [(f.name, f.raw) for f in component.fields if f.name != "guid"],
                )
                for component in duplicate.components
            ]
            == [
                (
                    component.type_name,
                    component.enabled,
                    [(f.name, f.raw) for f in component.fields if f.name != "guid"],
                )
                for component in source_before.components
            ]
        )
        duplicate_triggers = [
            component
            for component in duplicate.components
            if component.type_name == "ModTrigger"
        ]
        checks["empty_action_graphs_preserved"] = all(
            (
                lambda parsed: parsed.fully_consumed
                and not parsed.actions
                and not parsed.references
            )(
                parse_action_payload(
                    trigger.get_field(field_name).raw,
                    source_property=field_name,
                )
            )
            for trigger in duplicate_triggers
            for field_name in EMPTY_TRIGGER_FIELDS
        )
        checks["existing_siblings_preserved"] = (
            original_parent is not None
            and [
                (child.guid, child.sibling_index, child.preorder_index)
                for child in new_parent.children[:-1]
            ]
            == [
                (child.guid, child.sibling_index, child.preorder_index)
                for child in original_parent.children
            ]
        )
        graph = build_reference_graph(reparsed)
        duplicate_ids = {duplicate.guid, *(c.guid for c in duplicate.components)}
        duplicate_unknown = [
            edge
            for edge in graph.edges
            if edge.source.guid in duplicate_ids
            and edge.classification is ReferenceClassification.UNKNOWN
        ]
        checks["reference_graph_valid"] = not duplicate_unknown
    checks["expected_size_delta"] = len(patched) - len(original_raw) == sum(
        item.output_length - item.source_length for item in plan.mutations
    )
    validation = DuplicationValidation(all(checks.values()), checks)
    if not validation.passed:
        failed = ", ".join(name for name, passed in checks.items() if not passed)
        raise WriterValidationError(f"duplicate validation failed: {failed}")
    return validation


def duplicate_transform_only_leaf(
    scene_path: str | Path,
    identifier: ObjectIdentifier | str,
    *,
    expected_scene_hash: str | None = None,
    backup: bool = True,
    guid_factory: Callable[[], object] = uuid.uuid4,
) -> DuplicationReport:
    """Duplicate one approved transform-only leaf through temp validation and replace."""
    return _duplicate_leaf(
        scene_path,
        identifier,
        expected_scene_hash=expected_scene_hash,
        backup=backup,
        guid_factory=guid_factory,
        allow_known_components=False,
    )


def duplicate_leaf(
    scene_path: str | Path,
    identifier: ObjectIdentifier | str,
    *,
    expected_scene_hash: str | None = None,
    backup: bool = True,
    guid_factory: Callable[[], object] = uuid.uuid4,
) -> DuplicationReport:
    """Duplicate one Oracle-approved leaf through temp validation and atomic replace."""
    return _duplicate_leaf(
        scene_path,
        identifier,
        expected_scene_hash=expected_scene_hash,
        backup=backup,
        guid_factory=guid_factory,
        allow_known_components=True,
    )


def _duplicate_leaf(
    scene_path: str | Path,
    identifier: ObjectIdentifier | str,
    *,
    expected_scene_hash: str | None,
    backup: bool,
    guid_factory: Callable[[], object],
    allow_known_components: bool,
) -> DuplicationReport:
    loaded = PMHScene.load(scene_path)
    before_hash = loaded.source_sha256
    if expected_scene_hash is not None and expected_scene_hash.casefold() != before_hash:
        raise ConcurrentModificationError(
            "UNSAFE_STALE_SCENE: expected_scene_hash does not match the current Scene"
        )
    plan = _plan_leaf_duplication(
        loaded.source,
        identifier,
        guid_factory=guid_factory,
        max_guid_attempts=MAX_GUID_ATTEMPTS,
        allow_known_components=allow_known_components,
    )
    if plan.source_sha256 != before_hash:
        raise ConcurrentModificationError(
            "UNSAFE_STALE_SCENE: Scene changed between load and ClonePlan"
        )
    patched = _construct_duplicate_bytes(loaded, plan)
    try:
        validation, backup_path = loaded._replace_with_validation(
            patched,
            lambda reparsed, data: _validate_duplicate(
                loaded.scene, loaded._original_bytes, reparsed, data, plan
            ),
            backup=backup,
        )
    except ConcurrentModificationError as exc:
        raise ConcurrentModificationError(f"UNSAFE_STALE_SCENE: {exc}") from exc
    after_hash = _sha(patched)
    source = loaded.get_object(plan.source.gameobject_guid)
    parent = source.parent
    if parent is None:
        raise WriterValidationError("validated source unexpectedly has no parent")
    return DuplicationReport(
        source_file=loaded.source,
        source_name=plan.source.name,
        source_guid=plan.source.gameobject_guid,
        duplicate_name=plan.destination.serialized_name,
        duplicate_guid=plan.destination.gameobject_guid,
        parent_name=parent.name,
        parent_guid=plan.destination.parent_guid,
        sibling_index=plan.destination.sibling_index,
        preorder_index=plan.destination.preorder_index,
        components=tuple(item.type for item in plan.source.components),
        safety_class=plan.safety_class,
        backup_path=backup_path,
        before_sha256=before_hash,
        after_sha256=after_hash,
        bytes_added=len(patched) - len(loaded._original_bytes),
        validation=validation,
    )
