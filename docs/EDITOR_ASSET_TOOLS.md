# Editor asset tools

Stage 16 adds the ability to reference the built-in assets registered in the
Pummel Party editor's **Asset Browser** and to spawn them as real `ModProp`
objects. Before this, the only way to put a `ModProp` into a scene was to copy
one that already existed (`duplicate_leaf`) or to copy a 38-byte reference
envelope out of another object in the same scene
(`replace_prop_reference_from_template`). Both required an in-scene template.
Now the reference is resolved from the game's own registry instead.

## Where the registry lives

The Asset Browser's built-in list is the `ModAssetList` ScriptableObject:

```csharp
// research/decompiled/FileBrowserWidget.cs:85
foreach (ModAsset asset in WorkshopController.Instance.Assets.assets)
    if (asset.enabled) m_rootInternalFolder.PlaceNode(asset, 0);

// research/decompiled/WorkshopController.cs:121
Assets = Addressables.LoadAssetAsync<ModAssetList>("ScriptableObjects/ModAssetList")
             .WaitForCompletion();
```

The shipped build serializes that object into

```
<StreamingAssets>/aa/StandaloneWindows64/wholegameruntimeassets_assets_scriptableobjects/modassetlist.bundle
```

which is a UnityFS v8 container: big-endian header and blocks directory, 25
LZ4HC blocks, wrapping a 3,264,212-byte SerializedFile (format version 22,
`enableTypeTree = 1`) whose single `MonoBehaviour` object is the registry.

`pummelmcp.pmh.unity_bundle` decodes the container and
`pummelmcp.pmh.serialized_file` walks the object with the type tree the file
itself carries. **Neither step guesses.** Both are self-validating:

| Check | Where |
|---|---|
| declared file size equals the file length | `unity_bundle.read_unityfs_bundle` |
| the block table exactly accounts for the file length, tried against both candidate data offsets | `unity_bundle.read_unityfs_bundle` |
| every node range lies inside the decompressed stream | `UnityFsBundle.node_bytes` |
| declared SerializedFile size equals the decoded stream | `serialized_file.read_serialized_file` |
| the object table cannot overrun the metadata section | `serialized_file.read_serialized_file` |
| walking the object consumes **exactly** its declared `byteSize` | `editor_assets._read_modasset_records` |
| every record carries exactly the nine expected `ModAsset` fields | `editor_assets.EXPECTED_MODASSET_FIELDS` |
| the registry object is the `ModAssetList` | positional `m_Name` value |
| every GUID is a canonical lowercase UUID | `editor_assets._as_guid` |

The walker dispatches on **structure, not names**: a `string` and a `List<T>`
both derive from `Array`, and the `Array`/`size`/`data`/`char` names resolve
through Unity's built-in string table, which a standalone SerializedFile does
not carry. `serialized_file._list_element` therefore identifies a list by shape
(variable-length node with one variable-length child holding a bare four-byte
`sizer`). A future game build that changes the layout fails closed with
`DAMAGED_EDITOR_ASSET_CATALOG` rather than silently mis-resolving references.

## What the catalog resolves to

Decoding `modassetlist.bundle` yields **16,516 records**, all at asset location
`4dde710c-fa53-4c44-8bcd-99be6ee28513`, which `AssetLocationGuid.cs` defines as
`Internal`:

| `assetTypeString` | count |
|---|---|
| `Prop` | 16,419 |
| `UnityEngine.Material` | 39 |
| `UnityEngine.AudioClip` | 36 |
| `UnityEngine.Texture2D` | 18 |
| `UnityEngine.Shader` | 4 |

After collapsing the GUIDs the registry lists twice, the catalog holds **16,511
entries** across **163 folders**, of which **15,868** are spawnable
(`Prop` + `Internal` + `enabled`). `Viking/Environment/` is one of them and
contains 77 props, including the trees, glaciers, ice chunks, icebergs, rocks,
stones, mountains, grass, grass patches and snow piles this stage was validated
against.

Because the Asset Browser nests them under a root folder literally named
`"Assets"` (`FileBrowserWidget.cs`: `new FilterFolder("Assets")`), an entry's
stable identifier is `Assets/<assetFolder><name>`, for example
`Assets/Viking/Environment/Tree Pine 01`.

## Why the GUID *is* the reference

Two game facts make the registry GUID directly usable as the `ModProp.prop`
payload:

```csharp
// research/decompiled/ModAsset.cs:289 -- the GUID string is the Addressables key
if (m_assetType == typeof(Prop))
    m_addressablesLoadAssetHandle = Addressables.LoadAssetAsync<Prop>(guid.ToString());

// research/decompiled/SerializableGuid.cs:56 -- and it is what gets serialized
public void OnBeforeSerialize() { serializedGuid = guid.ToString(); }
```

