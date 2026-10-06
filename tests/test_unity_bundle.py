"""Unit tests for the UnityFS container layer and its raw LZ4 decoder.

The synthetic containers built here pin the parser's contract, including the
consistency identities it refuses to guess through. The byte layout itself is
pinned separately by the real ``modassetlist.bundle`` tests in
``test_editor_assets.py``.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from pummelmcp.pmh.errors import EditorAssetCatalogError
from pummelmcp.pmh.unity_bundle import (
    COMPRESSION_LZ4,
    align,
    lz4_decompress_block,
    read_unityfs_bundle,
)


def _build_container(
    *,
    stored: bytes,
    uncompressed_size: int | None = None,
    declared_compressed_size: int | None = None,
    block_compression: int = 0,
    node_paths: tuple[str, ...] = ("CAB-test",),
) -> bytes:
    """Assemble a UnityFS container around the bytes that go in the file.

    ``blocks_info_compression`` is left at zero so the directory stays readable;
    only the data block's own flag selects a decompressor.
    """
    size = len(stored) if uncompressed_size is None else uncompressed_size
    declared = (
        len(stored) if declared_compressed_size is None else declared_compressed_size
    )
    node_bytes = b"".join(
        struct.pack(">qqI", 0, size, 0x4) + path.encode() + b"\x00"
        for path in node_paths
    )
    blocks_info = (
        bytes(16)
        + struct.pack(">I", 1)
        + struct.pack(">II", size, declared)
        + struct.pack(">H", block_compression)
        + struct.pack(">I", len(node_paths))
        + node_bytes
    )
    header = (
        b"UnityFS\x00"
        + struct.pack(">I", 8)
        + b"5.x.x\x00"
        + b"0.0.0\x00"
        + struct.pack(">q", 0)
        + struct.pack(">I", len(blocks_info))
        + struct.pack(">I", len(blocks_info))
        + struct.pack(">I", 0x200)
    )
    blocks_info_start = align(len(header))
    data_start = align(blocks_info_start + len(blocks_info))
    header = header[:24] + struct.pack(">q", data_start + len(stored)) + header[32:]
    return (
        header
        + bytes(blocks_info_start - len(header))
        + blocks_info
        + bytes(data_start - blocks_info_start - len(blocks_info))
        + stored
    )


def test_lz4_decodes_a_literal_only_block() -> None:
    assert lz4_decompress_block(b"\x30abc", 3) == b"abc"


def test_lz4_decodes_a_non_overlapping_match() -> None:
    assert lz4_decompress_block(b"\x40abcd\x04\x00", 8) == b"abcdabcd"


def test_lz4_decodes_an_overlapping_match() -> None:
    # A one-byte offset with a four-byte match repeats the seed byte.
    assert lz4_decompress_block(b"\x10a\x01\x00", 5) == b"aaaaa"


def test_lz4_decodes_extended_literal_lengths() -> None:
    literals = bytes(range(256)) + b"0123456789abcde"
    stream = b"\xf0\xff\x01" + literals
    assert lz4_decompress_block(stream, len(literals)) == literals


def test_lz4_decodes_extended_match_lengths() -> None:
    # Token 0x4f: four literals, then a 15-nibble match length extended by
    # 255 + 1. The four-byte minimum makes the copy 275 bytes long, and a
    # four-byte offset means the copy overlaps its own output.
    stream = b"\x4fabcd\x04\x00\xff\x01"
    decoded = lz4_decompress_block(stream, 279)
    assert len(decoded) == 279
    assert decoded == b"abcd" * 69 + b"abc"


def test_lz4_rejects_a_zero_match_offset() -> None:
    with pytest.raises(EditorAssetCatalogError, match="match offset escapes"):
        lz4_decompress_block(b"\x10a\x00\x00", 5)


def test_lz4_rejects_a_truncated_literal_run() -> None:
    with pytest.raises(EditorAssetCatalogError, match="truncated LZ4 literal run"):
        lz4_decompress_block(b"\x50ab", 5)


def test_lz4_rejects_a_size_disagreement() -> None:
    with pytest.raises(EditorAssetCatalogError, match="decoded to 3 bytes, expected 4"):
        lz4_decompress_block(b"\x30abc", 4)


def test_container_round_trips_a_stored_payload(tmp_path: Path) -> None:
    payload = bytes(range(256)) * 3
    bundle_path = tmp_path / "sample.bundle"
    bundle_path.write_bytes(_build_container(stored=payload))

    bundle = read_unityfs_bundle(bundle_path)
    assert bundle.unity_version == "5.x.x"
    assert bundle.data == payload
    assert len(bundle.blocks) == 1
    assert [node.path for node in bundle.nodes] == ["CAB-test"]
    assert bundle.serialized_file_nodes() == bundle.nodes
    assert bundle.node_bytes(bundle.nodes[0]) == payload


def test_container_rejects_a_file_that_is_not_a_unityfs_archive(tmp_path: Path) -> None:
    target = tmp_path / "plain.bin"
    target.write_bytes(b"not a bundle at all")
    with pytest.raises(EditorAssetCatalogError, match="is not a UnityFS container"):
        read_unityfs_bundle(target)


def test_container_refuses_a_block_table_that_does_not_account_for_the_file(
    tmp_path: Path,
) -> None:
    target = tmp_path / "corrupt.bundle"
    target.write_bytes(
        _build_container(stored=b"payload bytes", declared_compressed_size=12)
    )
    with pytest.raises(EditorAssetCatalogError, match="does not account for the file"):
        read_unityfs_bundle(target)


def test_container_rejects_a_declared_size_mismatch(tmp_path: Path) -> None:
    raw = bytearray(_build_container(stored=b"payload bytes"))
    raw[28:36] = struct.pack(">q", len(raw) + 8)
    target = tmp_path / "size.bundle"
    target.write_bytes(bytes(raw))
    with pytest.raises(EditorAssetCatalogError, match="declared size disagrees"):
        read_unityfs_bundle(target)


def test_lz4_block_compression_is_reachable_through_the_container(tmp_path: Path) -> None:
    payload = b"abcdabcd"
    target = tmp_path / "lz4.bundle"
    target.write_bytes(
        _build_container(
            stored=b"\x40abcd\x04\x00",
            uncompressed_size=len(payload),
            block_compression=COMPRESSION_LZ4,
            node_paths=("CAB-lz4",),
        )
    )

    bundle = read_unityfs_bundle(target)
    assert bundle.data == payload
    assert bundle.node_bytes(bundle.nodes[0]) == payload
