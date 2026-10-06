from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import anyio
import pytest

from pummelmcp.mcp_server import create_server
from pummelmcp.minigame_config import get_minigame_config
from pummelmcp.pmh import read_pmh
from pummelmcp.pmh.actions import parse_action_payload
from pummelmcp.pmh.errors import ConcurrentModificationError, UnsafeDuplicationError
from pummelmcp.pmh.minigame_v2 import build_minigame_v2, plan_minigame_v2
from test_minigame_planner import setup, seed_phase3_templates


CONFIG_SOURCE = Path(__file__).resolve().parents[1] / "src" / "pummelmcp" / "templates" / "minimal-third-person" / "Data"


def ready_mod(tmp_path):
    mod, scene = setup(tmp_path)
    seed_phase3_templates(scene)
    for name in ("ModSettings.json", "MinigameDefinitionData.json"):
        shutil.copyfile(CONFIG_SOURCE / name, mod / "Data" / name)
    return mod, scene


def spec(scene):
    def pad(identifier, score, x):
        return {"id": identifier, "role": "trigger", "template_id": "button",
                "position": {"x": x, "y": 1, "z": 4},
                "rule": {"event_property": "OnEnterActions", "condition": "always", "actions": [
                    {"type": "CHANGE_SCORE", "target": "triggering_player", "operation": "add", "value": score}]}}
    return {"version": "0.2", "scene": str(scene), "output_mod": "BuiltArena",
            "title": "夺分竞技场", "description": "踩踏板得分，先到十分快者获胜。",
            "min_players": 2, "max_players": 8,
            "settings": {"rounds": 2, "end_conditions": ["obtain_points"],
                         "points_to_win": 10, "placement_condition": "most_points",
                         "movement_speed": 8, "use_health": True, "health_points": 80},
            "objects": [pad("pad_3", 3, -3), pad("pad_7", 7, 3),
                        {"id": "spawn_1", "role": "spawn", "template_id": "spawn",
                         "position": {"x": 0, "y": 1, "z": 0}}]}


def test_v2_plan_and_staged_build_preserve_source(tmp_path):
    mod, scene = ready_mod(tmp_path)
    request = spec(scene)
    request["objects"][0]["rule"]["condition"] = "once_per_player"
    request["objects"][1]["rule"]["condition"] = "once_global"
    original = {name: (mod / "Data" / name).read_bytes() for name in
                ("MainScene.scene", "ModSettings.json", "MinigameDefinitionData.json")}
    plan = plan_minigame_v2(scene, tmp_path, request)
    assert plan["status"] == "SAFE_TO_BUILD" and plan["runtime_status"] == "NOT_RUN"
    assert not (tmp_path / "BuiltArena").exists()
    result = build_minigame_v2(scene, tmp_path, request, plan["plan_sha256"])
    assert result["status"] == "BUILD_PASS"
    assert result["completion"] == "CONFIGURED_UNVERIFIED"
    assert (tmp_path / "BuiltArena").is_dir()
    assert all((mod / "Data" / name).read_bytes() == raw for name, raw in original.items())
    output_scene = Path(result["scene"])
    config = get_minigame_config(output_scene, tmp_path)
    assert config["settings"]["values"]["movement_speed"] == 8
    assert config["settings"]["values"]["points_to_win"] == 10
    assert config["details"]["values"]["name"] == "夺分竞技场"
    values = []
    for name in ("pad_3", "pad_7"):
        guid = result["created_objects"][name]
        obj = next(o for o in read_pmh(output_scene).walk() if o.guid == guid)
        action = parse_action_payload(obj.get_component("ModTrigger").get_field("OnEnterActions").raw).actions[0]
        values.append((action.fields["m_value"], action.fields["m_targetFlags"]))
    assert values == [(3, 1), (7, 1)]
    first = next(o for o in read_pmh(output_scene).walk() if o.guid == result["created_objects"]["pad_3"])
    second = next(o for o in read_pmh(output_scene).walk() if o.guid == result["created_objects"]["pad_7"])
    assert first.get_component("ModTrigger").get_field("OneUsePerPlayer").value is True
    assert second.get_component("ModTrigger").get_field("DisableAfterTriggered").value is True


