"""Evidence-driven Action field and source-template schemas."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .errors import ActionTemplateError, InvalidActionFieldValueError


TemplateValidator = Callable[[Mapping[str, Any]], None]


@dataclass(frozen=True, slots=True)
class ActionFieldSchema:
    logical_type: str
    writable: bool
    representation: str
    evidence: str
    note: str | None = None


@dataclass(frozen=True, slots=True)
class ActionSchema:
    namespace: str
    class_name: str
    fields: Mapping[str, ActionFieldSchema]
    type_tag: int | None = None
    template_fields: frozenset[str] = frozenset()
    template_validator: TemplateValidator | None = field(
        default=None, repr=False, compare=False
    )

    def get(self, field_name: str) -> ActionFieldSchema | None:
        return self.fields.get(field_name)

    @property
    def creation_supported(self) -> bool:
        return (
            self.type_tag is not None
            and bool(self.template_fields)
            and self.template_validator is not None
        )


_BOOL = ActionFieldSchema(
    logical_type="BoolJson",
    writable=True,
    representation="JSON true/false token",
    evidence="CONFIRMED",
)
_VECTOR3 = ActionFieldSchema(
    logical_type="Vector3Json",
    writable=True,
    representation="JSON object with numeric x/y/z tokens; partial-member patch",
    evidence="CONFIRMED",
)
_INT32 = ActionFieldSchema("Int32Json", True, "JSON signed integer token", "ASSEMBLY_TRUTH")
_OPERATION = ActionFieldSchema("OperationJson", True, "JSON operation enum token", "ASSEMBLY_TRUTH")
_TARGET_FLAGS = ActionFieldSchema("TargetFlagsJson", True, "JSON Source(1) or Receiver(2) token", "ASSEMBLY_TRUTH")
_MESSAGE = ActionFieldSchema("MessageJson", True, "JSON string token", "ASSEMBLY_TRUTH")
_DURATION = ActionFieldSchema("DurationJson", True, "JSON finite seconds token", "ASSEMBLY_TRUTH")


def _readonly(
    logical_type: str, note: str, *, evidence: str = "CONFIRMED"
) -> ActionFieldSchema:
    return ActionFieldSchema(
        logical_type=logical_type,
        writable=False,
        representation="preserved JSON subtree",
        evidence=evidence,
        note=note,
    )


_TYPE_FIELDS = {
    "m_type": _readonly("JsonNumber", "Action type mutation is not supported."),
    "m_version": _readonly("JsonNumber", "Action metadata mutation is not supported."),
}
_TARGET_FIELDS = {
    **_TYPE_FIELDS,
    "m_targetFlags": _readonly(
        "TargetFlagsJson",
        "Target semantics are assembly-confirmed; field mutation is not enabled by Phase 3.",
        evidence="ASSEMBLY_TRUTH",
    ),
    "m_targets": _readonly(
        "TargetReferenceList",
        "Reference mutation is not supported; creation templates require this list to be empty.",
    ),
}

_WRITABLE_TARGET_FIELDS = {**_TARGET_FIELDS, "m_targetFlags": _TARGET_FLAGS}


def _target_fields(**extra: ActionFieldSchema) -> dict[str, ActionFieldSchema]:
    return {**_TARGET_FIELDS, **extra}


def _template_schema(
    class_name: str,
    type_tag: int,
    fields: Mapping[str, ActionFieldSchema],
    validator: TemplateValidator,
) -> ActionSchema:
    return ActionSchema(
        namespace="ModSystem.Logic",
        class_name=class_name,
        fields=fields,
        type_tag=type_tag,
        template_fields=frozenset(fields),
        template_validator=validator,
    )


SPAWN_PREFAB_ACTION_SCHEMA = _template_schema(
    "SpawnPrefabAction",
    576,
    _target_fields(
        m_spawnAtPosition=_BOOL,
        m_parentToTarget=_BOOL,
        m_position=_VECTOR3,
        m_rotation=_VECTOR3,
        m_prefabs=_readonly(
            "PrefabReferenceList",
            "Prefab/reference mutation is not supported in v0.4b.",
        ),
        m_targetPositionOffset=_readonly(
            "Vector3Json", "Field is not in the v0.4b allowlist."
        ),
        m_targetRotationOffset=_readonly(
            "Vector3Json", "Field is not in the v0.4b allowlist."
        ),
    ),
    lambda data: _validate_spawn_prefab(data),
)

SPAWN_EFFECT_ACTION_SCHEMA = _template_schema(
    "SpawnEffectAction",
    160,
    _target_fields(
        m_effectType=_readonly(
            "EffectTypeJson",
            "Enum values are assembly-confirmed; field mutation is not enabled by Phase 3.",
            evidence="ASSEMBLY_TRUTH",
        )
    ),
    lambda data: _validate_spawn_effect(data),
)

PLAY_SOUND_ACTION_SCHEMA = _template_schema(
    "PlaySoundAction",
    544,
    _target_fields(
        m_clip=_readonly(
            "AudioReferenceJson",
            "Serialized shape is confirmed, but arbitrary audio replacement is unsupported.",
            evidence="INFERRED",
        ),
        m_volume=_readonly(
            "JsonNumber",
            "Template cloning preserves volume exactly; field mutation is not enabled.",
        ),
    ),
    lambda data: _validate_play_sound(data),
)

KILL_ACTION_SCHEMA = _template_schema(
    "KillAction", 64, dict(_WRITABLE_TARGET_FIELDS), lambda data: _validate_target_base(data)
)

CHANGE_SCORE_ACTION_SCHEMA = _template_schema(
    "ChangeScoreAction",
    128,
    {**_WRITABLE_TARGET_FIELDS, "m_operation": _OPERATION, "m_value": _INT32},
    lambda data: _validate_change_score(data),
)

CHANGE_HEALTH_ACTION_SCHEMA = _template_schema(
    "ChangeHealthAction", 96,
    {**_WRITABLE_TARGET_FIELDS, "m_operation": _OPERATION, "m_value": _INT32},
    lambda data: _validate_change_score(data),
)

POSITION_ACTION_SCHEMA = _template_schema(
    "PositionAction",
    384,
    _target_fields(
        m_space=_readonly("ModActionSpaceJson", "Template-clone only.", evidence="ASSEMBLY_TRUTH"),
        m_operation=_readonly("ModActionOperationJson", "Template-clone only.", evidence="ASSEMBLY_TRUTH"),
        m_position=_VECTOR3,
    ),
    lambda data: _validate_transform_action(data, "m_position"),
)

SET_PLAYER_VISUAL_ACTION_SCHEMA = _template_schema(
    "SetPlayerVisualAction",
    1248,
    _target_fields(
        m_prefab=_readonly(
            "PrefabReferenceJson",
            "Visual Prefab is retained from the selected source-backed Action template.",
            evidence="ASSEMBLY_TRUTH",
        ),
        m_recolorPrefabUsingPlayerColor=_BOOL,
        m_verticalLookTargetChildIndex=_readonly("Int32Json", "Template-clone only."),
        m_movementRotationTargetChildIndex=_readonly("Int32Json", "Template-clone only."),
    ),
    lambda data: _validate_player_visual(data),
)

ROTATION_ACTION_SCHEMA = _template_schema(
    "RotationAction",
    416,
    _target_fields(
        m_space=_readonly("ModActionSpaceJson", "Template-clone only.", evidence="ASSEMBLY_TRUTH"),
        m_operation=_readonly("ModActionOperationJson", "Template-clone only.", evidence="ASSEMBLY_TRUTH"),
        m_rotation=_readonly("Vector3Json", "Template-clone only."),
    ),
    lambda data: _validate_transform_action(data, "m_rotation"),
)

SHOW_MESSAGE_ACTION_SCHEMA = _template_schema(
    "ShowMessageAction",
    1024,
    {**_WRITABLE_TARGET_FIELDS,
        "m_messageTarget": _readonly("ShowMessageTargetJson", "Template-clone only.", evidence="ASSEMBLY_TRUTH"),
        "m_message": _MESSAGE,
        "m_duration": _DURATION,
    },
    lambda data: _validate_show_message(data),
)

SET_PLACEMENT_ACTION_SCHEMA = _template_schema(
    "SetPlacementAction",
    480,
    dict(_WRITABLE_TARGET_FIELDS),
    lambda data: _validate_target_base(data),
)


ACTION_SCHEMAS: Mapping[tuple[str, str], ActionSchema] = {
    (schema.namespace, schema.class_name): schema
    for schema in (
        SPAWN_PREFAB_ACTION_SCHEMA,
        SPAWN_EFFECT_ACTION_SCHEMA,
        PLAY_SOUND_ACTION_SCHEMA,
        KILL_ACTION_SCHEMA,
        CHANGE_SCORE_ACTION_SCHEMA,
        CHANGE_HEALTH_ACTION_SCHEMA,
        POSITION_ACTION_SCHEMA,
        SET_PLAYER_VISUAL_ACTION_SCHEMA,
        ROTATION_ACTION_SCHEMA,
        SHOW_MESSAGE_ACTION_SCHEMA,
        SET_PLACEMENT_ACTION_SCHEMA,
    )
}

CREATABLE_ACTION_CLASSES = frozenset(
    key for key, schema in ACTION_SCHEMAS.items() if schema.creation_supported
)


def get_action_schema(namespace: str | None, class_name: str | None) -> ActionSchema | None:
    if namespace is None or class_name is None:
        return None
    return ACTION_SCHEMAS.get((namespace, class_name))


def validate_action_template(
    namespace: str,
    class_name: str,
    assembly: str,
    type_tag: int,
    data: Mapping[str, Any],
) -> ActionSchema:
    """Validate one complete source-backed managed-reference template."""
    schema = get_action_schema(namespace, class_name)
    if schema is None or not schema.creation_supported:
        raise ActionTemplateError(
            f"no Phase 3 creation schema for {namespace}.{class_name}"
        )
    if assembly != "Assembly-CSharp":
        raise ActionTemplateError(
            f"template assembly must be 'Assembly-CSharp', got {assembly!r}"
        )
    if not _strict_int(type_tag) or type_tag != schema.type_tag:
        raise ActionTemplateError(
            f"{class_name} template type tag must be {schema.type_tag}, got {type_tag!r}"
        )
    if not isinstance(data, Mapping):
        raise ActionTemplateError(f"{class_name} template data must be an object")
    actual_fields = frozenset(data)
    if actual_fields != schema.template_fields:
        missing = sorted(schema.template_fields - actual_fields)
        extra = sorted(actual_fields - schema.template_fields)
        raise ActionTemplateError(
            f"{class_name} template field set mismatch; missing={missing!r}, extra={extra!r}"
        )
    if not _strict_int(data.get("m_type")) or data["m_type"] != schema.type_tag:
        raise ActionTemplateError(
            f"{class_name}.m_type must exactly match type tag {schema.type_tag}"
        )
    expected_version = 1 if class_name == "SetPlayerVisualAction" else 0
    if not _strict_int(data.get("m_version")) or data["m_version"] != expected_version:
        raise ActionTemplateError(
            f"{class_name}.m_version must be integer {expected_version}"
        )
    assert schema.template_validator is not None
    schema.template_validator(data)
    return schema


def validate_bool_json(value: Any) -> bool:
    if not isinstance(value, bool):
        raise InvalidActionFieldValueError("value must be a strict boolean")
    return value


def validate_vector3_json(value: Any) -> dict[str, float | int]:
    if not isinstance(value, Mapping):
        raise InvalidActionFieldValueError("value must be an object containing x, y, or z")
    unknown = set(value) - {"x", "y", "z"}
    if unknown:
        labels = sorted(repr(item) for item in unknown)
        raise InvalidActionFieldValueError(f"invalid Vector3 members: {labels!r}")
    if not value:
        raise InvalidActionFieldValueError("at least one Vector3 member is required")
    result: dict[str, float | int] = {}
    for axis, item in value.items():
        if not _finite_number(item):
            raise InvalidActionFieldValueError(f"{axis} must be a finite strict JSON number")
        result[axis] = item
    return result


def _validate_target_base(data: Mapping[str, Any]) -> None:
    flags = data.get("m_targetFlags")
    if not _strict_int(flags) or flags not in {1, 2}:
        raise ActionTemplateError(
            "template m_targetFlags must be the minigame-observed Source(1) or Receiver(2)"
        )
    if data.get("m_targets") != []:
        raise ActionTemplateError(
            "Phase 3 creation templates require an empty m_targets list"
        )


def _validate_change_score(data: Mapping[str, Any]) -> None:
    _validate_target_base(data)
    _validate_enum(data, "m_operation", range(5))
    value = data.get("m_value")
    if not _strict_int(value) or not -(2**31) <= value < 2**31:
        raise ActionTemplateError("ChangeScoreAction.m_value must be a signed 32-bit integer")


def _validate_spawn_effect(data: Mapping[str, Any]) -> None:
    _validate_target_base(data)
    _validate_enum(data, "m_effectType", range(5))


def _validate_transform_action(data: Mapping[str, Any], vector_field: str) -> None:
    _validate_target_base(data)
    _validate_enum(data, "m_space", range(2))
    _validate_enum(data, "m_operation", range(5))
    _validate_vector3_template(data.get(vector_field), vector_field)


def _validate_player_visual(data: Mapping[str, Any]) -> None:
    _validate_target_base(data)
    prefab = data.get("m_prefab")
    # A zero-GUID / zero-PathID prefab is the native null-visual value used by
    # the shipped serializer to restore the original player appearance.
    empty_visual = (
        isinstance(prefab, Mapping)
        and set(prefab) == {"m_assetGUID", "m_asset"}
        and isinstance(prefab.get("m_assetGUID"), Mapping)
        and prefab["m_assetGUID"].get("serializedGuid") == "00000000-0000-0000-0000-000000000000"
        and isinstance(prefab.get("m_asset"), Mapping)
        and prefab["m_asset"].get("m_FileID") == 0
        and prefab["m_asset"].get("m_PathID") == 0
    )
    if not empty_visual:
        _validate_asset_reference(prefab, "SetPlayerVisualAction.m_prefab")
    if not isinstance(data.get("m_recolorPrefabUsingPlayerColor"), bool):
        raise ActionTemplateError("SetPlayerVisualAction.m_recolorPrefabUsingPlayerColor must be boolean")
    for field_name in ("m_verticalLookTargetChildIndex", "m_movementRotationTargetChildIndex"):
        value = data.get(field_name)
        if not _strict_int(value) or not -1 <= value <= 1024:
            raise ActionTemplateError(f"SetPlayerVisualAction.{field_name} must be between -1 and 1024")


def _validate_show_message(data: Mapping[str, Any]) -> None:
    _validate_target_base(data)
    _validate_enum(data, "m_messageTarget", {1, 2})
    message = data.get("m_message")
    if not isinstance(message, str) or len(message) > 4096:
        raise ActionTemplateError(
            "ShowMessageAction.m_message must be a string of at most 4096 characters"
        )
    duration = data.get("m_duration")
    if not _finite_number(duration) or not 0 <= float(duration) <= 3600:
        raise ActionTemplateError(
            "ShowMessageAction.m_duration must be finite and between 0 and 3600"
        )


def _validate_play_sound(data: Mapping[str, Any]) -> None:
    _validate_target_base(data)
    _validate_asset_reference(data.get("m_clip"), "PlaySoundAction.m_clip")
    volume = data.get("m_volume")
    if not _finite_number(volume) or not 0 <= float(volume) <= 1:
        raise ActionTemplateError(
            "PlaySoundAction.m_volume must be finite and between 0 and 1"
        )


def _validate_spawn_prefab(data: Mapping[str, Any]) -> None:
    _validate_target_base(data)
    for field_name in ("m_spawnAtPosition", "m_parentToTarget"):
        if not isinstance(data.get(field_name), bool):
            raise ActionTemplateError(f"SpawnPrefabAction.{field_name} must be boolean")
    for field_name in (
        "m_position",
        "m_rotation",
        "m_targetPositionOffset",
        "m_targetRotationOffset",
    ):
        _validate_vector3_template(data.get(field_name), field_name)
    prefabs = data.get("m_prefabs")
    if not isinstance(prefabs, list) or not 1 <= len(prefabs) <= 256:
        raise ActionTemplateError(
            "SpawnPrefabAction.m_prefabs must contain between 1 and 256 references"
        )
    for index, item in enumerate(prefabs):
        _validate_asset_reference(item, f"SpawnPrefabAction.m_prefabs[{index}]")


def _validate_asset_reference(value: Any, label: str) -> None:
    if not isinstance(value, Mapping) or set(value) != {"m_assetGUID", "m_asset"}:
        raise ActionTemplateError(
            f"{label} must contain exactly m_assetGUID and m_asset"
        )
    guid_holder = value.get("m_assetGUID")
    if not isinstance(guid_holder, Mapping) or set(guid_holder) != {"serializedGuid"}:
        raise ActionTemplateError(f"{label}.m_assetGUID has an unsupported shape")
    guid = guid_holder.get("serializedGuid")
    try:
        uuid.UUID(guid)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ActionTemplateError(f"{label} has an invalid serialized GUID") from exc
    if not isinstance(value.get("m_asset"), Mapping) or not value["m_asset"]:
        raise ActionTemplateError(f"{label}.m_asset must be a non-empty object")


def _validate_enum(data: Mapping[str, Any], name: str, allowed: Any) -> None:
    value = data.get(name)
    if not _strict_int(value) or value not in allowed:
        raise ActionTemplateError(f"{name} has an unsupported enum value {value!r}")


def _validate_vector3_template(value: Any, name: str) -> None:
    if not isinstance(value, Mapping) or set(value) != {"x", "y", "z"}:
        raise ActionTemplateError(f"{name} must be an exact x/y/z object")
    if not all(_finite_number(value[axis]) for axis in ("x", "y", "z")):
        raise ActionTemplateError(f"{name} members must be finite strict JSON numbers")


def _strict_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, ValueError):
        return False
