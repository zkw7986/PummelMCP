# GameObject Duplication research status

Stage 11B adds a separate `duplicate_subtree` operation for the narrow, reference-free, transform-only subtree class documented in `SUBTREE_DUPLICATION.md`. The existing `duplicate_gameobject` leaf gate and behavior remain unchanged.

Stage 10B is complete for transform-only parented leaves. Stage 10C extends the same
Writer to one Oracle-approved known-Component leaf shape while preserving the
fail-closed reference policy.

## Ctrl+D Oracle matrix

Previous status: no official before/after fixture pair was available, so every rule
was `UNKNOWN`. Stage 10A.5 supplied one usable pair and two invalid pairs. Stage
10A.6 replaces the invalid pairs and adds a disambiguating middle-sibling pair. The
historical boundary remains documented; this matrix reflects the active evidence.

| Oracle case | Result |
|---|---|
| Empty leaf GameObject | OBSERVED_ONCE — valid replacement root duplicate |
| Ordinary Prop | UNKNOWN |
| GameObject + Collider | UNKNOWN |
| Trigger + Collider | OBSERVED_ONCE — valid replacement with `ModMeshCollider` |
| Trigger + SpawnPrefabAction | OBSERVED — complete populated Action copied byte-identically |
| Action Target to self GameObject | UNKNOWN |
| Action Target to self Component | CONFIRMED — preserved to original source Transform |
| Action Target to external GameObject | UNKNOWN |
| Action Target to external Component | CONFIRMED — preserved to external Transform |
| External object points to duplicated GameObject | UNKNOWN |
| Parent-owned leaf | CONFIRMED — prior last-child plus replacement middle-child fixtures |
| Prefab instance | UNKNOWN |
| Object with child | UNKNOWN |

## Invalid Oracle history

- Stage 10A.5 old `01_empty` is invalid because after contained two new objects.
- Stage 10A.5 old `03_trigger` is invalid because Components were added to the source
  instead of duplicating the GameObject.

Their hashes and findings remain in repository history and the Stage 10A.5 sections
below. Stage 10A.6 replacement files use new hashes and are the active baseline.

## Stage 10A.6 replacement findings

All three replacement pairs are valid single-GameObject additions.

### EmptyLeaf

GameObject/ModTransform mapping:
`e339d894-3e3b-415c-ae83-5518afc4f423` →
`7df544bf-4c24-442f-ac08-4b3f726fcb65`. The duplicate is a same-named root and
copies active/layer/tag and all non-identity Transform bytes. The source is retained.
Delta: +1 object, +1 Component, +238 bytes (26 hierarchy, 92 index, 120 payload).

### TriggerLeaf

| Type | Source GUID | Duplicate GUID |
|---|---|---|
| GameObject / `ModTransform` | `095b954f-863d-4514-8561-3ec94e8c9bd2` | `725e1311-8e83-40d1-a70f-b37840c4a2d0` |
| `ModTrigger` | `9ba52ff7-94fe-4cff-8af9-c46f2d9672e4` | `c93af563-07d3-4f40-a30c-92cad3158e3b` |
| `ModMeshCollider` | `6964e13a-6530-468f-ab64-c5ecff03d201` | `e4cb24cf-8821-435d-8d0a-e8349a711a94` |

Component order and enabled flags are preserved. Every non-identity property is
byte-equivalent. Each non-Transform Component gets a fresh UUID independent of the
duplicate GameObject GUID. All four Trigger event payloads are byte-equivalent,
fully parsed empty Action graphs; this establishes
`EMPTY_ACTION_GRAPH_DUPLICATION_OBSERVED`, not populated Action remapping.

Delta: +1 object, +3 Components, +1240 bytes: 28 hierarchy bytes, 195 index bytes,
and 1017 payload bytes (120 Transform, 827 Trigger, 70 MeshCollider).

### Middle sibling

Before order is `[BeforeSibling, ChildLeaf, AfterSibling]`. After order is
`[BeforeSibling, ChildLeaf, AfterSibling, ChildLeaf]`. Mapping is
`bd314d55-1423-478e-95f8-28209bfc025c` →
`9d2f64e2-7a97-4f94-ab51-e3428603db6a`.

The duplicate retains parent `f75cc755-fa0d-48c7-9395-d36c548b1894` and is appended
as sibling 3/preorder 42. Original `AfterSibling` remains sibling 2/preorder 41.
Thus official Ctrl+D uses append-to-parent-list for this fixture; it does not insert
immediately after the middle source and does not shift the following sibling.

