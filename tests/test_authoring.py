from __future__ import annotations
import copy
import os
import json
from pathlib import Path
import struct
import zipfile

import pytest

from pummelmcp.authoring import (Donor, Compiler, action_document, frame_actions,
    encode_scene, plan_authoring, build_authoring)
from pummelmcp.pmh.actions import parse_action_payload
from pummelmcp.pmh.reader import PMHReader
from pummelmcp.card_arena import generate_card_arena
from pummelmcp.mcp_server import create_server
from test_mcp_server import _structured


def test_action_framing_roundtrip_and_utf8():
    doc = {"m_type": 0, "m_actions": [{"rid": 1000}], "references": {"version": 2, "RefIds": [
        {"rid": 1000, "type": {"class": "ShowMessageAction", "ns": "ModSystem.Logic", "asm": "Assembly-CSharp"},
         "data": {"m_type": 1024, "m_version": 0, "m_targetFlags": 1, "m_targets": [],
                  "m_messageTarget": 2, "m_message": "小丑！" * 70, "m_duration": 1.0}}]}}
    parsed = parse_action_payload(frame_actions(doc))
    assert parsed.fully_consumed and len(parsed.actions) == 1
    assert parsed.root_json == doc
    assert parsed.references[0].data["m_message"] == "小丑！" * 70


@pytest.mark.parametrize('trigger_type', [0, 40, 96])
def test_binary_execution_order_follows_actions_not_reference_registry(trigger_type):
    # The game replaces JSON Actions with these trailing binary records on load.
    refs = [{"rid": rid, "type": {"class": "WaitAction", "ns": "ModSystem.Logic", "asm": "Assembly-CSharp"},
             "data": {"m_type": 32, "m_version": 0, "m_seconds": {"valueType": 0, "min": seconds, "max": seconds}}}
            for rid, seconds in [(1000, 5), (1001, 4), (1002, 2)]]
    doc = {"m_type": trigger_type, "m_actions": [{"rid": 1002}, {"rid": 1000}, {"rid": 1001}],
           "references": {"version": 2, "RefIds": refs}}
    raw = frame_actions(doc, prefix_type=trigger_type)
    assert struct.unpack_from('<H', raw, 0)[0] == trigger_type
    offset = 2
    def read_length():
        nonlocal offset
        value = shift = 0
        while True:
            byte = raw[offset]; offset += 1
            value |= (byte & 127) << shift
            if not byte & 128: return value
            shift += 7
    size = read_length(); offset += size
    count = struct.unpack_from('<H', raw, offset)[0]; offset += 2
    assert count == 3
    durations = []
    for _ in range(count):
        assert struct.unpack_from('<H', raw, offset)[0] == 32
        offset += 2
        size = read_length()
        durations.append(json.loads(raw[offset:offset+size])["m_seconds"]["min"])
        offset += size
    assert offset == len(raw)
    assert durations == [2, 5, 4]


def test_position_and_player_visual_actions_compile_with_bound_prefabs():
    class SourceBackedActions:
        actions = {
            "PositionAction": {"rid": 1, "type": {"class": "PositionAction", "ns": "ModSystem.Logic", "asm": "Assembly-CSharp"},
                "data": {"m_type": 384, "m_version": 0, "m_targetFlags": 2, "m_targets": [],
                    "m_space": 1, "m_operation": 0, "m_position": {"x": 1, "y": 2, "z": 3}}},
            "SetPlayerVisualAction": {"rid": 2, "type": {"class": "SetPlayerVisualAction", "ns": "ModSystem.Logic", "asm": "Assembly-CSharp"},
                "data": {"m_type": 1248, "m_version": 1, "m_targetFlags": 2, "m_targets": [],
                    "m_prefab": {"m_assetGUID": {"serializedGuid": "00000000-0000-0000-0000-000000000000"}, "m_asset": {"m_FileID": 0, "m_PathID": 0}},
                    "m_recolorPrefabUsingPlayerColor": False, "m_verticalLookTargetChildIndex": -1,
                    "m_movementRotationTargetChildIndex": -1}},
        }

    prefab = {"guid": {"serializedGuid": "aee1a792-b067-48d7-a59e-d2a4760076bf"}, "name": "Cube"}
    document = action_document([
        {"type": "PositionAction", "target": "source", "fields": {"m_space": 0, "m_operation": 0,
            "m_position": {"x": 0, "y": 0.1, "z": 8}}},
        {"type": "SetPlayerVisualAction", "target": "source", "prefab": "Cube",
            "fields": {"m_recolorPrefabUsingPlayerColor": False}},
        {"type": "SetPlayerVisualAction", "target": "source", "prefab": None},
    ], SourceBackedActions(), {"Cube": prefab})
    parsed = parse_action_payload(frame_actions(document))
    assert parsed.fully_consumed
    assert [item.type_info.class_name for item in parsed.references] == [
        "PositionAction", "SetPlayerVisualAction", "SetPlayerVisualAction"]
    assert parsed.references[0].data["m_position"] == {"x": 0, "y": 0.1, "z": 8}
    assert parsed.references[1].data["m_prefab"]["m_assetGUID"]["serializedGuid"] == prefab["guid"]["serializedGuid"]
    assert parsed.references[2].data["m_prefab"]["m_asset"]["m_PathID"] == 0


