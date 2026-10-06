"""Lossless, read-only classification of reference-like Action fields."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .actions import ActionEntry
from .errors import PMHError
from .json_spans import JsonNode
from .models import SourceSpan


MAX_ASSET_METADATA_FILES = 10_000
MAX_ASSET_METADATA_BYTES = 1024 * 1024
MAX_ASSET_METADATA_TOTAL_BYTES = 128 * 1024 * 1024
MAX_ASSET_HASH_BYTES = 64 * 1024 * 1024
MAX_ASSET_HASH_TOTAL_BYTES = 512 * 1024 * 1024


class ActionReferenceError(PMHError, ValueError):
    """Reference inspection cannot safely interpret the requested structure."""


class AssetResolutionError(ActionReferenceError):
    """An asset catalog path violates containment or metadata safety rules."""


@dataclass(frozen=True, slots=True)
class ReferenceFieldSchema:
    namespace: str
    class_name: str
    field_name: str
    kind: str
    observed_shape: str
    evidence_status: str
    semantic_status: str
    writable: bool = False


@dataclass(frozen=True, slots=True)
class AssetRecord:
    guid: str
    name: str | None
    asset_type: str | None
    relative_path: str | None
    metadata_relative_path: str
    sha256: str | None
    metadata: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class AssetResolution:
    resolved: bool
    guid: str | None
    asset_type: str | None = None
    asset_name: str | None = None
    relative_path: str | None = None
    metadata_relative_path: str | None = None
    sha256: str | None = None
    status: str = "UNKNOWN"
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReferenceFingerprint:
    namespace: str | None
    class_name: str | None
    field_name: str
    source_span: SourceSpan
    raw_sha256: str
    normalized_sha256: str


@dataclass(frozen=True, slots=True)
class ActionReferenceItem:
    index: int
    kind: str
    raw_value: Any
    normalized_value: Mapping[str, Any]
    source_span: SourceSpan
    value_spans: Mapping[str, SourceSpan]
    raw_bytes: bytes
    unknown_fields: Mapping[str, Any]
    resolution: AssetResolution | None
    writable: bool = False
    evidence_status: str = "UNKNOWN"
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ActionReference:
    kind: str
    source_field: str
    raw_value: Any
    normalized_value: Any
    source_span: SourceSpan
    value_spans: Mapping[str, SourceSpan]
    raw_bytes: bytes
    items: tuple[ActionReferenceItem, ...]
    fingerprint: ReferenceFingerprint
    writable: bool
    evidence_status: str
    semantic_status: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ActionReferenceInspection:
    action_index: int
    action_rid: int
    namespace: str | None
    class_name: str | None
    assembly: str | None
    coordinate_space: str
    reference_segment_span: SourceSpan | None
    references: tuple[ActionReference, ...]
    warnings: tuple[str, ...] = ()


def _schema(
    class_name: str,
    field_name: str,
    kind: str,
    shape: str,
    evidence: str = "CONFIRMED",
    semantic: str = "UNKNOWN",
) -> ReferenceFieldSchema:
    return ReferenceFieldSchema(
        namespace="ModSystem.Logic",
        class_name=class_name,
        field_name=field_name,
        kind=kind,
        observed_shape=shape,
        evidence_status=evidence,
        semantic_status=semantic,
        writable=False,
    )


REFERENCE_SCHEMAS: Mapping[tuple[str, str, str], ReferenceFieldSchema] = {
    (item.namespace, item.class_name, item.field_name): item
    for item in (
        _schema("SpawnPrefabAction", "m_prefabs", "PrefabReferenceList", "list<object>", semantic="CONFIRMED"),
        _schema(
            "SpawnPrefabAction",
            "m_targets",
            "TransformReferenceList",
            "list<object>",
            semantic="CONFIRMED",
        ),
        _schema("SpawnPrefabAction", "m_targetFlags", "TargetFlags", "number"),
        _schema("SpawnEffectAction", "m_effectType", "EffectIndexReference", "number", semantic="UNKNOWN"),
        _schema("SpawnEffectAction", "m_targets", "TargetSelectorList", "list<unknown>"),
        _schema("SpawnEffectAction", "m_targetFlags", "TargetFlags", "number"),
        _schema("PlaySoundAction", "m_clip", "AudioReference", "object", evidence="INFERRED"),
        _schema("PlaySoundAction", "m_targets", "TargetSelectorList", "list<unknown>"),
        _schema("PlaySoundAction", "m_targetFlags", "TargetFlags", "number"),
        _schema("KillAction", "m_targets", "TargetSelectorList", "list<unknown>"),
        _schema("KillAction", "m_targetFlags", "TargetFlags", "number"),
    )
}


def get_reference_schema(
    namespace: str | None, class_name: str | None, field_name: str
) -> ReferenceFieldSchema | None:
    if namespace is None or class_name is None:
        return None
    return REFERENCE_SCHEMAS.get((namespace, class_name, field_name))


def inspect_action_entry_references(
    action: ActionEntry,
    *,
    asset_catalog: Mapping[str, tuple[AssetRecord, ...]] | None = None,
) -> ActionReferenceInspection:
    """Return full-fidelity reference models for one resolved Action entry."""
    reference = action.resolved_reference
    if action.rid is None or reference is None or reference.json_document is None:
        raise ActionReferenceError(f"Action rid {action.rid!r} has no source-spanned reference JSON")
    if not isinstance(reference.data, Mapping):
        raise ActionReferenceError("Action reference data is not a JSON object")
    namespace = reference.type_info.namespace
    class_name = reference.type_info.class_name
    root = reference.json_document.root
    results: list[ActionReference] = []
    registered_fields: set[str] = set()
    for member in root.members:
        schema = get_reference_schema(namespace, class_name, member.key)
        if schema is None:
            continue
        registered_fields.add(member.key)
        results.append(
            _parse_reference_field(
                namespace,
                class_name,
                member.key,
                member.value,
                reference.raw_segment or b"",
                schema,
                asset_catalog,
            )
        )

    # Preserve structurally reference-like fields introduced by a future class/version.
    for member in root.members:
        if member.key in registered_fields or not _looks_reference_like(
            member.key, member.value.value
        ):
            continue
        unknown_schema = ReferenceFieldSchema(
            namespace=namespace or "",
            class_name=class_name or "",
            field_name=member.key,
            kind="UnknownReference",
            observed_shape=member.value.kind,
            evidence_status="UNKNOWN",
            semantic_status="UNKNOWN",
            writable=False,
        )
        results.append(
            _parse_reference_field(
                namespace,
                class_name,
                member.key,
                member.value,
                reference.raw_segment or b"",
                unknown_schema,
                asset_catalog,
            )
        )
    return ActionReferenceInspection(
        action_index=action.index,
        action_rid=action.rid,
        namespace=namespace,
        class_name=class_name,
        assembly=reference.type_info.assembly,
        coordinate_space="managed-reference JSON segment byte offsets",
        reference_segment_span=reference.source_span,
        references=tuple(results),
    )


def _parse_reference_field(
    namespace: str | None,
    class_name: str | None,
    field_name: str,
    node: JsonNode,
    segment_raw: bytes,
    schema: ReferenceFieldSchema,
    asset_catalog: Mapping[str, tuple[AssetRecord, ...]] | None,
) -> ActionReference:
    raw_bytes = segment_raw[node.span.start : node.span.end]
    warnings: list[str] = []
    items: tuple[ActionReferenceItem, ...] = ()
    normalized: Any
    if schema.kind == "PrefabReferenceList":
        if node.kind != "array":
            warnings.append("expected a JSON array")
            normalized = {"count": 0, "shape": node.kind}
        else:
            items = tuple(
                _parse_prefab_item(item, index, segment_raw, asset_catalog)
                for index, item in enumerate(node.items)
            )
            guid_counts: dict[str, int] = {}
            for item in items:
                guid = item.normalized_value.get("guid")
                if isinstance(guid, str):
                    guid_counts[guid] = guid_counts.get(guid, 0) + 1
            duplicates = [guid for guid, count in guid_counts.items() if count > 1]
            if duplicates:
                warnings.append(
                    f"{len(duplicates)} duplicate prefab GUID value(s) preserved; "
                    f"preview: {duplicates[:10]!r}"
                )
            normalized = {
                "count": len(items),
                "ordered_guids": [item.normalized_value.get("guid") for item in items],
                "duplicate_guids": duplicates,
            }
    elif schema.kind == "AudioReference":
        normalized = _normalize_audio(node.value)
        items = (
            _generic_item(
                node,
                0,
                schema.kind,
                segment_raw,
                normalized,
                _audio_unknown_fields(node.value),
                _resolve_guid(normalized.get("guid"), asset_catalog, "audio"),
                schema.evidence_status,
            ),
        )
    elif schema.kind in {"TargetFlags", "EffectIndexReference"}:
        normalized = {"raw_value": node.value, "semantic": None}
    elif schema.kind == "TransformReferenceList":
        if node.kind == "array":
            items = tuple(
                _parse_transform_reference(item, index, segment_raw)
                for index, item in enumerate(node.items)
            )
            normalized = {
                "count": len(items),
                "ordered_guids": [item.normalized_value.get("guid") for item in items],
                "serialization": "m_assetGUID + m_asset.m_FileID/m_PathID",
            }
        else:
            normalized = {"count": 0, "shape": node.kind}
            warnings.append("expected a JSON array")
    elif schema.kind == "TargetSelectorList":
        if node.kind == "array":
            items = tuple(
                _generic_item(
                    item,
                    index,
                    "UnknownTargetReference",
                    segment_raw,
                    {"raw_value": item.value, "semantic": None},
                    {},
                    None,
                    "UNKNOWN",
                )
                for index, item in enumerate(node.items)
            )
            normalized = {"count": len(items), "semantic": None}
        else:
            normalized = {"raw_value": node.value, "semantic": None}
            warnings.append("expected a JSON array")
    else:
        normalized = {"raw_value": node.value, "semantic": None}
        items = (
            _generic_item(
                node,
                0,
                "UnknownReference",
                segment_raw,
                normalized,
                node.value if isinstance(node.value, Mapping) else {},
                None,
                "UNKNOWN",
            ),
        )
    return ActionReference(
        kind=schema.kind,
        source_field=field_name,
        raw_value=node.value,
        normalized_value=normalized,
        source_span=node.span,
        value_spans=_collect_spans(node),
        raw_bytes=raw_bytes,
        items=items,
        fingerprint=ReferenceFingerprint(
            namespace=namespace,
            class_name=class_name,
            field_name=field_name,
            source_span=node.span,
            raw_sha256=_sha256(raw_bytes),
            normalized_sha256=_normalized_sha256(node.value),
        ),
        writable=False,
        evidence_status=schema.evidence_status,
        semantic_status=schema.semantic_status,
        warnings=tuple(warnings),
    )


def _parse_transform_reference(
    node: JsonNode, index: int, segment_raw: bytes
) -> ActionReferenceItem:
    value = node.value
    normalized = _normalize_scene_component_reference(value)
    unknown = _unknown_object_fields(value, {"m_assetGUID", "m_asset"})
    if isinstance(value, Mapping):
        guid_object = value.get("m_assetGUID")
        asset_object = value.get("m_asset")
        if isinstance(guid_object, Mapping):
            nested = _unknown_object_fields(guid_object, {"serializedGuid"})
            if nested:
                unknown = {**unknown, "m_assetGUID.unknown": nested}
        if isinstance(asset_object, Mapping):
            nested = _unknown_object_fields(asset_object, {"m_FileID", "m_PathID"})
            if nested:
                unknown = {**unknown, "m_asset.unknown": nested}
    warnings = ()
    if normalized["guid"] is None:
        warnings = ("Transform reference has no string GUID",)
    return ActionReferenceItem(
        index=index,
        kind="TransformReference",
        raw_value=value,
        normalized_value=normalized,
        source_span=node.span,
        value_spans=_collect_spans(node),
        raw_bytes=segment_raw[node.span.start : node.span.end],
        unknown_fields=unknown,
        resolution=None,
        writable=False,
        evidence_status="CONFIRMED",
        warnings=warnings,
    )


def _normalize_scene_component_reference(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {
            "guid": None,
            "file_id": None,
            "path_id": None,
            "component_type": "ModTransform",
            "shape": type(value).__name__,
        }
    asset = value.get("m_asset")
    return {
        "guid": _nested_string(value, "m_assetGUID", "serializedGuid"),
        "file_id": asset.get("m_FileID") if isinstance(asset, Mapping) else None,
        "path_id": asset.get("m_PathID") if isinstance(asset, Mapping) else None,
        "component_type": "ModTransform",
        "shape": "scene_component_reference",
    }


def _parse_prefab_item(
    node: JsonNode,
    index: int,
    segment_raw: bytes,
    asset_catalog: Mapping[str, tuple[AssetRecord, ...]] | None,
) -> ActionReferenceItem:
    warnings: list[str] = []
    value = node.value
    if not isinstance(value, Mapping):
        normalized = {"guid": None, "shape": node.kind}
        unknown = {"raw_value": value}
    else:
        guid = _nested_string(value, "m_assetGUID", "serializedGuid")
        asset = value.get("m_asset")
        asset_guid = _nested_string(asset, "guid", "serializedGuid") if isinstance(asset, Mapping) else None
        if guid != asset_guid:
            warnings.append("m_assetGUID and m_asset.guid differ")
        normalized = {
            "guid": guid,
            "asset_guid": asset_guid,
            "name": asset.get("name") if isinstance(asset, Mapping) else None,
            "folder": asset.get("assetFolder") if isinstance(asset, Mapping) else None,
            "tags": asset.get("tags") if isinstance(asset, Mapping) else None,
            "atlas_index": asset.get("atlasIndex") if isinstance(asset, Mapping) else None,
            "atlas_pointer_index": asset.get("atlasPtrIndex") if isinstance(asset, Mapping) else None,
            "asset_location_guid": (
                _nested_string(asset, "assetLocation", "serializedGuid")
                if isinstance(asset, Mapping)
                else None
            ),
            "enabled": asset.get("enabled") if isinstance(asset, Mapping) else None,
            "is_enabled": asset.get("isEnabled") if isinstance(asset, Mapping) else None,
        }
        unknown = _unknown_object_fields(value, {"m_assetGUID", "m_asset"})
        asset_guid_object = value.get("m_assetGUID")
        if isinstance(asset_guid_object, Mapping):
            nested_unknown = _unknown_object_fields(asset_guid_object, {"serializedGuid"})
            if nested_unknown:
                unknown = {**unknown, "m_assetGUID.unknown": nested_unknown}
        if isinstance(asset, Mapping):
            asset_unknown = _unknown_object_fields(
                asset,
                {
                    "enabled",
                    "name",
                    "guid",
                    "tags",
                    "assetTypeString",
                    "atlasIndex",
                    "atlasPtrIndex",
                    "assetLocation",
                    "assetFolder",
                    "isEnabled",
                },
            )
            if asset_unknown:
                unknown = {**unknown, "m_asset.unknown": asset_unknown}
            for nested_name in ("guid", "assetLocation"):
                nested = asset.get(nested_name)
                if isinstance(nested, Mapping):
                    nested_unknown = _unknown_object_fields(nested, {"serializedGuid"})
                    if nested_unknown:
                        unknown = {
                            **unknown,
                            f"m_asset.{nested_name}.unknown": nested_unknown,
                        }
    return ActionReferenceItem(
        index=index,
        kind="PrefabReference",
        raw_value=value,
        normalized_value=normalized,
        source_span=node.span,
        value_spans=_collect_spans(node),
        raw_bytes=segment_raw[node.span.start : node.span.end],
        unknown_fields=unknown,
        resolution=_resolve_guid(normalized.get("guid"), asset_catalog, "prefab"),
        writable=False,
        evidence_status="CONFIRMED",
        warnings=tuple(warnings),
    )


def _generic_item(
    node: JsonNode,
    index: int,
    kind: str,
    segment_raw: bytes,
    normalized: Mapping[str, Any],
    unknown: Mapping[str, Any],
    resolution: AssetResolution | None,
    evidence: str,
) -> ActionReferenceItem:
    return ActionReferenceItem(
        index=index,
        kind=kind,
        raw_value=node.value,
        normalized_value=normalized,
        source_span=node.span,
        value_spans=_collect_spans(node),
        raw_bytes=segment_raw[node.span.start : node.span.end],
        unknown_fields=unknown,
        resolution=resolution,
        writable=False,
        evidence_status=evidence,
    )


def _normalize_audio(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {"guid": None, "file_id": None, "path_id": None, "shape": type(value).__name__}
    asset = value.get("m_asset")
    return {
        "guid": _nested_string(value, "m_assetGUID", "serializedGuid"),
        "file_id": asset.get("m_FileID") if isinstance(asset, Mapping) else None,
        "path_id": asset.get("m_PathID") if isinstance(asset, Mapping) else None,
    }


def _collect_spans(node: JsonNode, prefix: str = "$") -> dict[str, SourceSpan]:
    spans = {prefix: node.span}
    for member in node.members:
        spans.update(_collect_spans(member.value, f"{prefix}.{member.key}"))
    for index, item in enumerate(node.items):
        spans.update(_collect_spans(item, f"{prefix}[{index}]"))
    return spans


def _contains_reference_marker(value: Any) -> bool:
    if isinstance(value, Mapping):
        if any(
            key in value
            for key in (
                "serializedGuid",
                "guid",
                "GUID",
                "m_assetGUID",
                "m_FileID",
                "m_PathID",
                "fileId",
                "pathId",
                "rid",
            )
        ):
            return True
        return any(_contains_reference_marker(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_reference_marker(item) for item in value)
    return False


def _looks_reference_like(field_name: str, value: Any) -> bool:
    folded = field_name.casefold()
    name_marker = (
        folded in {"m_targets", "m_targetflags", "m_clip"}
        or "reference" in folded
        or "prefab" in folded
        or "asset" in folded
        or "guid" in folded
        or folded.endswith("ref")
    )
    return name_marker or _contains_reference_marker(value)


def _nested_string(value: Mapping[str, Any], first: str, second: str) -> str | None:
    nested = value.get(first)
    result = nested.get(second) if isinstance(nested, Mapping) else None
    return result if isinstance(result, str) else None


def _unknown_object_fields(value: Any, known: set[str]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key not in known} if isinstance(value, Mapping) else {}


def _audio_unknown_fields(value: Any) -> dict[str, Any]:
    unknown = _unknown_object_fields(value, {"m_assetGUID", "m_asset"})
    if not isinstance(value, Mapping):
        return unknown
    guid_object = value.get("m_assetGUID")
    if isinstance(guid_object, Mapping):
        nested = _unknown_object_fields(guid_object, {"serializedGuid"})
        if nested:
            unknown = {**unknown, "m_assetGUID.unknown": nested}
    asset_object = value.get("m_asset")
    if isinstance(asset_object, Mapping):
        nested = _unknown_object_fields(asset_object, {"m_FileID", "m_PathID"})
        if nested:
            unknown = {**unknown, "m_asset.unknown": nested}
    return unknown


def find_mod_root(scene_path: str | Path, allowed_root: str | Path) -> Path | None:
    """Find the nearest contained ancestor with an Assets directory."""
    scene = Path(scene_path).resolve(strict=True)
    allowed = Path(allowed_root).resolve(strict=True)
    try:
        scene.relative_to(allowed)
    except ValueError as exc:
        raise AssetResolutionError("scene path is outside the allowed root") from exc
    current = scene.parent
    while True:
        try:
            current.relative_to(allowed)
        except ValueError:
            return None
        assets = (current / "Assets").resolve(strict=False)
        if assets.is_dir():
            try:
                assets.relative_to(current)
                assets.relative_to(allowed)
            except ValueError as exc:
                raise AssetResolutionError("Assets directory escapes the allowed root") from exc
            return current
        if current == allowed:
            return None
        current = current.parent


def build_asset_catalog(
    mod_root: str | Path, allowed_root: str | Path
) -> tuple[dict[str, tuple[AssetRecord, ...]], tuple[str, ...]]:
    """Index contained `.pmeta` files by exact metadata GUID without guessing."""
    root = Path(mod_root).resolve(strict=True)
    allowed = Path(allowed_root).resolve(strict=True)
    try:
        root.relative_to(allowed)
    except ValueError as exc:
        raise AssetResolutionError("Mod root is outside the allowed root") from exc
    assets = (root / "Assets").resolve(strict=True)
    try:
        assets.relative_to(root)
        assets.relative_to(allowed)
    except ValueError as exc:
        raise AssetResolutionError("Assets directory escapes containment") from exc
    by_guid: dict[str, list[AssetRecord]] = {}
    warnings: list[str] = []
    count = 0
    metadata_bytes = 0
    hashed_bytes = 0
    for metadata_path in assets.rglob("*.pmeta"):
        count += 1
        if count > MAX_ASSET_METADATA_FILES:
            warnings.append(f"asset metadata scan stopped at {MAX_ASSET_METADATA_FILES} files")
            break
        try:
            resolved_meta = metadata_path.resolve(strict=True)
        except OSError:
            warnings.append(f"skipped unreadable metadata: {metadata_path.name}")
            continue
        try:
            resolved_meta.relative_to(assets)
            resolved_meta.relative_to(allowed)
        except ValueError:
            warnings.append("skipped metadata path outside containment")
            continue
        try:
            is_file = resolved_meta.is_file()
            metadata_size = resolved_meta.stat().st_size
        except OSError:
            warnings.append(f"skipped unreadable metadata: {resolved_meta.name}")
            continue
        if not is_file or metadata_size > MAX_ASSET_METADATA_BYTES:
            warnings.append(f"skipped oversized/non-file metadata: {resolved_meta.name}")
            continue
        if metadata_bytes + metadata_size > MAX_ASSET_METADATA_TOTAL_BYTES:
            warnings.append(
                f"asset metadata scan stopped at {MAX_ASSET_METADATA_TOTAL_BYTES} bytes"
            )
            break
        metadata_bytes += metadata_size
        try:
            metadata = json.loads(resolved_meta.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            warnings.append(f"skipped malformed metadata: {resolved_meta.name}")
            continue
        if not isinstance(metadata, Mapping):
            continue
        guid = _nested_string(metadata, "guid", "serializedGuid")
        if guid is None:
            continue
        asset_path = Path(str(resolved_meta)[: -len(".pmeta")]).resolve(strict=False)
        relative_asset: str | None = None
        asset_hash: str | None = None
        if asset_path.is_file():
            try:
                asset_path.relative_to(assets)
                asset_path.relative_to(allowed)
            except ValueError:
                warnings.append(f"skipped asset path outside containment: {asset_path.name}")
            else:
                relative_asset = asset_path.relative_to(root).as_posix()
                try:
                    asset_size = asset_path.stat().st_size
                except OSError:
                    warnings.append(f"could not stat asset for hashing: {asset_path.name}")
                else:
                    if (
                        asset_size <= MAX_ASSET_HASH_BYTES
                        and hashed_bytes + asset_size <= MAX_ASSET_HASH_TOTAL_BYTES
                    ):
                        try:
                            asset_hash = _hash_file(asset_path)
                        except OSError:
                            warnings.append(f"could not hash asset: {asset_path.name}")
                        else:
                            hashed_bytes += asset_size
                    else:
                        warnings.append(f"asset hash skipped by byte limit: {asset_path.name}")
        tags = metadata.get("tags")
        asset_type = tags[0] if isinstance(tags, list) and tags and isinstance(tags[0], str) else None
        record = AssetRecord(
            guid=guid,
            name=metadata.get("name") if isinstance(metadata.get("name"), str) else None,
            asset_type=asset_type,
            relative_path=relative_asset,
            metadata_relative_path=resolved_meta.relative_to(root).as_posix(),
            sha256=asset_hash,
            metadata=metadata,
        )
        by_guid.setdefault(guid, []).append(record)
    return {key: tuple(value) for key, value in by_guid.items()}, tuple(warnings)


def _resolve_guid(
    guid: Any,
    catalog: Mapping[str, tuple[AssetRecord, ...]] | None,
    expected_type: str,
) -> AssetResolution:
    if not isinstance(guid, str):
        return AssetResolution(False, None, status="UNKNOWN", warnings=("reference has no string GUID",))
    if catalog is None:
        return AssetResolution(False, guid, status="UNKNOWN", warnings=("asset catalog unavailable",))
    matches = catalog.get(guid, ())
    if not matches:
        return AssetResolution(False, guid, status="CONFIRMED", warnings=("no exact metadata GUID match",))
    if len(matches) != 1:
        return AssetResolution(False, guid, status="CONFIRMED", warnings=("metadata GUID is ambiguous",))
    record = matches[0]
    type_matches = record.asset_type == expected_type or (
        expected_type == "prefab" and record.asset_type == "prefabs"
    )
    warnings = () if type_matches else (f"metadata tag {record.asset_type!r} differs from expected {expected_type!r}",)
    return AssetResolution(
        resolved=record.relative_path is not None and type_matches,
        guid=guid,
        asset_type=record.asset_type,
        asset_name=record.name,
        relative_path=record.relative_path if type_matches else None,
        metadata_relative_path=record.metadata_relative_path,
        sha256=record.sha256 if type_matches else None,
        status="CONFIRMED",
        warnings=warnings,
    )


def _normalized_sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return _sha256(raw)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
