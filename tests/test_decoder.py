from __future__ import annotations

import struct

from pummelmcp.pmh import DecodeConfidence, Vector3, decode_field


def test_transform_vector_is_confirmed_and_keeps_raw_bytes() -> None:
    raw = struct.pack("<3f", 1.25, -2.5, 3.75)

    field = decode_field("ModTransform", "position", raw)

    assert field.value == Vector3(1.25, -2.5, 3.75)
    assert field.raw == raw
    assert field.confidence is DecodeConfidence.CONFIRMED


def test_unknown_property_is_preserved_verbatim() -> None:
    raw = b"\x00\xff\x81\x02\x99\x10"

    field = decode_field("FutureComponent", "FutureProperty", raw)

    assert field.value == raw
    assert field.raw == raw
    assert field.confidence is DecodeConfidence.UNKNOWN
