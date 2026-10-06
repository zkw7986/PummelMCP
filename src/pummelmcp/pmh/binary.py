"""Bounds-checked little-endian binary primitives used by the PMH reader."""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .errors import PMHFormatError


@dataclass(slots=True)
class BinaryReader:
    data: bytes
    offset: int = 0

    @property
    def remaining(self) -> int:
        return len(self.data) - self.offset

    @property
    def fully_consumed(self) -> bool:
        return self.offset == len(self.data)

    def read_bytes(self, length: int) -> bytes:
        if length < 0:
            raise PMHFormatError(f"negative byte length {length} at 0x{self.offset:x}")
        end = self.offset + length
        if end > len(self.data):
            raise PMHFormatError(
                f"unexpected EOF at 0x{self.offset:x}: wanted {length} bytes, "
                f"only {self.remaining} remain"
            )
        value = self.data[self.offset:end]
        self.offset = end
        return value

    def _unpack(self, fmt: str, length: int) -> int:
        start = self.offset
        raw = self.read_bytes(length)
        try:
            return struct.unpack(fmt, raw)[0]
        except struct.error as exc:  # pragma: no cover - guarded by read_bytes
            raise PMHFormatError(f"invalid value at 0x{start:x}") from exc

    def read_u8(self) -> int:
        return self._unpack("<B", 1)

    def read_u16(self) -> int:
        return self._unpack("<H", 2)

    def read_u32(self) -> int:
        return self._unpack("<I", 4)

    def read_i32(self) -> int:
        return self._unpack("<i", 4)

    def read_str8(self, *, encoding: str = "utf-8") -> str:
        start = self.offset
        length = self.read_u8()
        raw = self.read_bytes(length)
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError as exc:
            raise PMHFormatError(
                f"invalid {encoding} string at 0x{start:x} (length {length})"
            ) from exc
