# Object Reference Graph v0.6b

Stage 11 hierarchy mutation uses the graph as a fail-closed safety gate. Initial reparent and subtree duplication accept only standard `ModTransform` nodes whose related edges are structural identity, membership, and hierarchy edges; all other reference kinds are rejected.

Stage 10A is read-only. The implementation parses the PMH hierarchy, object /
component index, component property table, and managed-reference Action payloads.
It does not search the complete file for GUID-shaped strings.

## Structured identities

The approved Joker 21 fixture contains 142 GameObjects and 267 Components.
Each identity is read from its `str8` slot in the object/component index, and the
Reader records the exact half-open source span of that slot.

Observed fixture facts:

- all 142 GameObject identifiers are lowercase, hyphenated, 36-character RFC 4122 UUID v4 values;
- all 267 Component identifiers have the same representation;
- identifiers are unique within their own domain;
- each GameObject has exactly one `ModTransform`;
- a GameObject identifier is equal to the identifier of its `ModTransform`;
- each observed Component `guid` property equals its component-index identifier.

The last two facts mean GameObject and Component identifier namespaces overlap.
Graph endpoints therefore always carry both `kind` and `guid`; GUID text alone is
not treated as a globally typed endpoint. These facts do not prove how the Editor
allocates identities during duplication.

## Hierarchy and ownership

Hierarchy records are recursive and child arrays are serialized in order. The
Reader records each hierarchy record's source span, preorder index, zero-based
sibling index, parent, and ordered children. The later index and payload tables
associate entries with the same preorder traversal, recovering Component ownership
without a fixed offset or GUID search.

Stage 10A.5 added one usable parented-last-child observation. The duplicate kept
the same parent and serialized at sibling/preorder `source + 1`. Because the source
was the parent's only and last child, immediate-after insertion versus append-to-end
remains `UNKNOWN`; following-sibling shifting was `NOT_OBSERVED`.

## Edge classifications

| Classification | v0.6b source |
|---|---|
| `IDENTITY` | object index GUID and matching Component `guid` property |
| `INTERNAL_COMPONENT_REFERENCE` | serialized membership, or resolved target Component owned by the selected clone source |
| `HIERARCHY_REFERENCE` | recursive parent-to-child membership |
| `ASSET_REFERENCE` | structured Action prefab and audio reference fields |
| `INTERNAL_OBJECT_REFERENCE` | modelled; no confirmed property sample exists |
| `EXTERNAL_OBJECT_REFERENCE` | modelled; no confirmed property sample exists |
| `EXTERNAL_COMPONENT_REFERENCE` | resolved target Component owned outside the selected clone source |
| `UNKNOWN` | unconfirmed Component references, unresolved Actions, and unknown Action target/flag/effect semantics |

`ModProp.prop` and `ModProp.customMaterials` are deliberately `UNKNOWN`. Their
payloads contain reference-like data, but there is no Editor-validated proof enough
to classify or remap them safely. The graph never extracts a target by guessing
their bytes.

Action `m_prefabs`, `m_clip`, and `SpawnPrefabAction.m_targets` fields use the
managed-reference JSON parser, and exact JSON spans are translated to scene spans.
The last field is now a confirmed list of `ModTransform` references. Empty target
selector lists on other Action classes, target flags, and effect indices remain
`UNKNOWN` because their semantics are not known.

## Read-only MCP tools

- `inspect_reference_graph`
- `inspect_object_references`
- `analyze_duplication_safety`

All have read-only annotations. Tests hash the fixture before and after graph
inspection. No graph API retains a writable handle or exposes raw payload bytes.

## Ctrl+D Oracle fixtures (Stage 10A.5)

Three supplied pairs were parsed and hash-pinned by the read-only Oracle analyzer.
The actual second after filename is `02_parent_leaf_ after.scene` (with a space).

| File | SHA-256 | Parse |
|---|---|---|
| `01_empty_before.scene` | `3e4a47c61599d36512d777227bb4335c37e7adbcc9af014789fdfe90028bfb92` | exact EOF |
| `01_empty_after.scene` | `43ec00cce563420cca7e83e40cf9d085534bd33ae02979fe9907cc6220aaad8a` | exact EOF |
| `02_parent_leaf_before.scene` | `6142fb2e7eeed761915b2b8c3b7ac8c2fbd68f5812bf2f90b213f8ec9abcfcb7` | exact EOF |
| `02_parent_leaf_ after.scene` | `1ce10a42b8e3155915e28b08d3b6af1d8103c37b3d39ee280aea027a0f7126eb` | exact EOF |
| `03_trigger_before.scene` | `01b0609881e76f815d6926286fd8d9018d6ca791aeafd02859bb55bd6f235be2` | exact EOF |
| `03_trigger_after.scene` | `2b70357ec0ae944ef902651af0f27734a72d18b075b49e47ffe65fbdc0f14b36` | exact EOF |

