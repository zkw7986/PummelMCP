# Minigame planner

`plan_minigame` validates MinigameSpec, audits capabilities and exact template
fingerprints/assets, expands a typed GameplaySpec, and invokes the Stage 13
read-only composition planner. Its hash binds the spec, Scene, Composer plan,
templates, assets, and runtime expectations.

`build_minigame` accepts only the matching plan hash and delegates all Scene
work to the Stage 13 temporary-Mod transaction. It adds no PMH writer. A sidecar
records provenance and rejects the same spec a second time. Unknown Components,
Actions, references, assets, arbitrary operations, and cross-Scene templates
remain unavailable.

Phase 4 keeps the planner schema at v0.1 and adds archetype-specific element and
layout validation. Its three new trigger recipes expand only to fixed Action
sequences registered by Stage 13. The plan hash covers those recipe choices and
the Composer verifies every exact Action-template hash before any temporary-Mod
write begins.

## Phase 5 natural-language workflow

A request such as “Make a two-player score-pad arena with three pads, feedback,
and a manual playtest report” maps to `SCORE_PAD_CHALLENGE`. The agent lists
archetypes and registered templates, creates the strict MinigameSpec, calls
`plan_minigame`, confirms the hash-bound plan, calls `build_minigame`, then uses
the unchanged Stage 14 playtest session pipeline. Build success is never treated
as runtime success: without a manual game start and observable appended logs,
runtime assertions remain `UNKNOWN` or `NOT_OBSERVABLE`.
