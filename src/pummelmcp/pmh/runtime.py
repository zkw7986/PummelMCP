"""Stage 14 runtime evidence pipeline with manual-start, fail-closed semantics."""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .reader import read_pmh
from .validator import validate_scene
from .scene_identity import semantic_scene_fingerprint, classify_scene_identity

SESSION_DIR = ".pummelmcp-playtests"
LOG_NAMES = ("Player.log", "Editor.log")
ERROR_RULES = (
    ("MISSING_ASSET", re.compile(r"missing (?:asset|prefab)|failed to load asset", re.I)),
    ("MISSING_REFERENCE", re.compile(r"missing reference|null reference", re.I)),
    ("SCENE_LOAD_ERROR", re.compile(r"(?:failed|error).*(?:load|loading).*scene", re.I)),
    ("SPAWN_ERROR", re.compile(r"(?:spawn|instantiate).*(?:failed|error|exception)", re.I)),
    ("TRIGGER_ERROR", re.compile(r"trigger.*(?:failed|error|exception)", re.I)),
    ("ACTION_ERROR", re.compile(r"action.*(?:failed|error|exception)", re.I)),
    ("RUNTIME_EXCEPTION", re.compile(r"\b(?:exception|stack trace)\b", re.I)),
    ("CRASH", re.compile(r"\b(?:crash|fatal error)\b", re.I)),
)
GUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b", re.I)
TIME_RE = re.compile(r"^(?:\[)?(?P<time>\d{4}[-/]\d\d[-/]\d\d[ T]\d\d:\d\d:\d\d(?:[.,]\d+)?)")

def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _session_path(mod_root: Path, session_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f-]{36}", session_id, re.I):
        raise ValueError("invalid session_id")
    return mod_root / SESSION_DIR / f"{session_id}.json"

def _load(mod_root: Path, session_id: str) -> dict[str, Any]:
    path = _session_path(mod_root, session_id)
    if not path.is_file():
        raise ValueError("playtest session not found")
    return json.loads(path.read_text(encoding="utf-8"))

def _save(mod_root: Path, session: Mapping[str, Any]) -> None:
    directory = mod_root / SESSION_DIR
    directory.mkdir(exist_ok=True)
    target = directory / f"{session['session_id']}.json"
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(session, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, target)

def _snapshot_path(mod_root: Path, session_id: str) -> Path:
    return mod_root / SESSION_DIR / f"{session_id}.planned.scene"

def _observed_snapshot_path(mod_root: Path, session_id: str) -> Path:
    return mod_root / SESSION_DIR / f"{session_id}.observed.scene"

def discover_runtime_logs(scene_path: Path, mod_root: Path) -> list[Path]:
    """Discover only exact known log filenames in proven ancestors of the Mod root."""
    found: list[Path] = []
    current = mod_root
    for _ in range(4):
        for name in LOG_NAMES:
            candidate = current / name
            if candidate.is_file() and candidate not in found:
                found.append(candidate.resolve())
        if current.parent == current:
            break
        current = current.parent
    return found

