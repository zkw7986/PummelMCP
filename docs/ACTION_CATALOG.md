# Observed Action catalog

The game assembly's own class definitions, enum numerics, and target
semantics for every Action class are documented separately in
[`ASSEMBLY_TRUTH.md`](ASSEMBLY_TRUTH.md) (Stage 16, evidence level
ASSEMBLY_TRUTH).

The Phase 1 minigame-only corpus census is documented in
[`BUILTIN_MOD_CENSUS.md`](BUILTIN_MOD_CENSUS.md). It adds read-only scene
evidence for 74 complete Action instances across eight classes; Board
templates are intentionally excluded.

The first table is limited to the approved Joker 21 `MainScene.scene`. Counts are
Action entries, not distinct byte payloads. Class discovery is generic and
unknown classes remain inspectable. “Partial typed view” means the inspector
identifies namespace/class and returns bounded structured fields while core
retains every original field; it does not claim full game semantics.

| Namespace | Class | Seen | Event properties | Inspector | Writer | Status |
|---|---|---:|---|---|---|---|
| `ModSystem.Logic` | `SpawnPrefabAction` | 8 | `OnHitActions` | supported | four scalar/vector fields plus existing-item prefab template replacement | **CONFIRMED** |
| `ModSystem.Logic` | `SpawnEffectAction` | 1 | `OnHitActions` | supported | Phase 3 validated-template creation; fields read-only | **CONFIRMED** |
| `ModSystem.Logic` | `PlaySoundAction` | 1 | `OnHitActions` | supported | Phase 3 validated-template creation; fields read-only | **CONFIRMED** |
| `ModSystem.Logic` | `KillAction` | 1 | `OnEnterActions` | supported | Phase 3 validated-template creation; fields read-only | **CONFIRMED** |
| `ModSystem.Logic` | `SetPlayerVisualAction` | whitebox authoring scene | `OnEnterActions`, `OnStayActions` | supported | Source-backed template creation; visual reference retained from template | **ASSEMBLY_TRUTH** |

## Observed serialized fields

| Class | Field names preserved from real data |
|---|---|
| `SpawnPrefabAction` | `m_type`, `m_version`, `m_targetFlags`, `m_targets`, `m_prefabs`, `m_spawnAtPosition`, `m_position`, `m_rotation`, `m_parentToTarget`, `m_targetPositionOffset`, `m_targetRotationOffset` |
| `SpawnEffectAction` | `m_type`, `m_version`, `m_targetFlags`, `m_targets`, `m_effectType` |
| `PlaySoundAction` | `m_type`, `m_version`, `m_targetFlags`, `m_targets`, `m_clip`, `m_volume` |
| `KillAction` | `m_type`, `m_version`, `m_targetFlags`, `m_targets` |

## Phase 1 built-in minigame observations

The shipped minigame corpus adds scene observations for four classes absent
from the Joker 21 table and expands the sample counts for all four original
classes. Phase 3 promotes safe v2 instances to schema-validated creation
templates while leaving individual fields read-only:

| Class | Complete instances | Writer status |
|---|---:|---|
| `SpawnEffectAction` | 24 | validated-template creation; fields read-only |
| `PositionAction` | 20 | validated-template creation; `m_position` can be set |
| `ShowMessageAction` | 10 | validated-template creation; fields read-only |
| `ChangeScoreAction` | 7 | validated-template creation; fields read-only |
| `RotationAction` | 4 | validated-template creation; fields read-only |
| `PlaySoundAction` | 4 | validated-template creation; fields read-only |
| `KillAction` | 3 | validated-template creation; fields read-only |
| `SetPlacementAction` | 2 | validated-template creation; fields read-only |

All eight observed field unions match `ASSEMBLY_TRUTH.md`. The complete
per-instance source records, v1/v2 framing counts, target-flag distribution,
and minigame asset summary live in `BUILTIN_MOD_CENSUS.md`. Of the 74
instances, all 55 safe v2 templates pass Phase 3 schemas; 19 legacy v1 Actions
remain read-only evidence.

For `SpawnPrefabAction`, the serialized names align with the user-supplied
official semantic labels Prefabs, Parent To Target, Target Position Offset,
Target Rotation Offset, Spawn At Position, Position, and Rotation. This is
semantic assistance, not proof of every internal field's runtime behavior.
The API therefore keeps internal names and values instead of translating or
rewriting them.

## Action schema registry

Eleven classes now have evidence-driven schemas: the original four plus
`ChangeHealthAction`, `ChangeScoreAction`, `PositionAction`, `RotationAction`,
`ShowMessageAction`, `SetPlacementAction`, and `SetPlayerVisualAction`. Each schema records the fixed type tag, exact complete
template field set, and class-specific validation rule. This makes exact
source-template creation available without making effect, target, audio,
score, message, operation, or reference fields independently writable. PositionAction's
existing `m_position` vector can be changed after template creation; its World/Local
space and Set/Add operation remain source-template values.

The read-only Workshop corpus contained 201 Actions in 13 parseable scenes:
40 Kill, 27 SpawnEffect, 16 PlaySound, 11 SpawnPrefab, and additional classes
kept generically inspectable. `.pfab` framing is not assumed to be PMH scene
framing; 93 `.pfab` files rejected the scene magic and were left untouched.

## SpawnPrefabAction field schema

| Field | Logical type | Writable | Boundary |
|---|---|---:|---|
| `m_spawnAtPosition` | strict JSON boolean | yes | existing token only |
| `m_parentToTarget` | strict JSON boolean | yes | existing token only |
| `m_position` | JSON Vector3 | yes | partial existing `x/y/z` tokens |
| `m_rotation` | JSON Vector3 | yes | partial existing `x/y/z` tokens |
| `m_prefabs` | prefab/reference list | no | prefab/reference mutation prohibited |
| `m_targets`, `m_targetFlags` | target/reference data | no | target mutation prohibited |
| offsets, type, version, unknown fields | preserved JSON | no | not in v0.4b allowlist |

Fields cannot be added when absent. Namespace, class, assembly, type tag,
`rid`, `RefIds`, and Action ordering are not field values and cannot be
changed through `set_action_field`.

No real nested ActionList was found. No support is claimed for classes not in
this table, although the generic reader will preserve and report them.

Reference-like fields on all four observed classes have a separate read-only
v0.4c registry. Their exact shapes, asset-resolution rules, and the boundary
between confirmed representation and unknown semantics are documented in
[`ACTION_REFERENCES.md`](ACTION_REFERENCES.md). This does not expand the four
writable fields listed above.

v0.4d additionally permits replacement of exactly one existing
`m_prefabs[index]` with a complete, unambiguous, already-observed and
asset-validated template. It does not make `m_prefabs` a generic writable
field and does not change the v0.4b `set_action_field` registry. See
[`PREFAB_REFERENCE_WRITER.md`](PREFAB_REFERENCE_WRITER.md).

v0.5 plus Phase 3 permits source-template creation of the eleven registered
classes and validated top-level deletion/reordering through the graph writer.
This does not make raw Action JSON, legacy v1 graphs, Board-only Actions, or
arbitrary classes writable. See
[`ACTION_GRAPH_WRITER.md`](ACTION_GRAPH_WRITER.md).
