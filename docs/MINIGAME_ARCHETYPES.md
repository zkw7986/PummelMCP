# Minigame archetypes

Phase 4 exposes four available v0.1 playable-prototype archetypes:

| Archetype | Trigger recipe | Objective boundary |
|---|---|---|
| `BUTTON_SPAWN_CHALLENGE` | `BUTTON_SPAWN_PREFAB` | instructional |
| `SCORE_PAD_CHALLENGE` | `SCORE_PAD` | score mutation enforced; no winner |
| `DEATH_PENALTY_ARENA` | `DEATH_PENALTY_ZONE` | penalty and kill enforced; no winner |
| `TRIGGER_FEEDBACK_COURSE` | `FEEDBACK_CHECKPOINT` | instructional sequence |

Each expands to configurable trigger objects, two PlayerSpawns, one Text
template, one Light template, and a ROW layout. Every Action sequence is fixed
by its recipe and must resolve to one unambiguous Phase 3 validated template
per Action class in the target Scene.

`SCORE_CONTROL`, `PLAYER_ELIMINATION`, `AUDIO`, and `EFFECT` are now reported as
`PARTIAL`: their concrete Actions can be cloned, but field synthesis and runtime
proof remain unavailable. Full minigames and the older blocked archetypes still
report round, timer, score-state, or win-condition gaps as data. The planner
never substitutes visual text for an enforced game rule.
