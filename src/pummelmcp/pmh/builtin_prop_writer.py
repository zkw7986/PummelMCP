"""Spawn a registered built-in prop into a PMH scene.

This is the first object-creation path in the project that does not need an
existing in-scene template. Everything it writes is either derived from the
scene's own verified layout or from a
:class:`~pummelmcp.pmh.editor_assets.VerifiedPropReference`, which only the
scanned game registry can produce.

The byte layout mirrors what the game itself serializes for a prop object -- one
GameObject carrying ``ModTransform`` then ``ModProp``, with the GameObject and
its ``ModTransform`` sharing an identity GUID:

* hierarchy record: ``str8 name + u8 active + i32 layer + str8 tag + u16 0``
* object index record: ``str8 guid + u32 2``, then each component as
  ``str8 type + str8 guid + u8 enabled``
* ``ModTransform`` payload: 120 bytes, ``position``/``rotation``/``scale``/``guid``
* ``ModProp`` payload: 197 bytes, ``prop``/``tintColor``/``collisionType``/
  ``shadowCastingMode``/``customMaterials``/``guid``

Insertion reuses the duplication writer's layout arithmetic so there is exactly
one implementation of the PMH section layout in the codebase. The four mutations
are the same ones approved for leaf duplication, and the commit goes through
``PMHScene._replace_with_validation`` like every other writer, so an invalid
patch never replaces the source.
"""

from __future__ import annotations

import math
import struct
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .duplication_writer import (
    MAX_GUID_ATTEMPTS,
    _canonical_uuid4,
    _layout,
    _parent_child_count_offset,
    _sha,
    _stable_object_snapshot,
)
from .editor_assets import (
    PROP_REFERENCE_BYTE_LENGTH,
    PROP_REFERENCE_PREFIX,
    VerifiedPropReference,
)
from .errors import (
    ConcurrentModificationError,
    GuidAllocationError,
    PMHLookupError,
    UnsafeBuiltinPropSpawnError,
    WriterValidationError,
)
from .models import GameObject, Scene
from .reader import PMHReader
from .validator import validate_scene
from .writer import PMHScene

SAFETY_CLASS = "SAFE_REGISTERED_BUILTIN_PROP_SPAWN"

MODTRANSFORM_COMPONENT = "ModTransform"
MODPROP_COMPONENT = "ModProp"
PROP_COMPONENT_SHAPE = (MODTRANSFORM_COMPONENT, MODPROP_COMPONENT)
TRANSFORM_FIELDS = ("position", "rotation", "scale")

#: ``PropCollisionType`` (research/decompiled/PropCollisionType.cs).
COLLISION_TYPES: Mapping[str, int] = {"none": 0, "box": 1, "sphere": 2, "mesh": 3}
DEFAULT_COLLISION_TYPE = "mesh"  # ModProp.m_collisionType = PropCollisionType.Mesh

#: ``UnityEngine.Rendering.ShadowCastingMode``, as observed in shipped scenes.
SHADOW_CASTING_MODES: Mapping[str, int] = {
    "off": 0,
    "on": 1,
    "two_sided": 2,
    "shadows_only": 3,
}
DEFAULT_SHADOW_CASTING_MODE = "on"

DEFAULT_POSITION = (0.0, 0.0, 0.0)
DEFAULT_ROTATION = (0.0, 0.0, 0.0)
DEFAULT_SCALE = (1.0, 1.0, 1.0)
DEFAULT_TINT = (1.0, 1.0, 1.0, 1.0)  # ModProp.m_color = Color.white

#: Observed serialized payload sizes, asserted on every construction.
MODTRANSFORM_PAYLOAD_BYTES = 120
MODPROP_PAYLOAD_BYTES = 197
OBJECT_INDEX_RECORD_BYTES = 138

STR8_MAX_BYTES = 255

MUTATION_KINDS = (
    "UPDATE_PARENT_CHILD_COUNT",
    "INSERT_HIERARCHY_RECORD",
    "INSERT_OBJECT_INDEX_RECORD",
    "INSERT_COMPONENT_PAYLOADS",
)