def test_v2_rejects_unenforced_winner_and_stale_plan(tmp_path):
    mod, scene = ready_mod(tmp_path)
    request = spec(scene)
    request["settings"]["end_conditions"] = ["finish_minigame"]
    with pytest.raises(UnsafeDuplicationError, match="FINISH_ENDING_REQUIRES_PLACEMENT_ACTION"):
        plan_minigame_v2(scene, tmp_path, request)
    request = spec(scene)
    plan = plan_minigame_v2(scene, tmp_path, request)
    (mod / "Data" / "ModSettings.json").write_bytes((mod / "Data" / "ModSettings.json").read_bytes() + b" ")
    with pytest.raises(ConcurrentModificationError):
        build_minigame_v2(scene, tmp_path, request, plan["plan_sha256"])
    assert not (tmp_path / "BuiltArena").exists()


def test_v2_timed_health_and_placement_rules(tmp_path):
    _, scene = ready_mod(tmp_path)
    request = spec(scene)
    request["settings"]["end_conditions"] = ["finish_minigame"]
    request["settings"]["placement_condition"] = "finish_order"
    request["objects"][0]["rule"] = {
        "event_property": "OnStayActions", "condition": "always", "interval_seconds": 0.5,
        "actions": [{"type": "CHANGE_HEALTH", "target": "triggering_player", "operation": "subtract", "value": 20}]}
    request["objects"][1]["rule"] = {
        "event_property": "OnEnterActions", "condition": "always",
        "actions": [{"type": "SET_PLACEMENT", "target": "triggering_player"}]}
    plan = plan_minigame_v2(scene, tmp_path, request)
    result = build_minigame_v2(scene, tmp_path, request, plan["plan_sha256"])
    output = read_pmh(result["scene"])
    damage = next(o for o in output.walk() if o.guid == result["created_objects"]["pad_3"])
    trigger = damage.get_component("ModTrigger")
    assert trigger.get_field("TriggerOnStay").value is True
    assert trigger.get_field("StayTriggerInterval").value == 0.5
    action = parse_action_payload(trigger.get_field("OnStayActions").raw).actions[0]
    assert action.type_info.class_name == "ChangeHealthAction"
    assert (action.fields["m_operation"], action.fields["m_value"], action.fields["m_targetFlags"]) == (2, 20, 1)
    finish = next(o for o in output.walk() if o.guid == result["created_objects"]["pad_7"])
    finish_action = parse_action_payload(finish.get_component("ModTrigger").get_field("OnEnterActions").raw).actions[0]
    assert finish_action.type_info.class_name == "SetPlacementAction"


def test_v2_mcp_plan_and_build(tmp_path):
    from test_workflow import proposal
    workshop = tmp_path / "WorkshopMods"
    workshop.mkdir()
    _, scene = ready_mod(workshop)
    request = spec(scene)
    server = create_server(workshop)

    async def call(name, arguments):
        return await server.call_tool(name, arguments)

    anyio.run(call, "authorize_workshop_directory", {"workshop_directory": str(workshop), "user_granted_write_permission": True})
    review = anyio.run(call, "submit_gameplay_review", proposal(workshop / request["output_mod"]))
    anyio.run(call, "approve_gameplay_review", {"review_sha256": review.structured_content["review_sha256"], "user_approved": True, "user_reply": "Approve this test plan"})
    planned = anyio.run(call, "plan_minigame_v2", {"scene_path": str(scene), "spec": request})
    assert not planned.is_error
    assert planned.structured_content["status"] == "SAFE_TO_BUILD"
    built = anyio.run(call, "build_minigame_v2", {
        "scene_path": str(scene), "spec": request,
        "expected_plan_sha256": planned.structured_content["plan_sha256"],
    })
    assert not built.is_error
    assert built.structured_content["status"] == "BUILD_PASS"
    assert Path(built.structured_content["scene"]).is_file()


