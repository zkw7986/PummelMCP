# One-sentence complete minigame roadmap

Update 2026-09-25: the independent experimental v0.3 authoring compiler now
constructs new scenes, component compositions, complete action sequences, new
Prefabs, symbolic bindings and player events. The one-call card-arena recipe is
implemented. See [the current boundary report](JOKER_AUTHORING_BOUNDARY_ZH.md)
and [v0.3 contract](AUTHORING_V0_3.md). This extends the historical milestones
below without changing their legacy writer gates. The user explicitly deferred
official Editor, gameplay and multiplayer verification to manual checking.

The target is: a short natural-language request produces a Mod that opens in the
official Editor, plays with the requested mechanics and player settings, reaches
an enforced ending, and yields a verifiable winner. A generated Scene that merely
parses is not sufficient.

## Milestone 1 — minigame configuration

**Deliverable:** safe read/plan/apply MCP tools for the existing Mod's
`Data/ModSettings.json` and `Data/MinigameDefinitionData.json`. Expose the
controller and player settings needed for rounds, timer, points, remaining-player
ending, placement, movement, jumping, punching, respawn, and health, plus title,
description and player-count limits.

**Acceptance:** accepted values match the game's enum numerics and Editor clamps;
only requested JSON value tokens change; all other bytes remain unchanged;
plan hashes bind the Scene, source file and updates; stale plans fail; each real
write creates an exact backup and atomically replaces one file; malformed JSON,
unknown fields, conflicting ending/respawn rules, and paths outside the MCP root
fail closed. A fixture-based test and an official Editor save/play check confirm
the settings load as intended.

**Current implementation:** `get_minigame_config`,
`plan_minigame_config_update`, and `apply_minigame_config_update` implement the
file-level contract. `settings` and `details` are separate, one-file transactions.
The official Editor check remains outstanding. The original Stage 15
`build_minigame` path still reports `FULL_MINIGAME` as blocked; the separate
`build_minigame_v2` path now incorporates these settings in a staged Mod build.

## Milestone 2 — configurable gameplay logic

**Deliverable:** extend MinigameSpec from fixed archetypes to typed rules: event,
target, condition, Action sequence and bounded parameters. Cover collectible
score, damage/health, elimination, placement, timed triggers and win conditions
first. Provide validated Action-field writers and safe object/component-reference
construction for only confirmed serialized shapes; keep unknown classes and
references read-only. Build the settings and Scene from one hash-bound plan, with
consistent rollback or a staged Mod transaction.

**Acceptance:** two requests that differ in score amount, damage, timing or
target produce correspondingly different playable behavior; the plan reports
unimplemented rules instead of substituting instructional text. Tests prove
unrelated Action bytes, references and Scene objects are preserved, and official
Editor/play checks confirm events execute on the intended player.

**Current implementation:** `plan_minigame_v2` and `build_minigame_v2` accept
the typed [v0.2 format](MINIGAME_SPEC_V0_2.md). The planner rejects rules for
which a suitable template or writer is unavailable. The builder clones the
source Mod into a temporary directory, constructs the Scene, writes settings,
validates both, and publishes a new Mod directory; it never edits the source
Mod. Fixture tests verify distinct score values, health changes, timed stay
events, placement Actions, plan staleness and failed-build cleanup. These
checks establish serialized output, not actual gameplay behavior. Official
Editor and play checks are still needed to confirm the collision event target,
score and winner at runtime. Arbitrary conditions, arbitrary references,
new Prefab assets, and natural-language-to-spec translation remain open.

## Milestone 3 — Prefab and reusable object construction

**Deliverable:** evidence-backed `.pfab` parsing and writing, creation/editing of
Prefab assets and instances, component membership changes, and references for
Item, Spawner, Weapon and similar gameplay objects. Start with a small approved
Prefab grammar and grow it through Editor before/after fixtures. Include asset
metadata and dependency management.

**Acceptance:** from a clean official minigame template, create a collectible
visual Prefab, an Item with scoring behavior, multiple Prefab instances or a
Spawner, and a level using built-in props. Open, save and play it in the official
Editor; changing the Prefab updates every instance. Unknown asset and reference
forms fail closed.

**Current implementation:** all 58 shipped `.pfab` samples parse to the end
under the observed one-root PMH grammar. The new
[`plan_prefab_pack` / `build_prefab_pack` path](PREFAB_PACK_V0_1.md) clones
approved Prefab files and `.pmeta` records into a staged new Mod, renews asset
and internal object GUIDs, remaps dependencies between cloned Prefabs, permits
bounded root Transform edits, add a template-grounded pickup score Action to a
cloned Item, and retarget an existing `ModSpawner`'s Prefab reference. A Top Down Shooter
fixture verifies a Visual/Item pair, pickup score and four-point spawner, source preservation,
stale-plan rejection and failed-build cleanup. This is a **partial milestone**:
Prefab component membership editing, creating
new spawner/instance structures, propagation after editing an existing Prefab,
and official Editor/play verification remain outstanding. Existing built-in prop
references inside a cloned Visual are retained from the source template.

After these three milestones, automatic runtime launch, interaction, outcome
observation and repair remain a separate verification milestone. Until that is
available, `BUILD_PASS` must not be presented as a proven playable result.
