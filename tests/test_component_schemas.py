from __future__ import annotations

import struct

import pytest

from pummelmcp.pmh import (
    Bool1,
    Color4,
    Color4Float32,
    COMPONENT_SCHEMAS,
    Float32LE,
    Int32LE,
    SchemaValueError,
    Utf8String,
    Vector2,
    Vector2Float32,
    Vector3,
    Vector3Float32,
)


@pytest.mark.parametrize("value", [False, True])
def test_bool1_roundtrip(value: bool) -> None:
    encoded = Bool1.encode(value)
    assert len(encoded) == 1
    assert Bool1.decode(encoded) is value


def test_int32le_roundtrip_and_explicit_endian() -> None:
    assert Int32LE.encode(0x01020304) == b"\x04\x03\x02\x01"
    assert Int32LE.decode(b"\xfc\xff\xff\xff") == -4


def test_float32le_roundtrip_and_explicit_endian() -> None:
    encoded = Float32LE.encode(1.5)
    assert encoded == struct.pack("<f", 1.5)
    assert Float32LE.decode(encoded) == 1.5


def test_vector2_roundtrip() -> None:
    encoded = Vector2Float32.encode({"x": 1.25, "y": -2.5})
    assert Vector2Float32.decode(encoded) == Vector2(1.25, -2.5)


def test_vector3_roundtrip() -> None:
    encoded = Vector3Float32.encode({"x": 1, "y": 2, "z": 3})
    assert Vector3Float32.decode(encoded) == Vector3(1.0, 2.0, 3.0)


def test_color4_roundtrip() -> None:
    encoded = Color4Float32.encode({"r": 0.25, "g": 0.5, "b": 0.75, "a": 1})
    assert Color4Float32.decode(encoded) == Color4(0.25, 0.5, 0.75, 1.0)


def test_utf8_string_roundtrip_uses_byte_length() -> None:
    encoded = Utf8String.encode("玻璃")
    assert encoded[0] == len("玻璃".encode("utf-8"))
    assert Utf8String.decode(encoded) == "玻璃"


@pytest.mark.parametrize(
    ("codec", "value"),
    [(Bool1, 1), (Int32LE, True), (Float32LE, True), (Utf8String, b"text")],
)
def test_schema_codecs_reject_wrong_value_types(codec, value) -> None:
    with pytest.raises(SchemaValueError):
        codec.encode(value)


def test_partial_vector_and_color_patches_preserve_other_raw_members() -> None:
    vector_raw = Vector3Float32.encode({"x": 1, "y": 2, "z": 3})
    vector_patch = Vector3Float32.patch(vector_raw, {"y": 8})
    assert vector_patch.raw[:4] == vector_raw[:4]
    assert vector_patch.raw[8:] == vector_raw[8:]
    assert vector_patch.changes == {"y": (2.0, 8.0)}

    color_raw = Color4Float32.encode({"r": 0.1, "g": 0.2, "b": 0.3, "a": 1})
    color_patch = Color4Float32.patch(color_raw, {"r": 0.5})
    assert color_patch.raw[4:] == color_raw[4:]
    assert color_patch.changes["r"][1] == 0.5


def test_registry_has_exact_v03_component_and_writable_allowlists() -> None:
    expected = {
        "ModPlayerSpawn": {
            "SharedSpawn", "SpawnUsageType", "AllowedPlayers", "SpawnShape",
            "PlayerCountMask", "SpawnDistribution", "LineLength", "Radius", "Advanced",
        },
        "ModBoxCollider": {"center", "size"},
        "ModProp": {"tintColor", "collisionType", "shadowCastingMode"},
        "ModLight": {"type", "color", "range", "intensity", "spotAngle", "shadows"},
        "ModText": {
            "Color", "FontSize", "HorizontalAlignment", "Size", "FontStyles",
            "Outline", "OutlineThickness", "OutlineColor", "Shadow", "ShadowColor",
            "ShadowOffsetX", "ShadowOffsetY", "ShadowSoftness",
        },
        "ModTrigger": {
            "TriggerShape", "Size", "Center", "Radius", "Height",
            "TriggerOnHit", "TriggerOnEnter", "TriggerOnExit", "TriggerOnStay",
            "StayTriggerInterval", "DisableAfterTriggered", "OneUsePerPlayer",
        },
    }
    assert set(COMPONENT_SCHEMAS) == set(expected)
    assert {
        component: {name for name, schema in properties.items() if schema.writable}
        for component, properties in COMPONENT_SCHEMAS.items()
    } == expected
    assert COMPONENT_SCHEMAS["ModText"]["Text"].writable is False
    assert COMPONENT_SCHEMAS["ModProp"]["prop"].writable is False
    assert COMPONENT_SCHEMAS["ModProp"]["customMaterials"].writable is False
