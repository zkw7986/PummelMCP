from __future__ import annotations

import pytest

from pummelmcp.pmh import (
    ACTION_SCHEMAS,
    CREATABLE_ACTION_CLASSES,
    InvalidActionFieldValueError,
    ActionTemplateError,
    get_action_schema,
    validate_action_template,
)
from pummelmcp.pmh.action_schemas import validate_bool_json, validate_vector3_json


def test_spawn_prefab_action_schema_registered() -> None:
    schema = get_action_schema("ModSystem.Logic", "SpawnPrefabAction")
    assert schema is not None
    assert ACTION_SCHEMAS[("ModSystem.Logic", "SpawnPrefabAction")] is schema


def test_phase3_minigame_creation_schemas_registered() -> None:
    expected = {
        "SpawnPrefabAction": 576,
        "KillAction": 64,
        "ChangeScoreAction": 128,
        "ChangeHealthAction": 96,
        "SpawnEffectAction": 160,
        "PositionAction": 384,
        "SetPlayerVisualAction": 1248,
        "RotationAction": 416,
        "SetPlacementAction": 480,
        "PlaySoundAction": 544,
        "ShowMessageAction": 1024,
    }
    assert {name for _, name in CREATABLE_ACTION_CLASSES} == set(expected)
    assert len(ACTION_SCHEMAS) == 11
    for class_name, type_tag in expected.items():
        schema = get_action_schema("ModSystem.Logic", class_name)
        assert schema is not None
        assert schema.creation_supported is True
        assert schema.type_tag == type_tag
        assert schema.template_fields == frozenset(schema.fields)


def test_spawn_prefab_writable_fields() -> None:
    schema = get_action_schema("ModSystem.Logic", "SpawnPrefabAction")
    assert schema is not None
    assert {name for name, item in schema.fields.items() if item.writable} == {
        "m_spawnAtPosition",
        "m_parentToTarget",
        "m_position",
        "m_rotation",
    }


def test_spawn_prefab_prefabs_read_only() -> None:
    schema = get_action_schema("ModSystem.Logic", "SpawnPrefabAction")
    assert schema is not None
    assert schema.fields["m_prefabs"].writable is False
    assert "Prefab/reference" in schema.fields["m_prefabs"].note


@pytest.mark.parametrize(
    "class_name",
    [
        "SpawnEffectAction",
        "PlaySoundAction",
        "RotationAction",
    ],
)
def test_other_action_classes_read_only(class_name: str) -> None:
    schema = get_action_schema("ModSystem.Logic", class_name)
    assert schema is not None
    assert schema.fields
    assert all(item.writable is False for item in schema.fields.values())


def test_unknown_action_semantics_are_not_writable() -> None:
    effect = get_action_schema("ModSystem.Logic", "SpawnEffectAction")
    sound = get_action_schema("ModSystem.Logic", "PlaySoundAction")
    kill = get_action_schema("ModSystem.Logic", "KillAction")
    assert effect.fields["m_effectType"].writable is False
    assert sound.fields["m_clip"].evidence == "INFERRED"
    assert sound.fields["m_volume"].writable is False
    assert kill.fields["m_targetFlags"].writable is True


def test_configurable_minigame_action_fields() -> None:
    score = get_action_schema("ModSystem.Logic", "ChangeScoreAction")
    health = get_action_schema("ModSystem.Logic", "ChangeHealthAction")
    message = get_action_schema("ModSystem.Logic", "ShowMessageAction")
    assert all(score.fields[name].writable for name in ("m_value", "m_operation", "m_targetFlags"))
    assert all(health.fields[name].writable for name in ("m_value", "m_operation", "m_targetFlags"))
    assert all(message.fields[name].writable for name in ("m_message", "m_duration", "m_targetFlags"))


def test_position_action_can_set_destination_vector() -> None:
    schema = get_action_schema("ModSystem.Logic", "PositionAction")
    assert schema is not None
    assert schema.fields["m_position"].writable is True
    assert schema.fields["m_space"].writable is False
    assert schema.fields["m_operation"].writable is False


def test_player_visual_action_is_source_template_only() -> None:
    schema = get_action_schema("ModSystem.Logic", "SetPlayerVisualAction")
    assert schema is not None and schema.creation_supported
    assert schema.fields["m_prefab"].writable is False
    assert schema.fields["m_recolorPrefabUsingPlayerColor"].writable is True
    assert schema.fields["m_verticalLookTargetChildIndex"].writable is False


def test_template_validator_rejects_wrong_type_tag() -> None:
    data = {
        "m_type": 64,
        "m_version": 0,
        "m_targetFlags": 1,
        "m_targets": [],
    }
    with pytest.raises(ActionTemplateError, match="type tag must be 64"):
        validate_action_template(
            "ModSystem.Logic", "KillAction", "Assembly-CSharp", 65, data
        )


def test_template_validator_rejects_extra_fields() -> None:
    data = {
        "m_type": 64,
        "m_version": 0,
        "m_targetFlags": 1,
        "m_targets": [],
        "unexpected": 1,
    }
    with pytest.raises(ActionTemplateError, match="field set mismatch"):
        validate_action_template(
            "ModSystem.Logic", "KillAction", "Assembly-CSharp", 64, data
        )


@pytest.mark.parametrize("value", [0, 1, "true", None, [], {}])
def test_action_bool_validation_is_strict(value) -> None:
    with pytest.raises(InvalidActionFieldValueError):
        validate_bool_json(value)


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"q": 1.0},
        {"x": "5"},
        {"x": True},
        {"x": float("nan")},
        {"x": float("inf")},
        {"x": float("-inf")},
    ],
)
def test_action_vector_validation_is_strict(value) -> None:
    with pytest.raises(InvalidActionFieldValueError):
        validate_vector3_json(value)


def test_action_vector_partial_validation() -> None:
    assert validate_vector3_json({"x": 5, "z": 45.0}) == {"x": 5, "z": 45.0}
