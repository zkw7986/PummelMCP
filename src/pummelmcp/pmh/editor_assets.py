"""Read-only catalog of the built-in assets registered in the mod editor.

Pummel Party's Asset Browser lists ``WorkshopController.Instance.Assets.assets``,
a ``ModAssetList`` ScriptableObject loaded from the Addressables address
``ScriptableObjects/ModAssetList`` (``research/decompiled/FileBrowserWidget.cs``,
``WorkshopController.cs``). The shipped game serializes that object into
``StreamingAssets/aa/StandaloneWindows64/wholegameruntimeassets_assets_scriptableobjects/modassetlist.bundle``,
which this module decodes deterministically.

Two game facts make this catalog exactly the right resolution source for a
``ModProp.prop`` reference:

* ``ModAsset.GetAddressable()`` loads a prop with
  ``Addressables.LoadAssetAsync<Prop>(guid.ToString())`` -- the GUID string *is*
  the address.
* ``SerializableGuid.OnBeforeSerialize()`` stores ``Guid.ToString()``, which is
  the same lowercase hyphenated form found in scene files.

An entry is therefore spawnable only when the game itself registers it as an
``Internal`` ``Prop``. Nothing here reads or writes a mod's own ``.pmeta``
sidecars: those describe ``External`` assets, which are never valid ``Prop``
targets.
"""

from __future__ import annotations

import hashlib
import struct
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from .errors import (
    AmbiguousEditorAssetError,
    DamagedEditorAssetCatalogError,
    EditorAssetCatalogError,
    EditorAssetNotFoundError,
    EditorAssetNotInternalError,
)
from .serialized_file import TypeTreeReader, read_serialized_file
from .unity_bundle import read_unityfs_bundle

#: ``AssetLocationGuid.Internal`` -- the game's marker for a shipped asset.
INTERNAL_ASSET_LOCATION = "4dde710c-fa53-4c44-8bcd-99be6ee28513"
EXTERNAL_ASSET_LOCATION = "757b15db-4305-4406-b767-1ec002daa769"

#: The Asset Browser's internal root folder; entries render as ``Assets/<folder><name>``.
ASSET_BROWSER_ROOT = "Assets"

PROP_ASSET_TYPE = "Prop"

#: Observed ``ModProp.prop`` envelope: a presence byte plus a 36-byte ``str8`` UUID.
PROP_REFERENCE_PREFIX = b"\x01\x24"
PROP_REFERENCE_BYTE_LENGTH = 38
REFERENCE_SCHEMA = "COMPACT_ASSET_REFERENCE_V1"
REFERENCE_CLASSIFICATION = "PROP_ASSET_REFERENCE"
REFERENCE_VERIFICATION = "REGISTERED_INTERNAL_PROP"

REGISTRY_BUNDLE_NAME = "modassetlist.bundle"

MAX_BUNDLE_CANDIDATES = 512
MAX_SCAN_DEPTH = 4
MAX_ENTRIES = 32768
MAX_TAGS = 32
MAX_TAG_BYTES = 128
MAX_NAME_BYTES = 255
MAX_FOLDER_BYTES = 512
MAX_DISAMBIGUATION_CANDIDATES = 8
MAX_WARNINGS = 16

#: Every ``ModAsset`` record must carry exactly these serialized fields.
EXPECTED_MODASSET_FIELDS = frozenset(
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
    }
)
EXPECTED_ROOT_TRAILING_FIELDS = ("iconAtlas", "iconAtlasDirectory", "gridSizes")


def _damaged(detail: str) -> DamagedEditorAssetCatalogError:
    return DamagedEditorAssetCatalogError(f"DAMAGED_EDITOR_ASSET_CATALOG: {detail}")


