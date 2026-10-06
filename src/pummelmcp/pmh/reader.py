"""Structural reader for the experimentally documented PMH v1 layout."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from .binary import BinaryReader
from .decoder import decode_field
from .errors import PMHFormatError
from .models import Component, GameObject, Scene, SourceSpan


@dataclass(frozen=True, slots=True)
class _ComponentIndex:
    type_name: str
    guid: str
    enabled: bool
    identity_span: SourceSpan
    index_span: SourceSpan


@dataclass(frozen=True, slots=True)
class _ObjectIndex:
    guid: str
    components: tuple[_ComponentIndex, ...]
    identity_span: SourceSpan
    index_span: SourceSpan


class PMHReader:
    """Parse PMH bytes without mutating or retaining a writable file handle."""

    def read_path(self, path: str | Path) -> Scene:
        source = Path(path)
        data = source.read_bytes()
        return self.read_bytes(data, source=source)

    def read_file(self, stream: BinaryIO) -> Scene:
        data = stream.read()
        if not isinstance(data, bytes):
            raise TypeError("PMH streams must be opened in binary mode")
        return self.read_bytes(data)

    def read_bytes(self, data: bytes, *, source: Path | None = None) -> Scene:
        reader = BinaryReader(bytes(data))

        magic = reader.read_str8(encoding="ascii")
        if magic != "PMH":
            raise PMHFormatError(f"invalid PMH magic {magic!r}")
        version = reader.read_u32()
        if version != 1:
            raise PMHFormatError(f"unsupported PMH version {version}")

        root_count = reader.read_u16()
        return self._read_body(reader, root_count, len(data), source, magic, version)

    def read_prefab_bytes(self, data: bytes, *, source: Path | None = None) -> Scene:
        """Read the observed PMH v1 Prefab grammar: one root, no root count."""
        reader = BinaryReader(bytes(data))
        magic = reader.read_str8(encoding="ascii")
        if magic != "PMH" or reader.read_u32() != 1:
            raise PMHFormatError("unsupported PMH prefab header")
        return self._read_body(reader, 1, len(data), source, magic, 1)

    def _read_body(self, reader: BinaryReader, root_count: int, byte_length: int,
                   source: Path | None, magic: str, version: int) -> Scene:
        roots = [self._read_game_object(reader, sibling_index=index) for index in range(root_count)]
        objects = [obj for root in roots for obj in root.walk()]
        if not objects:
            raise PMHFormatError("PMH scene contains no game objects")

        index = [self._read_object_index(reader) for _ in objects]
        for preorder_index, (obj, entry) in enumerate(zip(objects, index, strict=True)):
            obj.guid = entry.guid
            obj.identity_span = entry.identity_span
            obj.index_span = entry.index_span
            obj.preorder_index = preorder_index
            obj.components = [
                self._read_component_payload(reader, component)
                for component in entry.components
            ]

        return Scene(
            magic=magic,
            version=version,
            roots=roots,
            byte_length=byte_length,
            consumed_bytes=reader.offset,
            source=source,
        )

    def _read_game_object(
        self,
        reader: BinaryReader,
        parent: GameObject | None = None,
        sibling_index: int = -1,
    ) -> GameObject:
        start = reader.offset
        obj = GameObject(
            name=reader.read_str8(),
            active=bool(reader.read_u8()),
            layer=reader.read_i32(),
            tag=reader.read_str8(),
            parent=parent,
            sibling_index=sibling_index,
        )
        child_count = reader.read_u16()
        obj.children = [
            self._read_game_object(reader, obj, index) for index in range(child_count)
        ]
        obj.hierarchy_span = SourceSpan(start, reader.offset - start)
        return obj

    @staticmethod
    def _read_object_index(reader: BinaryReader) -> _ObjectIndex:
        record_start = reader.offset
        guid_start = reader.offset
        guid = reader.read_str8()
        guid_span = SourceSpan(guid_start, reader.offset - guid_start)
        component_count = reader.read_u32()
        components = []
        for _ in range(component_count):
            component_start = reader.offset
            type_name = reader.read_str8()
            component_guid_start = reader.offset
            component_guid = reader.read_str8()
            component_guid_span = SourceSpan(
                component_guid_start, reader.offset - component_guid_start
            )
            enabled = bool(reader.read_u8())
            components.append(
                _ComponentIndex(
                    type_name=type_name,
                    guid=component_guid,
                    enabled=enabled,
                    identity_span=component_guid_span,
                    index_span=SourceSpan(component_start, reader.offset - component_start),
                )
            )
        return _ObjectIndex(
            guid=guid,
            components=tuple(components),
            identity_span=guid_span,
            index_span=SourceSpan(record_start, reader.offset - record_start),
        )

    @staticmethod
    def _read_component_payload(
        reader: BinaryReader, index: _ComponentIndex
    ) -> Component:
        fields = []
        field_count = reader.read_u16()
        for _ in range(field_count):
            name = reader.read_str8()
            payload_length = reader.read_u32()
            payload_start = reader.offset
            payload = reader.read_bytes(payload_length)
            fields.append(
                decode_field(
                    index.type_name,
                    name,
                    payload,
                    source_span=SourceSpan(payload_start, payload_length),
                )
            )
        return Component(
            type_name=index.type_name,
            guid=index.guid,
            enabled=index.enabled,
            fields=fields,
            identity_span=index.identity_span,
            index_span=index.index_span,
        )


def read_pmh(path: str | Path) -> Scene:
    """Convenience wrapper for parsing one PMH file from a read-only byte copy."""
    return PMHReader().read_path(path)


def read_pfab(path: str | Path) -> Scene:
    """Parse one .pfab without interpreting it as a Scene container."""
    source = Path(path)
    if source.suffix.lower() != ".pfab":
        raise PMHFormatError("expected .pfab asset")
    return PMHReader().read_prefab_bytes(source.read_bytes(), source=source)