Integrity findings:

- `01_empty`: failed the one-operation invariant; two new roots appeared.
- `02_parent_leaf`: one new object appeared and is a usable `OBSERVED_ONCE`
  hierarchy/identity sample.
- `03_trigger`: failed as a duplication Oracle; zero objects appeared and two
  Components were added to the existing object.
- every pair changed the position payload of all eight PlayerSpawn objects. This is
  recorded as Editor-save noise; it was not attributed to Ctrl+D semantics.

The analyzer reports structural node/component deltas, changes on preserved objects,
and bounded raw changed/inserted ranges. Raw ranges corroborate the structural
layout but are not used to discover GUIDs or references.

## Replacement Oracle fixtures (Stage 10A.6)

The invalid Stage 10A.5 `01_empty` and `03_trigger` pairs remain documented above.
They were replaced, not silently reinterpreted. A new middle-sibling pair was also
added. All six current files parse to exact EOF and are SHA-pinned:

| File | SHA-256 |
|---|---|
| `01_empty_before.scene` | `a7f47e3741400be48ab85c2f74caacd6188cbb9381bc030ac200db7c3ad64b24` |
| `01_empty_  after.scene` | `f4aebadf56b7e91c8ec641ea41612063a548fa249cf140fa5b0fe31a248b116c` |
| `03_trigger_before.scene` | `3c0223bc35b3c6a158732be55b758beb0d7a12ddf083e9a9e83b22df9e893802` |
| `03_trigger_after.scene` | `bee51a74c6654b485cbc97607ecb09342c8084142cdf7e8413f6a7971eb99aeb` |
| `04_middle_sibling before.scene` | `8212c182f7e8f1a3f97d86ba0cc2ca6185eecbdfcfd7f7d6dad38c360b1d9cc4` |
| `04_middle_sibling after.scene` | `8649704b376c43d6f4df35951000c138bf586d922902ea1915599f07d4fef05a` |

All three replacement pairs contain exactly one added GameObject and no removed
GameObject. `01` adds one Component, `03` adds three, and `04` adds one. The source
objects and source Component identities are preserved.

The analyzer now labels changes as `KNOWN_EDITOR_SAVE_NOISE` only when an existing
`/Player Spawnpoints/PlayerSpawn_*` object changes solely in
`ModTransform.position`. Eight such changes occur in replacement 01 and 03; none
occur in 04. Noise remains in the files and raw diff, but is excluded from semantic
existing-object changes.

The middle-sibling hierarchy is decisive. Before is
`[BeforeSibling, ChildLeaf, AfterSibling]`; after is
`[BeforeSibling, ChildLeaf, AfterSibling, ChildLeaf]`. Ctrl+D therefore appends the
duplicate to the same parent's ordered children list. The original following sibling
remains sibling 2/preorder 41; it does not shift. The duplicate is sibling 3/preorder
42. This changes the parent's child count and recursive span but preserves the source
child hierarchy bytes.

## Reference remapping evidence

The usable `ChildLeaf` sample contains only `ModTransform`, whose GUID is also the
GameObject identity. It proves identity regeneration for that one shape, but contains
no independent GameObject/Component reference field.

| Rule | Evidence |
|---|---|
| identity regenerated | `OBSERVED_ACROSS_MULTIPLE_FIXTURES` on 01, 03, and 04 |
| hierarchy parent membership retained | `CONFIRMED` by middle-sibling fixture plus prior last-child observation |
| internal Object reference remap | `NOT_OBSERVED` |
| external Object reference preservation | `NOT_OBSERVED` |
| internal Component reference remap | `CONFIRMED_BY_ORACLE`: preserved to original source Transform, not remapped |
| external Component reference preservation | `CONFIRMED_BY_ORACLE` |
| incoming external reference preservation | `NOT_OBSERVED` |
| Action Target remap | `NOT_OBSERVED` |
| Action asset preservation during duplication | `NOT_OBSERVED` |

## Transform Reference Oracle (Stage 10A.7)

The official Editor path was `Spawn Prefab -> Targets -> Advanced -> Transforms
to Spawn Prefabs At`. Both pairs parse to exact EOF, add exactly one complete
three-Component GameObject, remove none, and remained SHA-identical throughout
read-only analysis.

| File | SHA-256 |
|---|---|
| `05_self_component_before.scene` | `a394b6b00434778f0a98941cd7000b7b820b85b8127917a530263b7c1fe5628a` |
| `05_self_component_after.scene` | `04a7cced260cf77d7550a5d08066ef322f692925799ff490090ff301f8ff105d` |
| `06_external_component_before.scene` | `7af1b95b7941bedf3dfdff94b05ba7113be3ad21e8191de7b49ef3ad8cfeaead` |
| `06_external_component_after.scene` | `148dcd559764c626008791f43161577081e7006f6105ca4e945178dd1c3f4427` |

