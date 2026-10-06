from __future__ import annotations

import struct

from pummelmcp.pmh import DecodeConfidence, PMHReader, Vector3


def _str8(value: str) -> bytes:
    encoded = value.encode("utf-8")
    assert len(encoded) <= 255
    return struct.pack("<B", len(encoded)) + encoded


def _node(name: str, children: bytes = b"", child_count: int = 0) -> bytes:
    return b"".join(
        (
            _str8(name),
            struct.pack("<Bi", 1, 0),
            _str8("Untagged"),
            struct.pack("<H", child_count),
            children,
        )
    )


def _property(name: str, payload: bytes) -> bytes:
    return _str8(name) + struct.pack("<I", len(payload)) + payload


def test_reader_associates_payload_by_structure_and_preserves_unknown() -> None:
    hierarchy = _node("Root", _node("Target"), child_count=1)
    root_index = _str8("root-guid") + struct.pack("<I", 0)
    target_index = b"".join(
        (
            _str8("target-guid"),
            struct.pack("<I", 1),
            _str8("ModTransform"),
            _str8("transform-guid"),
            struct.pack("<B", 1),
        )
    )
    unknown = b"\x00\xff\x81\x02\x99\x10"
    payload = b"".join(
        (
            struct.pack("<H", 4),
            _property("position", struct.pack("<3f", 4.0, 5.0, 6.0)),
            _property("rotation", struct.pack("<3f", 0.0, 0.0, 0.0)),
            _property("scale", struct.pack("<3f", 1.0, 1.0, 1.0)),
            _property("future", unknown),
        )
    )
    data = b"".join(
        (
            _str8("PMH"),
            struct.pack("<IH", 1, 1),
            hierarchy,
            root_index,
            target_index,
            payload,
        )
    )

    scene = PMHReader().read_bytes(data)
    transform = scene.find_game_object("Target").get_component("ModTransform")

    assert scene.fully_consumed
    position = transform.get_field("position")
    assert position.value == Vector3(4.0, 5.0, 6.0)
    assert position.source_span is not None
    assert data[position.source_span.start : position.source_span.end] == position.raw
    assert transform.get_field("future").raw == unknown
    assert transform.get_field("future").confidence is DecodeConfidence.UNKNOWN
