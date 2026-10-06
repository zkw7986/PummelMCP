"""Explicit codecs and allowlisted schemas for ordinary PMH components."""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Any, Mapping

from .models import Color4, DecodeConfidence, Vector2, Vector3


class SchemaValueError(ValueError):
    """A payload or requested value does not satisfy an explicit schema."""


@dataclass(frozen=True, slots=True)
class CodecPatch:
    raw: bytes
    changes: Mapping[str, tuple[Any, Any]]


class Codec:
    name: str
    byte_length: int | None

    def decode(self, raw: bytes) -> Any:
        raise NotImplementedError

    def encode(self, value: Any) -> bytes:
        raise NotImplementedError

    def patch(self, raw: bytes, value: Any) -> CodecPatch:
        before = self.decode(raw)
        after_raw = self.encode(value)
        return CodecPatch(after_raw, {"value": (before, self.decode(after_raw))})

    def _require_length(self, raw: bytes) -> None:
        if self.byte_length is not None and len(raw) != self.byte_length:
            raise SchemaValueError(
                f"{self.name} requires {self.byte_length} bytes, got {len(raw)}"
            )


class _Bool1(Codec):
    name = "Bool1"
    byte_length = 1

    def decode(self, raw: bytes) -> bool:
        self._require_length(raw)
        if raw not in (b"\x00", b"\x01"):
            raise SchemaValueError(f"Bool1 payload must be 0 or 1, got {raw.hex()}")
        return raw == b"\x01"

    def encode(self, value: Any) -> bytes:
        if not isinstance(value, bool):
            raise SchemaValueError(f"Bool1 requires a boolean, got {value!r}")
        return b"\x01" if value else b"\x00"


class _Int32LE(Codec):
    name = "Int32LE"
    byte_length = 4

    def decode(self, raw: bytes) -> int:
        self._require_length(raw)
        return struct.unpack("<i", raw)[0]

    def encode(self, value: Any) -> bytes:
        if isinstance(value, bool) or not isinstance(value, int):
            raise SchemaValueError(f"Int32LE requires an integer, got {value!r}")
        try:
            return struct.pack("<i", value)
        except struct.error as exc:
            raise SchemaValueError(f"Int32LE value is out of range: {value!r}") from exc


class _Float32LE(Codec):
    name = "Float32LE"
    byte_length = 4

    def decode(self, raw: bytes) -> float:
        self._require_length(raw)
        return struct.unpack("<f", raw)[0]

    def encode(self, value: Any) -> bytes:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SchemaValueError(f"Float32LE requires a number, got {value!r}")
        try:
            finite = math.isfinite(value)
        except OverflowError as exc:
            raise SchemaValueError(f"Float32LE value is out of range: {value!r}") from exc
        if not finite:
            raise SchemaValueError(f"Float32LE requires a finite number, got {value!r}")
        try:
            return struct.pack("<f", value)
        except (OverflowError, struct.error) as exc:
            raise SchemaValueError(f"Float32LE value is out of range: {value!r}") from exc


class _FloatMembersCodec(Codec):
    members: tuple[str, ...]
    value_type: type

    def decode(self, raw: bytes) -> Any:
        self._require_length(raw)
        return self.value_type(*struct.unpack(f"<{len(self.members)}f", raw))

    def encode(self, value: Any) -> bytes:
        if isinstance(value, self.value_type):
            supplied = {member: getattr(value, member) for member in self.members}
        elif isinstance(value, Mapping):
            supplied = dict(value)
        else:
            raise SchemaValueError(f"{self.name} requires an object with {self.members!r}")
        if set(supplied) != set(self.members):
            raise SchemaValueError(f"{self.name} requires exactly {self.members!r}")
        return b"".join(Float32LE.encode(supplied[member]) for member in self.members)

    def patch(self, raw: bytes, value: Any) -> CodecPatch:
        self._require_length(raw)
        if not isinstance(value, Mapping):
            raise SchemaValueError(
                f"{self.name} partial update requires an object with {self.members!r}"
            )
        supplied = dict(value)
        unknown = set(supplied) - set(self.members)
        if unknown:
            raise SchemaValueError(f"{self.name} has unknown members: {sorted(unknown)!r}")
        if not supplied:
            raise SchemaValueError(f"{self.name} requires at least one member")
        patched = bytearray(raw)
        changes: dict[str, tuple[float, float]] = {}
        for index, member in enumerate(self.members):
            if member not in supplied:
                continue
            start = index * 4
            before = Float32LE.decode(raw[start : start + 4])
            encoded = Float32LE.encode(supplied[member])
            patched[start : start + 4] = encoded
            changes[member] = (before, Float32LE.decode(encoded))
        return CodecPatch(bytes(patched), changes)