@dataclass(frozen=True, slots=True)
class VerifiedPropReference:
    """A ``ModProp.prop`` envelope resolved from the scanned game catalog.

    Instances can only be produced by :func:`resolve_editor_asset_entry`, which
    takes a catalog entry the server itself decoded. The constructor re-checks
    the envelope invariants, so a malformed reference cannot reach a writer even
    if it is built by hand.
    """

    guid: str
    payload: bytes
    payload_sha256: str
    asset_id: str
    asset_name: str
    asset_folder: str
    catalog_sha256: str

    def __post_init__(self) -> None:
        parsed = _canonical_guid(self.guid)
        if parsed != self.guid:
            raise _damaged("reference GUID is not a canonical lowercase UUID")
        if len(self.payload) != PROP_REFERENCE_BYTE_LENGTH:
            raise _damaged(
                f"reference payload is {len(self.payload)} bytes, "
                f"expected {PROP_REFERENCE_BYTE_LENGTH}"
            )
        if self.payload != PROP_REFERENCE_PREFIX + self.guid.encode("ascii"):
            raise _damaged("reference payload does not match its GUID")
        if self.payload_sha256 != hashlib.sha256(self.payload).hexdigest():
            raise _damaged("reference payload hash does not match its bytes")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": REFERENCE_SCHEMA,
            "classification": REFERENCE_CLASSIFICATION,
            "field": "ModProp.prop",
            "byte_length": len(self.payload),
            "prefix_hex": PROP_REFERENCE_PREFIX.hex(),
            "verification": REFERENCE_VERIFICATION,
            "asset_location": INTERNAL_ASSET_LOCATION,
            "payload_sha256": self.payload_sha256,
            "source_catalog_sha256": self.catalog_sha256,
        }


@dataclass(frozen=True, slots=True)
class EditorAssetEntry:
    """One registered built-in asset, with its verified prop reference."""

    asset_id: str
    name: str
    asset_folder: str
    relative_path: str
    asset_type: str
    tags: tuple[str, ...]
    enabled: bool
    guid: str
    asset_location: str
    atlas_index: int
    atlas_ptr_index: int

    @property
    def spawnable(self) -> bool:
        """Whether this entry can back a new ``ModProp`` component."""
        return (
            self.enabled
            and self.asset_type == PROP_ASSET_TYPE
            and self.asset_location == INTERNAL_ASSET_LOCATION
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "asset_id": self.asset_id,
            "name": self.name,
            "asset_folder": self.asset_folder,
            "relative_path": self.relative_path,
            "asset_type": self.asset_type,
            "tags": list(self.tags),
            "enabled": self.enabled,
            "guid": self.guid,
            "asset_location": self.asset_location,
            "spawnable": self.spawnable,
        }


@dataclass(frozen=True, slots=True)
class EditorAssetCatalog:
    source_relative_path: str
    bundle_sha256: str
    entries: tuple[EditorAssetEntry, ...]
    warnings: tuple[str, ...]
    skipped_count: int
    collision_count: int = 0
    folder_counts: Mapping[str, int] = field(default_factory=dict)

    def summary(self) -> dict[str, object]:
        by_type = Counter(item.asset_type for item in self.entries)
        by_location = Counter(item.asset_location for item in self.entries)
        return {
            "scope": {
                "library": "Asset Browser built-in assets",
                "source": "ModAssetList (Addressables: ScriptableObjects/ModAssetList)",
                "mod_local_assets_included": False,
            },
            "source_relative_path": self.source_relative_path,
            "source_sha256": self.bundle_sha256,
            "asset_count": len(self.entries),
            "prop_count": sum(1 for item in self.entries if item.asset_type == PROP_ASSET_TYPE),
            "spawnable_count": sum(1 for item in self.entries if item.spawnable),
            "unique_guid_count": len({item.guid for item in self.entries}),
            "folder_count": len(self.folder_counts),
            "by_asset_type": dict(sorted(by_type.items())),
            "by_asset_location": dict(sorted(by_location.items())),
            "internal_asset_location": INTERNAL_ASSET_LOCATION,
            "skipped_record_count": self.skipped_count,
            "ambiguous_id_count": self.collision_count,
            "warnings": list(self.warnings),
            "writable": False,
        }


def _canonical_guid(value: object) -> str | None:
    try:
        parsed = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None
    text = str(parsed)
    if text != text.lower() or len(text) != 36:
        return None
    return text


def normalize_folder(value: str | None) -> str:
    """Normalize an ``assetFolder`` into ``""`` or ``"A/B/"`` form."""
    if not value:
        return ""
    parts = [part for part in value.replace("\\", "/").split("/") if part]
    if not parts:
        return ""
    for part in parts:
        if part in (".", ".."):
            raise EditorAssetCatalogError(
                "INVALID_EDITOR_ASSET_FOLDER: folder contains a relative segment"
            )
        if len(part.encode("utf-8")) > MAX_FOLDER_BYTES:
            raise EditorAssetCatalogError(
                "INVALID_EDITOR_ASSET_FOLDER: folder segment is too long"
            )
    return "/".join(parts) + "/"


