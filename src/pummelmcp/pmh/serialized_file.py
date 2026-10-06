"""Minimal, fail-closed reader for a Unity SerializedFile (format version 22).

Only the structure needed to read a game-authored ScriptableObject is
implemented. Every step is validated against the container's own declared sizes,
and the type tree carried in the file drives the field walk, so the reader never
guesses a field order or a record stride. Anything unexpected fails closed with
:class:`~pummelmcp.pmh.errors.DamagedEditorAssetCatalogError`.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .errors import DamagedEditorAssetCatalogError
from .unity_bundle import align

ENDIANNESS_LITTLE = 0

CLASS_ID_MONO_BEHAVIOUR = 114

TYPE_TREE_NODE_BYTES = 32
COMMON_STRING_MASK = 0x80000000

MAX_TYPE_COUNT = 512
MAX_OBJECT_COUNT = 4096
MAX_NODE_COUNT = 4096
MAX_STRING_BUFFER_BYTES = 1 << 20
MAX_TYPE_DEPENDENCIES = 1024
MAX_ARRAY_COUNT = 1 << 20
MAX_STRING_BYTES = 1 << 16
MAX_DEPTH = 64

PRIMITIVE_STRIDE = 4


def _damaged(detail: str) -> DamagedEditorAssetCatalogError:
    return DamagedEditorAssetCatalogError(f"DAMAGED_EDITOR_ASSET_CATALOG: {detail}")


class _Cursor:
    """A bounds-checked little-endian cursor over one SerializedFile payload."""

    __slots__ = ("data", "offset", "limit")

    def __init__(self, data: bytes, offset: int = 0, limit: int | None = None) -> None:
        self.data = data
        self.offset = offset
        self.limit = len(data) if limit is None else limit

    def _require(self, count: int) -> None:
        if count < 0 or self.offset + count > self.limit:
            raise _damaged(
                f"truncated metadata at offset {self.offset} (wanted {count} bytes)"
            )

    def read(self, count: int) -> bytes:
        self._require(count)
        chunk = self.data[self.offset : self.offset + count]
        self.offset += count
        return chunk

    def u8(self) -> int:
        self._require(1)
        value = self.data[self.offset]
        self.offset += 1
        return value

    def u16(self) -> int:
        self._require(2)
        value = struct.unpack_from("<H", self.data, self.offset)[0]
        self.offset += 2
        return value

    def i16(self) -> int:
        self._require(2)
        value = struct.unpack_from("<h", self.data, self.offset)[0]
        self.offset += 2
        return value

    def u32(self) -> int:
        self._require(4)
        value = struct.unpack_from("<I", self.data, self.offset)[0]
        self.offset += 4
        return value

    def i32(self) -> int:
        self._require(4)
        value = struct.unpack_from("<i", self.data, self.offset)[0]
        self.offset += 4
        return value

    def i64(self) -> int:
        self._require(8)
        value = struct.unpack_from("<q", self.data, self.offset)[0]
        self.offset += 8
        return value

    def u32_be(self) -> int:
        self._require(4)
        value = struct.unpack_from(">I", self.data, self.offset)[0]
        self.offset += 4
        return value

    def i64_be(self) -> int:
        self._require(8)
        value = struct.unpack_from(">q", self.data, self.offset)[0]
        self.offset += 8
        return value

    def align(self, alignment: int = PRIMITIVE_STRIDE) -> None:
        remainder = self.offset % alignment
        if remainder:
            self.offset += alignment - remainder
        if self.offset > self.limit:
            raise _damaged("metadata alignment escaped its section")


@dataclass(slots=True)
class TypeTreeNode:
    level: int
    type_offset: int
    name_offset: int
    byte_size: int
    index: int
    meta_flag: int
    name: str | None
    children: list[TypeTreeNode] = field(default_factory=list)

    def child(self, name: str) -> TypeTreeNode | None:
        for item in self.children:
            if item.name == name:
                return item
        return None

    def is_leaf(self) -> bool:
        return self.byte_size >= 0


@dataclass(frozen=True, slots=True)
class SerializedType:
    class_id: int
    is_stripped: bool
    script_type_index: int
    nodes: tuple[TypeTreeNode, ...]

    @property
    def root(self) -> TypeTreeNode | None:
        return self.nodes[0] if self.nodes else None


@dataclass(frozen=True, slots=True)
class SerializedObject:
    path_id: int
    byte_start: int
    byte_size: int
    type_id: int


@dataclass(frozen=True, slots=True)
class SerializedFile:
    version: int
    unity_version: str
    target_platform: int
    enable_type_tree: bool
    data_offset: int
    types: tuple[SerializedType, ...]
    objects: tuple[SerializedObject, ...]
    payload: bytes

    def object_bytes(self, item: SerializedObject) -> bytes:
        start = self.data_offset + item.byte_start
        end = start + item.byte_size
        if start < 0 or end > len(self.payload):
            raise _damaged("object range escapes the serialized file")
        return self.payload[start:end]

    def type_of(self, item: SerializedObject) -> SerializedType:
        if not 0 <= item.type_id < len(self.types):
            raise _damaged(f"object {item.path_id} references type {item.type_id}")
        return self.types[item.type_id]


def _read_cstring(data: bytes, offset: int) -> str:
    end = data.find(b"\x00", offset)
    if end == -1:
        raise _damaged("unterminated string in the metadata section")
    return data[offset:end].decode("utf-8", "replace")


def _read_type_tree_node(cursor: _Cursor) -> tuple[int, int, int, int, int, int]:
    cursor.u16()  # node serialization version
    level = cursor.u8()
    cursor.u8()  # raw type byte; the type name is resolved separately
    type_offset = cursor.u32()
    name_offset = cursor.u32()
    byte_size = cursor.i32()
    index = cursor.i32()
    meta_flag = cursor.i32()
    cursor.i64()  # ref type hash
    return level, type_offset, name_offset, byte_size, index, meta_flag


def _resolve_name(offset: int, string_buffer: bytes) -> str | None:
    if offset & COMMON_STRING_MASK:
        return None
    if offset >= len(string_buffer):
        return None
    return _read_cstring(string_buffer, offset)


def _build_type_tree(
    flat: list[tuple[int, int, int, int, int, int]], string_buffer: bytes
) -> tuple[TypeTreeNode, ...]:
    roots: list[TypeTreeNode] = []
    stack: list[TypeTreeNode] = []
    for level, type_offset, name_offset, byte_size, index, meta_flag in flat:
        node = TypeTreeNode(
            level=level,
            type_offset=type_offset,
            name_offset=name_offset,
            byte_size=byte_size,
            index=index,
            meta_flag=meta_flag,
            name=_resolve_name(name_offset, string_buffer),
        )
        while stack and stack[-1].level >= level:
            stack.pop()
        if stack:
            stack[-1].children.append(node)
        else:
            if level != 0:
                raise _damaged(f"type tree node level {level} has no parent")
            roots.append(node)
        stack.append(node)
    return tuple(roots)


def _locate_header_tail(payload: bytes, string_end: int) -> int | None:
    """Return the offset of ``targetPlatform`` after the unity version string.

    The shipped bundles place it immediately after the NUL-terminated version
    with no padding; other Unity versions pad the tail to a four-byte boundary.
    Both candidates are probed and only one that yields a plausible platform id
    and type count is accepted, so a wrong choice fails closed here instead of
    producing a misaligned type table downstream.
    """
    candidates = [string_end]
    padded = align(string_end, PRIMITIVE_STRIDE)
    if padded != string_end:
        candidates.append(padded)
    for candidate in candidates:
        if candidate + 9 > len(payload):
            continue
        probe = _Cursor(payload, candidate, len(payload))
        target_platform = probe.i32()
        enable_type_tree = probe.u8()
        if not 0 <= target_platform <= 128 or enable_type_tree > 1:
            continue
        type_count = probe.i32()
        if 1 <= type_count <= MAX_TYPE_COUNT:
            return candidate
    return None


def read_serialized_file(payload: bytes, *, require_objects: bool = True) -> SerializedFile:
    """Decode one SerializedFile payload (already decompressed)."""
    cursor = _Cursor(payload)

    cursor.u32_be()  # legacy metadata size
    cursor.u32_be()  # legacy file size
    version = cursor.u32_be()
    cursor.u32_be()  # legacy data offset
    endianness = cursor.u8()
    cursor.read(3)
    if endianness != ENDIANNESS_LITTLE:
        raise _damaged(f"unsupported serialized file endianness {endianness}")
    if version < 15:
        raise _damaged(f"unsupported serialized file version {version}")

    metadata_size = cursor.u32_be()
    declared_size = cursor.i64_be()
    data_offset = cursor.i64_be()
    cursor.i64_be()  # reserved
    if declared_size != len(payload):
        raise _damaged(
            "declared serialized file size disagrees with the decoded stream"
        )
    unity_version = _read_cstring(payload, cursor.offset)
    string_end = cursor.offset + len(unity_version.encode("utf-8", "replace")) + 1
    tail = _locate_header_tail(payload, string_end)
    if tail is None:
        raise _damaged("serialized file header exposes no usable type table")
    cursor = _Cursor(payload, tail)
    target_platform = cursor.i32()
    enable_type_tree = bool(cursor.u8())

    if metadata_size <= 0 or metadata_size > len(payload):
        raise _damaged("implausible serialized file metadata size")
    if data_offset <= cursor.offset or data_offset > len(payload):
        raise _damaged("serialized file data offset is outside its metadata")

    type_count = cursor.i32()
    if not 1 <= type_count <= MAX_TYPE_COUNT:
        raise _damaged(f"implausible serialized file type count {type_count}")

    types: list[SerializedType] = []
    for _ in range(type_count):
        class_id = cursor.i32()
        is_stripped = bool(cursor.u8())
        script_type_index = cursor.i16() if version >= 17 else -1
        if version >= 13:
            if class_id == CLASS_ID_MONO_BEHAVIOUR or class_id < 0:
                cursor.read(16)  # script id hash
                cursor.read(16)  # old type hash
            else:
                cursor.read(16)  # old type hash
        nodes: tuple[TypeTreeNode, ...] = ()
        if enable_type_tree:
            node_count = cursor.i32()
            string_buffer_size = cursor.i32()
            if not 0 <= node_count <= MAX_NODE_COUNT:
                raise _damaged(f"implausible type tree node count {node_count}")
            if not 0 <= string_buffer_size <= MAX_STRING_BUFFER_BYTES:
                raise _damaged("implausible type tree string buffer size")
            flat = [_read_type_tree_node(cursor) for _ in range(node_count)]
            string_buffer = cursor.read(string_buffer_size)
            if version >= 21:
                dependency_count = cursor.i32()
                if not 0 <= dependency_count <= MAX_TYPE_DEPENDENCIES:
                    raise _damaged(
                        f"implausible type dependency count {dependency_count}"
                    )
                cursor.read(dependency_count * 4)
            nodes = _build_type_tree(flat, string_buffer)
        types.append(
            SerializedType(
                class_id=class_id,
                is_stripped=is_stripped,
                script_type_index=script_type_index,
                nodes=nodes,
            )
        )

    object_count = cursor.i32()
    if not 0 <= object_count <= MAX_OBJECT_COUNT:
        raise _damaged(f"implausible serialized file object count {object_count}")
    objects: list[SerializedObject] = []
    for _ in range(object_count):
        cursor.align(PRIMITIVE_STRIDE)
        path_id = cursor.i64()
        if version >= 22:
            byte_start = cursor.i64()
            byte_size = cursor.i32()
        else:
            byte_start = cursor.u32()
            byte_size = cursor.u32()
        type_id = cursor.i32()
        objects.append(
            SerializedObject(
                path_id=path_id,
                byte_start=byte_start,
                byte_size=byte_size,
                type_id=type_id,
            )
        )

    if require_objects and not objects:
        raise _damaged("serialized file contains no objects")
    if cursor.offset > data_offset:
        raise _damaged("the object table overruns the metadata section")

    return SerializedFile(
        version=version,
        unity_version=unity_version,
        target_platform=target_platform,
        enable_type_tree=enable_type_tree,
        data_offset=data_offset,
        types=tuple(types),
        objects=tuple(objects),
        payload=payload,
    )


class TypeTreeReader:
    """Walks one object's bytes using that object's own type tree."""

    def __init__(self, data: bytes) -> None:
        self._cursor = _Cursor(data, 0, len(data))
        self._data = data

    @property
    def consumed(self) -> int:
        return self._cursor.offset

    def read_object(self, node: TypeTreeNode) -> dict[str, object]:
        """Read a composite root node.

        Field names resolve locally for every field this project consumes.
        Unity writes the names of engine-internal fields (``m_Name`` and the
        array plumbing) through a built-in string table that a standalone
        SerializedFile does not carry, so those entries are keyed positionally
        as ``#<index>`` instead of being dropped.
        """
        return self._read_composite(node, 0)

    def read_ordered(self, node: TypeTreeNode) -> tuple[object, ...]:
        """Read a composite node's children in serialized order."""
        return tuple(self.read_node(child) for child in node.children)

    def _read_composite(self, node: TypeTreeNode, depth: int) -> dict[str, object]:
        if depth > MAX_DEPTH:
            raise _damaged("type tree nesting exceeds the supported depth")
        result: dict[str, object] = {}
        for index, child in enumerate(node.children):
            key = child.name if child.name is not None else f"#{index}"
            if key in result:
                raise _damaged(f"duplicate field {key!r} in the type tree")
            result[key] = self.read_node(child, depth + 1)
        return result

    def read_node(self, node: TypeTreeNode, depth: int = 0) -> object:
        if depth > MAX_DEPTH:
            raise _damaged("type tree nesting exceeds the supported depth")
        if node.is_leaf():
            return self._read_leaf(node)

        element = _list_element(node)
        if element is not None:
            count = self._cursor.i32()
            if not 0 <= count <= MAX_ARRAY_COUNT:
                raise _damaged(f"implausible array count {count}")
            if element.byte_size == 1 and not element.children:
                if count > MAX_STRING_BYTES:
                    raise _damaged("implausible string length")
                raw = self._cursor.read(count)
                self._cursor.align(PRIMITIVE_STRIDE)
                return raw.decode("utf-8", "replace")
            items = [self.read_node(element, depth + 1) for _ in range(count)]
            self._cursor.align(PRIMITIVE_STRIDE)
            return items

        return self._read_composite(node, depth)

    def _read_leaf(self, node: TypeTreeNode) -> bytes:
        raw = self._cursor.read(node.byte_size)
        self._cursor.align(PRIMITIVE_STRIDE)
        return raw


def _list_element(node: TypeTreeNode) -> TypeTreeNode | None:
    """Return the element node if ``node`` serializes as ``string`` or ``List<T>``.

    Both shapes derive from ``Array`` and have identical plumbing, so the
    discriminator has to be structural -- the ``Array``/``size``/``data`` names
    are engine-internal and absent from a standalone file's string buffer:

    * the node is variable-length and has exactly one child;
    * that child is itself variable-length with exactly two children;
    * the first grandchild is a bare, four-byte ``size`` field.

    Anything else is a plain composite and is walked field by field.
    """
    if node.byte_size != -1 or len(node.children) != 1:
        return None
    inner = node.children[0]
    if inner.byte_size != -1 or len(inner.children) != 2:
        return None
    size_node, element = inner.children
    if size_node.byte_size != PRIMITIVE_STRIDE or size_node.children:
        return None
    return element
