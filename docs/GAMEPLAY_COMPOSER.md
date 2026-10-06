# Gameplay Composer

The Composer is an orchestration layer over existing approved writers. It adds
no PMH serializer. `plan_gameplay_composition` validates the strict spec,
resolves each Template Factory plan, applies destination gates, and returns
scene, spec, template, and plan hashes. `compose_gameplay` requires the plan
hash.

Execution copies the complete Mod to a temporary workspace, creates every
template instance there, applies local transforms and approved Action recipes,
then validates exact EOF, Scene structure, GUID uniqueness, logical mapping,
and source-template fingerprints. The real Scene receives one backup and one
atomic replacement only after all checks pass. Any intermediate exception
discards the temporary workspace and leaves the real Scene byte-identical.

Logical IDs exist only in the plan and result. They map directly to freshly
allocated GameObject GUIDs and are never written as object names. Existing
objects may be referenced as destination parents but are never configured by
the Composer.

## Official Editor verification

Stage 13A and Stage 13B were manually accepted in the official Editor using
`Stage13 Manual Verify`. One transaction created three Button leaves and two
PlayerSpawn leaves (five GameObjects and thirteen Components). The user saved
the Scene successfully. The post-save parser reported `valid=true`,
`fully_consumed=true`, and zero errors; all five logical-ID GUIDs and their
expected Component shapes remained present.

This acceptance approves the strict planner, logical mapping, multi-object
temporary-workspace transaction, source-template protection, and atomic Scene
commit for the exercised recipes. `BUTTON_SPAWN_PREFAB` remains unavailable:
the audited Mod set contains Scenes with safe empty Buttons and other Scenes
with SpawnPrefabAction templates, but no Scene containing both.

Stage 13D supersedes that limitation after an official-Editor template was
created in the same Scene. Automated integration now covers one atomic
transaction containing three `BUTTON_SPAWN_PREFAB` instances, three exact
SpawnPrefabAction templates bound to `Prefab_0`, two PlayerSpawns, one Text,
and one Light. ROW and GRID placement are deterministic and validated before
execution. The final official Editor verification passed on 2026-09-15 using
`Stage13D Manual Verify`; the saved Scene reparsed to exact EOF with zero errors.

Stage 15 uses this Composer unchanged. Minigame plans expand to its strict
GameplaySpec and retain the same all-or-nothing transaction and source gates.