VALIDATION_STEPS = (
    "parse_to_exact_eof",
    "scene_validation",
    "diff_only_in_planned_regions",
    "created_object_resolves_once",
    "created_component_shape_matches",
    "created_identity_relation_matches",
    "created_transform_matches_request",
    "created_prop_reference_matches_catalog",
    "created_custom_materials_empty",
    "all_existing_objects_preserved",
    "single_atomic_replace",
)


@dataclass(frozen=True, slots=True)
class BuiltinPropSpawnPlan:
    safety_class: str
    source_file: str
    source_sha256: str
    asset_id: str
    asset_name: str
    reference_guid: str
    reference_payload_sha256: str
    catalog_sha256: str
    destination_parent_guid: str
    destination_parent_path: str
    created_gameobject_guid: str
    created_transform_guid: str
    created_prop_guid: str
    created_name: str
    created_hierarchy_path: str
    created_sibling_index: int
    created_preorder_index: int
    position: tuple[float, float, float]
    rotation: tuple[float, float, float]
    scale: tuple[float, float, float]
    tint_color: tuple[float, float, float, float]
    collision_type: str
    collision_type_raw: int
    shadow_casting_mode: str
    shadow_casting_mode_raw: int
    inserted_bytes: int
    mutation_kinds: tuple[str, ...]
    validation_steps: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class _ExpectedBytes:
    """The exact serialized bytes the patch is expected to produce."""

    position: bytes
    rotation: bytes
    scale: bytes
    tint_color: bytes
    collision_type: bytes
    shadow_casting_mode: bytes
    reference: bytes
    custom_materials: bytes


@dataclass(frozen=True, slots=True)
class _PreparedSpawn:
    loaded: PMHScene
    plan: BuiltinPropSpawnPlan
    patched: bytes
    operations: tuple[tuple[int, int, bytes], ...]
    expected: _ExpectedBytes


def _finite(values: Sequence[object], *, what: str) -> tuple[float, ...]:
    result: list[float] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise UnsafeBuiltinPropSpawnError(f"{what} must contain numbers")
        number = float(value)
        if not math.isfinite(number):
            raise UnsafeBuiltinPropSpawnError(
                f"{what} must be finite; ModTransform stores float32 values"
            )
        result.append(number)
    return tuple(result)


def _as_triple(
    values: object, default: tuple[float, float, float], *, what: str
) -> tuple[float, float, float]:
    if values is None:
        return default
    if isinstance(values, Mapping):
        merged: dict[str, object] = {"x": default[0], "y": default[1], "z": default[2]}
        for axis in ("x", "y", "z"):
            if values.get(axis) is not None:
                merged[axis] = values[axis]
        if not set(values) <= {"x", "y", "z"}:
            raise UnsafeBuiltinPropSpawnError(f"{what} accepts only x, y, and z")
        resolved = _finite((merged["x"], merged["y"], merged["z"]), what=what)
    elif isinstance(values, (list, tuple)) and len(values) == 3:
        resolved = _finite(values, what=what)
    else:
        raise UnsafeBuiltinPropSpawnError(
            f"{what} must be a three-axis mapping or a three-element sequence"
        )
    return (resolved[0], resolved[1], resolved[2])


def _as_color(
    values: object, default: tuple[float, float, float, float]
) -> tuple[float, float, float, float]:
    if values is None:
        return default
    if isinstance(values, Mapping):
        merged: dict[str, object] = {
            "r": default[0],
            "g": default[1],
            "b": default[2],
            "a": default[3],
        }
        if not set(values) <= {"r", "g", "b", "a"}:
            raise UnsafeBuiltinPropSpawnError("tint_color accepts only r, g, b, and a")
        for channel in ("r", "g", "b", "a"):
            if values.get(channel) is not None:
                merged[channel] = values[channel]
        resolved = _finite(
            (merged["r"], merged["g"], merged["b"], merged["a"]), what="tint_color"
        )
    elif isinstance(values, (list, tuple)) and len(values) == 4:
        resolved = _finite(values, what="tint_color")
    else:
        raise UnsafeBuiltinPropSpawnError(
            "tint_color must be a four-channel mapping or a four-element sequence"
        )
    return (resolved[0], resolved[1], resolved[2], resolved[3])


