"""Constrained, source-span based writer for existing minigame JSON settings."""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from .pmh.errors import ConcurrentModificationError, PMHWriterError
from .pmh.json_spans import parse_json_spans


class MinigameConfigError(PMHWriterError, ValueError):
    """A settings request is outside the verified minigame configuration surface."""


FILES = {"settings": "ModSettings.json", "details": "MinigameDefinitionData.json"}
END_FLAGS = {"timer": 1, "obtain_points": 2, "remaining_players_alive": 4, "finish_minigame": 8}
PLACEMENT = {"most_points": 0, "least_points": 1, "finish_order": 2, "last_man_standing": 3}
PLAYER_TYPE = {"third_person": 0, "top_down": 1, "twin_stick": 2}

# API name -> (JSON path, type, lower bound, upper bound). Bounds mirror the
# shipped editor's clamps where present, rather than inventing wider ranges.
SCHEMA: dict[str, dict[str, tuple[tuple[str, ...], str, float | None, float | None]]] = {
    "settings": {
        "rounds": (("SimpleMinigameSettings", "NumberOfRounds"), "int", 1, 20),
        "round_duration_seconds": (("SimpleMinigameSettings", "RoundDuration"), "float", 5, 1200),
        "points_to_win": (("SimpleMinigameSettings", "PointsToWin"), "int", 1, 1_000_000),
        "players_alive_to_end": (("SimpleMinigameSettings", "MinPlayersAlive"), "int", 1, 7),
        "end_conditions": (("SimpleMinigameSettings", "EndConditions"), "end_flags", None, None),
        "placement_condition": (("SimpleMinigameSettings", "PlacementCondition"), "placement", None, None),
        "player_type": (("SimpleMinigameSettings", "ChosenPlayerType"), "player_type", None, None),
        "movement_speed": (("SimpleModPlayerSettings", "movementSpeed"), "float", 0, 1000),
        "movement_acceleration": (("SimpleModPlayerSettings", "movementAcceleration"), "float", 0, 1000),
        "movement_deceleration": (("SimpleModPlayerSettings", "movementDecelaration"), "float", 0, 10000),
        "can_jump": (("SimpleModPlayerSettings", "canJump"), "bool", None, None),
        "jump_speed": (("SimpleModPlayerSettings", "jumpSpeed"), "float", 0, 1000),
        "can_punch": (("SimpleModPlayerSettings", "canPunch"), "bool", None, None),
        "respawn_enabled": (("SimpleModPlayerSettings", "respawnEnabled"), "bool", None, None),
        "respawn_time_seconds": (("SimpleModPlayerSettings", "respawnTime"), "float", 0, 1000),
        "respawn_invulnerable_seconds": (("SimpleModPlayerSettings", "respawnInvulnerableTime"), "float", 0, 1000),
        "use_health": (("SimpleModPlayerSettings", "useHealth"), "bool", None, None),
        "show_health_bar": (("SimpleModPlayerSettings", "showHealthBar"), "bool", None, None),
        "health_points": (("SimpleModPlayerSettings", "health"), "int", 1, 1000),
    },
    "details": {
        "name": (("MinigameName",), "text", 1, 120),
        "description": (("Description",), "text", 1, 2000),
        "min_players": (("MinPlayers",), "int", 1, 8),
        "max_players": (("MaxPlayers",), "int", 1, 8),
    },
}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _paths(scene_path: str | Path, allowed_root: str | Path) -> dict[str, Path]:
    scene = Path(scene_path).resolve(strict=True)
    allowed = Path(allowed_root).resolve(strict=True)
    try:
        scene.relative_to(allowed)
    except ValueError as exc:
        raise MinigameConfigError("scene is outside the allowed root") from exc
    if scene.suffix.lower() != ".scene" or scene.parent.name != "Data":
        raise MinigameConfigError("expected an existing Mod Data/*.scene path")
    result: dict[str, Path] = {}
    for kind, filename in FILES.items():
        path = (scene.parent / filename).resolve(strict=True)
        try:
            path.relative_to(allowed)
        except ValueError as exc:
            raise MinigameConfigError("configuration file escapes the allowed root") from exc
        if not path.is_file():
            raise MinigameConfigError(f"missing {filename}")
        result[kind] = path
    return result


