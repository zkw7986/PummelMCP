"""Read-only catalog over assets shipped with built-in minigames."""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from .action_references import build_asset_catalog
from .errors import PMHError


MAX_BUILTIN_ASSET_SOURCES = 512
MAX_BUILTIN_ASSET_RECORDS = 10_000


class BuiltinAssetCatalogError(PMHError, ValueError):
    """The configured built-in asset root or a catalog request is invalid."""


@dataclass(frozen=True, slots=True)
class BuiltinAssetEntry:
    guid: str
    name: str | None
    asset_type: str
    tags: tuple[str, ...]
    asset_folder: str | None
    source: str
    relative_path: str | None
    metadata_relative_path: str
    sha256: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class BuiltinAssetCatalog:
    entries: tuple[BuiltinAssetEntry, ...]
    sources: tuple[str, ...]
    warnings: tuple[str, ...]

    def summary(self) -> dict[str, object]:
        by_type = Counter(item.asset_type for item in self.entries)
        by_source = Counter(item.source for item in self.entries)
        by_guid = Counter(item.guid for item in self.entries)
        return {
            "scope": {
                "inbuilt_mods": True,
                "workshop_templates": "Minigames",
                "boards_included": False,
            },
            "source_count": len(self.sources),
            "sources": list(self.sources),
            "asset_count": len(self.entries),
            "unique_guid_count": len(by_guid),
            "duplicate_guid_count": sum(1 for count in by_guid.values() if count > 1),
            "by_type": dict(sorted(by_type.items())),
            "by_source": dict(sorted(by_source.items())),
            "warnings": list(self.warnings),
            "writable": False,
        }


def load_builtin_asset_catalog(asset_root: str | Path) -> BuiltinAssetCatalog:
    """Load the contained InbuiltMods and Minigames template asset metadata."""
    root = Path(asset_root).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise BuiltinAssetCatalogError("built-in asset root is not a directory")

    source_roots = _discover_source_roots(root)
    entries: list[BuiltinAssetEntry] = []
    warnings: list[str] = []
    sources: list[str] = []
    for source_root, source in source_roots:
        sources.append(source)
        catalog, scan_warnings = build_asset_catalog(source_root, root)
        warnings.extend(f"{source}: {warning}" for warning in scan_warnings)
        for records in catalog.values():
            for record in records:
                if len(entries) >= MAX_BUILTIN_ASSET_RECORDS:
                    raise BuiltinAssetCatalogError(
                        f"built-in asset catalog exceeds {MAX_BUILTIN_ASSET_RECORDS} records"
                    )
                try:
                    guid = str(uuid.UUID(record.guid))
                except (ValueError, AttributeError, TypeError):
                    warnings.append(f"{source}: skipped metadata with invalid GUID")
                    continue
                tags_raw = record.metadata.get("tags")
                tags = (
                    tuple(item[:128] for item in tags_raw[:32] if isinstance(item, str))
                    if isinstance(tags_raw, list)
                    else ()
                )
                folder_raw = record.metadata.get("assetFolder")
                asset_folder = folder_raw[:512] if isinstance(folder_raw, str) else None
                name = record.name[:512] if record.name is not None else None
                relative_path = (
                    f"{source}/{record.relative_path}"
                    if record.relative_path is not None
                    else None
                )
                metadata_relative_path = (
                    f"{source}/{record.metadata_relative_path}"
                )
                entries.append(
                    BuiltinAssetEntry(
                        guid=guid,
                        name=name,
                        asset_type=_asset_type(record.metadata_relative_path),
                        tags=tags,
                        asset_folder=asset_folder,
                        source=source,
                        relative_path=relative_path,
                        metadata_relative_path=metadata_relative_path,
                        sha256=record.sha256,
                    )
                )

    entries.sort(
        key=lambda item: (
            (item.name or "").casefold(),
            item.source.casefold(),
            item.guid,
            item.metadata_relative_path.casefold(),
        )
    )
    return BuiltinAssetCatalog(tuple(entries), tuple(sources), tuple(warnings))