def _resolve_enum(
    value: object, table: Mapping[str, int], default: str, *, what: str
) -> tuple[str, int]:
    if value is None:
        name = default
    elif isinstance(value, str):
        name = value.strip().casefold().replace("-", "_").replace(" ", "_")
    else:
        raise UnsafeBuiltinPropSpawnError(f"{what} must be one of {sorted(table)}")
    if name not in table:
        raise UnsafeBuiltinPropSpawnError(
            f"{what} must be one of {sorted(table)}, not {value!r}"
        )
    return name, table[name]


def _str8(text: str, *, what: str) -> bytes:
    encoded = text.encode("utf-8")
    if not encoded:
        raise UnsafeBuiltinPropSpawnError(f"{what} must not be empty")
    if len(encoded) > STR8_MAX_BYTES:
        raise UnsafeBuiltinPropSpawnError(
            f"{what} is {len(encoded)} bytes, above the {STR8_MAX_BYTES}-byte limit"
        )
    return bytes([len(encoded)]) + encoded


def _field(name: str, raw: bytes) -> bytes:
    return _str8(name, what="field name") + struct.pack("<I", len(raw)) + raw


def _guid_payload(guid: str) -> bytes:
    """The 37-byte ``str8`` UUID value stored in a component ``guid`` field."""
    return bytes([36]) + guid.encode("ascii")


def _component_payload(fields: Sequence[bytes], *, expected: int, what: str) -> bytes:
    payload = struct.pack("<H", len(fields)) + b"".join(fields)
    if len(payload) != expected:
        raise WriterValidationError(
            f"constructed {what} payload is {len(payload)} bytes, expected {expected}"
        )
    return payload


def _mod_transform_payload(
    guid: str,
    position: tuple[float, float, float],
    rotation: tuple[float, float, float],
    scale: tuple[float, float, float],
) -> bytes:
    return _component_payload(
        (
            _field("position", struct.pack("<3f", *position)),
            _field("rotation", struct.pack("<3f", *rotation)),
            _field("scale", struct.pack("<3f", *scale)),
            _field("guid", _guid_payload(guid)),
        ),
        expected=MODTRANSFORM_PAYLOAD_BYTES,
        what="ModTransform",
    )


def _mod_prop_payload(
    guid: str,
    reference: VerifiedPropReference,
    tint_color: tuple[float, float, float, float],
    collision_type: int,
    shadow_casting_mode: int,
    custom_materials: bytes,
) -> bytes:
    return _component_payload(
        (
            _field("prop", reference.payload),
            _field("tintColor", struct.pack("<4f", *tint_color)),
            _field("collisionType", struct.pack("<i", collision_type)),
            _field("shadowCastingMode", struct.pack("<i", shadow_casting_mode)),
            _field("customMaterials", custom_materials),
            _field("guid", _guid_payload(guid)),
        ),
        expected=MODPROP_PAYLOAD_BYTES + len(custom_materials) - 4,
        what="ModProp",
    )


def _hierarchy_record(name: str, active: bool, layer: int, tag: str) -> bytes:
    if not isinstance(layer, int) or isinstance(layer, bool):
        raise UnsafeBuiltinPropSpawnError("layer must be an integer")
    if not -2147483648 <= layer <= 2147483647:
        raise UnsafeBuiltinPropSpawnError("layer must fit a signed 32-bit integer")
    return (
        _str8(name, what="object name")
        + bytes([1 if active else 0])
        + struct.pack("<i", layer)
        + _str8(tag, what="tag")
        + struct.pack("<H", 0)
    )


def _object_index_record(guid: str, prop_guid: str) -> bytes:
    record = (
        _str8(guid, what="object GUID")
        + struct.pack("<I", len(PROP_COMPONENT_SHAPE))
        + _str8(MODTRANSFORM_COMPONENT, what="component type")
        + _str8(guid, what="component GUID")
        + bytes([1])
        + _str8(MODPROP_COMPONENT, what="component type")
        + _str8(prop_guid, what="component GUID")
        + bytes([1])
    )
    if len(record) != OBJECT_INDEX_RECORD_BYTES:
        raise WriterValidationError(
            f"constructed object index record is {len(record)} bytes, "
            f"expected {OBJECT_INDEX_RECORD_BYTES}"
        )
    return record


