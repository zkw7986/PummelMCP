# MinigameSpec v0.1

MinigameSpec is the strict Codex-to-MCP contract for a playable prototype. It
contains version, title, archetype, exact Scene, gameplay root, typed objective,
two spawn positions, deterministic trigger layout, explicit template IDs,
visual positions, runtime expectations, and completion level. The original
Button archetype also requires a Prefab name. Phase 4 archetypes use
`trigger_count`, `trigger_template_id`, `trigger_origin`, `trigger_direction`,
and `trigger_spacing`; their exact objective is registry-owned.
Unknown fields, arbitrary rules, scripts, operations, RPC names, GUIDs, raw PMH,
and raw JSON are rejected. The end user supplies natural language; Codex creates
this contract after inspecting available archetypes and templates.

`PLAYABLE_PROTOTYPE` has runtime interaction but may lack enforced scoring,
rounds, and a winner. `FULL_MINIGAME` requires those capabilities and is blocked
in v0.1 rather than silently downgraded.