def search_builtin_asset_catalog(
    catalog: BuiltinAssetCatalog,
    *,
    query: str | None = None,
    asset_type: str | None = None,
    source: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> dict[str, object]:
    if offset < 0:
        raise BuiltinAssetCatalogError("offset must be zero or greater")
    if not 1 <= limit <= 100:
        raise BuiltinAssetCatalogError("limit must be between 1 and 100")
    folded_query = query.casefold() if query else None
    folded_type = asset_type.casefold() if asset_type else None
    folded_source = source.casefold() if source else None

    def matches(item: BuiltinAssetEntry) -> bool:
        if folded_type is not None and item.asset_type.casefold() != folded_type:
            return False
        if folded_source is not None and item.source.casefold() != folded_source:
            return False
        if folded_query is None:
            return True
        searchable = (
            item.guid,
            item.name or "",
            item.relative_path or "",
            item.metadata_relative_path,
            *item.tags,
        )
        return any(folded_query in value.casefold() for value in searchable)

    filtered = [item for item in catalog.entries if matches(item)]
    selected = filtered[offset : offset + limit]
    return {
        "total": len(filtered),
        "offset": offset,
        "limit": limit,
        "assets": [item.to_dict() for item in selected],
        "warnings": list(catalog.warnings),
        "writable": False,
    }


def get_builtin_asset_by_guid(
    catalog: BuiltinAssetCatalog, guid: str
) -> dict[str, object]:
    try:
        canonical = str(uuid.UUID(guid))
    except (ValueError, AttributeError, TypeError) as exc:
        raise BuiltinAssetCatalogError("guid must be a valid UUID") from exc
    matches = [item for item in catalog.entries if item.guid == canonical]
    if not matches:
        raise BuiltinAssetCatalogError(f"built-in asset GUID was not found: {canonical}")
    return {
        "guid": canonical,
        "match_count": len(matches),
        "ambiguous": len(matches) > 1,
        "assets": [item.to_dict() for item in matches],
        "warnings": list(catalog.warnings),
        "writable": False,
    }


def _discover_source_roots(root: Path) -> list[tuple[Path, str]]:
    groups = (
        (root / "InbuiltMods", "InbuiltMods"),
        (root / "WorkshopTemplates" / "Minigames", "WorkshopTemplates/Minigames"),
    )
    if not any(directory.is_dir() for directory, _ in groups):
        raise BuiltinAssetCatalogError(
            "built-in asset root must contain InbuiltMods or WorkshopTemplates/Minigames"
        )
    sources: list[tuple[Path, str]] = []
    for directory, label in groups:
        if not directory.is_dir():
            continue
        resolved_directory = directory.resolve(strict=True)
        try:
            resolved_directory.relative_to(root)
        except ValueError as exc:
            raise BuiltinAssetCatalogError(
                f"built-in asset group escapes configured root: {label}"
            ) from exc
        for child in sorted(directory.iterdir(), key=lambda item: item.name.casefold()):
            if not child.is_dir() or not (child / "Assets").is_dir():
                continue
            resolved_child = child.resolve(strict=True)
            try:
                resolved_child.relative_to(root)
            except ValueError as exc:
                raise BuiltinAssetCatalogError(
                    f"built-in asset source escapes configured root: {child.name}"
                ) from exc
            sources.append((resolved_child, f"{label}/{child.name}"))
            if len(sources) > MAX_BUILTIN_ASSET_SOURCES:
                raise BuiltinAssetCatalogError(
                    f"built-in asset catalog exceeds {MAX_BUILTIN_ASSET_SOURCES} sources"
                )
    return sources


def _asset_type(metadata_relative_path: str) -> str:
    asset_path = Path(metadata_relative_path[: -len(".pmeta")])
    suffix = asset_path.suffix.casefold()
    return {
        ".pfab": "prefab",
        ".pmat": "material",
        ".jpg": "image",
        ".jpeg": "image",
        ".png": "image",
    }.get(suffix, suffix.lstrip(".") or "unknown")
