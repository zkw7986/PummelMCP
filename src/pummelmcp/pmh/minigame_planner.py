"""Stage 15: strict whole-minigame prototype planning over Stage 13."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from .errors import ConcurrentModificationError, UnsafeDuplicationError
from .gameplay_composer import compose_gameplay, plan_gameplay_composition
from .scene_identity import semantic_scene_fingerprint


CAPABILITIES = {
    "SCENE_BUILDING": "AVAILABLE",
    "PLAYER_SPAWN": "AVAILABLE",
    "BUTTON_TRIGGER": "AVAILABLE",
    "PREFAB_SPAWN": "AVAILABLE",
    "TEXT": "PARTIAL",
    "LIGHT": "AVAILABLE",
    "PROP": "PARTIAL",
    "STATIC_GEOMETRY": "AVAILABLE",
    "ROUND_CONTROL": "BLOCKED",
    "SCORE_CONTROL": "PARTIAL",
    "WIN_CONDITION": "BLOCKED",
    "LOSE_CONDITION": "NOT_IMPLEMENTED",
    "PLAYER_ELIMINATION": "PARTIAL",
    "TIMER": "BLOCKED",
    "RANDOMIZATION": "NOT_IMPLEMENTED",
    "AUDIO": "PARTIAL",
    "EFFECT": "PARTIAL",
    "RUNTIME_OBSERVATION": "PARTIAL",
}

CAPABILITY_ACTIONS = {
    "PREFAB_SPAWN": "SpawnPrefabAction",
    "SCORE_CONTROL": "ChangeScoreAction",
    "PLAYER_ELIMINATION": "KillAction",
    "AUDIO": "PlaySoundAction",
    "EFFECT": "SpawnEffectAction",
}

ARCHETYPES = {
    "BUTTON_SPAWN_CHALLENGE": {
        "availability": "AVAILABLE",
        "completion_level": "PLAYABLE_PROTOTYPE",
        "required_capabilities": [
            "SCENE_BUILDING",
            "PLAYER_SPAWN",
            "BUTTON_TRIGGER",
            "PREFAB_SPAWN",
            "TEXT",
            "LIGHT",
        ],
        "required_recipes": [
            "BUTTON_SPAWN_PREFAB",
            "SPAWN_POINT_SET",
            "TEXT_SIGN",
            "LIGHT_SET",
        ],
        "trigger_recipe": "BUTTON_SPAWN_PREFAB",
        "trigger_prefix": "button",
        "objective": {
            "type": "TRIGGER_ALL_BUTTONS",
            "enforcement": "INSTRUCTIONAL_OBJECTIVE_ONLY",
        },
        "uses_prefab_asset": True,
    },
    "SCORE_PAD_CHALLENGE": {
        "availability": "AVAILABLE",
        "completion_level": "PLAYABLE_PROTOTYPE",
        "required_capabilities": [
            "SCENE_BUILDING",
            "PLAYER_SPAWN",
            "BUTTON_TRIGGER",
            "SCORE_CONTROL",
            "EFFECT",
            "AUDIO",
            "TEXT",
            "LIGHT",
        ],
        "required_recipes": [
            "SCORE_PAD",
            "SPAWN_POINT_SET",
            "TEXT_SIGN",
            "LIGHT_SET",
        ],
        "trigger_recipe": "SCORE_PAD",
        "trigger_prefix": "score_pad",
        "objective": {
            "type": "COLLECT_SCORE_FROM_PADS",
            "enforcement": "ACTION_ENFORCED_NO_WIN_CONDITION",
        },
        "uses_prefab_asset": False,
    },
    "DEATH_PENALTY_ARENA": {
        "availability": "AVAILABLE",
        "completion_level": "PLAYABLE_PROTOTYPE",
        "required_capabilities": [
            "SCENE_BUILDING",
            "PLAYER_SPAWN",
            "BUTTON_TRIGGER",
            "SCORE_CONTROL",
            "PLAYER_ELIMINATION",
            "TEXT",
            "LIGHT",
        ],
        "required_recipes": [
            "DEATH_PENALTY_ZONE",
            "SPAWN_POINT_SET",
            "TEXT_SIGN",
            "LIGHT_SET",
        ],
        "trigger_recipe": "DEATH_PENALTY_ZONE",
        "trigger_prefix": "hazard",
        "objective": {
            "type": "AVOID_DEATH_PENALTY_ZONES",
            "enforcement": "ACTION_ENFORCED_NO_WIN_CONDITION",
        },
        "uses_prefab_asset": False,
    },
    "TRIGGER_FEEDBACK_COURSE": {
        "availability": "AVAILABLE",
        "completion_level": "PLAYABLE_PROTOTYPE",
        "required_capabilities": [
            "SCENE_BUILDING",
            "PLAYER_SPAWN",
            "BUTTON_TRIGGER",
            "EFFECT",
            "AUDIO",
            "TEXT",
            "LIGHT",
        ],
        "required_recipes": [
            "FEEDBACK_CHECKPOINT",
            "SPAWN_POINT_SET",
            "TEXT_SIGN",
            "LIGHT_SET",
        ],
        "trigger_recipe": "FEEDBACK_CHECKPOINT",
        "trigger_prefix": "checkpoint",
        "objective": {
            "type": "TRIGGER_ALL_CHECKPOINTS",
            "enforcement": "INSTRUCTIONAL_OBJECTIVE_ONLY",
        },
        "uses_prefab_asset": False,
    },
    "OBSTACLE_COURSE": {
        "availability": "BLOCKED",
        "gaps": ["PLAYER_ELIMINATION", "WIN_CONDITION"],
    },
    "SPAWN_ARENA": {
        "availability": "BLOCKED",
        "gaps": ["ROUND_CONTROL", "SCORE_CONTROL", "WIN_CONDITION"],
    },
    "STATIC_DODGE_ARENA": {
        "availability": "BLOCKED",
        "gaps": ["PLAYER_ELIMINATION", "TIMER"],
    },
    "TRIGGER_CHALLENGE": {
        "availability": "BLOCKED",
        "gaps": ["WIN_CONDITION"],
    },
}

TOP = {
    "version",
    "title",
    "archetype",
    "scene",
    "gameplay_root",
    "objective",
    "players",
    "layout",
    "elements",
    "rules",
    "visuals",
    "runtime_expectations",
    "completion_level",
}
COMMON_ELEMENTS = {
    "trigger_count",
    "trigger_template_id",
    "player_spawn_template_id",
    "text_template_id",
    "light_template_id",
}
BUTTON_ELEMENTS = {
    "button_count",
    "button_template_id",
    "player_spawn_template_id",
    "text_template_id",
    "light_template_id",
    "prefab_name",
}


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def capability_report() -> dict[str, Any]:
    return {
        "capabilities": [
            {
                "id": capability,
                "status": status,
                "evidence": (
                    "Phase 3 validated-template Action plus Stage 10-14 surface"
                    if capability in CAPABILITY_ACTIONS
                    else "Stage 10-14 approved surface"
                    if status in {"AVAILABLE", "PARTIAL"}
                    else "No approved recipe/Writer"
                ),
                "required_recipe": None,
                "required_component": None,
                "required_action": CAPABILITY_ACTIONS.get(capability),
                "required_runtime_evidence": (
                    "MANUAL_START_ONLY"
                    if capability == "RUNTIME_OBSERVATION"
                    else None
                ),
            }
            for capability, status in CAPABILITIES.items()
        ]
    }


def list_minigame_archetypes() -> dict[str, Any]:
    return {
        "version": "0.1",
        "archetypes": [
            {"id": name, "version": "0.1", **definition}
            for name, definition in ARCHETYPES.items()
        ],
        "capability_report": capability_report(),
    }


def _vec(value: Any, name: str) -> dict[str, float]:
    if (
        not isinstance(value, dict)
        or set(value) != {"x", "y", "z"}
        or any(
            isinstance(member, bool) or not isinstance(member, (int, float))
            for member in value.values()
        )
    ):
        raise UnsafeDuplicationError(f"INVALID_{name}")
    return {axis: float(value[axis]) for axis in ("x", "y", "z")}


def _active_definition(archetype: str) -> Mapping[str, Any]:
    definition = ARCHETYPES[archetype]
    if definition["availability"] == "AVAILABLE":
        return definition
    # Preserve the v0.1 validation contract for capability-blocked archetypes.
    return ARCHETYPES["BUTTON_SPAWN_CHALLENGE"]


def validate_minigame_spec(spec: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(spec, dict) or set(spec) != TOP:
        fields = sorted(set(spec) if isinstance(spec, dict) else [])
        raise UnsafeDuplicationError(f"INVALID_MINIGAME_SPEC_FIELDS: {fields}")
    if spec["version"] != "0.1":
        raise UnsafeDuplicationError("UNSUPPORTED_MINIGAME_SPEC_VERSION")
    if spec["archetype"] not in ARCHETYPES:
        raise UnsafeDuplicationError("UNKNOWN_MINIGAME_ARCHETYPE")
    if not isinstance(spec["title"], str) or not spec["title"]:
        raise UnsafeDuplicationError("INVALID_TITLE")
    if not isinstance(spec["scene"], str):
        raise UnsafeDuplicationError("INVALID_SCENE")
    if spec["completion_level"] not in {
        "PLAYABLE_PROTOTYPE",
        "FULL_MINIGAME",
    }:
        raise UnsafeDuplicationError("INVALID_COMPLETION_LEVEL")

    players = spec["players"]
    if (
        not isinstance(players, dict)
        or set(players) != {"spawn_count", "spawn_positions", "template_id"}
        or players["spawn_count"] != 2
        or not isinstance(players["spawn_positions"], list)
        or len(players["spawn_positions"]) != 2
    ):
        raise UnsafeDuplicationError("INVALID_PLAYER_COUNT")
    [_vec(position, "SPAWN_POSITION") for position in players["spawn_positions"]]

    definition = _active_definition(spec["archetype"])
    elements = spec["elements"]
    layout = spec["layout"]
    if definition["uses_prefab_asset"]:
        if (
            not isinstance(elements, dict)
            or set(elements) != BUTTON_ELEMENTS
            or not 1 <= elements["button_count"] <= 16
        ):
            raise UnsafeDuplicationError("INVALID_ELEMENTS")
        trigger_template_id = elements["button_template_id"]
        layout_fields = {
            "button_origin",
            "button_direction",
            "button_spacing",
        }
        origin_name, direction_name, spacing_name = (
            "button_origin",
            "button_direction",
            "button_spacing",
        )
    else:
        if (
            not isinstance(elements, dict)
            or set(elements) != COMMON_ELEMENTS
            or not 1 <= elements["trigger_count"] <= 16
        ):
            raise UnsafeDuplicationError("INVALID_ELEMENTS")
        trigger_template_id = elements["trigger_template_id"]
        layout_fields = {
            "trigger_origin",
            "trigger_direction",
            "trigger_spacing",
        }
        origin_name, direction_name, spacing_name = (
            "trigger_origin",
            "trigger_direction",
            "trigger_spacing",
        )
    if not isinstance(trigger_template_id, str) or not trigger_template_id:
        raise UnsafeDuplicationError("INVALID_ELEMENTS")
    if players["template_id"] != elements["player_spawn_template_id"]:
        raise UnsafeDuplicationError("SPAWN_TEMPLATE_MISMATCH")
    if not isinstance(layout, dict) or set(layout) != layout_fields:
        raise UnsafeDuplicationError("INVALID_LAYOUT")
    spacing = layout[spacing_name]
    if (
        isinstance(spacing, bool)
        or not isinstance(spacing, (int, float))
        or spacing == 0
    ):
        raise UnsafeDuplicationError("INVALID_LAYOUT")
    _vec(layout[origin_name], "TRIGGER_ORIGIN")
    direction = _vec(layout[direction_name], "TRIGGER_DIRECTION")
    if all(member == 0 for member in direction.values()):
        raise UnsafeDuplicationError("INVALID_LAYOUT")

    visuals = spec["visuals"]
    if not isinstance(visuals, dict) or set(visuals) != {
        "text_position",
        "light_position",
    }:
        raise UnsafeDuplicationError("INVALID_VISUALS")
    _vec(visuals["text_position"], "TEXT_POSITION")
    _vec(visuals["light_position"], "LIGHT_POSITION")
    if spec["rules"] != []:
        raise UnsafeDuplicationError("UNSUPPORTED_RULES")
    if spec["objective"] != definition["objective"]:
        raise UnsafeDuplicationError("UNSUPPORTED_OBJECTIVE")
    runtime = spec["runtime_expectations"]
    if (
        not isinstance(runtime, list)
        or any(not isinstance(item, str) or not item for item in runtime)
    ):
        raise UnsafeDuplicationError("INVALID_RUNTIME_EXPECTATIONS")
    return {"valid": True, "spec_sha256": _sha(_canonical(spec))}


@dataclass(frozen=True, slots=True)
class MinigamePlan:
    status: str
    spec_sha256: str
    scene_sha256: str
    archetype: str
    requested_completion_level: str
    actual_completion_level: str
    capability_gaps: tuple[str, ...]
    gameplay_spec: dict | None
    gameplay_plan: dict | None
    template_fingerprints: dict
    asset_hashes: dict
    runtime_assertions: tuple[str, ...]
    plan_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _trigger_shape(spec: Mapping[str, Any], definition: Mapping[str, Any]):
    elements = spec["elements"]
    layout = spec["layout"]
    if definition["uses_prefab_asset"]:
        return (
            elements["button_count"],
            elements["button_template_id"],
            layout["button_origin"],
            layout["button_direction"],
            layout["button_spacing"],
        )
    return (
        elements["trigger_count"],
        elements["trigger_template_id"],
        layout["trigger_origin"],
        layout["trigger_direction"],
        layout["trigger_spacing"],
    )


def plan_minigame(scene_path, catalog_root, spec) -> MinigamePlan:
    validation = validate_minigame_spec(spec)
    scene = Path(scene_path).resolve(strict=True)
    root = Path(catalog_root).resolve(strict=True)
    if Path(spec["scene"]).resolve(strict=False) != scene:
        raise UnsafeDuplicationError("MINIGAME_SCENE_MISMATCH")
    archetype = ARCHETYPES[spec["archetype"]]
    gaps = list(archetype.get("gaps", []))
    if spec["completion_level"] == "FULL_MINIGAME":
        gaps += [
            capability
            for capability in ("ROUND_CONTROL", "SCORE_CONTROL", "WIN_CONDITION")
            if CAPABILITIES[capability] != "AVAILABLE"
            and capability not in gaps
        ]
    if archetype["availability"] != "AVAILABLE" or gaps:
        payload = {
            "spec": validation["spec_sha256"],
            "scene": _sha(scene.read_bytes()),
            "gaps": sorted(set(gaps)),
        }
        return MinigamePlan(
            "BLOCKED",
            validation["spec_sha256"],
            payload["scene"],
            spec["archetype"],
            spec["completion_level"],
            "PLAYABLE_PROTOTYPE",
            tuple(sorted(set(gaps))),
            None,
            None,
            {},
            {},
            (),
            _sha(_canonical(payload)),
        )

    elements = spec["elements"]
    players = spec["players"]
    visuals = spec["visuals"]
    count, template_id, origin, direction, spacing = _trigger_shape(spec, archetype)
    objects: list[dict[str, Any]] = []
    trigger_ids: list[str] = []
    for index in range(count):
        logical_id = f'{archetype["trigger_prefix"]}_{index + 1:02d}'
        trigger_ids.append(logical_id)
        objects.append(
            {
                "logical_id": logical_id,
                "recipe_type": archetype["trigger_recipe"],
                "template_id": template_id,
                "trigger_recipe": {"event_property": "OnEnterActions"},
            }
        )
    for index, position in enumerate(players["spawn_positions"]):
        objects.append(
            {
                "logical_id": f"player_spawn_{index + 1:02d}",
                "recipe_type": "SPAWN_POINT_SET",
                "template_id": players["template_id"],
                "transform": {"position": _vec(position, "SPAWN_POSITION")},
            }
        )
    objects += [
        {
            "logical_id": "instruction_text",
            "recipe_type": "TEXT_SIGN",
            "template_id": elements["text_template_id"],
            "transform": {
                "position": _vec(visuals["text_position"], "TEXT_POSITION")
            },
        },
        {
            "logical_id": "main_light",
            "recipe_type": "LIGHT_SET",
            "template_id": elements["light_template_id"],
            "transform": {
                "position": _vec(visuals["light_position"], "LIGHT_POSITION")
            },
        },
    ]
    metadata = {
        "minigame_title": spec["title"],
        "archetype": spec["archetype"],
    }
    if archetype["uses_prefab_asset"]:
        metadata["prefab"] = elements["prefab_name"]
    gameplay_spec = {
        "version": "0.1",
        "scene": str(scene),
        "destination_root": spec["gameplay_root"],
        "objects": objects,
        "layout": [
            {
                "type": "ROW",
                "objects": trigger_ids,
                "origin": _vec(origin, "TRIGGER_ORIGIN"),
                "direction": _vec(direction, "TRIGGER_DIRECTION"),
                "spacing": float(spacing),
            }
        ],
        "metadata": metadata,
    }
    gameplay_plan = plan_gameplay_composition(scene, root, gameplay_spec).to_dict()
    templates = gameplay_plan["template_fingerprints"]
    assets: dict[str, str] = {}
    if archetype["uses_prefab_asset"]:
        asset = root / "Assets" / "Prefabs" / f'{elements["prefab_name"]}.pfab'
        for candidate in (asset, Path(str(asset) + ".pmeta")):
            if not candidate.is_file():
                raise UnsafeDuplicationError("MINIGAME_ASSET_MISSING")
            relative = str(candidate.relative_to(root)).replace("\\", "/")
            assets[relative] = _sha(candidate.read_bytes())
    payload = {
        "spec": validation["spec_sha256"],
        "gameplay_plan": gameplay_plan["plan_sha256"],
        "templates": templates,
        "assets": assets,
        "runtime": spec["runtime_expectations"],
    }
    return MinigamePlan(
        "SAFE_TO_BUILD",
        validation["spec_sha256"],
        gameplay_plan["scene_sha256"],
        spec["archetype"],
        spec["completion_level"],
        "PLAYABLE_PROTOTYPE",
        (),
        gameplay_spec,
        gameplay_plan,
        templates,
        assets,
        tuple(spec["runtime_expectations"]),
        _sha(_canonical(payload)),
    )


def build_minigame(
    scene_path, catalog_root, spec, *, expected_plan_hash
) -> dict[str, Any]:
    plan = plan_minigame(scene_path, catalog_root, spec)
    if plan.status != "SAFE_TO_BUILD":
        raise UnsafeDuplicationError("MINIGAME_BUILD_BLOCKED")
    if plan.plan_sha256.casefold() != expected_plan_hash.casefold():
        raise ConcurrentModificationError("MINIGAME_PLAN_STALE")
    root = Path(catalog_root)
    sidecar = root / ".pummelmcp-minigames.json"
    prior = (
        json.loads(sidecar.read_text(encoding="utf-8"))
        if sidecar.is_file()
        else {"builds": []}
    )
    if any(
        item["spec_sha256"] == plan.spec_sha256 for item in prior["builds"]
    ):
        raise UnsafeDuplicationError("MINIGAME_ALREADY_APPLIED")
    result = compose_gameplay(
        scene_path,
        root,
        plan.gameplay_spec,
        expected_plan_sha256=plan.gameplay_plan["plan_sha256"],
    )
    record = {
        "minigame_id": str(uuid.uuid4()),
        "title": spec["title"],
        "archetype": plan.archetype,
        "spec_sha256": plan.spec_sha256,
        "plan_sha256": plan.plan_sha256,
        "scene_sha256": result["after_scene_sha256"],
        "semantic_fingerprint": semantic_scene_fingerprint(scene_path),
        "composition_id": result["composition_id"],
        "logical_mappings": result["created_objects"],
    }
    prior["builds"].append(record)
    temporary = sidecar.with_suffix(".tmp")
    temporary.write_text(json.dumps(prior, indent=2), encoding="utf-8")
    os.replace(temporary, sidecar)
    return {
        **record,
        "requested_completion_level": plan.requested_completion_level,
        "actual_completion_level": plan.actual_completion_level,
        "created_objects": result["created_objects"],
        "created_actions": result["created_actions"],
        "assets": plan.asset_hashes,
        "templates": plan.template_fingerprints,
        "capabilities_used": ARCHETYPES[plan.archetype]["required_capabilities"],
        "capability_gaps": [],
        "build_result": "BUILD_PASS",
        "runtime_result": "NOT_RUN",
        "runtime_assertions": plan.runtime_assertions,
        "repair_result": "NOT_ATTEMPTED",
        "remaining_limitations": [
            "ROUND_CONTROL",
            "SCORE_CONTROL",
            "WIN_CONDITION",
            "MANUAL_PLAYTEST_START",
        ],
    }
