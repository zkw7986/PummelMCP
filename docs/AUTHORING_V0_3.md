# Experimental authored minigames v0.3

This is a new-file compiler, separate from legacy in-place writers. It composes
source-backed component defaults into a newly serialized hierarchy, creates new
Prefab files/metadata, and compiles explicit action lists. It does not claim
official Editor or runtime approval. Source artwork and unspecified component
defaults are reused; arbitrary behavior and arbitrary artwork are not generated.

## Workflow

When a user asks to make a game, first ask for their existing `WorkshopMods`
absolute path and explicit permission to create a new Mod folder and files
there. Wait for both before any game-specific inspection, planning, template
listing, or build call. Authoring and construction tools enforce this grant in
the current MCP session. After authorization, use `get_authoring_capabilities`,
`audit_minigame_archive`, and `get_authoring_donors`, then pass a spec to
`plan_authored_minigame` and its hash to `build_authored_minigame`. For the
bundled Joker-style recipe use
`generate_card_minigame(archive_path, options, dry_run=false)`. `dry_run=true`
returns the complete proposed spec and plan without writing.

Configure `PUMMELMCP_AUTHORING_READ_ROOTS` as a JSON array of read-only source
directories. The server instructions require collecting the user's absolute
existing `WorkshopMods` directory and explicit permission to create a new Mod
there, then calling `authorize_workshop_directory`. No game authoring, template
listing, inspection, planning, or build tool is used before both arrive. This
per-session grant selects the direct output root. The output may be the same
WorkshopMods folder that contains read-only donor Mods; each generated
destination must be a new, distinct child and is never overwritten. The source format is
one bounded ZIP with one `Data/MainScene.scene`, config JSON, optional Prefabs and
assets. ZIP contents are data; no embedded instructions or programs are run.

## Spec

Required keys: `version: "0.3"`, `output_mod`, `title`, `description`,
`min_players`, `max_players`, `settings`, `scene`, `prefabs`.
Optional: `player_events`, `player_tick_seconds`.

`output_mod`, node `id`, and Prefab `id` must start with an ASCII letter and
contain only ASCII letters/digits/underscore/hyphen, at most 64 characters.
`name` is the displayed object name. No existing output is overwritten.

`settings` uses the existing minigame config API. Explicit `rounds`,
`round_duration_seconds`, `end_conditions` (including `timer`), and
`placement_condition` are required. Timer is a guaranteed configuration-level
fallback, not proof of a correct ranking mechanic.

`scene` is an array of nodes. Each node has `id`, `components`, and optionally
`parent`, `name`, `position`, `rotation`, `scale`, `active`, `layer`, `tag`.
Transforms are xyz objects; scales must be positive. Transform is automatically
created. Parent is another node's local symbol. Cycles/disconnected cycles fail.

Each component has:

```json
{
  "type": "ModTrigger",
  "donor": {"file": "Data/MainScene.scene", "object": "unique-object-guid-from-catalog"},
  "properties": {"Size": {"x": 1, "y": 1, "z": 1}},
  "events": {
    "OnHitActions": [{"type": "SpawnPrefabAction", "target": "source", "prefabs": ["CardA", "Joker", "Joker"],
      "fields": {"m_spawnAtPosition": false, "m_parentToTarget": false}}]
  }
}
```

The example is one component, not a complete spec. A full working spec is saved
with each build as `authoring-spec.json`.

Component types: ModProp, ModBoxCollider, ModPlayerSpawn, ModTrigger, ModLight,
ModText, ModItem, ModLogic. Properties are limited to existing writable scalar/vector
schemas. New-file ModText.Text is also supported using its observed UTF-8 str8
encoding (maximum 255 UTF-8 bytes); the legacy in-place Text writer stays closed.
ModItem requires `item_visual`, the ID of an explicitly declared Prefab.
Raw GUID/property bytes cannot be supplied. Components are enabled on creation.

`prefabs` is an array of `{id, nodes}` with exactly one root per Prefab; it uses
the same node/component grammar. All symbols are allocated before compilation,
so forward bindings are allowed. Unknown bindings and dependency cycles fail.
The source's Prefab gameplay files are never copied wholesale.

## Events and actions

ModTrigger events: OnHitActions, OnEnterActions, OnExitActions, OnStayActions.
The compiler clears all undeclared events and sets enable flags from the declared
lists. The engine suppresses collision events in hit mode, so hit+collision
events on one trigger are rejected. Separate nodes can provide both behaviors.

