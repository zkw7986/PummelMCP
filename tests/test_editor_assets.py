"""Tests for the built-in Asset Browser catalog and its reference resolver.

The synthetic catalog cases pin the resolution contract (ambiguity, cross-library,
disabled, and malformed identifiers) without touching the shipped game data. The
real-data cases pin the decoded registry against GUIDs that are independently
verifiable from the shipped scenes themselves.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from pummelmcp.pmh.editor_assets import (
    ASSET_BROWSER_ROOT,
    INTERNAL_ASSET_LOCATION,
    PROP_ASSET_TYPE,
    PROP_REFERENCE_BYTE_LENGTH,
    PROP_REFERENCE_PREFIX,
    EditorAssetCatalog,
    EditorAssetEntry,
    VerifiedPropReference,
    build_verified_prop_reference,
    describe_editor_asset,
    resolve_editor_asset_entries,
    resolve_editor_asset_entry,
    search_editor_assets,
)
from pummelmcp.pmh.errors import (
    AmbiguousEditorAssetError,
    DamagedEditorAssetCatalogError,
    EditorAssetNotFoundError,
    EditorAssetNotInternalError,
)

#: GUIDs recovered independently from the shipped scenes and the mod-editor scene
#: bundle; see docs/EDITOR_ASSET_TOOLS.md for the derivation.
GROUND_TRUTH = {
    "LowPolyCube": ("cfc9faf9-3852-4dbe-803b-32cec4c94139", "Primitives/"),
    "LowPolySphere": ("c7d7ef4d-4e66-4b08-b91a-d5b0190b74ee", "Primitives/"),
    "LowPolyPlane": ("e314a70b-98f2-4459-9214-ef4da97b4e6d", "Primitives/"),
    "Plane": ("331019a7-e85f-47d4-a19d-703dc99a3fab", "Primitives/"),
}

VIKING_ENVIRONMENT = (
    "Tree Pine 01",
    "Tree Pine 02",
    "Tree Pine 03",
    "Tree Pine 04",
    "Tree Pine Large 01",
    "Glacier 01",
    "Glacier 02",
    "Glacier 03",
    "Glacier Arch 01",
    "IceChunk 01",
    "IceChunk 06",
    "Iceberg 01",
    "Rock 01",
    "Rock 07",
    "Stone 01",
    "Mountains 01",
    "Grass 01",
    "Grass Large 01",
    "GrassPatch 01",
    "SnowPile 01",
    "SnowPile 02",
)


def _entry(
    name: str,
    guid: str,
    *,
    folder: str = "Test/Props/",
    asset_type: str = PROP_ASSET_TYPE,
    enabled: bool = True,
    asset_location: str = INTERNAL_ASSET_LOCATION,
) -> EditorAssetEntry:
    return EditorAssetEntry(
        asset_id=f"{ASSET_BROWSER_ROOT}/{folder}{name}",
        name=name,
        asset_folder=folder,
        relative_path=f"{folder}{name}",
        asset_type=asset_type,
        tags=(),
        enabled=enabled,
        guid=guid,
        asset_location=asset_location,
        atlas_index=0,
        atlas_ptr_index=0,
    )


def _catalog(*entries: EditorAssetEntry) -> EditorAssetCatalog:
    return EditorAssetCatalog(
        source_relative_path="aa/synthetic/modassetlist.bundle",
        bundle_sha256="b" * 64,
        entries=tuple(entries),
        warnings=(),
        skipped_count=0,
        folder_counts={},
    )


def _guid(seed: int) -> str:
    return f"{seed:08x}-1111-4111-8111-111111111111"


# --------------------------------------------------------------------------- #
# Synthetic resolution contract
# --------------------------------------------------------------------------- #


def test_resolver_accepts_a_folder_qualified_relative_path() -> None:
    catalog = _catalog(_entry("Rock 01", _guid(1)))
    entry = resolve_editor_asset_entry(catalog, "Test/Props/Rock 01")
    assert entry.asset_id == "Assets/Test/Props/Rock 01"


def test_resolver_accepts_the_asset_browser_assets_prefix() -> None:
    catalog = _catalog(_entry("Rock 01", _guid(1)))
    assert (
        resolve_editor_asset_entry(catalog, "Assets/Test/Props/Rock 01").asset_id
        == "Assets/Test/Props/Rock 01"
    )


def test_resolver_rejects_a_bare_name_that_matches_several_assets() -> None:
    catalog = _catalog(
        _entry("Rock 01", _guid(1), folder="Viking/Environment/"),
        _entry("Rock 01", _guid(2), folder="Nature/Environment/Rocks/"),
    )
    with pytest.raises(AmbiguousEditorAssetError, match="matches 2 registered assets"):
        resolve_editor_asset_entry(catalog, "Rock 01", require_spawnable=True)
    # Qualifying by folder disambiguates without guessing.
    assert (
        resolve_editor_asset_entry(
            catalog, "Rock 01", folder="Viking/Environment"
        ).guid
        == _guid(1)
    )


def test_resolver_returns_every_registration_for_inspection() -> None:
    catalog = _catalog(
        _entry("Rock 01", _guid(1), folder="Viking/Environment/"),
        _entry("Rock 01", _guid(2), folder="Nature/Environment/Rocks/"),
    )
    matches = resolve_editor_asset_entries(catalog, "Rock 01")
    assert [item.guid for item in matches] == [_guid(2), _guid(1)]


def test_resolver_rejects_an_unregistered_name() -> None:
    catalog = _catalog(_entry("Rock 01", _guid(1)))
    with pytest.raises(EditorAssetNotFoundError, match="EDITOR_ASSET_NOT_REGISTERED"):
        resolve_editor_asset_entry(catalog, "NoSuchAsset")


@pytest.mark.parametrize(
    "identifier",
    [
        "/absolute/path",
        "C:/windows/style",
        "Viking/../../etc/passwd",
        "Viking/./Environment",
        "Viking\\Environment\\Rock 01",
        "",
        "   ",
    ],
)
def test_resolver_rejects_uncontrolled_identifiers(identifier: str) -> None:
    catalog = _catalog(_entry("Rock 01", _guid(1)))
    with pytest.raises(EditorAssetNotFoundError, match="EDITOR_ASSET_IDENTIFIER"):
        resolve_editor_asset_entry(catalog, identifier)


def test_resolver_rejects_a_non_prop_asset_for_spawning() -> None:
    catalog = _catalog(
        _entry("Standard Shader", _guid(3), folder="Shaders/", asset_type="UnityEngine.Shader")
    )
    with pytest.raises(EditorAssetNotInternalError, match="EDITOR_ASSET_NOT_A_PROP"):
        resolve_editor_asset_entry(catalog, "Shaders/Standard Shader", require_spawnable=True)
    # A read-only lookup still sees it.
    assert resolve_editor_asset_entry(catalog, "Shaders/Standard Shader").spawnable is False


def test_resolver_rejects_a_disabled_asset_for_spawning() -> None:
    catalog = _catalog(_entry("Rock 01", _guid(1), enabled=False))
    with pytest.raises(EditorAssetNotInternalError, match="EDITOR_ASSET_DISABLED"):
        resolve_editor_asset_entry(catalog, "Test/Props/Rock 01", require_spawnable=True)


def test_resolver_rejects_a_foreign_asset_location() -> None:
    catalog = _catalog(
        _entry("Mod Local Thing", _guid(4), asset_location="757b15db-4305-4406-b767-1ec002daa769")
    )
    entry = resolve_editor_asset_entry(catalog, "Test/Props/Mod Local Thing")
    assert entry.spawnable is False
    with pytest.raises(EditorAssetNotInternalError, match="EDITOR_ASSET_NOT_INTERNAL"):
        resolve_editor_asset_entry(catalog, "Test/Props/Mod Local Thing", require_spawnable=True)


# --------------------------------------------------------------------------- #
# Reference envelope
# --------------------------------------------------------------------------- #


def test_verified_reference_is_the_observed_compact_envelope() -> None:
    catalog = _catalog(_entry("Rock 01", _guid(0xAB)))
    entry = resolve_editor_asset_entry(catalog, "Test/Props/Rock 01")
    reference = build_verified_prop_reference(catalog, entry)

    assert reference.payload == PROP_REFERENCE_PREFIX + entry.guid.encode("ascii")
    assert len(reference.payload) == PROP_REFERENCE_BYTE_LENGTH
    assert reference.payload[0] == 0x01
    assert reference.payload[1] == 36
    assert reference.payload[2:].decode("ascii") == entry.guid
    assert reference.payload_sha256 == hashlib.sha256(reference.payload).hexdigest()
    assert reference.catalog_sha256 == catalog.bundle_sha256


def test_verified_reference_rejects_a_mismatched_payload() -> None:
    with pytest.raises(DamagedEditorAssetCatalogError, match="payload"):
        VerifiedPropReference(
            guid=_guid(1),
            payload=PROP_REFERENCE_PREFIX + _guid(2).encode("ascii"),
            payload_sha256="0" * 64,
            asset_id="Assets/Test/Rock 01",
            asset_name="Rock 01",
            asset_folder="Test/",
            catalog_sha256="c" * 64,
        )


def test_verified_reference_rejects_a_mismatched_hash() -> None:
    guid = _guid(1)
    payload = PROP_REFERENCE_PREFIX + guid.encode("ascii")
    with pytest.raises(DamagedEditorAssetCatalogError, match="hash"):
        VerifiedPropReference(
            guid=guid,
            payload=payload,
            payload_sha256="0" * 64,
            asset_id="Assets/Test/Rock 01",
            asset_name="Rock 01",
            asset_folder="Test/",
            catalog_sha256="c" * 64,
        )


def test_verified_reference_rejects_a_non_canonical_guid() -> None:
    guid = _guid(0xAB).upper()
    assert guid != guid.lower()
    payload = PROP_REFERENCE_PREFIX + guid.encode("ascii")
    with pytest.raises(DamagedEditorAssetCatalogError):
        VerifiedPropReference(
            guid=guid,
            payload=payload,
            payload_sha256=hashlib.sha256(payload).hexdigest(),
            asset_id="Assets/Test/Rock 01",
            asset_name="Rock 01",
            asset_folder="Test/",
            catalog_sha256="c" * 64,
        )


# --------------------------------------------------------------------------- #
# The shipped Asset Browser registry
# --------------------------------------------------------------------------- #


def test_real_catalog_decodes_the_whole_registry(editor_asset_catalog) -> None:
    summary = editor_asset_catalog.summary()
    assert summary["asset_count"] > 16000
    assert summary["prop_count"] > 15500
    assert summary["spawnable_count"] > 15000
    assert summary["folder_count"] > 150
    assert summary["skipped_record_count"] == 0
    assert summary["writable"] is False
    assert summary["by_asset_location"] == {INTERNAL_ASSET_LOCATION: summary["asset_count"]}
    assert summary["source_relative_path"].endswith("modassetlist.bundle")


def test_real_catalog_agrees_with_scene_derived_ground_truth(editor_asset_catalog) -> None:
    for name, (guid, folder) in GROUND_TRUTH.items():
        entry = resolve_editor_asset_entry(editor_asset_catalog, name, folder=folder)
        assert entry.guid == guid, name
        assert entry.asset_type == PROP_ASSET_TYPE
        assert entry.spawnable is True


def test_real_catalog_exposes_the_viking_environment_set(editor_asset_catalog) -> None:
    result = search_editor_assets(
        editor_asset_catalog, folder="Viking/Environment", limit=200
    )
    assert result["folders"] == [{"folder": "Viking/Environment/", "count": 77}]
    names = {item["name"] for item in result["assets"]}
    missing = sorted(set(VIKING_ENVIRONMENT) - names)
    assert not missing, f"Viking/Environment is missing {missing}"
    assert all(item["spawnable"] for item in result["assets"])
    assert all(item["asset_folder"] == "Viking/Environment/" for item in result["assets"])


def test_real_catalog_folder_prefix_filter_narrows_by_theme(editor_asset_catalog) -> None:
    result = search_editor_assets(
        editor_asset_catalog, folder_prefix="Viking/", limit=1
    )
    assert result["total"] > 200
    folders = {item["folder"] for item in result["folders"]}
    assert "Viking/Environment/" in folders
    assert "Viking/Snow/" in folders


def test_real_catalog_resolves_a_viking_prop_to_a_verified_reference(editor_asset_catalog) -> None:
    entry = resolve_editor_asset_entry(
        editor_asset_catalog, "Viking/Environment/Tree Pine 01", require_spawnable=True
    )
    reference = build_verified_prop_reference(editor_asset_catalog, entry)
    assert reference.guid == "00fc069a-530c-4036-ab72-f2150ce0fc64"
    assert reference.payload == b"\x01\x24" + b"00fc069a-530c-4036-ab72-f2150ce0fc64"
    assert reference.asset_id == "Assets/Viking/Environment/Tree Pine 01"


def test_real_catalog_reports_ambiguous_names_honestly(editor_asset_catalog) -> None:
    # "Rock 01" is registered in many themed folders; the resolver must not pick one.
    with pytest.raises(AmbiguousEditorAssetError, match="matches"):
        resolve_editor_asset_entry(
            editor_asset_catalog, "Rock 01", require_spawnable=True
        )
    described = describe_editor_asset(editor_asset_catalog, "Rock 01")
    assert described["ambiguous"] is True
    assert described["match_count"] > 1
    assert described["resolvable_for_spawn"] is False
    assert all(item["asset_id"] == "Assets/Rock 01" or item["name"] == "Rock 01"
               for item in described["assets"])


def test_real_catalog_rejects_an_unknown_name(editor_asset_catalog) -> None:
    with pytest.raises(EditorAssetNotFoundError, match="EDITOR_ASSET_NOT_REGISTERED"):
        resolve_editor_asset_entry(
            editor_asset_catalog, "Definitely Not A Real Prop 99"
        )


def test_real_catalog_summary_reports_ambiguous_id_collisions(editor_asset_catalog) -> None:
    # The shipped registry lists a few folder/name pairs twice under different
    # GUIDs. They stay visible rather than being silently dropped.
    assert editor_asset_catalog.summary()["ambiguous_id_count"] > 0
    assert any("more than once" in warning for warning in editor_asset_catalog.warnings)


def test_real_catalog_missing_aa_directory_is_reported(tmp_path: Path) -> None:
    from pummelmcp.pmh import load_editor_asset_catalog

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(Exception, match="aa"):
        load_editor_asset_catalog(empty)
