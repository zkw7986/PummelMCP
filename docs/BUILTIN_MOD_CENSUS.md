# Built-in minigame census

Phase 1 read-only census of the minigame content shipped with Pummel Party
(game build dated 2025-12-23). The corpus is deliberately limited to the five
`InbuiltMods` scenes and the six `WorkshopTemplates/Minigames` scenes. Board
templates are excluded.

Evidence in this document is **OBSERVED** unless it is explicitly
cross-checked against [`ASSEMBLY_TRUTH.md`](ASSEMBLY_TRUTH.md). Observation of
a complete serialized instance is template evidence, not Writer approval.

The local, gitignored details are in `research/census_output/census.json` and
`research/census_output/asset_catalog.json`. They can be regenerated with the
read-only `research/census.py` and `research/asset_catalog.py` scripts.

## Scene coverage

All 11 scenes parsed to EOF and passed the repository's structural validator.
No scene or Action payload failed to parse.

| Scene | Objects | Components | Actions |
|---|---:|---:|---:|
| InbuiltMods / Aftershock Arena | 170 | 329 | 14 |
| InbuiltMods / Foggy Fall | 238 | 490 | 41 |
| InbuiltMods / House Hunting | 479 | 925 | 0 |
| InbuiltMods / Pummel Prison | 563 | 1,130 | 0 |
| InbuiltMods / Pummel Punch | 80 | 154 | 2 |
| Templates / Minimal Third Person | 34 | 65 | 0 |
| Templates / Minimal Top Down | 24 | 45 | 0 |
| Templates / Simple Arena | 35 | 72 | 0 |
| Templates / Third Person Obstacle Course | 70 | 128 | 16 |
| Templates / Third Person Shooter | 130 | 239 | 1 |
| Templates / Top Down Shooter | 43 | 79 | 0 |

## Action distribution and template candidates

The corpus contains 74 complete Action instances in eight classes. The field
union for every class exactly matches the inherited plus class-owned fields in
`ASSEMBLY_TRUTH.md`: no unexpected or missing serialized field was found.

| Action class | Instances | Scene sources | Complete candidates |
|---|---:|---:|---:|
| `SpawnEffectAction` | 24 | 2 | 24 |
| `PositionAction` | 20 | 3 | 20 |
| `ShowMessageAction` | 10 | 1 | 10 |
| `ChangeScoreAction` | 7 | 4 | 7 |
| `RotationAction` | 4 | 1 | 4 |
| `PlaySoundAction` | 4 | 1 | 4 |
| `KillAction` | 3 | 3 | 3 |
| `SetPlacementAction` | 2 | 2 | 2 |

The two result-oriented classes have direct official examples:

- `ChangeScoreAction`: seven instances, including subtract-one-on-death and
  add-score patterns.
- `SetPlacementAction`: two instances, including the official Third Person
  Obstacle Course victory trigger.

Observed field unions:

| Class | Serialized fields |
|---|---|
| `KillAction` | `m_type`, `m_version`, `m_targetFlags`, `m_targets` |
| `ChangeScoreAction` | base target fields, `m_operation`, `m_value` |
| `PositionAction` | base target fields, `m_space`, `m_operation`, `m_position` |
| `RotationAction` | base target fields, `m_space`, `m_operation`, `m_rotation` |
| `PlaySoundAction` | base target fields, `m_clip`, `m_volume` |
| `ShowMessageAction` | base target fields, `m_messageTarget`, `m_message`, `m_duration` |
| `SpawnEffectAction` | base target fields, `m_effectType` |
| `SetPlacementAction` | `m_type`, `m_version`, `m_targetFlags`, `m_targets` |

Here “base target fields” means `m_type`, `m_version`, `m_targetFlags`, and
`m_targets`. Individual old-format instances may omit `m_version`; the union
across real instances contains it and matches the assembly definition.

## Framing and target evidence

The 29 `ModTrigger` components contribute 116 fixed event payloads: one each
for `OnHitActions`, `OnEnterActions`, `OnExitActions`, and `OnStayActions`.
Every payload is fully consumed.

Two managed-reference layouts are present:

| Reference version | Payloads | Representation |
|---:|---:|---|
| 2 | 80 | `RefIds` metadata plus framed reference tail segments |
| 1 | 36 | legacy inline managed-reference map keyed by zero-padded id |

Nine non-empty v1 payloads produce the generic parser's expected
tail-count/`RefIds` warning. The census resolves their inline reference map;
all 19 Actions in those payloads were classified. This is read-only legacy
format evidence and does not make v1 graphs writable.

All fixed `ModTrigger` envelopes have trigger `m_type=0`. No `ModLogic`
trigger arrays occur in the minigame-only corpus, so this phase makes no scene
observation claim for `StartTrigger`, `TimerTrigger`, or the round triggers.
Their class definitions remain documented at ASSEMBLY_TRUTH level.

Observed target flags:

| `m_targetFlags` | Meaning from assembly | Instances |
|---:|---|---:|
| 1 | Source | 61 |
| 2 | Receiver | 13 |

No Advanced-target instance appears after Board templates are excluded.

## Built-in minigame assets

The same scope contains 70 parseable `.pmeta` records and no metadata parse
failures or `.pfab`/`.pmat` files missing metadata.

| Asset type | Metadata records |
|---|---:|
| Prefab (`.pfab`) | 50 |
| Material (`.pmat`) | 11 |
| Preview JPEG | 7 |
| Preview PNG | 2 |

There are 59 unique non-empty GUIDs. Eleven GUIDs occur in two source Mods;
the catalog therefore stores a list of records per GUID instead of silently
choosing one source. Representative assets include `MachineGun`, `HealthKit`,
`Teleporter`, `Gravity Powerup`, and `FX_Fire`.

## Boundary

The candidate list records complete, parseable source instances only. Phase 3
subsequently registered all eight observed classes for exact source-template
creation: all 55 safe v2 instances pass their class schemas, while 19 legacy v1
instances remain read-only. This does not weaken the existing hash, rid,
reference, dependency, reparse, or atomic-replacement guards, and it does not
make individual Action fields generally writable.
