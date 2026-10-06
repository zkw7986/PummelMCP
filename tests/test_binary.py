from __future__ import annotations

import struct

import pytest

from pummelmcp.pmh.binary import BinaryReader
from pummelmcp.pmh.errors import PMHFormatError


def test_little_endian_primitives_and_consumption() -> None:
    reader = BinaryReader(struct.pack("<BHIi", 7, 513, 100_000, -42))

    assert reader.read_u8() == 7
    assert reader.read_u16() == 513
    assert reader.read_u32() == 100_000
    assert reader.read_i32() == -42
    assert reader.fully_consumed


def test_reader_rejects_truncated_data() -> None:
    reader = BinaryReader(b"\x01")

    with pytest.raises(PMHFormatError, match="unexpected EOF"):
        reader.read_u32()
