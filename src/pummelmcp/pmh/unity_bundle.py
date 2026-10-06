"""Read-only UnityFS asset bundle container decoding using the standard library.

The shipped Pummel Party ``StreamingAssets/aa`` tree is a Unity Addressables
build: 3,905 ``.bundle`` files in UnityFS v8 containers. The containers are
small-endian-agnostic in practice -- the header and blocks directory are
big-endian while the payload is little-endian -- so this module never assumes a
byte order. Instead it derives both candidate data-section offsets from the
container flags and keeps only the one whose block table accounts for the file
exactly. A container that does not satisfy that identity is reported as damaged
rather than decoded on a best-effort basis.
"""

from __future__ import annotations

import lzma
import struct
from dataclasses import dataclass
from pathlib import Path

from .errors import EditorAssetCatalogError

UNITYFS_SIGNATURE = b"UnityFS\x00"

BLOCK_INFO_AT_END = 0x40
BLOCK_INFO_NEEDS_PADDING = 0x200
COMPRESSION_MASK = 0x3F

COMPRESSION_NONE = 0
COMPRESSION_LZMA = 1
COMPRESSION_LZ4 = 2
COMPRESSION_LZ4HC = 3

LZ4_BLOCK = 3

PADDING = 16

NODE_FLAG_SERIALIZED_FILE = 0x4

MAX_BUNDLE_BYTES = 256 * 1024 * 1024
MAX_DECOMPRESSED_BYTES = 256 * 1024 * 1024
MAX_BLOCK_COUNT = 8192
MAX_NODE_COUNT = 4096


@dataclass(frozen=True, slots=True)
class UnityFsBlock:
    uncompressed_size: int
    compressed_size: int
    compression: int


@dataclass(frozen=True, slots=True)
class UnityFsNode:
    offset: int
    size: int
    flags: int
    path: str

    @property
    def is_serialized_file(self) -> bool:
        return bool(self.flags & NODE_FLAG_SERIALIZED_FILE)


@dataclass(frozen=True, slots=True)
class UnityFsBundle:
    """A decoded container: one decompressed stream plus its directory."""

    unity_version: str
    revision: str
    flags: int
    blocks: tuple[UnityFsBlock, ...]
    nodes: tuple[UnityFsNode, ...]
    data: bytes
    data_offset: int

    def node_bytes(self, node: UnityFsNode) -> bytes:
        end = node.offset + node.size
        if node.offset < 0 or end > len(self.data):
            raise EditorAssetCatalogError(
                "DAMAGED_UNITY_BUNDLE: node range is outside the decompressed stream"
            )
        return self.data[node.offset : end]

    def serialized_file_nodes(self) -> tuple[UnityFsNode, ...]:
        return tuple(node for node in self.nodes if node.is_serialized_file)


def align(value: int, alignment: int = PADDING) -> int:
    remainder = value % alignment
    return value if remainder == 0 else value + (alignment - remainder)


def _be_u32(data: bytes, offset: int) -> tuple[int, int]:
    if offset + 4 > len(data):
        raise EditorAssetCatalogError("DAMAGED_UNITY_BUNDLE: truncated header")
    return struct.unpack_from(">I", data, offset)[0], offset + 4


def _be_i64(data: bytes, offset: int) -> tuple[int, int]:
    if offset + 8 > len(data):
        raise EditorAssetCatalogError("DAMAGED_UNITY_BUNDLE: truncated header")
    return struct.unpack_from(">q", data, offset)[0], offset + 8


def _be_u16(data: bytes, offset: int) -> tuple[int, int]:
    if offset + 2 > len(data):
        raise EditorAssetCatalogError("DAMAGED_UNITY_BUNDLE: truncated header")
    return struct.unpack_from(">H", data, offset)[0], offset + 2


def _read_cstring(data: bytes, offset: int) -> tuple[str, int]:
    end = data.find(b"\x00", offset)
    if end == -1:
        raise EditorAssetCatalogError("DAMAGED_UNITY_BUNDLE: unterminated string")
    return data[offset:end].decode("utf-8", "replace"), end + 1