Delta: +1 object, +1 Component, +238 bytes. Only the parent's child relationship is
a semantic change to an existing object; the source record remains byte-identical.

## Current evidence table

| Rule | Status |
|---|---|
| source GameObject identity preserved | `OBSERVED_ACROSS_MULTIPLE_FIXTURES` |
| duplicate GameObject gets new GUID | `OBSERVED_ACROSS_MULTIPLE_FIXTURES` |
| GameObject GUID equals ModTransform GUID | `OBSERVED_ACROSS_MULTIPLE_FIXTURES` |
| non-Transform Component gets new GUID | `OBSERVED_ACROSS_MULTIPLE_FIXTURES` across `ModTrigger` and `ModMeshCollider` |
| serialized name copied unchanged | `OBSERVED_ACROSS_MULTIPLE_FIXTURES` |
| active/layer/tag copied | `OBSERVED_ACROSS_MULTIPLE_FIXTURES` |
| Transform copied | `OBSERVED_ACROSS_MULTIPLE_FIXTURES` |
| same parent retained | `CONFIRMED` by middle-sibling plus prior parent-leaf fixture |
| duplicate appended to parent children | `CONFIRMED` by disambiguating middle-sibling fixture |
| following sibling does not shift | `CONFIRMED` by middle-sibling fixture |
| source/duplicate Component order preserved | `OBSERVED_ONCE` on multi-Component Trigger |
| Trigger properties copied | `OBSERVED_ONCE` |
| empty Trigger Action payload copied | `OBSERVED_ONCE` (`EMPTY_ACTION_GRAPH_DUPLICATION_OBSERVED`) |
| internal Object reference remap | `NOT_OBSERVED` |
| external Object reference preserve | `NOT_OBSERVED` |
| internal Component reference remap | `CONFIRMED_BY_ORACLE`: preserved to source, not remapped |
| external Component reference preserve | `CONFIRMED_BY_ORACLE` |
| incoming reference preserve | `NOT_OBSERVED` |
| Action Target remap | `NOT_OBSERVED` |
| asset reference preserve | `NOT_OBSERVED` |

## Stage 10A.7 Transform reference findings

The `Spawn Prefab -> Targets -> Advanced -> Transforms to Spawn Prefabs At`
field serializes `SpawnPrefabAction.m_targets[0]` as a concrete Scene
`ModTransform` reference. It contains the Transform GUID plus file/path ids and is
parsed with exact JSON and scene spans. It is not an asset, prefab GUID, Action
rid, RefId, target flag, or an independently proven GameObject reference.

In `05_self_component`, source Transform
`095b954f-863d-4514-8561-3ec94e8c9bd2` maps to duplicate Transform
`9e6c5c5a-c364-4b69-8c7e-7038568b252d`. Both the retained source Action and the
duplicate Action point to the original source Transform. This is
`INTERNAL_COMPONENT_REFERENCE_PRESERVED_TO_SOURCE`, not remapping.

In `06_external_component`, both the source and duplicate Action point to
`ExternalTarget.ModTransform` `b8aa72c2-32b6-481f-bcd7-559415dbb9e0` after
duplication. This is `EXTERNAL_COMPONENT_REFERENCE_PRESERVED`.

For both pairs, rid and RefId remain `1000`, Action order is unchanged,
ManagedReference framing is unchanged, the advanced target list remains length
one, `m_prefabs` remains empty, and the complete Action payload is byte-identical.
The graph decides internal versus external from structured Component ownership:
ownership by the selected clone source is internal; any other resolved owner is
external. Duplicate mapping validates whether the target was remapped.

Gate 6 remains failed because internal/external GameObject-reference serialization
and pre-existing incoming-reference behavior are still `NOT_OBSERVED`. Sharing GUID
text between a GameObject and its `ModTransform` does not erase that type boundary.

The preceding sentence records the Stage 10A.7 decision under the old Gate 6
wording. Stage 10A.8 supersedes the gate status, without changing the observation:
independent GameObject references are still not observed.

The repository fixture and live Joker 21 Scene are not an Oracle pair. A read-only
comparison on 2026-09-14 found identical hierarchy and identity tables (41 roots,
142 objects, 267 components), with differences only in existing property payloads.
There is no evidence that one file was produced from the other by one Ctrl+D action.

## Observed duplication semantics

### `01_empty` integrity failure

Before had one root `EmptyLeaf` with GUID
`b4b44a90-9c6f-4230-a8b9-48dcfffd1b2c`. After retained it unchanged and added two
same-named roots:

- `ed26d57c-0947-418d-b558-ad38b636a073`
- `04bcbfa9-be21-4084-93b6-c67cb3ae0825`