def _require_reference(reference: VerifiedPropReference) -> VerifiedPropReference:
    """Re-check that a reference is the exact registered-prop envelope."""
    if not isinstance(reference, VerifiedPropReference):
        raise UnsafeBuiltinPropSpawnError(
            "a ModProp reference must be a VerifiedPropReference resolved from the "
            "scanned built-in asset catalog"
        )
    if reference.payload != PROP_REFERENCE_PREFIX + reference.guid.encode("ascii"):
        raise UnsafeBuiltinPropSpawnError(
            "the ModProp reference payload does not match its GUID"
        )
    if len(reference.payload) != PROP_REFERENCE_BYTE_LENGTH:
        raise UnsafeBuiltinPropSpawnError("the ModProp reference is not 38 bytes")
    if not reference.asset_id or not reference.catalog_sha256:
        raise UnsafeBuiltinPropSpawnError(
            "the ModProp reference is missing its catalog provenance"
        )
    return reference


def _material_reference_list(guids: Sequence[str] | None) -> bytes:
    """Serialize an exact ModProp custom-material list from canonical UUIDs."""
    values = tuple(guids or ())
    if len(values) > 256:
        raise UnsafeBuiltinPropSpawnError("custom_material_guids accepts at most 256 items")
    payload = bytearray(struct.pack("<I", len(values)))
    for value in values:
        canonical = _canonical_uuid4(value)
        if canonical is None or canonical != value:
            raise UnsafeBuiltinPropSpawnError(
                "custom material GUIDs must be canonical lowercase UUID v4 values"
            )
        payload.extend(PROP_REFERENCE_PREFIX)
        payload.extend(value.encode("ascii"))
    return bytes(payload)


def _allocate_guids(
    scene: Scene,
    count: int,
    *,
    guid_factory: Callable[[], object],
    max_attempts: int,
) -> tuple[str, ...]:
    occupied = {obj.guid.casefold() for obj in scene.walk()}
    occupied.update(
        component.guid.casefold()
        for obj in scene.walk()
        for component in obj.components
    )
    allocated: list[str] = []
    for _ in range(count):
        chosen = None
        for _ in range(max_attempts):
            candidate = _canonical_uuid4(guid_factory())
            if candidate is not None and candidate.casefold() not in occupied:
                chosen = candidate
                occupied.add(candidate.casefold())
                break
        if chosen is None:
            raise GuidAllocationError(
                "could not allocate a collision-free canonical UUID v4 in "
                f"{max_attempts} attempts"
            )
        allocated.append(chosen)
    return tuple(allocated)


def _resolve_parent(loaded: PMHScene, parent_identifier: object) -> GameObject:
    if parent_identifier is None:
        roots = loaded.scene.roots
        if not roots:
            raise UnsafeBuiltinPropSpawnError(
                "UNSAFE_HIERARCHY: the scene has no root object to parent under"
            )
        parent = roots[0]
    else:
        if not isinstance(parent_identifier, str):
            raise UnsafeBuiltinPropSpawnError("parent must be an object identifier")
        parent = loaded.get_object(parent_identifier)
    if parent.hierarchy_span is None:
        raise UnsafeBuiltinPropSpawnError(
            "UNSAFE_HIERARCHY: the destination parent has no hierarchy span"
        )
    return parent


def _construct_spawn_bytes(
    loaded: PMHScene,
    *,
    parent: GameObject,
    created_guid: str,
    prop_guid: str,
    hierarchy: bytes,
    index_record: bytes,
    payloads: bytes,
) -> tuple[bytes, tuple[tuple[int, int, bytes], ...]]:
    raw = loaded._original_bytes
    index_end, _component_spans, object_payload_starts = _layout(loaded.scene)
    objects = list(loaded.scene.walk())
    # The new object lands immediately after the parent's whole subtree in
    # preorder, which makes it the parent's last child in both tables at once.
    expected_preorder = parent.preorder_index + sum(1 for _ in parent.walk())
    if expected_preorder < len(objects):
        next_index = objects[expected_preorder].index_span
        if next_index is None:
            raise UnsafeBuiltinPropSpawnError(
                "UNSAFE_HIERARCHY: the destination index span is absent"
            )
        index_insert = next_index.start
        payload_insert = object_payload_starts[objects[expected_preorder].guid]
    else:
        index_insert = index_end
        payload_insert = loaded.scene.byte_length

    count_offset = _parent_child_count_offset(parent)
    if struct.unpack("<H", raw[count_offset : count_offset + 2])[0] != len(parent.children):
        raise UnsafeBuiltinPropSpawnError(
            "UNSAFE_HIERARCHY: the parent child count bytes disagree with the parse"
        )

    operations = (
        (count_offset, 2, struct.pack("<H", len(parent.children) + 1)),
        (parent.hierarchy_span.end, 0, hierarchy),
        (index_insert, 0, index_record),
        (payload_insert, 0, payloads),
    )
    patched = bytearray(raw)
    for offset, old_length, replacement in sorted(operations, reverse=True):
        patched[offset : offset + old_length] = replacement
    return bytes(patched), operations