So a catalog entry's GUID is exactly the value the game passes to Addressables
to load the `Prop`. The payload is then the same 38-byte envelope the scenes
already use: `01 24` followed by the 36-byte lowercase GUID
(`docs/ASSET_REFERENCE_SURFACE.md`). `VerifiedPropReference.__post_init__`
re-checks that identity, the 38-byte length and the SHA-256 before a writer can
see it.

## Spawn layout

`spawn_builtin_prop` creates one GameObject carrying `ModTransform` then
`ModProp`, with the GameObject and its `ModTransform` sharing an identity GUID,
matching every observed prop object in the shipped scenes:

| Section | Bytes | Layout |
|---|---|---|
| hierarchy record | `1 + len(name) + 1 + 4 + 1 + len(tag) + 2` | `str8 name, u8 active, i32 layer, str8 tag, u16 0` |
| object index record | 138 | `str8 guid, u32 2`, then `str8 type, str8 guid, u8 enabled` per component |
| `ModTransform` payload | 120 | `position`/`rotation`/`scale` as `<3f`, then `guid` as a 37-byte `str8` |
| `ModProp` payload | 197 | `prop` (38-byte reference), `tintColor` `<4f`, `collisionType` `i32`, `shadowCastingMode` `i32`, `customMaterials` `i32 0`, `guid` |

The four mutations are the ones already approved for leaf duplication
(`UPDATE_PARENT_CHILD_COUNT`, `INSERT_HIERARCHY_RECORD`,
`INSERT_OBJECT_INDEX_RECORD`, `INSERT_COMPONENT_PAYLOADS`), anchored by the
duplication writer's own `_layout` and `_parent_child_count_offset` so there is
one implementation of the PMH section layout. The new object lands immediately
after the destination parent's whole subtree in preorder, which makes it that
parent's last child in both tables at once.

Defaults mirror the game's own component defaults: `tintColor` is `Color.white`
(`ModProp.m_color`), `collisionType` is `mesh` = 3
(`ModProp.m_collisionType = PropCollisionType.Mesh`), `shadowCastingMode` is
`on` = 1 (the value every inspected scene uses), scale is `(1,1,1)`, and
`customMaterials` is always empty.

`PropCollisionType` is `{None, Box, Sphere, Mesh}` = 0–3
(`research/decompiled/PropCollisionType.cs`), which is why the existing scenes
serialize a plane as 3, a cube as 1 and a sphere as 2.

### Post-write validation

The commit goes through `PMHScene._replace_with_validation`, so the source file
is replaced only after the patched bytes have been parsed from a temporary file
and every check has passed. The spawn validator asserts 25 conditions, notably:

* `parse_to_exact_eof` and `scene_validation` (`validate_scene`)
* `component_count_incremented_by_two`, `gameobject_count_incremented_once`
* `old_gameobject_identities_preserved`, `old_component_identities_preserved`
* `all_existing_objects_preserved` — every pre-existing object re-read
  identically
* `diff_only_in_planned_regions` — reversing the four planned edits reproduces
  the original file **byte for byte**
* `created_component_shape_matches`, `created_identity_relation_matches`
* `created_transform_matches_request`, `created_prop_reference_matches_catalog`,
  `created_custom_materials_empty`, `created_collision_type_matches`,
  `created_shadow_casting_mode_matches`, `created_tint_color_matches`

All of these compare serialized bytes, not decoded approximations.

## Tool contracts

### `list_editor_assets`

Read-only. Filters: `folder` (exact), `folder_prefix`, `query`, `tag`,
`asset_type` (default `Prop`), `enabled_only`, `offset`, `limit` (≤ 200).
Returns `catalog`, a bounded `folders` breakdown, and the matching `assets`.

```json
{
  "catalog": {
    "asset_count": 16511, "prop_count": 16415, "spawnable_count": 15868,
    "folder_count": 163, "ambiguous_id_count": 549,
    "source_relative_path": "aa/StandaloneWindows64/wholegameruntimeassets_assets_scriptableobjects/modassetlist.bundle",
    "source_sha256": "3416529c…", "writable": false
  },
  "total": 77, "returned": 1, "folders": [{"folder": "Viking/Environment/", "count": 77}],
  "assets": [{
    "asset_id": "Assets/Viking/Environment/Tree Pine 01",
    "name": "Tree Pine 01", "asset_folder": "Viking/Environment/",
    "relative_path": "Viking/Environment/Tree Pine 01",
    "asset_type": "Prop", "tags": ["environment","viking","props","large"],
    "enabled": true, "guid": "00fc069a-530c-4036-ab72-f2150ce0fc64",
    "asset_location": "4dde710c-fa53-4c44-8bcd-99be6ee28513", "spawnable": true
  }]
}
```