def lz4_decompress_block(source: bytes, expected_size: int) -> bytes:
    """Decode one raw LZ4 block (no frame header, no content checksum)."""
    if expected_size < 0 or expected_size > MAX_DECOMPRESSED_BYTES:
        raise EditorAssetCatalogError(
            "DAMAGED_UNITY_BUNDLE: implausible LZ4 block size"
        )
    out = bytearray()
    index = 0
    length = len(source)
    while index < length:
        token = source[index]
        index += 1

        literal_length = token >> 4
        if literal_length == 15:
            while True:
                if index >= length:
                    raise EditorAssetCatalogError(
                        "DAMAGED_UNITY_BUNDLE: truncated LZ4 literal length"
                    )
                extra = source[index]
                index += 1
                literal_length += extra
                if extra != 255:
                    break
        if literal_length:
            if index + literal_length > length:
                raise EditorAssetCatalogError(
                    "DAMAGED_UNITY_BUNDLE: truncated LZ4 literal run"
                )
            out += source[index : index + literal_length]
            index += literal_length
        if index >= length:
            break

        if index + 2 > length:
            raise EditorAssetCatalogError("DAMAGED_UNITY_BUNDLE: truncated LZ4 offset")
        match_offset = source[index] | (source[index + 1] << 8)
        index += 2
        if match_offset == 0 or match_offset > len(out):
            raise EditorAssetCatalogError(
                "DAMAGED_UNITY_BUNDLE: LZ4 match offset escapes the output window"
            )

        match_length = token & 0x0F
        if match_length == 15:
            while True:
                if index >= length:
                    raise EditorAssetCatalogError(
                        "DAMAGED_UNITY_BUNDLE: truncated LZ4 match length"
                    )
                extra = source[index]
                index += 1
                match_length += extra
                if extra != 255:
                    break
        match_length += 4

        start = len(out) - match_offset
        if match_offset >= match_length:
            out += out[start : start + match_length]
        else:
            for step in range(match_length):
                out.append(out[start + step])

    if len(out) != expected_size:
        raise EditorAssetCatalogError(
            "DAMAGED_UNITY_BUNDLE: LZ4 block decoded to "
            f"{len(out)} bytes, expected {expected_size}"
        )
    return bytes(out)


def _lzma_decompress_block(source: bytes, expected_size: int) -> bytes:
    if len(source) < 5:
        raise EditorAssetCatalogError("DAMAGED_UNITY_BUNDLE: truncated LZMA header")
    properties = source[0]
    if properties >= 9 * 5 * 5:
        raise EditorAssetCatalogError("DAMAGED_UNITY_BUNDLE: invalid LZMA properties")
    lc = properties % 9
    remainder = properties // 9
    lp = remainder % 5
    pb = remainder // 5
    dict_size = struct.unpack_from("<I", source, 1)[0]
    if dict_size == 0:
        dict_size = 1 << 16
    decoder = lzma.LZMADecompressor(
        format=lzma.FORMAT_RAW,
        filters=[
            {
                "id": lzma.FILTER_LZMA1,
                "lc": lc,
                "lp": lp,
                "pb": pb,
                "dict_size": min(dict_size, MAX_DECOMPRESSED_BYTES),
            }
        ],
    )
    try:
        decoded = decoder.decompress(source[5:], max_length=expected_size)
    except lzma.LZMAError as exc:
        raise EditorAssetCatalogError(
            f"DAMAGED_UNITY_BUNDLE: LZMA block failed to decode ({exc})"
        ) from exc
    if len(decoded) != expected_size:
        raise EditorAssetCatalogError(
            "DAMAGED_UNITY_BUNDLE: LZMA block decoded to "
            f"{len(decoded)} bytes, expected {expected_size}"
        )
    return decoded


def decompress_block(compression: int, source: bytes, expected_size: int) -> bytes:
    if expected_size < 0 or expected_size > MAX_DECOMPRESSED_BYTES:
        raise EditorAssetCatalogError(
            "DAMAGED_UNITY_BUNDLE: implausible block size"
        )
    if compression == COMPRESSION_NONE:
        if len(source) != expected_size:
            raise EditorAssetCatalogError(
                "DAMAGED_UNITY_BUNDLE: stored block length disagrees with its header"
            )
        return source
    if compression == COMPRESSION_LZMA:
        return _lzma_decompress_block(source, expected_size)
    if compression in (COMPRESSION_LZ4, COMPRESSION_LZ4HC):
        return lz4_decompress_block(source, expected_size)
    raise EditorAssetCatalogError(
        f"DAMAGED_UNITY_BUNDLE: unsupported block compression {compression}"
    )