def _as_text(value: object, *, what: str) -> str:
    if not isinstance(value, str):
        raise _damaged(f"{what} is not a string")
    return value


def _as_guid(value: object, *, what: str) -> str:
    if not isinstance(value, Mapping):
        raise _damaged(f"{what} is not a serializable GUID record")
    canonical = _canonical_guid(value.get("serializedGuid"))
    if canonical is None:
        raise _damaged(f"{what} is not a canonical lowercase UUID")
    return canonical


def _enabled_flag(raw: object) -> bool:
    if not isinstance(raw, bytes) or len(raw) != 1:
        raise _damaged("ModAsset.enabled is not a single boolean byte")
    return raw[0] != 0


def _int32(raw: object, *, what: str) -> int:
    if not isinstance(raw, bytes) or len(raw) != 4:
        raise _damaged(f"{what} is not a four-byte integer")
    return struct.unpack("<i", raw)[0]


def _locate_registry_bundle(root: Path) -> Path:
    directory = root / "aa"
    if not directory.is_dir():
        raise EditorAssetCatalogError(
            "EDITOR_ASSET_ROOT_MISSING_AA: the configured root has no 'aa' build directory"
        )
    matches: list[Path] = []
    examined = 0
    for depth in range(1, MAX_SCAN_DEPTH + 1):
        pattern = "/".join(["*"] * depth) + f"/{REGISTRY_BUNDLE_NAME}"
        for candidate in directory.glob(pattern):
            if not candidate.is_file():
                continue
            examined += 1
            if examined > MAX_BUNDLE_CANDIDATES:
                break
            if candidate.name.casefold() == REGISTRY_BUNDLE_NAME:
                resolved = candidate.resolve()
                try:
                    resolved.relative_to(root)
                except ValueError as exc:
                    raise EditorAssetCatalogError(
                        "EDITOR_ASSET_CATALOG_ESCAPES_ROOT"
                    ) from exc
                matches.append(resolved)
        if examined > MAX_BUNDLE_CANDIDATES:
            break
    unique = sorted(set(matches))
    if not unique:
        raise EditorAssetCatalogError(
            "EDITOR_ASSET_CATALOG_NOT_FOUND: no "
            f"{REGISTRY_BUNDLE_NAME} below the configured root"
        )
    if len(unique) > 1:
        raise EditorAssetCatalogError(
            "AMBIGUOUS_EDITOR_ASSET_CATALOG: "
            f"{len(unique)} registry bundles satisfy the scan"
        )
    return unique[0]


def _read_modasset_records(asset_root: Path) -> tuple[Path, bytes, list[Mapping[str, object]]]:
    bundle_path = _locate_registry_bundle(asset_root)
    bundle_sha256 = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
    try:
        container = read_unityfs_bundle(bundle_path)
    except EditorAssetCatalogError as exc:
        raise _damaged(str(exc)) from exc

    nodes = container.serialized_file_nodes()
    if len(nodes) != 1:
        raise _damaged(
            f"registry bundle carries {len(nodes)} serialized files, expected one"
        )
    try:
        serialized = read_serialized_file(container.node_bytes(nodes[0]))
    except EditorAssetCatalogError as exc:
        raise _damaged(str(exc)) from exc

    candidates = [
        item
        for item in serialized.objects
        if serialized.type_of(item).class_id == 114
    ]
    if len(candidates) != 1:
        raise _damaged(
            f"registry bundle carries {len(candidates)} MonoBehaviour objects, expected one"
        )
    item = candidates[0]
    root = serialized.type_of(item).root
    if root is None or not root.children:
        raise _damaged("the registry MonoBehaviour carries no type tree")

    reader = TypeTreeReader(serialized.object_bytes(item))
    values = reader.read_object(root)
    if reader.consumed != item.byte_size:
        raise _damaged(
            "the registry object did not decode to its declared size "
            f"({reader.consumed} of {item.byte_size} bytes)"
        )

    for name in EXPECTED_ROOT_TRAILING_FIELDS:
        if name not in values:
            raise _damaged(f"the registry object is missing its {name!r} field")
    positional_name = values.get("#3")
    if isinstance(positional_name, str) and positional_name != "ModAssetList":
        raise _damaged(
            f"the registry object is named {positional_name!r}, expected 'ModAssetList'"
        )
    records = values.get("assets")
    if not isinstance(records, list):
        raise _damaged("the registry object exposes no ModAsset.assets list")
    if len(records) > MAX_ENTRIES:
        raise _damaged(f"the registry declares {len(records)} assets, above the limit")
    for record in records:
        if not isinstance(record, Mapping):
            raise _damaged("a ModAsset record is not a composite value")
        if frozenset(record) != EXPECTED_MODASSET_FIELDS:
            missing = sorted(EXPECTED_MODASSET_FIELDS - frozenset(record))
            extra = sorted(frozenset(record) - EXPECTED_MODASSET_FIELDS)
            raise _damaged(
                "a ModAsset record does not have the expected field set "
                f"(missing={missing}, unexpected={extra})"
            )
    relative_path = bundle_path.relative_to(asset_root).as_posix()
    return bundle_path, bundle_sha256, records