ModItem events: OnPickupTrigger, OnDropTrigger, OnUseTrigger,
OnWhileHeldTrigger. Each list is replaced completely; nothing is appended to
unknown old logic.

Player events: hitTrigger, weaponHitTrigger, tickTrigger, onDeathTrigger,
onRespawnTrigger, onFirstSpawnTrigger, onRoundEndTrigger. `player_events` maps
these names to action arrays. All are cleared first, then declared actions are
merged into one global managed-reference table with non-overlapping IDs.
`player_tick_seconds` sets a constant tick interval (0.1..3600 seconds).

Actions use native class names, typed `fields`, and explicit `target`:
source, receiver, both, none. WaitAction has no target. A matching source-backed
class template with the confirmed class/type ID must exist in the archive.
`get_authoring_capabilities.actions` returns the field allowlist and types.

- ChangeScoreAction / ChangeHealthAction: operation and integer value.
- KillAction / SetPlacementAction: target only.
- ShowMessageAction: message, duration, message target.
- WaitAction: constant or ranged interval.
- PositionAction: world/local space, Set/Add/etc operation, destination vector;
  use Source targeting and World + Set for a player teleport.
- SetPlayerVisualAction: bind `prefab` to a Prefab declared in this Mod, or set
  `prefab: null` to restore the native player appearance. The action retains the
  source template's movement/look child settings unless explicitly overridden.
- GiveMinigameItemAction: bind `prefab` to a local Prefab with ModItem on its
  root. A matching observed native type-1056 template is required.
- ScaleAction: operation, scale vector.
- ModifyPlayerAction: movement multiplier or gravity.
- StunPlayerAction: duration.
- SpawnEffectAction: one of the five observed built-in effects.
- PlaySoundAction: volume; source-backed clip retained.
- ChangeVelocityAction: speed/custom direction, native operation, world/local
  space, local target (0..2), and direction (0..8). Direction 6 points from the
  receiver to the source and supports source-targeted contact push effects.
- SpawnPrefabAction: new Prefab pool, placement/rotation/offset/parenting parameters.

Action lists contain at most 64 entries; pools at most 128 slots. Repeating a
Prefab symbol intentionally changes its probability under the game's uniform
random slot selection. This is sampling with replacement, not a finite deck.
SetActiveAction supports `target: "objects"`, `targets: ["local_node_id"]`, and
`fields: {"m_newState": 0}` (enable) or 1 (disable). Targets bind to generated
Transform GUIDs within the same scene/prefab scope. Only the observed modern
GUID + FileID/PathID reference envelope is accepted; legacy instanceID references
and other explicit component targets are rejected.

ModLogic requires `logic_timer: {"interval_seconds": 2, "actions": [...]}`.
Only one version-2 Timer trigger with the observed type-96 binary envelope is
supported. ModLogic and ModTrigger cannot share an object. These extensions are
source-backed and statically tested, with runtime verification still pending.

## Evidence and transactions

PMH hierarchy, index and payload sections are rebuilt using observed v1 framing.
Action JSON and binary tail frames are generated from the same data and parsed
back. Object/component identities are deterministic UUIDv5 values namespaced by
the archive hash and full spec hash. New Mod names participate in that hash.

Local visual/audio assets and metadata are copied, with their owned GUIDs renewed.
Source-backed external/built-in references remain; engine-side resource loading
is not verified. Workshop publishing IDs and source backup files are excluded.

Planning compiles all generated bytes and validates settings in memory; the hash
binds archive, full spec, output and file contents. Building regenerates the plan,
rejects staleness, writes a fresh staging directory, rechecks the donor hash, and
renames the directory only on success. Failed builds clean their staging area.

Successful results are `BUILD_PASS`, `AUTHORED_UNVERIFIED`, `NOT_RUN`.
The output includes the spec and a file hash report. There is no automatic game
launch, upload, Workshop install, Editor save, or multiplayer validation.

## Testing

`tests/test_authoring.py` contains portable framing/path/encoder tests and
read-only integration tests against the local Joker archive. The latter skip
when the local source is unavailable; they do not silently claim portable
runtime coverage. `tools/verify_authoring_delivery.py` exercises the installed
configuration through a real stdio client and produces a new deliverable once;
reruns refuse to overwrite the existing output.