def _decode(raw: bytes, kind: str):
    document = parse_json_spans(raw)
    if document.root.kind != "object":
        raise MinigameConfigError("configuration root must be an object")
    if kind == "settings" and document.root.member("Version").value != 0:
        raise MinigameConfigError("unsupported ModSettings version")
    for path, _, _, _ in SCHEMA[kind].values():
        document.node_at(path)
    return document


def _encode_value(name: str, value: Any, rule: tuple) -> Any:
    _, value_type, low, high = rule
    if value_type == "bool":
        if type(value) is not bool:
            raise MinigameConfigError(f"{name} requires a boolean")
        return value
    if value_type == "int":
        if type(value) is not int:
            raise MinigameConfigError(f"{name} requires an integer")
    elif value_type == "float":
        if type(value) not in (int, float):
            raise MinigameConfigError(f"{name} requires a finite number")
        try:
            value = float(value)
        except OverflowError as exc:
            raise MinigameConfigError(f"{name} requires a finite number") from exc
        if not math.isfinite(value):
            raise MinigameConfigError(f"{name} requires a finite number")
    elif value_type == "text":
        if not isinstance(value, str) or not value.strip():
            raise MinigameConfigError(f"{name} requires nonempty text")
    elif value_type == "end_flags":
        if not isinstance(value, list) or not value or any(type(v) is not str or v not in END_FLAGS for v in value) or len(value) != len(set(value)):
            raise MinigameConfigError("end_conditions requires a nonempty list of distinct known names")
        return sum(END_FLAGS[item] for item in value)
    elif value_type in ("placement", "player_type"):
        choices = PLACEMENT if value_type == "placement" else PLAYER_TYPE
        if type(value) is not str or value not in choices:
            raise MinigameConfigError(f"{name} must be one of {list(choices)}")
        return choices[value]
    if low is not None and not low <= (len(value) if value_type == "text" else value) <= high:
        raise MinigameConfigError(f"{name} is outside [{low}, {high}]")
    return value


def _display(kind: str, name: str, value: Any) -> Any:
    value_type = SCHEMA[kind][name][1]
    if value_type == "end_flags":
        return [key for key, flag in END_FLAGS.items() if value & flag]
    if value_type in ("placement", "player_type"):
        choices = PLACEMENT if value_type == "placement" else PLAYER_TYPE
        return next((key for key, number in choices.items() if number == value), {"unknown_raw_value": value})
    return value


def get_minigame_config(scene_path: str | Path, allowed_root: str | Path) -> dict[str, Any]:
    paths = _paths(scene_path, allowed_root)
    output = {}
    for kind, path in paths.items():
        raw = path.read_bytes()
        doc = _decode(raw, kind)
        output[kind] = {
            "path": str(path), "sha256": _sha(raw),
            "values": {name: _display(kind, name, doc.node_at(rule[0]).value) for name, rule in SCHEMA[kind].items()},
        }
    return output