### `get_editor_asset`

Read-only. Accepts a name (`Tree Pine 01`), a contained relative path
(`Viking/Environment/Tree Pine 01`) or the browser path
(`Assets/Viking/Environment/Tree Pine 01`), with an optional `folder` to
disambiguate. Returns every registration plus the verified reference.

```json
{
  "asset_id": "Assets/Viking/Environment/Tree Pine 01",
  "match_count": 1, "ambiguous": false, "resolvable_for_spawn": true,
  "assets": [{
    "name": "Tree Pine 01", "guid": "00fc069a-530c-4036-ab72-f2150ce0fc64",
    "prop_reference": {
      "schema": "COMPACT_ASSET_REFERENCE_V1",
      "classification": "PROP_ASSET_REFERENCE", "field": "ModProp.prop",
      "byte_length": 38, "prefix_hex": "0124",
      "verification": "REGISTERED_INTERNAL_PROP",
      "payload_sha256": "067917bb…", "source_catalog_sha256": "3416529c…"
    }
  }]
}
```

### `spawn_builtin_prop`

Mutation. `scene_path` plus `asset` are required; everything else is optional.

```
scene_path, asset, folder, parent, name,
position{x,y,z}, rotation{x,y,z}, scale{x,y,z},
tint_color{r,g,b,a}, collision_type, shadow_casting_mode,
layer, tag, active, expected_scene_hash, dry_run
```

* `parent` accepts a hierarchy path, a GUID or a unique name; the default is the
  scene's first root object.
* `position`/`rotation`/`scale` are partial updates; omitted axes take the
  documented default. Values are serialized **float32**, and rotation is euler
  **radians**, matching the existing `set_transform` and `create_from_template`
  tools rather than introducing a second unit for the same field.
* `collision_type` ∈ `none|box|sphere|mesh`, `shadow_casting_mode` ∈
  `off|on|two_sided|shadows_only`.
* `layer`/`tag` default to the destination parent's.
* `expected_scene_hash` re-checks the file before writing;
  `dry_run` runs the identical build and validation without writing.

```json
{
  "safety_class": "SAFE_REGISTERED_BUILTIN_PROP_SPAWN", "dry_run": false,
  "asset": {"asset_id": "Assets/Viking/Environment/Tree Pine 01",
            "guid": "00fc069a-…", "verification": "REGISTERED_INTERNAL_PROP",
            "reference_byte_length": 38, "catalog_sha256": "3416529c…"},
  "created": {"gameobject_guid": "c02a72e2-…", "transform_guid": "c02a72e2-…",
              "prop_guid": "472a1639-…", "name": "Tree Pine 01",
              "hierarchy_path": "/World/Tree Pine 01",
              "component_shape": ["ModTransform", "ModProp"]},
  "destination": {"parent_guid": "…", "parent_path": "/World",
                  "sibling_index": 19, "preorder_index": 23},
  "transform": {"position": {"x": 0, "y": 0, "z": -8}, "scale": {"x": 1.5, …},
                "unit": "serialized float32; rotation is euler radians"},
  "mod_prop": {"tint_color": {"r": 0.8, …},
               "collision_type": {"name": "mesh", "raw_value": 3},
               "shadow_casting_mode": {"name": "on", "raw_value": 1},
               "custom_materials": "EMPTY"},
  "mutation_kinds": ["UPDATE_PARENT_CHILD_COUNT", "INSERT_HIERARCHY_RECORD",
                     "INSERT_OBJECT_INDEX_RECORD", "INSERT_COMPONENT_PAYLOADS"],
  "bytes_inserted": 479, "transaction_before_sha256": "…",
  "transaction_after_sha256": "…", "backup_path": "…",
  "validation": {"passed": true, "checks": {"…": true}}
}
```

## Caller-submitted references are impossible

There is no parameter anywhere on the surface that accepts a GUID, a payload, a
byte string, or a serialized reference. `spawn_builtin_prop` takes a **name or a
contained relative path** and resolves it inside the server against the catalog
it scanned; the reference that reaches the writer is built by
`build_verified_prop_reference` from the decoded registry entry. The writer then
re-checks the envelope (`payload == 01 24 + guid`, 38 bytes, canonical lowercase
UUID, matching SHA-256, non-empty catalog provenance) and refuses anything that
is not a `VerifiedPropReference`.