def test_unsafe_archive_paths_and_symlinks(tmp_path):
    for index, name in enumerate(("../bad", "a/../../bad", "a\\bad", "C:/bad", "a/CON.txt", "a/b. ")):
        path = tmp_path / f"bad{index}.zip"
        with zipfile.ZipFile(path, "w") as z:
            info = zipfile.ZipInfo("placeholder"); info.filename = name
            z.writestr(info, b"bad")
        with pytest.raises(ValueError, match="UNSAFE_ARCHIVE_MEMBER"): Donor(path, [tmp_path])
    path = tmp_path / "link.zip"
    with zipfile.ZipFile(path, "w") as z:
        info = zipfile.ZipInfo("a/link"); info.external_attr = 0o120777 << 16
        z.writestr(info, "outside")
    with pytest.raises(ValueError, match="UNSAFE_ARCHIVE_MEMBER"): Donor(path, [tmp_path])


def test_read_root_escape(tmp_path):
    outside = tmp_path / "outside.zip"; outside.write_bytes(b"bad")
    inside = tmp_path / "inside"; inside.mkdir()
    with pytest.raises(ValueError, match="SOURCE_OUTSIDE"): Donor(outside, [inside])


def test_hierarchy_cycles_are_rejected():
    nodes = [{"id": "a", "parent": "b"}, {"id": "b", "parent": "a"}]
    with pytest.raises(ValueError, match="ROOT"): encode_scene(nodes)


def test_reference_encoder_is_byte_exact_for_existing_fixture(main_scene_path):
    model = PMHReader().read_path(main_scene_path)
    nodes = [{"id": o.guid, "guid": o.guid, "parent": o.parent.guid if o.parent else None,
        "name": o.name, "active": o.active, "layer": o.layer, "tag": o.tag,
        "components": [{"type": c.type_name, "guid": c.guid, "fields": {f.name: f.raw for f in c.fields}}
                       for c in o.components]} for o in model.walk()]
    # Disabled components require their enabled flag, so the lossless regression
    # fixture explicitly asserts the source matches the encoder's enabled surface.
    assert all(c.enabled for o in model.walk() for c in o.components)
    assert encode_scene(nodes) == main_scene_path.read_bytes()


@pytest.fixture
def real_donor():
    configured = os.environ.get("PUMMELMCP_TEST_DONOR_ARCHIVE")
    if not configured:
        pytest.skip("set PUMMELMCP_TEST_DONOR_ARCHIVE for optional donor integration tests")
    path = Path(configured)
    if not path.is_file(): pytest.skip("optional read-only Joker integration archive unavailable")
    return path


@pytest.fixture
def planned(real_donor, tmp_path):
    roots = [real_donor.parent]
    data = generate_card_arena(real_donor, roots, tmp_path,
        {"output_mod": "TestArena", "title": "测试竞技场", "players": 4, "scores": [3, 7], "joker_weight": 3}, dry_run=True)
    return roots, data