def _patched(raw: bytes, kind: str, updates: Mapping[str, Any]):
    if not isinstance(updates, dict) or not updates or set(updates) - set(SCHEMA[kind]):
        raise MinigameConfigError("updates must contain only supported, nonempty fields")
    document = _decode(raw, kind)
    replacements = []
    changes = {}
    for name, supplied in updates.items():
        rule = SCHEMA[kind][name]
        value = _encode_value(name, supplied, rule)
        node = document.node_at(rule[0])
        expected_kind = {"int": "number", "float": "number", "bool": "bool", "text": "string", "end_flags": "number", "placement": "number", "player_type": "number"}[rule[1]]
        if node.kind != expected_kind:
            raise MinigameConfigError(f"existing {name} has unexpected JSON type")
        if node.value == value:
            continue
        replacement = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        replacements.append((node.span.start, node.span.end, replacement))
        changes[name] = {"before": _display(kind, name, node.value), "after": _display(kind, name, value)}
    patched = raw
    for start, end, replacement in sorted(replacements, reverse=True):
        patched = patched[:start] + replacement + patched[end:]
    after = _decode(patched, kind)
    for name, rule in SCHEMA[kind].items():
        expected = _encode_value(name, updates[name], rule) if name in updates else document.node_at(rule[0]).value
        if after.node_at(rule[0]).value != expected:
            raise MinigameConfigError(f"post-write validation failed for {name}")
    if kind == "settings":
        controller = after.root.member("SimpleMinigameSettings").value
        player = after.root.member("SimpleModPlayerSettings").value
        if controller["EndConditions"] == END_FLAGS["remaining_players_alive"] and player["respawnEnabled"]:
            raise MinigameConfigError("remaining-players-only ending requires respawn_enabled=false")
    else:
        if after.root.member("MinPlayers").value > after.root.member("MaxPlayers").value:
            raise MinigameConfigError("min_players exceeds max_players")
    return patched, changes


def plan_minigame_config_update(scene_path: str | Path, allowed_root: str | Path, kind: str, updates: Mapping[str, Any]) -> dict[str, Any]:
    if kind not in FILES:
        raise MinigameConfigError("kind must be settings or details")
    paths = _paths(scene_path, allowed_root)
    scene_hash = _sha(Path(scene_path).read_bytes())
    path = paths[kind]
    raw = path.read_bytes()
    patched, changes = _patched(raw, kind, updates)
    payload = json.dumps({"kind": kind, "scene_sha256": scene_hash, "file_sha256": _sha(raw), "updates": updates}, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return {"kind": kind, "path": str(path), "scene_sha256": scene_hash, "before_sha256": _sha(raw), "after_sha256": _sha(patched), "plan_sha256": _sha(payload), "changes": changes, "no_op": not changes}


def apply_minigame_config_update(scene_path: str | Path, allowed_root: str | Path, kind: str, updates: Mapping[str, Any], expected_plan_sha256: str, *, backup: bool = True) -> dict[str, Any]:
    plan = plan_minigame_config_update(scene_path, allowed_root, kind, updates)
    if plan["plan_sha256"] != expected_plan_sha256:
        raise ConcurrentModificationError("minigame configuration plan is stale")
    if plan["no_op"]:
        return {**plan, "backup_path": None, "written": False}
    path = Path(plan["path"])
    original = path.read_bytes()
    if _sha(original) != plan["before_sha256"]:
        raise ConcurrentModificationError("configuration changed after planning")
    patched, _ = _patched(original, kind, updates)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temp_name)
    backup_path = None
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(patched)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
        _decode(temporary.read_bytes(), kind)
        if _sha(path.read_bytes()) != plan["before_sha256"] or _sha(Path(scene_path).read_bytes()) != plan["scene_sha256"]:
            raise ConcurrentModificationError("source changed before replacement")
        if backup:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            for counter in range(1000):
                candidate = path.with_name(f"{path.name}.bak.{stamp}.{counter}")
                try:
                    backup_fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
                    break
                except FileExistsError:
                    continue
            else:
                raise MinigameConfigError("could not allocate a backup path")
            backup_path = candidate
            try:
                with os.fdopen(backup_fd, "wb") as stream:
                    stream.write(original)
                    stream.flush()
                    os.fsync(stream.fileno())
            except Exception:
                backup_path.unlink(missing_ok=True)
                raise
        if _sha(path.read_bytes()) != plan["before_sha256"]:
            raise ConcurrentModificationError("configuration changed before replacement")
        os.replace(temporary, path)
        return {**plan, "backup_path": str(backup_path) if backup_path else None, "written": True}
    finally:
        temporary.unlink(missing_ok=True)
