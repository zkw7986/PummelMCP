"""Component property decoders, separated from structural PMH parsing."""

from __future__ import annotations

import struct

from .models import DecodeConfidence, Field, SourceSpan, Vector2, Vector3, Vector4
from .schemas import SchemaValueError, get_property_schema


_FLOAT32_FIELD_NAMES = frozenset(
    {
        "Radius",
        "Height",
        "StayTriggerInterval",
        "LineLength",
        "range",
        "intensity",
        "spotAngle",
        "FontSize",
        "OutlineThickness",
        "ShadowOffsetX",
        "ShadowOffsetY",
        "ShadowSoftness",
    }
)


def decode_field(
    component_type: str,
    name: str,
    payload: bytes,
    *,
    source_span: SourceSpan | None = None,
) -> Field:
    """Decode one property while retaining its exact payload in ``Field.raw``.

    Only the three ModTransform vectors are experimentally confirmed. Other
    successful decodes reproduce the inspector's heuristics and are marked
    INFERRED. Unrecognised payloads remain bytes and are marked UNKNOWN.
    """
    raw = bytes(payload)

    if component_type == "ModTransform" and name in {
        "position",
        "rotation",
        "scale",
    }:
        if len(raw) == 12:
            return Field(
                name,
                raw,
                Vector3(*struct.unpack("<3f", raw)),
                DecodeConfidence.CONFIRMED,
                source_span,
            )
        return Field(name, raw, raw, DecodeConfidence.UNKNOWN, source_span)

    schema = get_property_schema(component_type, name)
    if schema is not None and schema.codec is not None:
        try:
            value = schema.decode(raw)
        except SchemaValueError:
            return Field(name, raw, raw, DecodeConfidence.UNKNOWN, source_span)
        return Field(name, raw, value, schema.confidence, source_span)

    if len(raw) == 1:
        return Field(name, raw, bool(raw[0]), DecodeConfidence.INFERRED, source_span)
    if len(raw) == 4:
        fmt = "<f" if name in _FLOAT32_FIELD_NAMES else "<i"
        return Field(
            name, raw, struct.unpack(fmt, raw)[0], DecodeConfidence.INFERRED, source_span
        )
    if len(raw) == 8 and name == "Size":
        return Field(
            name,
            raw,
            Vector2(*struct.unpack("<2f", raw)),
            DecodeConfidence.INFERRED,
            source_span,
        )
    if len(raw) == 12:
        return Field(
            name,
            raw,
            Vector3(*struct.unpack("<3f", raw)),
            DecodeConfidence.INFERRED,
            source_span,
        )
    if len(raw) == 16:
        return Field(
            name,
            raw,
            Vector4(*struct.unpack("<4f", raw)),
            DecodeConfidence.INFERRED,
            source_span,
        )

    if raw and raw[0] == len(raw) - 1:
        try:
            return Field(
                name,
                raw,
                raw[1:].decode("utf-8"),
                DecodeConfidence.INFERRED,
                source_span,
            )
        except UnicodeDecodeError:
            pass
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    else:
        if text.isprintable() or "\n" in text:
            return Field(name, raw, text, DecodeConfidence.INFERRED, source_span)

    return Field(name, raw, raw, DecodeConfidence.UNKNOWN, source_span)