def test_full_build_and_bindings(real_donor, planned, tmp_path):
    roots, data = planned
    before = real_donor.read_bytes()
    result = build_authoring(real_donor, roots, tmp_path, data["spec"], data["plan"]["plan_sha256"])
    assert result["runtime_status"] == "NOT_RUN"
    output = Path(result["output_mod"])
    workshop = json.loads((output / "Data/WorkshopItem.json").read_bytes())
    assert workshop["title"] == data["spec"]["title"]
    assert workshop["publishedFileId"] == 256  # fresh, unpublished local Mod
    assert workshop["visibility"] == 2
    assert workshop["isInternal"] is False
    assert workshop["previewFiles"] == []
    assert not list(output.rglob("*.bak*"))
    scene = PMHReader().read_path(output / "Data/MainScene.scene")
    assert scene.object_count == 20
    ids = {json.loads(p.read_bytes())["guid"]["serializedGuid"] for p in output.glob("Assets/Prefabs/*.pmeta")}
    assert len(ids) == 5
    triggers = [c for o in scene.walk() for c in o.components if c.type_name == "ModTrigger"]
    assert len(triggers) == 4
    for trigger in triggers:
        parsed = parse_action_payload(trigger.get_field("OnHitActions").raw)
        pool = parsed.references[0].data["m_prefabs"]
        assert len(pool) == 5  # two score cards plus three equal-probability Joker slots
        assert all(p["m_assetGUID"]["serializedGuid"] in ids for p in pool)
        assert pool[-1] == pool[-2] == pool[-3]
        assert not parse_action_payload(trigger.get_field("OnEnterActions").raw).actions
    card = PMHReader().read_prefab_bytes((output / "Assets/Prefabs/Card_2.pfab").read_bytes())
    item = card.roots[0].get_component("ModItem")
    assert item.get_field("ItemPrefabGUID").raw[1:].decode() in ids
    assert parse_action_payload(item.get_field("OnPickupTrigger").raw).references[0].data["m_value"] == 7
    settings = json.loads((output / "Data/ModSettings.json").read_bytes())
    assert len(settings["references"]["RefIds"]) == 1
    assert settings["references"]["RefIds"][0]["type"]["class"] == "ChangeVelocityAction"
    assert settings["SimpleModPlayerSettings"]["weaponHitTrigger"]["m_actions"] == [{"rid": 1000}]
    assert settings["SimpleModPlayerSettings"]["onDeathTrigger"]["m_actions"] == []
    assert real_donor.read_bytes() == before
    assert not list(tmp_path.glob(".authoring-*"))
    with pytest.raises(ValueError, match="OUTPUT_ALREADY_EXISTS"):
        build_authoring(real_donor, roots, tmp_path, data["spec"], data["plan"]["plan_sha256"])


def test_plan_is_readonly_deterministic_and_stale_rejected(real_donor, planned, tmp_path):
    roots, data = planned
    again, _ = plan_authoring(real_donor, roots, tmp_path, data["spec"])
    assert again == data["plan"]
    assert list(tmp_path.iterdir()) == []
    changed = copy.deepcopy(data["spec"]); changed["title"] = "Changed"
    with pytest.raises(ValueError, match="STALE"):
        build_authoring(real_donor, roots, tmp_path, changed, data["plan"]["plan_sha256"])
    assert list(tmp_path.iterdir()) == []


def test_player_event_ids_are_global_and_old_logic_is_removed(real_donor, planned, tmp_path):
    roots, data = planned
    spec = copy.deepcopy(data["spec"])
    spec["player_events"]["tickTrigger"] = [{"type": "ShowMessageAction", "target": "source", "fields": {"m_message": "Tick"}}]
    spec["player_tick_seconds"] = 3
    _, files = plan_authoring(real_donor, roots, tmp_path, spec)
    settings = json.loads(files["Data/ModSettings.json"])
    assert [r["rid"] for r in settings["references"]["RefIds"]] == [1000, 1001]
    assert settings["SimpleModPlayerSettings"]["tickTrigger"]["m_actions"] == [{"rid": 1001}]
    assert settings["SimpleModPlayerSettings"]["tickTrigger"]["m_interval"] == {"valueType": 0, "min": 3, "max": 3}