class _Vector2Float32(_FloatMembersCodec):
    name = "Vector2Float32"
    byte_length = 8
    members = ("x", "y")
    value_type = Vector2


class _Vector3Float32(_FloatMembersCodec):
    name = "Vector3Float32"
    byte_length = 12
    members = ("x", "y", "z")
    value_type = Vector3


class _Color4Float32(_FloatMembersCodec):
    name = "Color4Float32"
    byte_length = 16
    members = ("r", "g", "b", "a")
    value_type = Color4


class _Utf8String(Codec):
    """Short .NET UTF-8 strings; a single length byte is valid only below 128."""

    name = "Utf8String"
    byte_length = None

    def decode(self, raw: bytes) -> str:
        if not raw:
            raise SchemaValueError("Utf8String payload is missing its length byte")
        if raw[0] != len(raw) - 1:
            raise SchemaValueError(
                f"Utf8String length byte is {raw[0]}, payload contains {len(raw) - 1} bytes"
            )
        try:
            return raw[1:].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SchemaValueError("Utf8String payload is not valid UTF-8") from exc

    def encode(self, value: Any) -> bytes:
        if not isinstance(value, str):
            raise SchemaValueError(f"Utf8String requires a string, got {value!r}")
        encoded = value.encode("utf-8")
        if len(encoded) > 127:
            raise SchemaValueError("Utf8String single-byte writer is limited to 127 UTF-8 bytes; longer .NET strings require a 7-bit variable-length prefix")
        return bytes((len(encoded),)) + encoded


Bool1 = _Bool1()
Int32LE = _Int32LE()
Float32LE = _Float32LE()
Vector2Float32 = _Vector2Float32()
Vector3Float32 = _Vector3Float32()
Color4Float32 = _Color4Float32()
Utf8String = _Utf8String()


@dataclass(frozen=True, slots=True)
class PropertySchema:
    codec: Codec | None
    writable: bool
    confidence: DecodeConfidence
    enum_names: Mapping[int, str] | None = None
    note: str | None = None
    display_name: str | None = None

    @property
    def schema_name(self) -> str | None:
        return self.codec.name if self.codec is not None else self.display_name

    @property
    def byte_length(self) -> int | None:
        return self.codec.byte_length if self.codec is not None else None

    def decode(self, raw: bytes) -> Any:
        if self.codec is None:
            raise SchemaValueError("property has no confirmed codec")
        return self.codec.decode(raw)

    def api_value(self, raw: bytes) -> Any:
        value = self.decode(raw)
        if self.enum_names is not None:
            return {"raw_value": value, "name": self.enum_names.get(value)}
        return value


def _w(codec: Codec, *, enum: bool = False) -> PropertySchema:
    return PropertySchema(
        codec, True, DecodeConfidence.CONFIRMED, {} if enum else None
    )


def _r(
    codec: Codec | None,
    confidence: DecodeConfidence = DecodeConfidence.CONFIRMED,
    *,
    note: str | None = None,
    schema_name: str | None = None,
) -> PropertySchema:
    return PropertySchema(
        codec, False, confidence, note=note, display_name=schema_name
    )


_GUID = _r(Utf8String)
_UNKNOWN = DecodeConfidence.UNKNOWN

