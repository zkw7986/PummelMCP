# Gameplay recipes

The v0.1 registry exposes nine typed recipes. The original six are
`BUTTON_SPAWN_PREFAB`, `SPAWN_POINT_SET`, `TEXT_SIGN`, `LIGHT_SET`,
`PROP_LAYOUT`, and `STATIC_TEMPLATE_BLOCK`. Phase 4 adds `SCORE_PAD`,
`DEATH_PENALTY_ZONE`, and `FEEDBACK_CHECKPOINT`. Every recipe requires an
explicit registered and currently valid object template and inherits its
destination policy.

Trigger recipes add a fixed, registry-owned Action sequence to one of the four
existing Trigger event properties:

| Recipe | Exact Action sequence |
|---|---|
| `BUTTON_SPAWN_PREFAB` | `SpawnPrefabAction` |
| `SCORE_PAD` | `ChangeScoreAction`, `SpawnEffectAction`, `PlaySoundAction` |
| `DEATH_PENALTY_ZONE` | `ChangeScoreAction`, `KillAction` |
| `FEEDBACK_CHECKPOINT` | `ShowMessageAction`, `SpawnEffectAction`, `PlaySoundAction` |

Each Action comes from the Phase 3 validated same-Scene template catalog. A
class must have exactly one distinct template hash or planning fails closed.
Arbitrary Actions, caller-supplied Action sequences, raw fields, and references
remain rejected. Text creation preserves template text. Prop remains limited
to its empty-material Stage 12 candidate.

Official Editor acceptance currently covers `SPAWN_POINT_SET` and safe Button
leaves through `STATIC_TEMPLATE_BLOCK` in a five-object composition.
`BUTTON_SPAWN_PREFAB` stays blocked until a same-Scene validated
SpawnPrefabAction template is available. Light, Text, Prop, and subtree
recipes remain capability-dependent and require a registered valid template
in the target Scene.

Stage 13D provides the same-Scene template mechanism. Phase 4 reuses it without
changing RID allocation, hash guards, managed-reference validation, or atomic
commit. `OnEnterActions` is the event emitted by all current minigame
archetypes; the lower-level recipes retain the four-event allowlist.
