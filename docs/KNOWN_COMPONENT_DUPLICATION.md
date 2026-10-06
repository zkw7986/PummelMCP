# Stage 10C known-component leaf duplication

Stage 10C1 audited known Components without broadening the Writer. Official Editor
Oracle 07 subsequently passed the Hard Gate and authorizes one exact multi-Component
leaf shape. The Writer remains fail-closed for every other shape.

## Oracle 07

| File | SHA-256 |
|---|---|
| `07_button_empty_actions_before.scene` | `2897b7d6bdd19e8a304ce56c56d8224b6baaaec751a7d2c59fd455dc42ab06ea` |
| `07_button_empty_actions_after.scene` | `a80c068f4e61ddf566a7523b6eb681b0f7b3a7bbb060b9f7184b6d6d9331aa48` |

Both files parse to exact EOF. Counts change from 50 to 51 GameObjects and from 93
to 96 Components. Exactly one `ButtonLeaf` is added under `father`; it is appended
after the source. No object or Component is removed.

| Identity | Source | Official duplicate |
|---|---|---|
| GameObject / `ModTransform` | `a08ec153-338d-4bc8-9196-0344654c98cf` | `0e11663f-cd1d-49b8-9123-4e99496e2798` |
| `ModBoxCollider` | `baa6d8b0-0b8b-417e-91dd-575c9c2796b8` | `ab5cfaee-ff93-4914-8e7d-c601fc2076d5` |
| `ModTrigger` | `211a01ed-7ae9-476e-b94b-903e14da020e` | `4c657968-104f-46e8-ab22-bf6a66ee3510` |

All new identities are independent canonical RFC 4122 UUID v4 values except for
the required GameObject/ModTransform shared identity. Component order, enabled
flags, ownership, all non-identity BoxCollider/Trigger bytes, and the four 115-byte
empty Action payloads are preserved. The reference graph contains no related
UNKNOWN edge.

## Supported Component matrix

| Component | Stage 10C status | Boundary |
|---|---|---|
| `ModTransform` | `SUPPORTED_FOR_DUPLICATION` | standard field sequence only |
| `ModBoxCollider` | `SUPPORTED_FOR_DUPLICATION`, `SAFE_KNOWN_VALUE_COMPONENT` | exact `center`, `size`, `guid` schema and approved shape only |
| `ModTrigger` | `EMPTY_ACTION_MODTRIGGER_DUPLICATION` | exact known field sequence; all four Action graphs empty |
| `ModMeshCollider` | `UNSUPPORTED_COMPONENT`, `UNSAFE_ASSET_SEMANTICS` | `mesh` remains unapproved |
| `ModProp` | `UNSAFE_UNKNOWN_REFERENCE`, `UNSAFE_ASSET_SEMANTICS` | `prop` and `customMaterials` unresolved |
| `ModPlayerSpawn`, `ModLight`, `ModText` | `POTENTIALLY_SUPPORTED` | no direct duplication Oracle |
| any unregistered type | `UNSUPPORTED_COMPONENT` | fail closed |

`COMMON_COMPONENT_IDENTITY_ENVELOPE` establishes ordered membership, index GUID,
enabled flag, payload framing, and matching payload GUID for PMH v1. It does not
authorize any payload or reference type by itself.

## Approved safety classes

`SAFE_TRANSFORM_ONLY_LEAF_DUPLICATION` remains supported.

`SAFE_EMPTY_TRIGGER_BOXCOLLIDER_LEAF_DUPLICATION` requires all of:

- a uniquely resolved parented leaf with no children;
- exact Component order `ModTransform`, `ModBoxCollider`, `ModTrigger`;
- the standard Transform, BoxCollider, and Trigger field sequences;
- GameObject GUID equal to the ModTransform GUID;
- all four Trigger Action graphs fully parsed and empty;
- no unknown incoming or outgoing reference type;
- no unsupported Component or asset-bearing payload.

## ClonePlan and identity policy

The common ClonePlan now contains a `components[]` entry for every source Component:
type, source/new GUID, index span, payload span and hash, identity policy, payload
policy, reference policy, and unknown fields. The destination exposes the generated
Component GUID mapping.

- GameObject and `ModTransform`: one shared new UUID v4.
- `ModBoxCollider`: an independent new UUID v4.
- `ModTrigger`: another independent new UUID v4.
- every allocation is checked against all Scene object/Component identities and all
  identities already allocated by the current plan.
- callers cannot supply Component GUIDs.

## Writer and validation

`duplicate_gameobject` uses the same structured Writer for both approved classes.
It copies the complete source object-index record and contiguous Component payload
records, then patches only parsed GameObject/index/payload identity slots. It updates
the parent child count and inserts the leaf hierarchy record at the proven append
position. It never globally searches for GUID text or reserializes the Scene.

Before atomic replacement, the temporary file must parse to exact EOF and pass
count, hierarchy, Component order, ownership, GUID uniqueness, payload equivalence,
empty-Action, reference-graph, source, sibling, and all-existing-object preservation
checks. Backup and stale-hash behavior are unchanged.

With the Oracle 07 official UUID sequence injected by the test allocator, the
generated output is byte-for-byte identical to the official `after` file.

## Empty Trigger and reference policies

- identity: `REGENERATE`;
- hierarchy: `REBUILD_FOR_DUPLICATE`;
- known value Component: `COPY_NON_IDENTITY_EXACTLY`;
- empty Trigger payload: `COPY_KNOWN_EMPTY_TRIGGER_PAYLOAD`;
- populated Trigger: `UNSAFE_POPULATED_ACTION_GRAPH`;
- `TransformReference`: `PRESERVE_REFERENCE_EXACTLY` for admitted future shapes;
- unknown/future reference: `UNSAFE_UNKNOWN_REFERENCE_TYPE`.

There is no global internal-reference remapping rule.

## End-to-end workflow status

Stage 10C5 is covered by an MCP integration test using sample Mod `1` only as a
read-only source. The test copies the complete Mod to a pytest temporary directory,
preserves its original Scene as `Template.scene`, and replaces only the copied
`MainScene.scene` with Oracle 07 before running the full workflow.

The workflow duplicates `ButtonLeaf`, changes only the duplicate position and
`DisableAfterTriggered`, adds a source-backed `SpawnPrefabAction`, changes the four
approved scalar/vector fields, replaces its existing prefab slot with another real
copied Prefab asset, and validates the result to exact EOF. The original ButtonLeaf
remains unchanged and has empty Action graphs.

Both `add_action` and `replace_prefab_reference` may use an explicitly allowlisted
template Scene. For Prefab replacement, an external Scene is accepted only with an
exact `template_reference_sha256`; the template Scene must remain inside the same
copied Mod root, and the selected `.pfab` plus `.pmeta` must still match their
catalogued hashes. Current Action inspection always uses the target Scene. No raw
reference is fabricated and no sample Mod file is written.