### Internal Transform preservation to source

Oracle 05 source `TriggerLeaf` Transform is
`095b954f-863d-4514-8561-3ec94e8c9bd2`; the duplicate Transform is
`9e6c5c5a-c364-4b69-8c7e-7038568b252d`. The source Action points to the source
Transform both before and after duplication. The duplicate Action also points
to the original source Transform. Result:
`INTERNAL_COMPONENT_REFERENCE_PRESERVED_TO_SOURCE`, confirmed by this Oracle;
official Ctrl+D did not remap the clone-internal Transform reference.

### External Transform preservation

Oracle 06 source and duplicate Transforms are
`2024921f-5822-4c24-a73d-a44188ef4863` and
`200e7643-195d-4e38-9618-3aff31b984f1`. Before and after, both Actions point to
external `ExternalTarget.ModTransform`
`b8aa72c2-32b6-481f-bcd7-559415dbb9e0`. Result:
`EXTERNAL_COMPONENT_REFERENCE_PRESERVED`, confirmed by this Oracle.

The graph classifies an outgoing Component reference by resolving the target GUID
through the Component index and comparing its owner with the selected clone-source
set. A target owned by the source is internal; a target owned elsewhere is external.
The source-to-duplicate Component mapping is used to verify observed remapping, not
to assign the classification. Property names and global GUID byte searches are not
used.

In both pairs, Action rid and RefId stay `1000`, order and list length stay one,
`m_prefabs` stays empty, and the complete source/duplicate Action payload is
byte-identical. ManagedReference bytes therefore preserve the Transform identity
rather than replacing it. This does not establish a separate GameObject-reference
serialization kind: the shared GameObject/Transform GUID text is insufficient.

## Hard Gate audit

| Gate | Result | Evidence / blocker |
|---:|---|---|
| 1. GameObject GUID structurally located | PASS | exact object-index `str8` spans |
| 2. Component GUID structurally located | PASS | exact component-index `str8` spans |
| 3. Component owner recovered | PASS | preorder association |
| 4. parent/child recovered | PASS | recursive ordered hierarchy |
| 5. insertion/order format confirmed | **PASS** | middle-child duplicate appends to same parent's list; following sibling does not shift |
| 6. supported reference surface distinguished | **PASS (revised scope)** | Transform/Component behavior is Oracle-proven; no independent GameObject-reference surface was observed, and any future unallowlisted reference kind fails closed |
| 7. Ctrl+D validated by real fixture | **PASS** | replacement 01/03/04 are valid, SHA-pinned, read-only regression fixtures |
| 8. supported candidates contain no relevant UNKNOWN | **PASS** | narrow parented standard-transform-only leaf subset has no unknown refs/components |
| 9. inspector byte-for-byte read-only | PASS | SHA regression |
| 10. repo fixture SHA unchanged | PASS | session fixture guard |
| 11. Joker 21 automation read-only | PASS | live Mod was inspected and hashed only |
| 12. Stage 1–9 tests pass | PASS | full pytest verification |

Result after the Stage 10A.8 scope audit: **Stage 10A Hard Gate passes for the
strict v0.6b supported surface.** This is eligibility only; Stage 10B has not been
implemented and no Writer is exposed.

## Minimum next experiment

An independently serialized GameObject reference was not found in the supported
surface, so it is not a prerequisite for the first narrow Writer. If one is found
later, it is a new unsupported reference kind and immediately makes the candidate
unsafe until dedicated self/external/incoming Oracles establish its policy.

## Reference Surface Audit (Stage 10A.8)

The audit covered `src/`, `tests/`, all documentation, the approved repository
fixture, the live Joker 21 Scene, and all ten Scene files currently present in the
active Oracle directory. The older Stage 10A.5 `02_parent_leaf` files are no longer
present there, so their prior structural findings were retained from the pinned
historical record rather than rescanned.

For the approved Joker 21 fixture and live Scene, the graph contains 369
property/Action reference-like occurrences: 183 `PrefabReference`, one
`AudioReference`, 170 known-but-unresolved `ModProp` property occurrences, 11
target flags, three empty selector-list fields, and one effect index. There are no
`TransformReference` items and no independent `GameObjectReference` items in Joker
21. Identity, Component membership, and 101 hierarchy edges are tracked separately
and are not counted as property reference envelopes.

Across 05/06 before and after, ten concrete Transform reference occurrences were
found. Seven use `(m_FileID, m_PathID) = (-71472, 0)` and three use `(-81578, 0)`;
all ten resolve structurally to `ModTransform`. Joker 21's one audio envelope uses
`(107498, 0)`. Prefab references use their richer embedded asset structure rather
than this FileID/PathID envelope. Because two FileID values resolve to Transform,
FileID alone is not treated as a target-type discriminator.