def _parse_blocks_info(blocks_info: bytes) -> tuple[tuple[UnityFsBlock, ...], tuple[UnityFsNode, ...]]:
    if len(blocks_info) < 20:
        raise EditorAssetCatalogError("DAMAGED_UNITY_BUNDLE: short blocks directory")
    offset = 16
    block_count, offset = _be_u32(blocks_info, offset)
    if block_count > MAX_BLOCK_COUNT:
        raise EditorAssetCatalogError(
            f"DAMAGED_UNITY_BUNDLE: {block_count} blocks exceeds the supported limit"
        )
    blocks: list[UnityFsBlock] = []
    for _ in range(block_count):
        uncompressed_size, offset = _be_u32(blocks_info, offset)
        compressed_size, offset = _be_u32(blocks_info, offset)
        block_flags, offset = _be_u16(blocks_info, offset)
        blocks.append(
            UnityFsBlock(
                uncompressed_size=uncompressed_size,
                compressed_size=compressed_size,
                compression=block_flags & COMPRESSION_MASK,
            )
        )
    node_count, offset = _be_u32(blocks_info, offset)
    if node_count > MAX_NODE_COUNT:
        raise EditorAssetCatalogError(
            f"DAMAGED_UNITY_BUNDLE: {node_count} nodes exceeds the supported limit"
        )
    nodes: list[UnityFsNode] = []
    for _ in range(node_count):
        node_offset, offset = _be_i64(blocks_info, offset)
        node_size, offset = _be_i64(blocks_info, offset)
        node_flags, offset = _be_u32(blocks_info, offset)
        path, offset = _read_cstring(blocks_info, offset)
        nodes.append(
            UnityFsNode(
                offset=node_offset, size=node_size, flags=node_flags, path=path
            )
        )
    return tuple(blocks), tuple(nodes)


def read_unityfs_bundle(path: str | Path) -> UnityFsBundle:
    """Decode one UnityFS container from disk."""
    source = Path(path)
    raw = source.read_bytes()
    if len(raw) > MAX_BUNDLE_BYTES:
        raise EditorAssetCatalogError(
            f"DAMAGED_UNITY_BUNDLE: {source.name} exceeds the supported size"
        )
    if not raw.startswith(UNITYFS_SIGNATURE):
        raise EditorAssetCatalogError(
            f"DAMAGED_UNITY_BUNDLE: {source.name} is not a UnityFS container"
        )

    offset = len(UNITYFS_SIGNATURE)
    version, offset = _be_u32(raw, offset)
    if version < 6:
        raise EditorAssetCatalogError(
            f"DAMAGED_UNITY_BUNDLE: unsupported container version {version}"
        )
    unity_version, offset = _read_cstring(raw, offset)
    revision, offset = _read_cstring(raw, offset)
    declared_size, offset = _be_i64(raw, offset)
    if declared_size != len(raw):
        raise EditorAssetCatalogError(
            "DAMAGED_UNITY_BUNDLE: declared size disagrees with the file length"
        )
    blocks_info_compressed, offset = _be_u32(raw, offset)
    blocks_info_uncompressed, offset = _be_u32(raw, offset)
    flags, offset = _be_u32(raw, offset)

    if flags & BLOCK_INFO_NEEDS_PADDING:
        offset = align(offset, PADDING)
    if offset + blocks_info_compressed > len(raw):
        raise EditorAssetCatalogError(
            "DAMAGED_UNITY_BUNDLE: blocks directory escapes the file"
        )
    blocks_info = decompress_block(
        flags & COMPRESSION_MASK,
        raw[offset : offset + blocks_info_compressed],
        blocks_info_uncompressed,
    )
    blocks, nodes = _parse_blocks_info(blocks_info)
    if not blocks:
        raise EditorAssetCatalogError("DAMAGED_UNITY_BUNDLE: no data blocks")

    after_blocks_info = offset + blocks_info_compressed
    candidates = [after_blocks_info]
    padded = align(after_blocks_info, PADDING)
    if padded != after_blocks_info:
        candidates.append(padded)
    if flags & BLOCK_INFO_NEEDS_PADDING:
        candidates.reverse()

    total_compressed = sum(block.compressed_size for block in blocks)
    data_offset = None
    for candidate in candidates:
        if candidate + total_compressed == len(raw):
            data_offset = candidate
            break
    if data_offset is None:
        raise EditorAssetCatalogError(
            "DAMAGED_UNITY_BUNDLE: the block table does not account for the file length"
        )

    chunks: list[bytes] = []
    cursor = data_offset
    for block in blocks:
        end = cursor + block.compressed_size
        chunks.append(
            decompress_block(
                block.compression,
                raw[cursor:end],
                block.uncompressed_size,
            )
        )
        cursor = end
    data = b"".join(chunks)

    node_end = 0
    for node in nodes:
        if node.offset < 0 or node.size < 0:
            raise EditorAssetCatalogError(
                "DAMAGED_UNITY_BUNDLE: negative node range"
            )
        node_end = max(node_end, node.offset + node.size)
    if node_end > len(data):
        raise EditorAssetCatalogError(
            "DAMAGED_UNITY_BUNDLE: node directory escapes the decompressed stream"
        )

    return UnityFsBundle(
        unity_version=unity_version,
        revision=revision,
        flags=flags,
        blocks=blocks,
        nodes=nodes,
        data=data,
        data_offset=data_offset,
    )