def inspect_runtime_capabilities(scene_path: str | Path, mod_root: str | Path) -> dict[str, Any]:
    scene_path, mod_root = Path(scene_path).resolve(strict=True), Path(mod_root).resolve(strict=True)
    logs = discover_runtime_logs(scene_path, mod_root)
    editor = os.environ.get("PUMMELMCP_EDITOR_EXE")
    game = os.environ.get("PUMMELMCP_GAME_EXE")
    editor_ok = bool(editor and Path(editor).is_file())
    game_ok = bool(game and Path(game).is_file())
    return {
        "EDITOR_FOUND": {"grade": "CONFIRMED" if editor_ok else "NOT_OBSERVED", "path": str(Path(editor).resolve()) if editor_ok else None},
        "GAME_FOUND": {"grade": "CONFIRMED" if game_ok else "NOT_OBSERVED", "path": str(Path(game).resolve()) if game_ok else None},
        "PLAYTEST_LAUNCH_METHOD": "MANUAL_START_ONLY",
        "RUNTIME_LOG_FOUND": {"grade": "CONFIRMED" if logs else "UNAVAILABLE", "paths": [str(x) for x in logs]},
        "EDITOR_LOG_FOUND": {"grade": "CONFIRMED" if any(x.name == "Editor.log" for x in logs) else "NOT_OBSERVED"},
        "MOD_LOG_FOUND": {"grade": "NOT_OBSERVED"},
        "PROCESS_CONTROL_AVAILABLE": {"grade": "UNAVAILABLE"},
        "PLAYTEST_SESSION_BOUNDARY_DETECTABLE": {"grade": "CONFIRMED" if logs else "UNAVAILABLE", "method": "byte offsets and prefix SHA-256" if logs else None},
        "SCENE_LOAD_DETECTABLE": {"grade": "PARTIAL" if logs else "UNAVAILABLE"},
        "RUNTIME_ERROR_DETECTABLE": {"grade": "CONFIRMED" if logs else "UNAVAILABLE"},
        "ACTION_EXECUTION_DETECTABLE": {"grade": "NOT_OBSERVED"},
        "SPAWN_EVENT_DETECTABLE": {"grade": "NOT_OBSERVED"},
        "PLAYER_EVENT_DETECTABLE": {"grade": "NOT_OBSERVED"},
        "automatic_launch_allowed": False,
    }

