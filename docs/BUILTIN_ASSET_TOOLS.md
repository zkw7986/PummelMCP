# Built-in minigame asset tools

Phase 2 exposes the shipped minigame `.pmeta` catalog through three strictly
read-only MCP tools. The implementation reuses the existing bounded metadata
parser and exact-GUID catalog; it does not add an asset Writer.

## Configuration and scope

Set `PUMMELMCP_BUILTIN_ASSET_ROOT` to the game's `StreamingAssets` directory:

```powershell
$env:PUMMELMCP_BUILTIN_ASSET_ROOT = "D:\SteamLibrary\steamapps\common\Pummel Party\PummelParty_Data\StreamingAssets"
```

The configured directory must exist. Scanning is hard-limited to:

- `InbuiltMods/*/Assets/`
- `WorkshopTemplates/Minigames/*/Assets/`

`WorkshopTemplates/Boards` is never enumerated. Tool responses expose paths
relative to the configured root, never absolute filesystem paths.

The configuration is optional for the rest of the MCP server. When it is
absent, only the three built-in asset tools return a configuration error;
existing Scene tools continue to use `PUMMELMCP_ALLOWED_ROOT` unchanged.

## Tools

### `get_builtin_asset_catalog_summary`

Returns source, asset, unique-GUID, duplicate-GUID, and per-type counts plus
bounded scan warnings. It also returns the fixed scope declaration with
`boards_included=false`.

### `search_builtin_assets`

Lists or searches metadata with offset/limit pagination. `query` is a
case-insensitive substring match over name, GUID, tags, and relative paths.
Optional `asset_type` and `source` filters are exact and case-insensitive.
`limit` is bounded to 1–100.

Returned records contain:

- canonical GUID and metadata name;
- normalized type (`prefab`, `material`, or `image` for observed assets);
- bounded tags and `assetFolder`;
- source Mod/template and contained relative paths;
- asset SHA-256 when the existing hashing budget permits it.

Calling the tool without filters is the paginated catalog listing operation.

### `get_builtin_asset`

Looks up one exact UUID. A missing GUID is an error. When the same GUID occurs
in multiple shipped Mods, the response sets `ambiguous=true` and returns every
contained source record instead of selecting one silently.

## Safety boundaries

All three tools carry MCP read-only, non-destructive, idempotent annotations.
The scanner retains the existing per-file, total-metadata, asset-hash, source,
and record limits; malformed or oversized metadata is skipped with a warning.
Resolved source and asset paths must remain contained by the configured root.

No tool copies, imports, edits, deletes, or constructs `.pmeta`, `.pfab`,
`.pmat`, image, Scene, or managed-reference data. Catalog presence is discovery
evidence only: it does not make a GUID writable and does not bypass the Action
or component Writer gates.

## Shipped-corpus acceptance result

Against the minigame-only corpus documented in
[`BUILTIN_MOD_CENSUS.md`](BUILTIN_MOD_CENSUS.md), the tools report eight asset
sources, 70 metadata records, 59 unique GUIDs, and 11 duplicated GUIDs:
50 prefabs, 11 materials, and 9 images. No scan warning was produced.
