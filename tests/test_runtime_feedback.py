from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from pummelmcp.pmh import read_pmh
from pummelmcp.pmh.runtime import (
    collect_playtest_evidence,
    evaluate_playtest,
    inspect_runtime_capabilities,
    plan_playtest_session,
    start_playtest_session,
)


@pytest.fixture
def runtime_mod(tmp_path: Path, main_scene_path: Path):
    root = tmp_path / "Stage14 Runtime Verify"
    data = root / "Data"; data.mkdir(parents=True)
    scene = data / "MainScene.scene"; shutil.copyfile(main_scene_path, scene)
    log = root / "Player.log"; log.write_text("old error must not be attributed\n", encoding="utf-8")
    return root, scene, log


def test_capability_report_is_evidence_graded_and_manual(runtime_mod):
    root, scene, log = runtime_mod
    report = inspect_runtime_capabilities(scene, root)
    assert report["RUNTIME_LOG_FOUND"]["grade"] == "CONFIRMED"
    assert report["PLAYTEST_SESSION_BOUNDARY_DETECTABLE"]["grade"] == "CONFIRMED"
    assert report["PLAYTEST_LAUNCH_METHOD"] == "MANUAL_START_ONLY"
    assert report["automatic_launch_allowed"] is False


def test_session_is_hash_bound_incremental_and_does_not_infer_spawn(runtime_mod):
    root, scene_path, log = runtime_mod
    guid = next(read_pmh(scene_path).walk()).guid
    planned = plan_playtest_session(scene_path, root, logical_mappings={"root": guid}, timeout_seconds=60)
    assert (root / ".pummelmcp-playtests" / f"{planned['session_id']}.planned.scene").read_bytes() == scene_path.read_bytes()
    assert planned["scene_sha256"] == hashlib.sha256(scene_path.read_bytes()).hexdigest()
    started = start_playtest_session(root, planned["session_id"])
    assert started["status"] == "PREPARED_WAITING_FOR_MANUAL_START"
    with log.open("a", encoding="utf-8") as stream:
        stream.write("Playtest began\n")
    evidence = collect_playtest_evidence(root, planned["session_id"])
    assert (root / ".pummelmcp-playtests" / f"{planned['session_id']}.observed.scene").read_bytes() == scene_path.read_bytes()
    assert [x["message"] for x in evidence["entries"]] == ["Playtest began"]
    report = evaluate_playtest(root, planned["session_id"])
    assert report["assertions"]["NoRuntimeErrors"]["result"] == "PASS"
    assert report["assertions"]["PrefabSpawned"]["result"] == "NOT_OBSERVABLE"
    assert report["final_status"] == "PARTIAL"


def test_stale_scene_fails_closed(runtime_mod):
    root, scene, _ = runtime_mod
    planned = plan_playtest_session(scene, root)
    scene.write_bytes(scene.read_bytes() + b"x")
    with pytest.raises(ValueError, match="STALE_PLAYTEST_SCENE"):
        start_playtest_session(root, planned["session_id"])


def test_log_rotation_invalidates_no_error_assertion(runtime_mod):
    root, scene, log = runtime_mod
    planned = plan_playtest_session(scene, root); start_playtest_session(root, planned["session_id"])
    log.write_text("replacement\n", encoding="utf-8")
    evidence = collect_playtest_evidence(root, planned["session_id"])
    assert evidence["sources"][0]["status"] == "LOG_ROTATED_OR_TRUNCATED"
    assert evaluate_playtest(root, planned["session_id"])["assertions"]["NoRuntimeErrors"]["result"] == "UNKNOWN"


def test_confirmed_unity_log_rotation_collects_new_player_log(runtime_mod):
    root, scene, log = runtime_mod
    planned = plan_playtest_session(scene, root); start_playtest_session(root, planned["session_id"])
    (root / "Player-prev.log").write_bytes(log.read_bytes())
    log.write_text("new runtime line\n", encoding="utf-8")
    evidence = collect_playtest_evidence(root, planned["session_id"])
    assert evidence["sources"][0]["status"] == "COLLECTED_AFTER_CONFIRMED_ROTATION"
    assert [x["message"] for x in evidence["entries"]] == ["new runtime line"]
    assert evaluate_playtest(root, planned["session_id"])["assertions"]["NoRuntimeErrors"]["result"] == "PASS"


def test_error_taxonomy_and_logical_guid_correlation(runtime_mod):
    root, scene, log = runtime_mod
    guid = next(read_pmh(scene).walk()).guid
    planned = plan_playtest_session(scene, root, logical_mappings={"arena_root": guid})
    start_playtest_session(root, planned["session_id"])
    with log.open("a", encoding="utf-8") as stream:
        stream.write(f"Null reference exception on action {guid}\n")
    collect_playtest_evidence(root, planned["session_id"])
    report = evaluate_playtest(root, planned["session_id"])
    assert report["assertions"]["NoRuntimeErrors"]["result"] == "FAIL"
    assert report["diagnostics"][0]["related_logical_id"] == "arena_root"
    assert {d["failure_type"] for d in report["diagnostics"]} >= {"MISSING_REFERENCE", "RUNTIME_EXCEPTION"}


def test_stage14_tests_have_no_environment_skip(runtime_mod):
    root, scene, _ = runtime_mod
    with pytest.raises(ValueError, match="timeout_seconds"):
        plan_playtest_session(scene, root, timeout_seconds=0)

def test_snapshot_lifecycle_records_both_fingerprints(runtime_mod):
    root,scene,_=runtime_mod;planned=plan_playtest_session(scene,root);start_playtest_session(root,planned['session_id']);collect_playtest_evidence(root,planned['session_id']);report=evaluate_playtest(root,planned['session_id'])
    assert report['scene_identity']['status']=='EXACT_SCENE_MATCH'
    assert report['scene_identity']['planned_semantic_fingerprint']==report['scene_identity']['observed_semantic_fingerprint']
