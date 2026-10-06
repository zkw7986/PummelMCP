# Stage 12 component surface expansion

Stage 12 began with a read-only census of every available Workshop Mod. The
generated verification Mods subsequently opened successfully in the official
Editor with the expected Components, hierarchy, payload values, and placement.

| Exact leaf shape | Automated candidate | Destination policy | Evidence boundary |
| --- | --- | --- | --- |
| `ModTransform + ModPlayerSpawn` | `SAFE_PLAYERSPAWN_LEAF_DUPLICATION` | source parent | fixed, complete value-only schema across 140 observed instances plus official Editor verification |
| `ModTransform + ModLight` | `SAFE_LIGHT_LEAF_DUPLICATION` | source parent | fixed, complete value-only schema across 20 observed instances plus official Editor verification |
| `ModTransform + ModText` | `SAFE_TEXT_LEAF_DUPLICATION` | source parent | complete schema across 13 instances; full variable-length Text payload passed official Editor verification |
| `ModTransform + ModBoxCollider + empty ModTrigger` | duplication approved; `SAFE_EMPTY_TRIGGER_BOXCOLLIDER_LEAF_REPARENT` | arbitrary parent | complete-record reparent and official Editor hierarchy/Component verification passed |

Every shape remains parented, childless, exact-order, exact-field-sequence, and
free of unapproved references. Unknown Components, populated Trigger graphs,
roots, children, and future reference kinds fail closed. No add-Component,
constructor, rename, delete, or cross-Scene path is introduced.

`ModProp` and `ModMeshCollider` are excluded from the candidate Writer. Prop asset
serialization is now recognizable as an asset-like UUID envelope, but its asset
semantics and Editor duplication policy are not proven. No inspected Scene contains
`ModMeshCollider`.