Both new objects have only `ModTransform`; each Transform GUID equals its GameObject
GUID. Their Transform position/rotation/scale, active, layer, tag, and name equal the
source. Both GUIDs are lowercase RFC 4122 UUID v4. Because two objects appeared,
these are observations of the file, not proof of a single Ctrl+D rule.

Structured size delta is +476 bytes: two 26-byte hierarchy records, two 92-byte
object/index records, and two 120-byte Transform payload records. Root count changes
from 4 to 6. The original hierarchy record and all source field values remain equal.

### `02_parent_leaf` usable observation

Mapping:

| Identity | Source | Duplicate |
|---|---|---|
| GameObject / ModTransform | `3b2e4185-968b-490f-80af-575baa03b395` | `551d2971-a52c-4859-869e-8c7a5c824207` |

The duplicate keeps parent `6ebc95e8-3e1c-48f7-b7da-6b19a3ac56d0`, keeps the
exact name `ChildLeaf`, and changes sibling/preorder from source `(0, 38)` to
duplicate `(1, 39)`. Component order remains `[ModTransform]` and all non-identity
properties are byte-equivalent. The duplicate GUID is lowercase RFC 4122 UUID v4.

`same_parent = OBSERVED_ONCE`. `duplicate_index = source_index + 1 = OBSERVED_ONCE`.
The source was the only/last child, so `insert immediately after` versus `append`
remains `UNKNOWN`; following-sibling shift is `NOT_OBSERVED`.

Structured size delta is +238 bytes: one 26-byte hierarchy record, one 92-byte
object/index record, and one 120-byte Transform payload record. Parent child count
changes from 1 to 2 and its recursive span grows. The source child hierarchy bytes,
identity, and properties remain equal.

### `03_trigger` integrity failure

No new GameObject exists. Existing `TriggerLeaf`
`5f7bc903-36ba-4557-ab63-cd228364ca33` changes Component order from
`[ModTransform]` to `[ModTransform, ModTrigger, ModMeshCollider]` with new Component
GUIDs:

- `ModTrigger`: `cf6a481b-79aa-4101-ac12-9a7a8aa24828`
- `ModMeshCollider`: `364491d8-182f-4685-87eb-964074aafead`

All four Trigger event payloads are identical, fully parsed empty Action graphs of
115 bytes with SHA-256
`e759e97e66bf795cdd312607449f855dcb556ed330135e54eab7d8daa58bb65d`.
No prefab, asset, Action Target, or object/component reference remapping is present.
`ModMeshCollider` remains an unknown Component type to the duplication safety layer.

The +1000-byte delta consists of two new Component index entries plus their payloads;
hierarchy and object count do not change. This pair cannot establish any GameObject
duplication rule.

## Raw diff corroboration

The read-only analyzer records 16, 16, and 15 non-equal ranges for 01, 02, and 03.
Most one-byte replacements are the eight PlayerSpawn position save-noise changes.
The remaining insertions align with the structural hierarchy/index/payload deltas
listed above. Repeated binary sequences can make a sequence matcher choose an
equivalent insertion boundary, so authoritative conclusions come from parsed spans.

## Historical Stage 10A.5 reference evidence

The following list records what the original Stage 10A.5 pairs alone established;
the Component-reference entries are superseded by the Stage 10A.7 findings above.

- Identity regeneration: `OBSERVED_ONCE` on `02_parent_leaf`.
- GameObject GUID equals duplicate ModTransform GUID: `OBSERVED_ONCE` on the usable
  pair; also observed on both extra objects in dirty `01_empty`.
- Internal Object reference: `NOT_OBSERVED`.
- External Object reference: `NOT_OBSERVED`.
- Internal Component reference: `NOT_OBSERVED`.
- External Component reference: `NOT_OBSERVED`.
- Incoming reference behavior: `NOT_OBSERVED`.
- Action Target and asset behavior during GameObject duplication: `NOT_OBSERVED`.

## Duplication safety results

The v0.6b safety analyzer remains fail-closed outside one Oracle-approved subset:

| Object | Status | Reasons |
|---|---|---|
| `EmptyLeaf` (replacement before) | `UNSAFE` | root duplication excluded |
| middle `ChildLeaf` (replacement before) | `SAFE_TRANSFORM_ONLY_LEAF_DUPLICATION` | parented leaf, standard Transform only, no unknown references |
| `TriggerLeaf` (replacement before) | `UNSAFE` | root duplication excluded and `ModMeshCollider` is unsupported |
| `TriggerLeaf` (05 self-reference before) | `UNSAFE` | `ModMeshCollider` unsupported and `m_targetFlags` semantics unknown |
| `TriggerLeaf` (06 external-reference before) | `UNSAFE` | `ModMeshCollider` unsupported and `m_targetFlags` semantics unknown |