def _undo_planned_operations(
    original: bytes, patched: bytes, operations: Sequence[tuple[int, int, bytes]]
) -> bytes:
    """Reverse the planned edits; the result must equal the original bytes.

    The forward pass applies the edits in descending offset order, so each
    offset stays valid in the pre-shift coordinate space. The inverse therefore
    has to walk them back in ascending order, which is what makes an insertion
    at a low offset stop displacing the ones after it.
    """
    restored = bytearray(patched)
    for offset, old_length, replacement in sorted(operations):
        if old_length == 0:
            restored[offset : offset + len(replacement)] = b""
        else:
            restored[offset : offset + len(replacement)] = original[
                offset : offset + old_length
            ]
    return bytes(restored)


def _prepare_spawn(
    scene_path: str | Path,
    reference: VerifiedPropReference,
    *,
    parent: str | None,
    name: str | None,
    position: object,
    rotation: object,
    scale: object,
    tint_color: object,
    collision_type: str | None,
    shadow_casting_mode: str | None,
    layer: int | None,
    tag: str | None,
    active: bool,
    custom_material_guids: Sequence[str] | None = None,
    expected_scene_hash: str | None,
    guid_factory: Callable[[], object],
    max_guid_attempts: int,
) -> _PreparedSpawn:
    reference = _require_reference(reference)
    source = Path(scene_path)
    loaded = PMHScene.load(source)
    before = loaded.source_sha256
    if expected_scene_hash and expected_scene_hash.casefold() != before:
        raise ConcurrentModificationError("UNSAFE_STALE_SCENE")

    resolved_position = _as_triple(position, DEFAULT_POSITION, what="position")
    resolved_rotation = _as_triple(rotation, DEFAULT_ROTATION, what="rotation")
    resolved_scale = _as_triple(scale, DEFAULT_SCALE, what="scale")
    resolved_tint = _as_color(tint_color, DEFAULT_TINT)
    collision_name, collision_raw = _resolve_enum(
        collision_type, COLLISION_TYPES, DEFAULT_COLLISION_TYPE, what="collision_type"
    )
    shadow_name, shadow_raw = _resolve_enum(
        shadow_casting_mode,
        SHADOW_CASTING_MODES,
        DEFAULT_SHADOW_CASTING_MODE,
        what="shadow_casting_mode",
    )

    parent_object = _resolve_parent(loaded, parent)
    resolved_name = reference.asset_name if name is None else name
    if not isinstance(resolved_name, str):
        raise UnsafeBuiltinPropSpawnError("name must be a string")
    resolved_tag = parent_object.tag if tag is None else tag
    if not isinstance(resolved_tag, str):
        raise UnsafeBuiltinPropSpawnError("tag must be a string")
    resolved_layer = parent_object.layer if layer is None else layer
    if not isinstance(active, bool):
        raise UnsafeBuiltinPropSpawnError("active must be a boolean")

    created_guid, prop_guid = _allocate_guids(
        loaded.scene, 2, guid_factory=guid_factory, max_attempts=max_guid_attempts
    )
    transform_payload = _mod_transform_payload(
        created_guid, resolved_position, resolved_rotation, resolved_scale
    )
    material_payload = _material_reference_list(custom_material_guids)
    prop_payload = _mod_prop_payload(
        prop_guid, reference, resolved_tint, collision_raw, shadow_raw, material_payload
    )
    patched, operations = _construct_spawn_bytes(
        loaded,
        parent=parent_object,
        created_guid=created_guid,
        prop_guid=prop_guid,
        hierarchy=_hierarchy_record(
            resolved_name, active, resolved_layer, resolved_tag
        ),
        index_record=_object_index_record(created_guid, prop_guid),
        payloads=transform_payload + prop_payload,
    )

    expected = _ExpectedBytes(
        position=struct.pack("<3f", *resolved_position),
        rotation=struct.pack("<3f", *resolved_rotation),
        scale=struct.pack("<3f", *resolved_scale),
        tint_color=struct.pack("<4f", *resolved_tint),
        collision_type=struct.pack("<i", collision_raw),
        shadow_casting_mode=struct.pack("<i", shadow_raw),
        reference=reference.payload,
        custom_materials=material_payload,
    )
    plan = BuiltinPropSpawnPlan(
        safety_class=SAFETY_CLASS,
        source_file=str(source),
        source_sha256=before,
        asset_id=reference.asset_id,
        asset_name=reference.asset_name,
        reference_guid=reference.guid,
        reference_payload_sha256=reference.payload_sha256,
        catalog_sha256=reference.catalog_sha256,
        destination_parent_guid=parent_object.guid,
        destination_parent_path=parent_object.hierarchy_path,
        created_gameobject_guid=created_guid,
        created_transform_guid=created_guid,
        created_prop_guid=prop_guid,
        created_name=resolved_name,
        created_hierarchy_path=f"{parent_object.hierarchy_path}/{resolved_name}",
        created_sibling_index=len(parent_object.children),
        created_preorder_index=parent_object.preorder_index
        + sum(1 for _ in parent_object.walk()),
        position=resolved_position,
        rotation=resolved_rotation,
        scale=resolved_scale,
        tint_color=resolved_tint,
        collision_type=collision_name,
        collision_type_raw=collision_raw,
        shadow_casting_mode=shadow_name,
        shadow_casting_mode_raw=shadow_raw,
        inserted_bytes=len(patched) - len(loaded._original_bytes),
        mutation_kinds=MUTATION_KINDS,
        validation_steps=VALIDATION_STEPS,
    )
    return _PreparedSpawn(
        loaded=loaded,
        plan=plan,
        patched=patched,
        operations=operations,
        expected=expected,
    )