### GameObjectReference search

No parser schema, Component schema, observed Action field, Joker 21 occurrence, or
active Oracle occurrence supplies an independently typed Scene GameObject reference.
The only Editor-confirmed concrete Scene picker is `Spawn Prefab -> Targets ->
Advanced -> Transforms to Spawn Prefabs At`, which serializes a `ModTransform`.
No documentation, test, comment, or observed payload establishes a separate
GameObject picker. Conclusion:
`NO_INDEPENDENT_GAMEOBJECT_REFERENCE_OBSERVED`. This does not prove such a format
cannot exist in another Mod, PMH version, Component, or future Editor release.

### Component and Action schema audit

The live/fixture Component set is `ModTransform`, `ModPlayerSpawn`,
`ModBoxCollider`, `ModProp`, `ModLight`, `ModText`, and `ModTrigger`; active
Oracles additionally contain unsupported `ModMeshCollider`. Ordinary names such as
spawn, parent, owner, or target do not establish reference semantics. No registered
Component property is a confirmed outgoing Scene reference. `guid` fields are
Component identity. `ModProp.prop` (85 occurrences) and `customMaterials` (85)
remain unresolved reference-bearing properties; `ModMeshCollider.mesh` remains an
unsupported unknown/asset-like candidate. None is decoded as a GameObject target.

Only four managed-reference Action classes were observed:
`SpawnPrefabAction` (8 instances in Joker 21), `SpawnEffectAction` (1),
`PlaySoundAction` (1), and `KillAction` (1). No `MatchTransform`, `SetActive`,
movement, destination, or other Action type was found in the scanned payloads.
`SpawnPrefabAction.m_parentToTarget` is an observed boolean, while its position,
rotation, and offsets are value fields; none is a concrete Scene reference.
Target flags and the empty selector lists on the other classes remain semantic
unknowns and are rejected.

### Reference surface matrix

| Kind | Serialization proven | Editor constructible | Observed fixture | Duplication semantics | v0.6b need/policy |
|---|---|---|---|---|---|
| `TransformReference` | yes | yes | 05/06 | preserve exact, internal and external | supported only in otherwise admitted shapes |
| other `ComponentReference` | no | not found | no | unknown | reject |
| `GameObjectReference` | no | `NO KNOWN GAMEOBJECT PICKER` | no | unknown | outside supported surface; detect and reject |
| `AudioReference` | inferred asset kind | Editor origin observed, construction not audited | Joker 21 | duplication unknown | outside initial clone subset |
| `PrefabReference` | yes | yes | Joker 21 | populated duplication unknown | exact-template policy only; outside initial clone subset |
| hierarchy reference | yes | yes through hierarchy | all Scenes | append/rebuild proven | required |
| Action rid/RefId | yes, Action-graph link | generated by Action graph | Joker 21 and 05/06 | populated Action copied unchanged in 05/06 | not a Scene object reference |
| semantic target flags | integer shape only | yes through Action UI | Joker 21 and 05/06 | semantics unknown | reject |

### Stage 10B reference policy

Policy is per serialization kind, never the global rule “internal remap, external
preserve”:

| Surface | v0.6b policy |
|---|---|
| GameObject/Component identity | regenerate according to the identity Oracle |
| hierarchy ownership | rebuild duplicate membership and append using the hierarchy Oracle |
| `TransformReference` | preserve exact serialized bytes, whether internal or external |
| asset/prefab reference | preserve exact bytes only within an otherwise supported template; populated duplication remains outside the initial subset |
| target flags/selectors and unresolved Component properties | reject |
| any newly decoded/unallowlisted Scene reference kind | return `UNSAFE_UNKNOWN_REFERENCE_TYPE` |

Gate 6 is therefore revised to the references that can occur in the explicitly
supported v0.6b subset. Absence of evidence does not become support: an unseen kind
is excluded by the detection guard and must obtain its own Oracle before admission.

## Stage 10C Component boundary

The common PMH Component index/payload envelope does not create a common reference
policy. Oracle 07 now admits only the exact parented-leaf sequence `ModTransform +
ModBoxCollider + ModTrigger`, with all four Trigger Action graphs empty. The graph
shows only identity, hierarchy, and Component-membership edges for that source and
duplicate; no related UNKNOWN edge exists.

`ModBoxCollider` is approved as a value-only Component on its exact current schema.
Empty `ModTrigger` payload copying is approved on the Oracle 07 shape. Populated
Trigger targets, `ModMeshCollider.mesh`, `ModProp` reference-bearing fields, assets,
and new reference kinds remain outside the supported surface and fail closed.
