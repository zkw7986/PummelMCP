from __future__ import annotations

import json
from pathlib import Path

import anyio
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from pummelmcp.mcp_server import BUILTIN_ASSET_ROOT_ENV, create_server
from pummelmcp.pmh import (
    BuiltinAssetCatalogError,
    get_builtin_asset_by_guid,
    load_builtin_asset_catalog,
    search_builtin_asset_catalog,
)


SHARED_GUID = "11111111-1111-4111-8111-111111111111"
MATERIAL_GUID = "22222222-2222-4222-8222-222222222222"
BOARD_GUID = "33333333-3333-4333-8333-333333333333"


def _write_asset(
    root: Path,
    source: str,
    relative_asset: str,
    *,
    guid: str,
    name: str,
    tags: list[str],
) -> None:
    asset = root / source / "Assets" / relative_asset
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_bytes(f"asset:{name}".encode())
    Path(str(asset) + ".pmeta").write_text(
        json.dumps(
            {
                "name": name,
                "guid": {"serializedGuid": guid},
                "tags": tags,
                "assetFolder": f"/{asset.parent.name}/",
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def builtin_asset_root(tmp_path: Path) -> Path:
    root = tmp_path / "StreamingAssets"
    _write_asset(
        root,
        "InbuiltMods/Arena",
        "Prefabs/SharedGun.pfab",
        guid=SHARED_GUID,
        name="Shared Gun",
        tags=["prefabs", "weapon"],
    )
    _write_asset(
        root,
        "WorkshopTemplates/Minigames/Shooter",
        "Prefabs/SharedGunCopy.pfab",
        guid=SHARED_GUID,
        name="Shared Gun Copy",
        tags=["prefabs", "weapon"],
    )
    _write_asset(
        root,
        "WorkshopTemplates/Minigames/Shooter",
        "Materials/Glow.pmat",
        guid=MATERIAL_GUID,
        name="Glow",
        tags=["materials"],
    )
    _write_asset(
        root,
        "WorkshopTemplates/Boards/Advanced Board",
        "Prefabs/BoardOnly.pfab",
        guid=BOARD_GUID,
        name="Board Only",
        tags=["prefabs"],
    )
    broken = root / "InbuiltMods" / "Arena" / "Assets" / "Broken.pmeta"
    broken.write_text("{not-json", encoding="utf-8")
    return root


def _call(server, name: str, arguments: dict):
    async def invoke():
        return await server.call_tool(name, arguments)

    return anyio.run(invoke)


def test_builtin_catalog_is_minigame_only_and_reports_duplicates(
    builtin_asset_root: Path,
) -> None:
    catalog = load_builtin_asset_catalog(builtin_asset_root)
    summary = catalog.summary()

    assert summary["asset_count"] == 3
    assert summary["unique_guid_count"] == 2
    assert summary["duplicate_guid_count"] == 1
    assert summary["by_type"] == {"material": 1, "prefab": 2}
    assert summary["scope"]["boards_included"] is False
    assert all("Boards" not in item.source for item in catalog.entries)
    assert any("skipped malformed metadata" in item for item in catalog.warnings)


def test_builtin_catalog_search_is_filtered_paginated_and_relative(
    builtin_asset_root: Path,
) -> None:
    catalog = load_builtin_asset_catalog(builtin_asset_root)
    result = search_builtin_asset_catalog(
        catalog, query="weapon", asset_type="prefab", offset=1, limit=1
    )

    assert result["total"] == 2
    assert len(result["assets"]) == 1
    item = result["assets"][0]
    assert item["asset_type"] == "prefab"
    assert not Path(item["relative_path"]).is_absolute()
    assert not Path(item["metadata_relative_path"]).is_absolute()
    assert result["writable"] is False


def test_builtin_catalog_exact_guid_returns_all_sources(
    builtin_asset_root: Path,
) -> None:
    catalog = load_builtin_asset_catalog(builtin_asset_root)
    result = get_builtin_asset_by_guid(catalog, SHARED_GUID.upper())

    assert result["match_count"] == 2
    assert result["ambiguous"] is True
    assert {item["source"] for item in result["assets"]} == {
        "InbuiltMods/Arena",
        "WorkshopTemplates/Minigames/Shooter",
    }
    with pytest.raises(BuiltinAssetCatalogError, match="valid UUID"):
        get_builtin_asset_by_guid(catalog, "not-a-guid")
    with pytest.raises(BuiltinAssetCatalogError, match="was not found"):
        get_builtin_asset_by_guid(
            catalog, "99999999-9999-4999-8999-999999999999"
        )


def test_builtin_asset_mcp_tools_are_read_only_and_structured(
    tmp_path: Path, builtin_asset_root: Path
) -> None:
    server = create_server(tmp_path, builtin_asset_root=builtin_asset_root)
    tools = {item.name: item for item in anyio.run(server.list_tools)}
    for name in (
        "get_builtin_asset_catalog_summary",
        "search_builtin_assets",
        "get_builtin_asset",
    ):
        assert tools[name].annotations.read_only_hint is True
        assert tools[name].annotations.destructive_hint is False

    summary = _call(server, "get_builtin_asset_catalog_summary", {})
    search = _call(server, "search_builtin_assets", {"query": "Glow"})
    exact = _call(server, "get_builtin_asset", {"guid": SHARED_GUID})
    assert summary.structured_content["asset_count"] == 3
    assert search.structured_content["assets"][0]["name"] == "Glow"
    assert exact.structured_content["match_count"] == 2


def test_builtin_asset_mcp_tools_require_separate_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(BUILTIN_ASSET_ROOT_ENV, raising=False)
    server = create_server(tmp_path)
    with pytest.raises(ToolError, match="PUMMELMCP_BUILTIN_ASSET_ROOT is required"):
        _call(server, "get_builtin_asset_catalog_summary", {})


def test_builtin_catalog_rejects_wrong_root(tmp_path: Path) -> None:
    with pytest.raises(BuiltinAssetCatalogError, match="must contain"):
        load_builtin_asset_catalog(tmp_path)
