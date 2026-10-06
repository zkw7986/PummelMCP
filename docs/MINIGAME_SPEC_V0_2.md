# MinigameSpec v0.2

`plan_minigame_v2(scene_path, spec)` checks an existing Mod's `Data/*.scene`,
settings JSON, object templates, and Action templates. It returns a plan hash.
Pass that hash to `build_minigame_v2(scene_path, spec, expected_plan_sha256)`.
The builder creates a **new Mod directory** under `PUMMELMCP_ALLOWED_ROOT`.
The source Mod is unchanged. The result is marked `CONFIGURED_UNVERIFIED`
until tested in the official Editor and game.

Example (the source Scene must already contain registered `button` and `spawn`
object templates and an unambiguous `ChangeScoreAction` template):

```json
{
  "version": "0.2",
  "scene": "C:/Mods/Source/Data/MainScene.scene",
  "output_mod": "ScoreArena",
  "title": "Score Arena",
  "description": "Step on a pad to score.",
  "min_players": 2,
  "max_players": 8,
  "settings": {
    "rounds": 2,
    "end_conditions": ["obtain_points"],
    "points_to_win": 10,
    "placement_condition": "most_points",
    "movement_speed": 8
  },
  "objects": [
    {
      "id": "score_pad",
      "role": "trigger",
      "template_id": "button",
      "position": {"x": 0, "y": 1, "z": 4},
      "rule": {
        "event_property": "OnEnterActions",
        "condition": "always",
        "actions": [
          {"type": "CHANGE_SCORE", "target": "triggering_player", "operation": "add", "value": 3}
        ]
      }
    },
    {
      "id": "spawn_1",
      "role": "spawn",
      "template_id": "spawn",
      "position": {"x": 0, "y": 1, "z": 0}
    }
  ]
}
```

## Rules and limits

The `objects` list requires at least one `trigger` and one `spawn`; it accepts
2–64 objects. Other roles are `text`, `light`, `prop`, and `static`. All objects
must name an existing registered template with the required component shape.
A trigger rule accepts `OnHitActions`, `OnEnterActions`, `OnExitActions`, or
`OnStayActions`. `condition` is `always`, `once_per_player`, or `once_global`.
`OnStayActions` additionally requires `interval_seconds` from 0.1 to 60.
Each rule has 1–16 Actions.

| Action | Parameters |
| --- | --- |
| `CHANGE_SCORE`, `CHANGE_HEALTH` | `target`, `operation`, integer `value` from -1000 to 1000 |
| `KILL`, `SET_PLACEMENT` | `target` |
| `SHOW_MESSAGE` | `target`, `message` (1–256 characters), `duration_seconds` (0.5–1200) |

`target` is currently only `triggering_player`. The observed game code creates
the collision event with the player as `Source` and the trigger as `Receiver`;
the builder writes the Source target flag. Operations are `set`, `add`,
`subtract`, `multiply`, and `divide` (nonzero divisor).

Settings use the same allowlist and ranges as the milestone-one
`plan_minigame_config_update` tool. A round count, placement condition, and at
least one ending are required. `obtain_points` requires `points_to_win` and a
positive score Action. `timer` requires `round_duration_seconds`.
`remaining_players_alive` requires `players_alive_to_end`, a `KILL` Action, and
`respawn_enabled: false`. `finish_minigame` requires `finish_order` placement
and a `SET_PLACEMENT` Action; its trigger cannot be `once_global`.

Unknown fields, conditions, Action types, targets, and reference shapes are
rejected. The builder does not create Action classes without a matching source
template, custom object references, Prefab files, or scripts. The v0.2 spec is
structured input; translating arbitrary one-sentence requests into this spec
and proving runtime behavior are separate work.