def load_editor_asset_catalog(asset_root: str | Path) -> EditorAssetCatalog:
    """Decode the Asset Browser registry below ``asset_root``."""
    root = Path(asset_root).expanduser().resolve()
    if not root.is_dir():
        raise EditorAssetCatalogError(
            f"EDITOR_ASSET_ROOT_NOT_A_DIRECTORY: {root}"
        )
    bundle_path, bundle_sha256, records = _read_modasset_records(root)
    entries: list[EditorAssetEntry] = []
    warnings: list[str] = []
    skipped = 0
    collisions = 0
    seen: dict[str, str] = {}
    for index, record in enumerate(records):
        try:
            name = _as_text(record["name"], what="ModAsset.name")
            asset_type = _as_text(record["assetTypeString"], what="assetTypeString")
            guid = _as_guid(record["guid"], what="ModAsset.guid")
            asset_location = _as_guid(record["assetLocation"], what="ModAsset.assetLocation")
            folder = normalize_folder(_as_text(record["assetFolder"], what="assetFolder"))
            tags_raw = record["tags"]
            if not isinstance(tags_raw, list):
                raise _damaged("ModAsset.tags is not a list")
            enabled = _enabled_flag(record["enabled"])
            atlas_index = _int32(record["atlasIndex"], what="atlasIndex")
            atlas_ptr_index = _int32(record["atlasPtrIndex"], what="atlasPtrIndex")
        except (EditorAssetCatalogError, KeyError, TypeError):
            skipped += 1
            if skipped <= 5:
                warnings.append(f"record {index}: skipped an undecodable ModAsset")
            continue

        if not name or len(name.encode("utf-8")) > MAX_NAME_BYTES:
            skipped += 1
            if skipped <= 5:
                warnings.append(f"record {index}: skipped an empty or oversized name")
            continue
        tags = tuple(
            item[:MAX_TAG_BYTES] for item in tags_raw[:MAX_TAGS] if isinstance(item, str)
        )
        asset_id = f"{ASSET_BROWSER_ROOT}/{folder}{name}"
        previous = seen.get(asset_id)
        if previous is not None:
            if previous == guid:
                # The registry legitimately lists a few GUIDs twice; collapse them.
                continue
            collisions += 1
            if len(warnings) < MAX_WARNINGS:
                warnings.append(
                    f"record {index}: {asset_id!r} is registered more than once with "
                    "different GUIDs; requests for it are rejected as ambiguous"
                )
        else:
            seen[asset_id] = guid
        entries.append(
            EditorAssetEntry(
                asset_id=asset_id,
                name=name,
                asset_folder=folder,
                relative_path=f"{folder}{name}",
                asset_type=asset_type,
                tags=tags,
                enabled=enabled,
                guid=guid,
                asset_location=asset_location,
                atlas_index=atlas_index,
                atlas_ptr_index=atlas_ptr_index,
            )
        )

    if not entries:
        raise _damaged("the registry decoded to zero usable assets")
    entries.sort(key=lambda item: (item.relative_path.casefold(), item.guid))
    folder_counts = Counter(item.asset_folder for item in entries)
    return EditorAssetCatalog(
        source_relative_path=bundle_path.relative_to(root).as_posix(),
        bundle_sha256=bundle_sha256,
        entries=tuple(entries),
        warnings=tuple(warnings),
        skipped_count=skipped,
        collision_count=collisions,
        folder_counts=dict(sorted(folder_counts.items())),
    )


