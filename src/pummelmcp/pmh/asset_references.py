"""Strict, read-only parsing and resolution for observed PMH asset references."""
from __future__ import annotations

import hashlib
import re
import struct
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

_GUID = re.compile(rb"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class AssetReferenceError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CompactAssetReference:
    guid: str
    raw: bytes
    shape: str = "COMPACT_UUID_REFERENCE_V1"
    semantic_status: str = "STRUCTURALLY_KNOWN_SEMANTIC_UNKNOWN"


@dataclass(frozen=True, slots=True)
class MaterialReferenceList:
    elements: tuple[CompactAssetReference, ...]
    raw: bytes
    shape: str = "COUNTED_COMPACT_UUID_REFERENCE_LIST_V1"


@dataclass(frozen=True, slots=True)
class AssetResolution:
    reference_kind: str
    serialized_guid: str
    asset_type: str
    resolved_path: str | None
    metadata_path: str | None
    exists: bool
    metadata_sha256: str | None
    asset_sha256: str | None
    mod_relative_path: str | None
    status: str
    source_evidence: str

    def to_dict(self):
        return asdict(self)


def parse_compact_asset_reference(raw: bytes) -> CompactAssetReference:
    if len(raw) != 38 or raw[:2] != b"\x01\x24" or not _GUID.fullmatch(raw[2:]):
        raise AssetReferenceError("unsupported compact asset reference framing")
    value = raw[2:].decode("ascii")
    try:
        uuid.UUID(value)
    except ValueError as exc:
        raise AssetReferenceError("invalid compact asset UUID") from exc
    return CompactAssetReference(value, bytes(raw))


def parse_material_reference_list(raw: bytes) -> MaterialReferenceList:
    if len(raw) < 4:
        raise AssetReferenceError("truncated material reference count")
    count = struct.unpack("<I", raw[:4])[0]
    if count > 4096 or len(raw) != 4 + 38 * count:
        raise AssetReferenceError("material reference list length/count mismatch")
    return MaterialReferenceList(tuple(parse_compact_asset_reference(raw[4+i*38:42+i*38]) for i in range(count)), bytes(raw))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_asset_reference(reference: CompactAssetReference, mod_root: str | Path, *, reference_kind: str = "PROP_ASSET_REFERENCE") -> AssetResolution:
    root = Path(mod_root).resolve(strict=True)
    if not root.is_dir():
        raise AssetReferenceError("Mod root is not a directory")
    needle = reference.guid.encode("ascii")
    matches: list[Path] = []
    for meta in root.rglob("*.pmeta"):
        resolved = meta.resolve(strict=True)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise AssetReferenceError("asset metadata escaped Mod root") from exc
        if needle in resolved.read_bytes().lower():
            matches.append(resolved)
    if len(matches) != 1:
        return AssetResolution(reference_kind, reference.guid, "PROP" if reference_kind.startswith("PROP") else "MATERIAL", None, None, False, None, None, None, "UNRESOLVED_BUILTIN" if not matches else "AMBIGUOUS_METADATA", "exact compact envelope; Mod-local metadata scan")
    meta = matches[0]
    candidates = [p for p in (meta.with_suffix(""), meta.with_suffix(".pfab")) if p.is_file()]
    asset = candidates[0].resolve(strict=True) if len(candidates) == 1 else None
    if asset is not None:
        try: asset.relative_to(root)
        except ValueError as exc: raise AssetReferenceError("resolved asset escaped Mod root") from exc
    return AssetResolution(reference_kind, reference.guid, "PROP" if reference_kind.startswith("PROP") else "MATERIAL", str(asset) if asset else None, str(meta), asset is not None, _sha(meta), _sha(asset) if asset else None, asset.relative_to(root).as_posix() if asset else None, "RESOLVED" if asset else "METADATA_ONLY", "exact GUID match in one Mod-local .pmeta")