def test_v2_rejects_bad_inputs_missing_template_and_output_collision(tmp_path):
    _, scene = ready_mod(tmp_path)
    request = spec(scene)
    for invalid in ([], {}, 3):
        request["objects"][0]["rule"]["condition"] = invalid
        with pytest.raises(UnsafeDuplicationError, match="RULE_CONDITION_UNSUPPORTED"):
            plan_minigame_v2(scene, tmp_path, request)
    request = spec(scene)
    request["objects"][0]["role"] = []
    with pytest.raises(UnsafeDuplicationError, match="INVALID_MINIGAME_OBJECT_ROLE"):
        plan_minigame_v2(scene, tmp_path, request)
    request = spec(scene)
    request["objects"][0]["template_id"] = "missing-template"
    with pytest.raises(UnsafeDuplicationError):
        plan_minigame_v2(scene, tmp_path, request)
    assert not (tmp_path / "BuiltArena").exists()
    request = spec(scene)
    (tmp_path / "BuiltArena").mkdir()
    with pytest.raises(UnsafeDuplicationError, match="OUTPUT_MOD_ALREADY_EXISTS"):
        plan_minigame_v2(scene, tmp_path, request)


def test_v2_stage_failure_leaves_no_partial_mod(tmp_path, monkeypatch):
    import pummelmcp.pmh.minigame_v2 as v2

    mod, scene = ready_mod(tmp_path)
    request = spec(scene)
    plan = plan_minigame_v2(scene, tmp_path, request)
    source_digest = hashlib.sha256((mod / "Data" / "MainScene.scene").read_bytes()).hexdigest()

    def fail_config(*args, **kwargs):
        raise RuntimeError("injected staged write failure")

    monkeypatch.setattr(v2, "apply_minigame_config_update", fail_config)
    with pytest.raises(RuntimeError, match="injected staged write failure"):
        build_minigame_v2(scene, tmp_path, request, plan["plan_sha256"])
    assert not (tmp_path / "BuiltArena").exists()
    assert not list(tmp_path.glob(".pummelmcp-rule-build-*"))
    assert hashlib.sha256((mod / "Data" / "MainScene.scene").read_bytes()).hexdigest() == source_digest


def test_v2_requires_enforceable_end_settings(tmp_path):
    _, scene = ready_mod(tmp_path)
    request = spec(scene)
    del request["settings"]["points_to_win"]
    with pytest.raises(UnsafeDuplicationError, match="POINT_ENDING_REQUIRES_SCORE_ACTION"):
        plan_minigame_v2(scene, tmp_path, request)

    request = spec(scene)
    request["settings"]["end_conditions"] = ["timer"]
    with pytest.raises(UnsafeDuplicationError, match="TIMER_ENDING_REQUIRES_DURATION"):
        plan_minigame_v2(scene, tmp_path, request)

    request = spec(scene)
    request["settings"]["end_conditions"] = ["remaining_players_alive"]
    with pytest.raises(UnsafeDuplicationError, match="ALIVE_ENDING_REQUIRES_KILL_ACTION"):
        plan_minigame_v2(scene, tmp_path, request)

    request = spec(scene)
    request["settings"]["end_conditions"] = ["finish_minigame"]
    request["settings"]["placement_condition"] = "finish_order"
    request["objects"][1]["rule"]["actions"] = [
        {"type": "SET_PLACEMENT", "target": "triggering_player"}]
    request["objects"][1]["rule"]["condition"] = "once_global"
    with pytest.raises(UnsafeDuplicationError, match="FINISH_TRIGGER_CANNOT_BE_GLOBAL_ONCE"):
        plan_minigame_v2(scene, tmp_path, request)