def _reject_invalid_identifier(value: str) -> None:
    if not value or not value.strip():
        raise EditorAssetNotFoundError("EDITOR_ASSET_IDENTIFIER_EMPTY")
    if "\x00" in value:
        raise EditorAssetNotFoundError("EDITOR_ASSET_IDENTIFIER_INVALID")
    if value.startswith("/") or value.startswith("\\"):
        raise EditorAssetNotFoundError(
            "EDITOR_ASSET_IDENTIFIER_INVALID: absolute paths are not accepted"
        )
    if len(value) >= 2 and value[1] == ":":
        raise EditorAssetNotFoundError(
            "EDITOR_ASSET_IDENTIFIER_INVALID: drive-qualified paths are not accepted"
        )
    if "\\" in value:
        raise EditorAssetNotFoundError(
            "EDITOR_ASSET_IDENTIFIER_INVALID: use '/' as the path separator"
        )
    if any(part in (".", "..") for part in value.split("/")):
        raise EditorAssetNotFoundError(
            "EDITOR_ASSET_IDENTIFIER_INVALID: relative path segments are not accepted"
        )


def split_editor_asset_identifier(value: str) -> tuple[str | None, str]:
    """Split ``Assets/<folder>/<name>``, ``<folder>/<name>`` or ``<name>``."""
    _reject_invalid_identifier(value)
    text = value.strip()
    head, separator, tail = text.partition("/")
    if separator and head.casefold() == ASSET_BROWSER_ROOT.casefold():
        text = tail
    if not text:
        raise EditorAssetNotFoundError("EDITOR_ASSET_IDENTIFIER_EMPTY")
    folder_part, separator, name = text.rpartition("/")
    if not separator:
        return None, text
    if not name:
        raise EditorAssetNotFoundError(
            "EDITOR_ASSET_IDENTIFIER_INVALID: the identifier ends with a separator"
        )
    return normalize_folder(folder_part), name


def resolve_editor_asset_entries(
    catalog: EditorAssetCatalog,
    identifier: str,
    *,
    folder: str | None = None,
) -> tuple[EditorAssetEntry, ...]:
    """Return every registered asset the identifier resolves to, in stable order.

    A single identifier can legitimately match more than one registration: the
    shipped registry lists a few folder/name pairs twice under different GUIDs.
    Returning all of them lets a read-only caller see the conflict, while
    :func:`resolve_editor_asset_entry` refuses to pick one for a writer.
    """
    if not isinstance(identifier, str):
        raise EditorAssetNotFoundError("EDITOR_ASSET_IDENTIFIER_INVALID")
    folder_part, name = split_editor_asset_identifier(identifier)
    if folder is not None:
        folder_part = normalize_folder(folder)
    folded_name = name.casefold()

    matches = [
        item
        for item in catalog.entries
        if item.name.casefold() == folded_name
        and (folder_part is None or item.asset_folder.casefold() == folder_part.casefold())
    ]
    if not matches:
        scope = "" if folder_part is None else f" in folder {folder_part!r}"
        raise EditorAssetNotFoundError(
            f"EDITOR_ASSET_NOT_REGISTERED: no built-in asset named {name!r}{scope}"
        )
    return tuple(sorted(matches, key=lambda item: (item.asset_id.casefold(), item.guid)))


def resolve_editor_asset_entry(
    catalog: EditorAssetCatalog,
    identifier: str,
    *,
    folder: str | None = None,
    require_spawnable: bool = False,
) -> EditorAssetEntry:
    """Resolve one identifier to exactly one entry, or reject it."""
    unique = resolve_editor_asset_entries(catalog, identifier, folder=folder)
    if len(unique) > 1:
        candidates = [
            item.asset_id for item in unique[:MAX_DISAMBIGUATION_CANDIDATES]
        ]
        raise AmbiguousEditorAssetError(
            f"AMBIGUOUS_EDITOR_ASSET: {identifier!r} matches {len(unique)} registered "
            f"assets; qualify it with a folder, for example {candidates}"
        )

    entry = unique[0]
    if require_spawnable and not entry.spawnable:
        if entry.asset_location != INTERNAL_ASSET_LOCATION:
            raise EditorAssetNotInternalError(
                "EDITOR_ASSET_NOT_INTERNAL: "
                f"{entry.asset_id!r} is registered at asset location "
                f"{entry.asset_location}, not the built-in library"
            )
        if entry.asset_type != PROP_ASSET_TYPE:
            raise EditorAssetNotInternalError(
                "EDITOR_ASSET_NOT_A_PROP: "
                f"{entry.asset_id!r} is registered as {entry.asset_type!r}; only "
                "Prop assets can back a ModProp reference"
            )
        raise EditorAssetNotInternalError(
            f"EDITOR_ASSET_DISABLED: {entry.asset_id!r} is registered but disabled"
        )
    return entry