def _field_raw(game_object: GameObject, component_type: str, field_name: str) -> bytes | None:
    try:
        return game_object.get_component(component_type).get_field(field_name).raw
    except (PMHLookupError, AttributeError):
        return None


def _validate_spawn(
    prepared: _PreparedSpawn,
    reparsed: Scene,
    patched: bytes,
) -> dict[str, Any]:
    original = prepared.loaded.scene
    original_raw = prepared.loaded._original_bytes
    plan = prepared.plan
    expected = prepared.expected

    created = [obj for obj in reparsed.walk() if obj.guid == plan.created_gameobject_guid]
    created_object = created[0] if len(created) == 1 else None
    old_object_guids = [obj.guid for obj in original.walk()]
    new_object_guids = [obj.guid for obj in reparsed.walk()]
    old_component_guids = [c.guid for obj in original.walk() for c in obj.components]
    new_component_guids = [c.guid for obj in reparsed.walk() for c in obj.components]

    checks: dict[str, bool] = {
        "parse_to_exact_eof": reparsed.fully_consumed,
        "scene_validation": validate_scene(reparsed).passed,
        "magic_and_version_preserved": (reparsed.magic, reparsed.version)
        == (original.magic, original.version),
        "root_count_preserved": reparsed.root_count == original.root_count,
        "gameobject_count_incremented_once": (
            reparsed.object_count == original.object_count + 1
        ),
        "component_count_incremented_by_two": (
            reparsed.component_count
            == original.component_count + len(PROP_COMPONENT_SHAPE)
        ),
        "gameobject_guids_unique": len(new_object_guids) == len(set(new_object_guids)),
        "component_guids_unique": len(new_component_guids) == len(set(new_component_guids)),
        "old_gameobject_identities_preserved": set(old_object_guids)
        < set(new_object_guids),
        "old_component_identities_preserved": set(old_component_guids)
        < set(new_component_guids),
        "created_object_resolves_once": len(created) == 1,
        "diff_only_in_planned_regions": (
            _undo_planned_operations(original_raw, patched, prepared.operations)
            == original_raw
        ),
    }

    original_snapshots = {
        obj.guid: _stable_object_snapshot(obj) for obj in original.walk()
    }
    reparsed_by_guid = {obj.guid: obj for obj in reparsed.walk()}
    checks["all_existing_objects_preserved"] = all(
        guid in reparsed_by_guid
        and _stable_object_snapshot(reparsed_by_guid[guid]) == snapshot
        for guid, snapshot in original_snapshots.items()
    )

    if created_object is None:
        checks.update(
            {
                "created_hierarchy_path_matches": False,
                "created_component_shape_matches": False,
                "created_identity_relation_matches": False,
                "created_parent_matches": False,
                "created_sibling_index_matches": False,
                "created_transform_matches_request": False,
                "created_prop_reference_matches_catalog": False,
                "created_custom_materials_empty": False,
                "created_prop_guid_matches": False,
            }
        )
    else:
        shape = tuple(component.type_name for component in created_object.components)
        is_prop_shape = shape == PROP_COMPONENT_SHAPE
        checks["created_hierarchy_path_matches"] = (
            created_object.hierarchy_path == plan.created_hierarchy_path
        )
        checks["created_component_shape_matches"] = is_prop_shape
        checks["created_identity_relation_matches"] = (
            is_prop_shape and created_object.guid == created_object.components[0].guid
        )
        checks["created_parent_matches"] = (
            created_object.parent is not None
            and created_object.parent.guid == plan.destination_parent_guid
        )
        checks["created_sibling_index_matches"] = (
            created_object.sibling_index == plan.created_sibling_index
        )
        checks["created_transform_matches_request"] = is_prop_shape and all(
            _field_raw(created_object, MODTRANSFORM_COMPONENT, name) == raw
            for name, raw in (
                ("position", expected.position),
                ("rotation", expected.rotation),
                ("scale", expected.scale),
            )
        )
        checks["created_prop_reference_matches_catalog"] = (
            is_prop_shape
            and _field_raw(created_object, MODPROP_COMPONENT, "prop") == expected.reference
        )
        checks["created_custom_materials_empty"] = (
            is_prop_shape
            and _field_raw(created_object, MODPROP_COMPONENT, "customMaterials")
            == expected.custom_materials
        )
        checks["created_collision_type_matches"] = is_prop_shape and (
            _field_raw(created_object, MODPROP_COMPONENT, "collisionType")
            == expected.collision_type
        )
        checks["created_shadow_casting_mode_matches"] = is_prop_shape and (
            _field_raw(created_object, MODPROP_COMPONENT, "shadowCastingMode")
            == expected.shadow_casting_mode
        )
        checks["created_tint_color_matches"] = is_prop_shape and (
            _field_raw(created_object, MODPROP_COMPONENT, "tintColor")
            == expected.tint_color
        )
        checks["created_prop_guid_matches"] = is_prop_shape and (
            created_object.get_component(MODPROP_COMPONENT).guid == plan.created_prop_guid
        )

    failed = sorted(name for name, passed in checks.items() if not passed)
    if failed:
        raise WriterValidationError(
            "built-in prop spawn validation failed: " + ", ".join(failed)
        )
    return {"passed": True, "checks": checks}


