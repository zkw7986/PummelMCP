# Stage 12 asset reference surface

The Workshop Mod census observed `ModProp.prop` as a 38-byte envelope beginning
with `01 24` followed by a lowercase 36-byte UUID. `customMaterials` uses a
little-endian 32-bit count followed by the same 38-byte envelope per observed
element. Empty lists are four zero bytes; single-element lists are 42 bytes.

This establishes framing only. Most referenced GUIDs belong to built-in game
assets and do not resolve through the inspected Mods' `.pmeta` files. The current
read-only taxonomy is therefore:

- `PROP_ASSET_REFERENCE_CANDIDATE`
- `MATERIAL_REFERENCE_LIST_CANDIDATE`
- `MESH_ASSET_REFERENCE` remains unobserved
- malformed, future, or unresolved envelopes remain `UNKNOWN_ASSET_REFERENCE`

Recognition does not authorize duplication or replacement. `ModProp`, non-empty
`customMaterials`, `ModMeshCollider`, missing metadata, cross-Mod resolution, and
asset mutation remain outside the Writer surface.
## Stage 12D structured findings

Observed `ModProp.prop` values use exactly 38 bytes: `01 24` followed by a
lowercase ASCII UUID. The first byte and length byte are part of the typed
envelope. No FileID, PathID, or additional instance bytes were observed.
PummelMCP classifies only this exact form as `PROP_ASSET_REFERENCE`; malformed,
extended, null, and future forms remain `UNKNOWN`.

Observed `customMaterials` uses a little-endian uint32 count followed by that
many 38-byte compact UUID envelopes. Empty and one-element samples exist.
Parsing is bounded and exact, but Writer approval remains `EMPTY_ONLY`; a
non-empty list rejects Prop duplication and replacement.

The read-only resolver searches only `.pmeta` files contained by the supplied
Mod root, records metadata and asset hashes when a unique local match exists,
and reports `UNRESOLVED_BUILTIN` honestly for built-in references. It never
modifies `.pmeta`, `.pfab`, mesh, material, or texture files.

Phase 2 adds a separate, configured read-only catalog for shipped minigame
assets. It supports discovery and exact GUID lookup but is not implicitly used
as a cross-Mod Writer resolution source. Its configuration, tools, containment
rules, and minigame-only scope are documented in
[`BUILTIN_ASSET_TOOLS.md`](BUILTIN_ASSET_TOOLS.md).

No `ModMeshCollider.mesh` Scene sample was found. MeshCollider stays
unsupported and fail-closed.