def build_verified_prop_reference(
    catalog: EditorAssetCatalog, entry: EditorAssetEntry
) -> VerifiedPropReference:
    """Build the only reference an ``ModProp.prop`` field may be given."""
    if entry.asset_location != INTERNAL_ASSET_LOCATION:
        raise EditorAssetNotInternalError(
            "EDITOR_ASSET_NOT_INTERNAL: refusing to build a reference to a "
            "non-internal asset"
        )
    payload = PROP_REFERENCE_PREFIX + entry.guid.encode("ascii")
    return VerifiedPropReference(
        guid=entry.guid,
        payload=payload,
        payload_sha256=hashlib.sha256(payload).hexdigest(),
        asset_id=entry.asset_id,
        asset_name=entry.name,
        asset_folder=entry.asset_folder,
        catalog_sha256=catalog.bundle_sha256,
    )


def search_editor_assets(
    catalog: EditorAssetCatalog,
    *,
    folder: str | None = None,
    folder_prefix: str | None = None,
    query: str | None = None,
    tag: str | None = None,
    asset_type: str | None = PROP_ASSET_TYPE,
    enabled_only: bool = True,
    offset: int = 0,
    limit: int = 50,
) -> dict[str, object]:
    if offset < 0:
        raise EditorAssetCatalogError("offset must be zero or greater")
    if not 1 <= limit <= 200:
        raise EditorAssetCatalogError("limit must be between 1 and 200")

    exact_folder = normalize_folder(folder) if folder is not None else None
    prefix = normalize_folder(folder_prefix) if folder_prefix is not None else None
    folded_query = query.casefold() if query else None
    folded_tag = tag.casefold() if tag else None
    folded_type = asset_type.casefold() if asset_type else None

    def matches(item: EditorAssetEntry) -> bool:
        if enabled_only and not item.enabled:
            return False
        if exact_folder is not None and item.asset_folder.casefold() != exact_folder.casefold():
            return False
        if prefix is not None and not item.asset_folder.casefold().startswith(
            prefix.casefold()
        ):
            return False
        if folded_type is not None and item.asset_type.casefold() != folded_type:
            return False
        if folded_tag is not None and not any(
            folded_tag == value.casefold() for value in item.tags
        ):
            return False
        if folded_query is None:
            return True
        searchable = (item.asset_id, item.name, item.guid, *item.tags)
        return any(folded_query in value.casefold() for value in searchable)

    filtered = [item for item in catalog.entries if matches(item)]
    selected = filtered[offset : offset + limit]
    folders = Counter(item.asset_folder for item in filtered)
    return {
        "catalog": catalog.summary(),
        "total": len(filtered),
        "offset": offset,
        "limit": limit,
        "returned": len(selected),
        "has_more": offset + len(selected) < len(filtered),
        "folders": [
            {"folder": name or ASSET_BROWSER_ROOT, "count": count}
            for name, count in sorted(folders.items())[:256]
        ],
        "assets": [item.to_dict() for item in selected],
        "warnings": list(catalog.warnings),
        "writable": False,
    }


def describe_editor_asset(
    catalog: EditorAssetCatalog, identifier: str, *, folder: str | None = None
) -> dict[str, object]:
    """Describe every registration an identifier resolves to."""
    matches = resolve_editor_asset_entries(catalog, identifier, folder=folder)
    described = []
    for entry in matches:
        reference = (
            build_verified_prop_reference(catalog, entry) if entry.spawnable else None
        )
        described.append(
            {
                **entry.to_dict(),
                "prop_reference": reference.to_dict() if reference is not None else None,
            }
        )
    return {
        "asset_id": matches[0].asset_id,
        "match_count": len(matches),
        "ambiguous": len(matches) > 1,
        "resolvable_for_spawn": len(matches) == 1 and matches[0].spawnable,
        "assets": described,
        "catalog_source_relative_path": catalog.source_relative_path,
        "catalog_sha256": catalog.bundle_sha256,
        "warnings": list(catalog.warnings),
        "writable": False,
    }