This approval does not extend to all leaves. Any extra
Component, nonstandard Transform field, child, root status, or UNKNOWN reference
falls outside the subset.

## Unconfirmed rules

- new GameObject and Component GUID allocation and collision behavior;
- whether the Editor directly uses UUID v4;
- naming and numeric suffix selection;
- root insertion behavior;
- local/world Transform behavior;
- self GameObject reference remapping;
- external GameObject and incoming reference behavior;
- Action Target and prefab/asset behavior;
- prefab instance serialization.

v0.6b now exposes a `ClonePlan` and `duplicate_gameobject` only for the approved
transform-only leaf subset. It still has no generic hierarchy writer, reference
remapper, arbitrary GameObject/Component creator, or deletion operation.

## Historical independent-GameObject fixture protocol

If an independent GameObject-reference surface is discovered, use a disposable
test Mod and retain immutable `before.scene` and `after.scene` for each case:

1. Find an official Editor field that selects a concrete GameObject and serializes
   independently from the confirmed Transform picker.
2. Create a parented source using that field to point to itself; save before,
   Ctrl+D only the source once without editing the target, and save after.
3. Repeat with the source pointing to an external sibling GameObject.
4. Create an external object whose same confirmed field points to the source;
   duplicate only the source to test incoming-reference preservation.

If the official UI exposes no concrete GameObject picker distinct from Transform,
report `NO_EDITOR_CONSTRUCTIBLE_SAMPLE FOUND`; another Transform experiment cannot
prove a different type boundary. After the Stage 10A.8 supported-surface audit,
these Oracles are required only if such a reference surface is actually discovered
or is proposed for support, not for the initial transform-only leaf Writer.

Record the Editor version, selected hierarchy path/GUID, reference property name and
target GUID visible before saving, exactly one Ctrl+D action, both SHA-256 values,
and the new object path visible afterward. That newly expanded reference surface
must remain unsupported until these structural diffs account for all changed spans
and the expanded set has no relevant `UNKNOWN`.

## Stage 10A.8 Gate 6 scope reassessment

A complete supported-surface audit found no independent Scene
`GameObjectReference` parser, serialization envelope, Component property, Action
field, live Joker 21 instance, active Oracle instance, or known Editor picker.
GameObject node identity and `ModTransform` Component identity remain distinct typed
graph endpoints even though their GUID strings are equal. Only the Transform picker
has a confirmed Scene-reference envelope.

This result is `NO_INDEPENDENT_GAMEOBJECT_REFERENCE_OBSERVED`, not proof of global
nonexistence. Gate 6 is revised as follows:

- known Scene Component reference behavior: PASS;
- internal/external `TransformReference` preservation: PASS;
- independent GameObject reference: not observed and outside the v0.6b supported
  surface;
- future or unallowlisted reference types: fail closed before candidate approval.

The initial Stage 10B candidate set may therefore remain the already approved
parented, standard-transform-only leaf. It does not need a theoretical, never-seen
reference format to be proven before implementation. This does not broaden the
candidate set to `ModProp`, populated asset-bearing Actions, unknown target flags,
unsupported Components, children, roots, or any object with an unknown reference.

### Reference policy preview

- identity: regenerate only by the confirmed identity rules;
- hierarchy: append the duplicate and rebuild its membership records;
- `TransformReference`: `PRESERVE_REFERENCE_EXACTLY` for both internal and external
  targets;
- asset/prefab references: preserve exact bytes only when a future supported clone
  template explicitly admits the enclosing shape; exclude populated cases from the
  initial subset;
- unknown or newly decoded reference kind: reject as
  `UNSAFE_UNKNOWN_REFERENCE_TYPE`.

There is no global internal-to-duplicate remapping rule. Each serialization kind
requires its own Oracle-backed policy. If a real GameObject reference appears later,
Gate 6 reopens for that expanded surface and 07/08/09 Oracles become mandatory.

## Stage 10B v0.6b

### Supported duplication surface

`duplicate_gameobject` accepts only a uniquely resolved, parented leaf containing
exactly one standard `ModTransform`. The GameObject and Transform GUID must match;
the Transform field sequence must be `position`, `rotation`, `scale`, `guid`; and
the structured graph must contain no unknown incoming/outgoing reference type or
unsupported outgoing reference. Roots, children, extra Components, Trigger,
Collider, Prop, prefab/asset-bearing objects, and nonstandard Transforms are
rejected.