def test_hit_and_enter_cannot_be_enabled_together(real_donor, planned, tmp_path):
    roots, data = planned
    spec = copy.deepcopy(data["spec"])
    component = next(c for node in spec["scene"] for c in node["components"] if c["type"] == "ModTrigger")
    component["events"]["OnEnterActions"] = copy.deepcopy(component["events"]["OnHitActions"])
    with pytest.raises(ValueError, match="EXCLUSIVE_IN_ENGINE"):
        plan_authoring(real_donor, roots, tmp_path, spec)


def test_unknown_actions_fields_and_bindings_fail_closed(real_donor, planned, tmp_path):
    roots, data = planned
    for mutation, expected in (("action", "ACTION_TEMPLATE"), ("binding", "UNBOUND"), ("cycle", "CYCLE"), ("settings", "min_players")):
        spec = copy.deepcopy(data["spec"])
        action = spec["prefabs"][2]["nodes"][0]["components"][0]["events"]["OnPickupTrigger"][0]
        if mutation == "action": action["type"] = "ExecuteCodeAction"
        if mutation == "binding": spec["prefabs"][2]["nodes"][0]["components"][0]["item_visual"] = "Missing"
        if mutation == "cycle": spec["prefabs"][2]["nodes"][0]["components"][0]["item_visual"] = "Card_1"
        if mutation == "settings": spec["min_players"] = 8
        with pytest.raises(ValueError, match=expected): plan_authoring(real_donor, roots, tmp_path, spec)
    assert list(tmp_path.iterdir()) == []


def test_output_cannot_be_in_readonly_root(real_donor, planned):
    roots, data = planned
    with pytest.raises(ValueError, match="READ_ONLY"):
        plan_authoring(real_donor, roots, real_donor.parent, data["spec"])


def test_mcp_end_to_end(real_donor, tmp_path):
    workshop = tmp_path / "WorkshopMods"
    workshop.mkdir()
    server = create_server(allowed_root=tmp_path, authoring_read_roots=[real_donor.parent])
    authorization = _structured(server, "authorize_workshop_directory", {
        "workshop_directory": str(workshop), "user_granted_write_permission": True})
    assert authorization["authorized"] is True
    audit = _structured(server, "audit_minigame_archive", {"archive_path": str(real_donor)})
    assert len(audit["pmh_files"]) == 27
    review = _structured(server, "submit_gameplay_review", {
        "game_directory": str(workshop / "McpArena"), "requested_rules": ["Two-player card arena"],
        "capability_assessment": [{"requested_rule": "Two-player card arena", "status": "supported",
            "editor_evidence": "Audited Joker donor and native score/trigger components",
            "implementation_or_boundary": "Use the native card arena recipe, two players, score 5"}],
        "gameplay_plan": {"flow": "Spawn, collect cards, settle scores", "rules_and_parameters": {"players": 2, "scores": [5]},
            "scene_and_assets": "Audited Joker donor", "limitations": "Runtime untested", "playtest_checks": ["Pickup updates score"]}})
    _structured(server, "approve_gameplay_review", {"review_sha256": review["review_sha256"],
        "user_approved": True, "user_reply": "I approve this exact plan."})
    result = _structured(server, "generate_card_minigame", {"archive_path": str(real_donor),
        "options": {"output_mod": "McpArena", "title": "MCP Arena", "players": 2, "scores": [5]}})
    assert result["report"]["status"] == "BUILD_PASS"
    assert result["report"]["runtime_status"] == "NOT_RUN"
    assert Path(result["report"]["output_mod"]).parent == workshop


def test_failed_publish_cleans_staging(real_donor, planned, tmp_path, monkeypatch):
    roots, data = planned
    def broken(*args): raise OSError("simulated publishing failure")
    monkeypatch.setattr("pummelmcp.authoring.os.rename", broken)
    with pytest.raises(OSError, match="simulated"):
        build_authoring(real_donor, roots, tmp_path, data["spec"], data["plan"]["plan_sha256"])
    assert list(tmp_path.iterdir()) == []