COMPONENT_SCHEMAS: Mapping[str, Mapping[str, PropertySchema]] = {
    "ModPlayerSpawn": {
        "SharedSpawn": _w(Bool1),
        "SpawnUsageType": _w(Int32LE, enum=True),
        "AllowedPlayers": _w(Int32LE),
        "SpawnShape": _w(Int32LE, enum=True),
        "PlayerCountMask": _w(Int32LE),
        "SpawnDistribution": _w(Int32LE, enum=True),
        "LineLength": _w(Float32LE),
        "Radius": _w(Float32LE),
        "Advanced": _w(Bool1),
        "guid": _GUID,
    },
    "ModBoxCollider": {
        "center": _w(Vector3Float32),
        "size": _w(Vector3Float32),
        "guid": _GUID,
    },
    "ModProp": {
        "prop": _r(None, _UNKNOWN, note="asset reference semantics are unknown"),
        "tintColor": _w(Color4Float32),
        "collisionType": _w(Int32LE, enum=True),
        "shadowCastingMode": _w(Int32LE, enum=True),
        "customMaterials": _r(
            None, _UNKNOWN, note="variable reference structure is unknown"
        ),
        "guid": _GUID,
    },
    "ModLight": {
        "type": _w(Int32LE, enum=True),
        "color": _w(Color4Float32),
        "range": _w(Float32LE),
        "intensity": _w(Float32LE),
        "spotAngle": _w(Float32LE),
        "shadows": _w(Int32LE, enum=True),
        "guid": _GUID,
    },
    "ModText": {
        "Text": _r(
            Utf8String,
            note="variable-length property rewrite is outside Writer v0.2 safety scope",
        ),
        "Font": _r(Int32LE, note="not in the v0.2 write allowlist"),
        "Color": _w(Color4Float32),
        "FontSize": _w(Float32LE),
        "HorizontalAlignment": _w(Int32LE, enum=True),
        "Size": _w(Vector2Float32),
        "FontStyles": _w(Int32LE, enum=True),
        "Outline": _w(Bool1),
        "OutlineThickness": _w(Float32LE),
        "OutlineColor": _w(Color4Float32),
        "Shadow": _w(Bool1),
        "ShadowColor": _w(Color4Float32),
        "ShadowOffsetX": _w(Float32LE),
        "ShadowOffsetY": _w(Float32LE),
        "ShadowSoftness": _w(Float32LE),
        "guid": _GUID,
    },
    "ModTrigger": {
        "TriggerShape": _w(Int32LE, enum=True),
        "Size": _w(Vector3Float32),
        "Center": _w(Vector3Float32),
        "Radius": _w(Float32LE),
        "Height": _w(Float32LE),
        "TriggerOnHit": _w(Bool1),
        "TriggerOnEnter": _w(Bool1),
        "TriggerOnExit": _w(Bool1),
        "TriggerOnStay": _w(Bool1),
        "OnHitActions": _r(
            None,
            _UNKNOWN,
            schema_name="ManagedReferencePayload",
            note="Action mutation is not supported in Trigger Writer v0.3",
        ),
        "OnEnterActions": _r(
            None,
            _UNKNOWN,
            schema_name="ManagedReferencePayload",
            note="Action mutation is not supported in Trigger Writer v0.3",
        ),
        "OnExitActions": _r(
            None,
            _UNKNOWN,
            schema_name="ManagedReferencePayload",
            note="Action mutation is not supported in Trigger Writer v0.3",
        ),
        "OnStayActions": _r(
            None,
            _UNKNOWN,
            schema_name="ManagedReferencePayload",
            note="Action mutation is not supported in Trigger Writer v0.3",
        ),
        "StayTriggerInterval": _w(Float32LE),
        "DisableAfterTriggered": _w(Bool1),
        "OneUsePerPlayer": _w(Bool1),
        "guid": _GUID,
    },
}


def get_component_schema(component_type: str) -> Mapping[str, PropertySchema] | None:
    return COMPONENT_SCHEMAS.get(component_type)


def get_property_schema(
    component_type: str, property_name: str
) -> PropertySchema | None:
    component = get_component_schema(component_type)
    return component.get(property_name) if component is not None else None