### ClonePlan and dry run

`plan_gameobject_duplication` is a read-only MCP tool. Its `ClonePlan` records the
source name/GUID/parent/sibling/preorder, exact hierarchy/index/Transform-payload
spans and hashes, a generated collision-free UUID v4, typed GameObject and
ModTransform identity mappings, destination sibling/preorder, and four mutations:

1. update the parent child count;
2. insert the copied leaf hierarchy record at the end of the parent's children;
3. insert a one-Transform object-index record at the destination preorder;
4. insert the copied Transform payload at the corresponding payload position.

Planning never writes the Scene. The public API never accepts a caller-selected
GUID.

### UUID generation and hierarchy writer

The allocator produces lowercase canonical RFC 4122 UUID v4 values and checks both
GameObject and Component identity sets, retrying at most 32 times. One UUID is used
for both the duplicate GameObject and its `ModTransform`. The duplicate retains the
same parent and serialized name and is appended after all existing children. Direct
siblings retain their order, sibling indices, preorder indices, and serialized
contents.

### Reference and copy policy

Identity is regenerated and hierarchy membership is rebuilt. All non-identity
Transform bytes, object active/layer/tag values, and name are copied exactly.
`TransformReference` policy remains `PRESERVE_REFERENCE_EXACTLY`, but the initial
supported source contains no outgoing reference. Any unknown or future kind rejects
the plan as `UNSAFE_UNKNOWN_REFERENCE_TYPE`; there is no generic internal-reference
remapping.

### Safety validation pipeline

The writer resolves and parses current bytes, optionally checks
`expected_scene_hash`, regenerates the ClonePlan, constructs only the four planned
mutations, writes a same-directory temporary file, parses it to exact EOF, builds
the reference graph, validates counts/GUID uniqueness/identity relation/append
position/source preservation/copied fields, creates a timestamped backup, and then
uses atomic replacement. Parse, validation, backup, stale-state, or replacement
failure leaves the source Scene unreplaced.

### Failure modes

Rejections include `UNSAFE_ROOT_OBJECT`, `UNSAFE_HAS_CHILDREN`,
`UNSAFE_UNSUPPORTED_COMPONENT`, `UNSAFE_UNKNOWN_REFERENCE_TYPE`,
`UNSAFE_INVALID_IDENTITY`, `UNSAFE_AMBIGUOUS_OBJECT`, `UNSAFE_OBJECT_NOT_FOUND`,
`UNSAFE_GUID_ALLOCATION`, `UNSAFE_STALE_SCENE`, and `UNSAFE_HIERARCHY`.

### Oracle structural equivalence

The writer test copies `04_middle_sibling before.scene` to a temporary file and
duplicates `ChildLeaf`. When the internal test allocator returns the official
duplicate UUID, the generated file is byte-for-byte equal to
`04_middle_sibling after.scene`. With a random UUID, the parsed structure is the
same after identity correspondence: 43 objects, 78 Components, the original three
children unchanged, and the duplicate at sibling 3/preorder 42. The delta is 238
bytes: 26 hierarchy, 92 object/index, and 120 Transform payload bytes.

## Stage 10C status

Oracle 07 supplies the clean `ModTransform + ModBoxCollider + ModTrigger(empty)`
Ctrl+D pair. It proves independent Component UUID v4 allocation, exact Component
order and ownership, value payload preservation, four byte-identical empty Action
graphs, same-parent append semantics, and source preservation. The Stage 10C Hard
Gate passes.

`duplicate_gameobject` now also accepts the exact
`SAFE_EMPTY_TRIGGER_BOXCOLLIDER_LEAF_DUPLICATION` shape. Its ClonePlan records every
Component's identity, index/payload spans and hashes, and per-type policies. With the
official UUID allocator sequence, generated output is byte-for-byte equal to Oracle
07 after. Populated Actions, `ModMeshCollider`, `ModProp`, assets, roots, children,
unknown references, and all other Component sequences remain rejected. See
[`KNOWN_COMPONENT_DUPLICATION.md`](KNOWN_COMPONENT_DUPLICATION.md).

The automated Stage 10C5 workflow is complete in a full temporary copy of sample
Mod `1`. Its original Editor-created Scene supplies pinned Action and PrefabReference
templates, while all mutation occurs in the copy. The integration verifies duplicate
isolation, Transform and Trigger edits, Action creation and approved field edits,
complete PrefabReference replacement backed by real copied assets, and final Scene
validation. External PrefabReference template Scenes require an exact reference
SHA-256 and containment in the current Mod root.
