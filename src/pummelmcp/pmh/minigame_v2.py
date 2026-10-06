"""Version 0.2 typed minigame rules and staged whole-Mod construction."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ..minigame_config import (
    get_minigame_config,
    plan_minigame_config_update,
    apply_minigame_config_update,
)
from .errors import ConcurrentModificationError, UnsafeDuplicationError, WriterValidationError
from .gameplay_composer import compose_gameplay, plan_gameplay_composition
from .gameplay_rules import compile_trigger_rule
from .reader import read_pmh
from .validator import validate_scene


ROLES = {
    "trigger": "CONFIGURABLE_TRIGGER",
    "spawn": "SPAWN_POINT_SET",
    "text": "TEXT_SIGN",
    "light": "LIGHT_SET",
    "prop": "PROP_LAYOUT",
    "static": "STATIC_TEMPLATE_BLOCK",
}
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,63}$")
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _source(scene_path: str | Path, allowed_root: str | Path) -> tuple[Path, Path, Path]:
    scene = Path(scene_path).resolve(strict=True)
    allowed = Path(allowed_root).resolve(strict=True)
    try:
        scene.relative_to(allowed)
    except ValueError as exc:
        raise UnsafeDuplicationError("MINIGAME_SCENE_OUTSIDE_ALLOWED_ROOT") from exc
    if scene.parent.name != "Data" or scene.suffix.lower() != ".scene":
        raise UnsafeDuplicationError("MINIGAME_SOURCE_REQUIRES_DATA_SCENE")
    mod = scene.parent.parent
    if not (mod / "Data" / "ModSettings.json").is_file() or not (mod / "Data" / "MinigameDefinitionData.json").is_file():
        raise UnsafeDuplicationError("MINIGAME_SOURCE_CONFIG_MISSING")
    return scene, mod, allowed


def _vec(value: Any) -> dict[str, float]:
    import math
    if not isinstance(value, dict) or set(value) != {"x", "y", "z"}:
        raise UnsafeDuplicationError("INVALID_OBJECT_POSITION")
    if any(type(item) not in (int, float) or not math.isfinite(item) for item in value.values()):
        raise UnsafeDuplicationError("INVALID_OBJECT_POSITION")
    return {key: float(value[key]) for key in ("x", "y", "z")}


def validate_minigame_v2_spec(spec: Any) -> dict[str, Any]:
    expected = {"version", "scene", "output_mod", "title", "description", "min_players", "max_players", "settings", "objects"}
    if not isinstance(spec, dict) or set(spec) != expected or spec.get("version") != "0.2":
        raise UnsafeDuplicationError("INVALID_MINIGAME_V2_SPEC")
    if not isinstance(spec["scene"], str):
        raise UnsafeDuplicationError("INVALID_MINIGAME_SCENE")
    if not isinstance(spec["output_mod"], str) or not _SAFE_NAME.fullmatch(spec["output_mod"]) or spec["output_mod"].strip() != spec["output_mod"] or spec["output_mod"].upper() in _WINDOWS_RESERVED:
        raise UnsafeDuplicationError("INVALID_OUTPUT_MOD_NAME")
    if not isinstance(spec["settings"], dict):
        raise UnsafeDuplicationError("INVALID_MINIGAME_SETTINGS")
    objects = spec["objects"]
    if not isinstance(objects, list) or not 2 <= len(objects) <= 64:
        raise UnsafeDuplicationError("INVALID_MINIGAME_OBJECTS")
    ids = set()
    roles = []
    for item in objects:
        if not isinstance(item, dict) or set(item) not in ({"id", "role", "template_id", "position"}, {"id", "role", "template_id", "position", "rule"}):
            raise UnsafeDuplicationError("INVALID_MINIGAME_OBJECT")
        if not isinstance(item["id"], str) or not item["id"] or item["id"] in ids:
            raise UnsafeDuplicationError("INVALID_MINIGAME_OBJECT_ID")
        ids.add(item["id"])
        if type(item["role"]) is not str or item["role"] not in ROLES or not isinstance(item["template_id"], str) or not item["template_id"]:
            raise UnsafeDuplicationError("INVALID_MINIGAME_OBJECT_ROLE")
        _vec(item["position"])
        if item["role"] == "trigger":
            if "rule" not in item:
                raise UnsafeDuplicationError("TRIGGER_RULE_REQUIRED")
            compile_trigger_rule(item["rule"])
        elif "rule" in item:
            raise UnsafeDuplicationError("RULE_ONLY_ALLOWED_ON_TRIGGER")
        roles.append(item["role"])
    if "trigger" not in roles or "spawn" not in roles:
        raise UnsafeDuplicationError("MINIGAME_NEEDS_TRIGGER_AND_SPAWN")
    return {"valid": True, "spec_sha256": _sha(_canonical(spec))}


def _gameplay_spec(scene: Path, spec: dict[str, Any]) -> dict[str, Any]:
    objects = []
    for item in spec["objects"]:
        row = {
            "logical_id": item["id"], "recipe_type": ROLES[item["role"]],
            "template_id": item["template_id"],
            "transform": {"position": _vec(item["position"])},
        }
        if item["role"] == "trigger":
            row["trigger_recipe"] = item["rule"]
        objects.append(row)
    return {"version": "0.1", "scene": str(scene), "destination_root": None,
            "objects": objects, "metadata": {"minigame_title": spec["title"], "spec_version": "0.2"}}


def _check_win_logic(spec: dict[str, Any]) -> None:
    settings = spec["settings"]
    if "rounds" not in settings or "placement_condition" not in settings:
        raise UnsafeDuplicationError("ROUND_AND_PLACEMENT_SETTINGS_REQUIRED")
    endings = settings.get("end_conditions")
    if not isinstance(endings, list) or not endings:
        raise UnsafeDuplicationError("END_CONDITION_REQUIRED")
    rules = [(item, action) for item in spec["objects"] if item["role"] == "trigger" for action in item["rule"]["actions"]]
    kinds = {action["type"] for _, action in rules}
    positive_score = any(action["type"] == "CHANGE_SCORE" and action["operation"] in ("add", "set") and action["value"] > 0 for _, action in rules)
    if "obtain_points" in endings and ("points_to_win" not in settings or not positive_score):
        raise UnsafeDuplicationError("POINT_ENDING_REQUIRES_SCORE_ACTION")
    if "timer" in endings and "round_duration_seconds" not in settings:
        raise UnsafeDuplicationError("TIMER_ENDING_REQUIRES_DURATION")
    if "remaining_players_alive" in endings and "KILL" not in kinds:
        raise UnsafeDuplicationError("ALIVE_ENDING_REQUIRES_KILL_ACTION")
    if "remaining_players_alive" in endings and "players_alive_to_end" not in settings:
        raise UnsafeDuplicationError("ALIVE_ENDING_REQUIRES_PLAYER_LIMIT")
    if "finish_minigame" in endings and "SET_PLACEMENT" not in kinds:
        raise UnsafeDuplicationError("FINISH_ENDING_REQUIRES_PLACEMENT_ACTION")
    if "finish_minigame" in endings and settings["placement_condition"] != "finish_order":
        raise UnsafeDuplicationError("FINISH_ENDING_REQUIRES_FINISH_ORDER")
    if "finish_minigame" in endings and any(item["rule"]["condition"] == "once_global" for item, action in rules if action["type"] == "SET_PLACEMENT"):
        raise UnsafeDuplicationError("FINISH_TRIGGER_CANNOT_BE_GLOBAL_ONCE")
    if endings == ["timer"] and settings.get("placement_condition") in ("most_points", "least_points") and "CHANGE_SCORE" not in kinds:
        raise UnsafeDuplicationError("POINT_PLACEMENT_REQUIRES_SCORE_ACTION")
    if "remaining_players_alive" in endings and settings.get("respawn_enabled") is not False:
        raise UnsafeDuplicationError("ALIVE_ENDING_REQUIRES_NO_RESPAWN")


def plan_minigame_v2(scene_path: str | Path, allowed_root: str | Path, spec: dict[str, Any]) -> dict[str, Any]:
    validated = validate_minigame_v2_spec(spec)
    scene, mod, allowed = _source(scene_path, allowed_root)
    if Path(spec["scene"]).resolve(strict=False) != scene:
        raise UnsafeDuplicationError("MINIGAME_SCENE_MISMATCH")
    output = (allowed / spec["output_mod"]).resolve(strict=False)
    if output.exists() or output == mod:
        raise UnsafeDuplicationError("OUTPUT_MOD_ALREADY_EXISTS")
    if output.parent != allowed:
        raise UnsafeDuplicationError("OUTPUT_MOD_OUTSIDE_ALLOWED_ROOT")
    _check_win_logic(spec)
    settings_plan = plan_minigame_config_update(scene, allowed, "settings", spec["settings"])
    details = {"name": spec["title"], "description": spec["description"],
               "min_players": spec["min_players"], "max_players": spec["max_players"]}
    details_plan = plan_minigame_config_update(scene, allowed, "details", details)
    gameplay_spec = _gameplay_spec(scene, spec)
    gameplay_plan = plan_gameplay_composition(scene, mod, gameplay_spec).to_dict()
    payload = {"spec_sha256": validated["spec_sha256"], "gameplay_plan_sha256": gameplay_plan["plan_sha256"],
               "settings_plan_sha256": settings_plan["plan_sha256"], "details_plan_sha256": details_plan["plan_sha256"],
               "output": str(output)}
    return {"status": "SAFE_TO_BUILD", "spec_version": "0.2", "plan_sha256": _sha(_canonical(payload)),
            "scene_sha256": gameplay_plan["scene_sha256"], "output_mod": str(output),
            "settings_before_sha256": settings_plan["before_sha256"],
            "details_before_sha256": details_plan["before_sha256"],
            "settings_changes": settings_plan["changes"], "details_changes": details_plan["changes"],
            "gameplay_plan": gameplay_plan, "runtime_status": "NOT_RUN", "completion": "CONFIGURED_UNVERIFIED"}


def build_minigame_v2(scene_path: str | Path, allowed_root: str | Path, spec: dict[str, Any], expected_plan_sha256: str) -> dict[str, Any]:
    plan = plan_minigame_v2(scene_path, allowed_root, spec)
    if type(expected_plan_sha256) is not str or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_plan_sha256):
        raise UnsafeDuplicationError("INVALID_MINIGAME_V2_PLAN_HASH")
    if plan["plan_sha256"].casefold() != expected_plan_sha256.casefold():
        raise ConcurrentModificationError("MINIGAME_V2_PLAN_STALE")
    scene, mod, allowed = _source(scene_path, allowed_root)
    output = Path(plan["output_mod"])
    stage_container = Path(tempfile.mkdtemp(prefix=".pummelmcp-rule-build-", dir=allowed))
    stage_mod = stage_container / "Mod"
    try:
        ignore_name = stage_container.name if mod == allowed else None
        def ignore(directory, names):
            return {ignore_name} if ignore_name and Path(directory).resolve() == mod and ignore_name in names else set()
        shutil.copytree(mod, stage_mod, ignore=ignore)
        staged_scene = stage_mod / scene.relative_to(mod)
        staged_gameplay_spec = _gameplay_spec(staged_scene, spec)
        staged_plan = plan_gameplay_composition(staged_scene, stage_mod, staged_gameplay_spec)
        composition = compose_gameplay(staged_scene, stage_mod, staged_gameplay_spec,
                                       expected_plan_sha256=staged_plan.plan_sha256, backup=False)
        details = {"name": spec["title"], "description": spec["description"],
                   "min_players": spec["min_players"], "max_players": spec["max_players"]}
        for kind, updates in (("settings", spec["settings"]), ("details", details)):
            config_plan = plan_minigame_config_update(staged_scene, allowed, kind, updates)
            apply_minigame_config_update(staged_scene, allowed, kind, updates, config_plan["plan_sha256"], backup=False)
        parsed = read_pmh(staged_scene)
        if not parsed.fully_consumed or not validate_scene(parsed).passed:
            raise WriterValidationError("STAGED_MINIGAME_SCENE_INVALID")
        config = get_minigame_config(staged_scene, allowed)
        for name, value in spec["settings"].items():
            actual = config["settings"]["values"][name]
            matches = set(actual) == set(value) if name == "end_conditions" else actual == value
            if not matches:
                raise WriterValidationError(f"STAGED_SETTING_MISMATCH: {name}")
        if config["details"]["values"]["name"] != spec["title"]:
            raise WriterValidationError("STAGED_TITLE_MISMATCH")
        # A late source change cannot be allowed to publish an output built from
        # an obsolete plan. The source Mod itself is never mutated.
        if plan_minigame_v2(scene, allowed, spec)["plan_sha256"].casefold() != expected_plan_sha256.casefold():
            raise ConcurrentModificationError("MINIGAME_V2_SOURCE_CHANGED")
        if output.exists():
            raise ConcurrentModificationError("OUTPUT_MOD_ALREADY_EXISTS")
        if stage_mod.resolve(strict=True).parent != stage_container.resolve(strict=True) or output.parent != allowed:
            raise UnsafeDuplicationError("STAGED_MOD_PATH_ESCAPED")
        os.rename(stage_mod, output)
        return {"status": "BUILD_PASS", "output_mod": str(output), "scene": str(output / scene.relative_to(mod)),
                "plan_sha256": plan["plan_sha256"], "created_objects": composition["created_objects"],
                "created_actions": composition["created_actions"], "runtime_status": "NOT_RUN",
                "completion": "CONFIGURED_UNVERIFIED"}
    finally:
        resolved_stage = stage_container.resolve(strict=False)
        if resolved_stage.parent == allowed and resolved_stage.name.startswith(".pummelmcp-rule-build-"):
            shutil.rmtree(resolved_stage, ignore_errors=True)