def _baseline(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {"path": str(path), "size": len(data), "sha256": _sha(data), "mtime_ns": path.stat().st_mtime_ns}

def plan_playtest_session(scene_path: str | Path, mod_root: str | Path, *, composition_id: str | None = None,
                          gameplay_spec_hash: str | None = None, logical_mappings: Mapping[str, str] | None = None,
                          expected_assertions: list[str] | None = None, timeout_seconds: int = 600) -> dict[str, Any]:
    scene_path, mod_root = Path(scene_path).resolve(strict=True), Path(mod_root).resolve(strict=True)
    if not 10 <= timeout_seconds <= 3600:
        raise ValueError("timeout_seconds must be between 10 and 3600")
    scene = read_pmh(scene_path); validation = validate_scene(scene)
    if not scene.fully_consumed or not validation.passed:
        raise ValueError("PLAYTEST_SCENE_INVALID")
    logs = discover_runtime_logs(scene_path, mod_root)
    if not logs:
        raise ValueError("RUNTIME_OBSERVABILITY_BLOCKER: no structured log source discovered")
    mappings = dict(logical_mappings or {})
    guids = {o.guid for o in scene.walk()}
    if len(set(mappings.values())) != len(mappings) or any(g not in guids for g in mappings.values()):
        raise ValueError("INVALID_LOGICAL_GUID_MAPPING")
    session = {
        "session_id": str(uuid.uuid4()), "mod_path": str(mod_root), "scene_path": str(scene_path),
        "scene_sha256": _sha(scene_path.read_bytes()), "planned_raw_sha256": _sha(scene_path.read_bytes()),
        "planned_semantic_fingerprint": semantic_scene_fingerprint(scene_path), "gameplay_spec_hash": gameplay_spec_hash,
        "composition_id": composition_id, "logical_mappings": mappings,
        "created_at": _now(), "runtime_start_timestamp": None,
        "baseline_logs": [_baseline(x) for x in logs], "process_identity": None,
        "expected_assertions": expected_assertions or ["SceneLoaded", "NoRuntimeErrors", "ReferencesValid", "NoCrash"],
        "timeout_seconds": timeout_seconds, "launch_mode": "MANUAL", "status": "PLANNED",
    }
    _save(mod_root, session)
    _snapshot_path(mod_root, session["session_id"]).write_bytes(scene_path.read_bytes())
    return session

def start_playtest_session(mod_root: str | Path, session_id: str) -> dict[str, Any]:
    mod_root = Path(mod_root).resolve(strict=True); session = _load(mod_root, session_id)
    if _sha(Path(session["scene_path"]).read_bytes()) != session["scene_sha256"]:
        raise ValueError("STALE_PLAYTEST_SCENE")
    session["runtime_start_timestamp"] = _now()
    session["baseline_logs"] = [_baseline(Path(x["path"])) for x in session["baseline_logs"]]
    session["status"] = "PREPARED_WAITING_FOR_MANUAL_START"
    _save(mod_root, session)
    return session

def _parse_lines(text: str, mapping: Mapping[str, str]) -> list[dict[str, Any]]:
    reverse = {v.casefold(): k for k, v in mapping.items()}; entries=[]
    for line in text.splitlines():
        if not line.strip(): continue
        kinds = [kind for kind, rule in ERROR_RULES if rule.search(line)]
        severity = "ERROR" if kinds else ("WARNING" if re.search(r"\bwarn(?:ing)?\b", line, re.I) else "INFO")
        guids = GUID_RE.findall(line); stamp = TIME_RE.match(line)
        entries.append({"timestamp": stamp.group("time") if stamp else None, "severity": severity,
                        "source": "runtime_log", "message": line, "error_types": kinds,
                        "guids": guids, "logical_ids": [reverse[g.casefold()] for g in guids if g.casefold() in reverse]})
    return entries

def collect_playtest_evidence(mod_root: str | Path, session_id: str) -> dict[str, Any]:
    mod_root = Path(mod_root).resolve(strict=True); session = _load(mod_root, session_id)
    if session["status"] not in {"PREPARED_WAITING_FOR_MANUAL_START", "EVIDENCE_COLLECTED", "EVALUATED", "EVALUATED_STALE_SCENE"}:
        raise ValueError("PLAYTEST_SESSION_NOT_STARTED")
    observed=_observed_snapshot_path(mod_root,session_id)
    observed.write_bytes(Path(session["scene_path"]).read_bytes())
    session["observed_snapshot"]=str(observed)
    session["observed_raw_sha256"]=_sha(observed.read_bytes())
    session["observed_semantic_fingerprint"]=semantic_scene_fingerprint(observed)
    sources=[]; all_entries=[]
    for base in session["baseline_logs"]:
        path=Path(base["path"]); data=path.read_bytes(); size=int(base["size"])
        prefix_ok=len(data)>=size and _sha(data[:size])==base["sha256"]
        if not prefix_ok:
            previous=path.with_name("Player-prev.log") if path.name=="Player.log" else None
            previous_data=previous.read_bytes() if previous and previous.is_file() else b""
            rotation_confirmed=len(previous_data)>=size and _sha(previous_data[:size])==base["sha256"]
            if not rotation_confirmed:
                sources.append({"path":str(path),"status":"LOG_ROTATED_OR_TRUNCATED","baseline_size":size,"current_size":len(data)})
                continue
            chunk=data
            status="COLLECTED_AFTER_CONFIRMED_ROTATION"
        else:
            chunk=data[size:]
            status="COLLECTED"
        text=chunk.decode("utf-8",errors="replace"); entries=_parse_lines(text,session["logical_mappings"])
        sources.append({"path":str(path),"status":status,"baseline_size":size,"current_size":len(data),"new_bytes":len(chunk),"new_sha256":_sha(chunk)})
        all_entries.extend(entries)
    evidence={"session_id":session_id,"collected_at":_now(),"scene_sha256":session["scene_sha256"],"sources":sources,
              "entries":all_entries,"runtime_errors":[e for e in all_entries if e["error_types"]],
              "crashes":[e for e in all_entries if "CRASH" in e["error_types"]]}
    session["evidence"]=evidence; session["status"]="EVIDENCE_COLLECTED"; _save(mod_root,session)
    return evidence

def evaluate_playtest(mod_root: str | Path, session_id: str) -> dict[str, Any]:
    mod_root=Path(mod_root).resolve(strict=True); session=_load(mod_root,session_id)
    evidence=session.get("evidence")
    if not evidence: raise ValueError("PLAYTEST_EVIDENCE_NOT_COLLECTED")
    scene=read_pmh(session["scene_path"]); valid=scene.fully_consumed and validate_scene(scene).passed
    snapshot=_snapshot_path(mod_root,session_id); observed=_observed_snapshot_path(mod_root,session_id)
    identity=classify_scene_identity(snapshot,observed) if snapshot.is_file() and observed.is_file() else {
      "status":"STALE_PLAYTEST_SCENE","planned_raw_sha256":session.get("planned_raw_sha256",session["scene_sha256"]),
      "observed_raw_sha256":_sha(Path(session["scene_path"]).read_bytes()),"planned_semantic_fingerprint":session.get("planned_semantic_fingerprint"),
      "observed_semantic_fingerprint":semantic_scene_fingerprint(session["scene_path"]),"reason":"PLANNED_SNAPSHOT_UNAVAILABLE"}
    stable=identity["status"] in {"EXACT_SCENE_MATCH","SEMANTIC_SCENE_MATCH"}
    isolated=all(x["status"] in {"COLLECTED","COLLECTED_AFTER_CONFIRMED_ROTATION"} for x in evidence["sources"])
    errors=evidence["runtime_errors"]
    assertions={
      "SceneLoaded":{"result":"NOT_OBSERVABLE","evidence_grade":"NOT_OBSERVED"},
      "NoRuntimeErrors":{"result":"PASS" if isolated and not errors else ("FAIL" if errors else "UNKNOWN"),"evidence_grade":"CONFIRMED" if isolated else "UNKNOWN"},
      "ReferencesValid":{"result":"PASS" if valid and stable else "FAIL","evidence_grade":"CONFIRMED","scope":"STATIC"},
      "TriggerObserved":{"result":"NOT_OBSERVABLE","evidence_grade":"NOT_OBSERVED"},
      "ActionExecuted":{"result":"NOT_OBSERVABLE","evidence_grade":"NOT_OBSERVED"},
      "PrefabSpawned":{"result":"NOT_OBSERVABLE","evidence_grade":"NOT_OBSERVED"},
      "NoCrash":{"result":"PASS" if isolated and not evidence["crashes"] else ("FAIL" if evidence["crashes"] else "UNKNOWN"),"evidence_grade":"CONFIRMED" if isolated else "UNKNOWN"},
    }
    diagnoses=[]
    for entry in errors:
        for kind in entry["error_types"]:
            diagnoses.append({"failure_type":kind,"severity":"ERROR","evidence":entry["message"],
              "related_logical_id":entry["logical_ids"][0] if entry["logical_ids"] else None,
              "related_guid":entry["guids"][0] if entry["guids"] else None,"related_component":None,"related_action_rid":None,
              "confidence":"CONFIRMED","repairability":"MANUAL_REVIEW" if kind!="UNKNOWN_RUNTIME_ERROR" else "UNSUPPORTED"})
    status="FAIL" if any(x["result"]=="FAIL" for x in assertions.values()) else "PARTIAL"
    report={"session_id":session_id,"scene_sha256":session["scene_sha256"],"composition_id":session["composition_id"],
      "runtime_start":session["runtime_start_timestamp"],"runtime_end":evidence["collected_at"],"launch_mode":"MANUAL",
      "process_status":"NOT_OBSERVED","logs_inspected":[x["path"] for x in evidence["sources"]],"runtime_errors":errors,
      "assertions":assertions,"diagnostics":diagnoses,"logical_object_mappings":session["logical_mappings"],
      "repair_attempted":False,"repair_result":"NOT_APPLICABLE","replay_result":"NOT_PERFORMED","final_status":status,
      "scene_identity":identity,
      "repair_boundary":"Only existing approved deterministic writers may execute a separately reviewed repair plan."}
    session["report"]=report; session["status"]="EVALUATED"; _save(mod_root,session)
    return report