def plan_builtin_prop_spawn(
    scene_path: str | Path,
    reference: VerifiedPropReference,
    *,
    parent: str | None = None,
    name: str | None = None,
    position: object = None,
    rotation: object = None,
    scale: object = None,
    tint_color: object = None,
    collision_type: str | None = None,
    shadow_casting_mode: str | None = None,
    layer: int | None = None,
    tag: str | None = None,
    active: bool = True,
    custom_material_guids: Sequence[str] | None = None,
    expected_scene_hash: str | None = None,
    guid_factory: Callable[[], object] = uuid.uuid4,
    max_guid_attempts: int = MAX_GUID_ATTEMPTS,
) -> BuiltinPropSpawnPlan:
    """Build a complete, read-only plan for one built-in prop spawn."""
    return _prepare_spawn(
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
        custom_material_guids=custom_material_guids,
        expected_scene_hash=expected_scene_hash,
        guid_factory=guid_factory,
        max_guid_attempts=max_guid_attempts,
    ).plan


def spawn_builtin_prop(
    scene_path: str | Path,
    reference: VerifiedPropReference,
    *,
    parent: str | None = None,
    name: str | None = None,
    position: object = None,
    rotation: object = None,
    scale: object = None,
    tint_color: object = None,
    collision_type: str | None = None,
    shadow_casting_mode: str | None = None,
    layer: int | None = None,
    tag: str | None = None,
    active: bool = True,
    custom_material_guids: Sequence[str] | None = None,
    expected_scene_hash: str | None = None,
    dry_run: bool = False,
    backup: bool = True,
    guid_factory: Callable[[], object] = uuid.uuid4,
    max_guid_attempts: int = MAX_GUID_ATTEMPTS,
) -> dict[str, Any]:
    """Create one ``ModTransform`` + ``ModProp`` object in a PMH scene."""
    prepared = _prepare_spawn(
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
        custom_material_guids=custom_material_guids,
        expected_scene_hash=expected_scene_hash,
        guid_factory=guid_factory,
        max_guid_attempts=max_guid_attempts,
    )
    plan = prepared.plan

    if dry_run:
        reparsed = PMHReader().read_bytes(prepared.patched, source=prepared.loaded.source)
        validation = _validate_spawn(prepared, reparsed, prepared.patched)
        backup_path = None
    else:
        validation, backup_path = prepared.loaded._replace_with_validation(
            prepared.patched,
            lambda reparsed, data: _validate_spawn(prepared, reparsed, data),
            backup=backup,
        )

    return {
        "safety_class": plan.safety_class,
        "dry_run": dry_run,
        "asset": {
            "asset_id": plan.asset_id,
            "name": plan.asset_name,
            "guid": plan.reference_guid,
            "reference_payload_sha256": plan.reference_payload_sha256,
            "reference_byte_length": PROP_REFERENCE_BYTE_LENGTH,
            "reference_schema": "COMPACT_ASSET_REFERENCE_V1",
            "verification": "REGISTERED_INTERNAL_PROP",
            "catalog_sha256": plan.catalog_sha256,
        },
        "created": {
            "gameobject_guid": plan.created_gameobject_guid,
            "transform_guid": plan.created_transform_guid,
            "prop_guid": plan.created_prop_guid,
            "name": plan.created_name,
            "hierarchy_path": plan.created_hierarchy_path,
            "component_shape": list(PROP_COMPONENT_SHAPE),
        },
        "destination": {
            "parent_guid": plan.destination_parent_guid,
            "parent_path": plan.destination_parent_path,
            "sibling_index": plan.created_sibling_index,
            "preorder_index": plan.created_preorder_index,
        },
        "transform": {
            "position": _xyz(plan.position),
            "rotation": _xyz(plan.rotation),
            "scale": _xyz(plan.scale),
            "unit": "serialized float32; rotation is euler radians",
        },
        "mod_prop": {
            "tint_color": {
                "r": plan.tint_color[0],
                "g": plan.tint_color[1],
                "b": plan.tint_color[2],
                "a": plan.tint_color[3],
            },
            "collision_type": {
                "name": plan.collision_type,
                "raw_value": plan.collision_type_raw,
            },
            "shadow_casting_mode": {
                "name": plan.shadow_casting_mode,
                "raw_value": plan.shadow_casting_mode_raw,
            },
            "custom_materials": (
                "EMPTY" if len(prepared.expected.custom_materials) == 4
                else list(custom_material_guids or ())
            ),
        },
        "mutation_kinds": list(plan.mutation_kinds),
        "bytes_inserted": plan.inserted_bytes,
        "transaction_before_sha256": plan.source_sha256,
        "transaction_after_sha256": _sha(prepared.patched),
        "backup_path": str(backup_path) if backup_path else None,
        "validation": validation,
    }


def _xyz(values: tuple[float, float, float]) -> dict[str, float]:
    return {"x": values[0], "y": values[1], "z": values[2]}