## Rejections

| Situation | Result |
|---|---|
| not in the registry | `EDITOR_ASSET_NOT_REGISTERED` |
| registered twice under different GUIDs | `AMBIGUOUS_EDITOR_ASSET`, listing the candidates |
| registered but not a `Prop` | `EDITOR_ASSET_NOT_A_PROP` |
| registered but `enabled = false` | `EDITOR_ASSET_DISABLED` |
| asset location is not `Internal` | `EDITOR_ASSET_NOT_INTERNAL` |
| matches a Mod-local `.pmeta` asset of the current mod | `EDITOR_ASSET_NOT_INTERNAL`, naming the sidecar — a mod-local asset is a different library and can never back `ModProp.prop` |
| absolute path, Windows separator, `..` or `.` segment, empty | `EDITOR_ASSET_IDENTIFIER_INVALID` |
| damaged bundle, unreadable registry, layout drift | `DAMAGED_EDITOR_ASSET_CATALOG` |
| no registry bundle, or more than one | `EDITOR_ASSET_CATALOG_NOT_FOUND` / `AMBIGUOUS_EDITOR_ASSET_CATALOG` |

## Configuration

`PUMMELMCP_EDITOR_ASSET_ROOT` points at the `StreamingAssets` copy that carries
the `aa` Addressables build. When it is unset the tools reuse
`PUMMELMCP_BUILTIN_ASSET_ROOT`, so an existing configuration keeps working.
The root must exist and contain an `aa` directory; the registry bundle must be
unique below it.

## Evidence

* `tests/test_unity_bundle.py` — 13 tests over the container, the raw LZ4 block
  decoder (literal runs, overlapping matches, extended lengths, corruption), and
  the self-validation identities.
* `tests/test_editor_assets.py` — 28 tests over the resolution contract on
  synthetic catalogs and the shipped registry: entry counts, the
  `Primitives/` ground truth, the full `Viking/Environment/` set, ambiguity,
  every rejection above, and the reference envelope.
* `tests/test_builtin_prop_writer.py` — 27 tests over spawn placement, defaults,
  byte-exact transform/reference/colour/enum output, repeated spawns, dry runs,
  backups, stale hashes, forged references, invalid arguments, and the exactness
  of the planned-region reversal.
* `tests/test_builtin_prop_e2e.py` — 8 tests driving the MCP surface against the
  real `teat` workshop mod: six distinct `Viking/Environment` assets
  (Tree Pine 01, Glacier 01, IceChunk 01, Rock 01, Grass 01, SnowPile 01) are
  created in `Data/MainScene.scene` and every reference, transform, `ModProp`
  property and scene-level invariant is re-read from the written file.

Ground truth for the catalog decoder is independent of the decoder: the GUIDs
`cfc9faf9…` (LowPolyCube), `c7d7ef4d…` (LowPolySphere), `e314a70b…`
(LowPolyPlane) and `331019a7…` (Plane) appear literally inside shipped `.scene`
files and inside the mod-editor scene bundle's `cubeGuid`/`sphereGuid`/
`planeGuid` serialized fields, and the catalog resolves all four to the right
names in `Primitives/`.

## Known limitations

1. **Editor rendering is not machine-verifiable.** The project can prove the
   bytes parse to exact EOF, that the reference equals the Addressables key the
   game loads for that prop, that the object shape matches the 19 pre-existing
   `ModProp` objects in `teat`, and that every invariant above holds. Whether the
   Pummel Party editor draws the mesh still needs a human to open the mod.
2. **The registry parser is pinned to the shipped build.** It fails closed
   rather than degrading.
3. **Only built-in `Prop` assets are supported.** Mod-local `.pfab`/`.pmat`
   assets are a different library and are rejected by name, not silently skipped.
4. **`customMaterials` is written empty by this built-in-asset path** and remains read-only here, matching
   the existing writer surface.
5. **No mesh or material inspection.** The meshes and materials of a prop live in
   separate compressed bundles; the catalog provides identity, path, type, tags
   and the verified reference, not previews.
6. **`collisionType` and `shadowCastingMode` names are derived**, not read from
   the game: `PropCollisionType` comes from the decompiled `PropCollisionType.cs`
   and the shadow values are the Unity enum as observed in shipped scenes.
7. **549 registry identifiers are duplicated** — the same folder and name under
   two different GUIDs. These stay visible in `list_editor_assets` and
   `get_editor_asset`, and `spawn_builtin_prop` refuses them as ambiguous rather
   than picking one.
8. **Rotation is in radians**, which matches the existing writers but is an easy
   thing to get wrong when calling by hand.
