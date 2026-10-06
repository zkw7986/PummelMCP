from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import anyio
from mcp import Client

from pummelmcp.mcp_server import create_server
from pummelmcp.pmh import ACTION_EVENT_PROPERTIES, parse_action_payload, read_pmh


def _target(path: Path) -> tuple[str, str, str, int]:
    for obj in read_pmh(path).walk():
        for component in obj.components:
            if component.type_name != "ModTrigger":
                continue
            for field in component.fields:
                if field.name not in ACTION_EVENT_PROPERTIES:
                    continue
                for action in parse_action_payload(field.raw).actions:
                    if action.type_info and action.type_info.class_name == "SpawnPrefabAction":
                        return obj.guid, component.guid, field.name, action.rid
    raise AssertionError("no SpawnPrefabAction")


def _call(server, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    async def invoke():
        result = await server.call_tool(name, arguments)
        return result.structured_content
    return anyio.run(invoke)


def test_graph_tools_list(main_scene_path: Path) -> None:
    tools = anyio.run(create_server(main_scene_path.parent).list_tools)
    by_name = {item.name: item for item in tools}
    assert {"add_action", "delete_action", "move_action"} <= set(by_name)
    assert all(by_name[name].annotations.read_only_hint is False for name in ("add_action", "delete_action", "move_action"))
    assert "Raw JSON" in by_name["add_action"].description


def test_mcp_add_move_delete_round_trip(main_scene_path: Path, tmp_path: Path) -> None:
    scene = tmp_path / "MainScene.scene"
    scene.write_bytes(main_scene_path.read_bytes())
    target = _target(scene)
    server = create_server(tmp_path)
    inspect_args = {
        "scene_path": str(scene), "object_identifier": target[0],
        "component_identifier": target[1], "event_property": target[2],
    }
    before = _call(server, "inspect_action_list", inspect_args)
    added = _call(server, "add_action", {
        **inspect_args, "action_class": "SpawnPrefabAction",
        "expected_payload_sha256": before["sha256"],
    })
    assert added["affected_rid"] == 1001
    after_add = _call(server, "inspect_action_list", inspect_args)
    assert [item["rid"] for item in after_add["actions"]] == [1000, 1001]
    moved = _call(server, "move_action", {
        **inspect_args, "action_rid": 1001, "new_index": 0,
        "expected_payload_sha256": after_add["sha256"],
    })
    assert moved["validation"]["checks"]["refids_unchanged"] is True
    after_move = _call(server, "inspect_action_list", inspect_args)
    assert [item["rid"] for item in after_move["actions"]] == [1001, 1000]
    deleted = _call(server, "delete_action", {
        **inspect_args, "action_rid": 1001,
        "expected_payload_sha256": after_move["sha256"],
    })
    assert deleted["validation"]["checks"]["deleted_rid_absent"] is True
    final = _call(server, "inspect_action_list", inspect_args)
    assert [item["rid"] for item in final["actions"]] == [1000]
    assert _call(server, "validate_scene", {"scene_path": str(scene)})["valid"] is True


def test_new_action_can_use_existing_writers(main_scene_path: Path, tmp_path: Path) -> None:
    mod = tmp_path / "mod"
    data = mod / "Data"
    assets = mod / "Assets" / "Prefabs"
    data.mkdir(parents=True); assets.mkdir(parents=True)
    scene = data / "MainScene.scene"
    scene.write_bytes(main_scene_path.read_bytes())
    target = _target(scene)
    server = create_server(mod)
    base = {
        "scene_path": str(scene), "object_identifier": target[0],
        "component_identifier": target[1], "event_property": target[2],
    }
    before = _call(server, "inspect_action_list", base)
    _call(server, "add_action", {**base, "action_class": "SpawnPrefabAction", "expected_payload_sha256": before["sha256"]})
    added = _call(server, "inspect_action_list", base)
    original_value = added["actions"][1]["fields"]["m_parentToTarget"]
    _call(server, "set_action_field", {
        **base, "action_rid": 1001, "expected_class": "SpawnPrefabAction",
        "field_name": "m_parentToTarget", "value": not original_value,
        "expected_payload_sha256": added["sha256"],
    })
    changed = _call(server, "inspect_action_list", base)
    assert changed["actions"][1]["fields"]["m_parentToTarget"] is not original_value
    references = _call(server, "inspect_action_references", {**base, "action_rid": 1001, "limit": 100})
    prefab_field = next(item for item in references["references"] if item["field"] == "m_prefabs")
    current_guid = prefab_field["items"][0]["normalized_value"]["guid"]
    target_guid = next(
        item["normalized_value"]["guid"] for item in prefab_field["items"]
        if item["normalized_value"]["guid"] != current_guid
    )
    for index, guid in enumerate((current_guid, target_guid)):
        asset = assets / f"Known{index}.pfab"
        asset.write_bytes(b"asset")
        Path(str(asset) + ".pmeta").write_text(
            json.dumps({"name": f"Known{index}", "guid": {"serializedGuid": guid}, "tags": ["prefabs"]}),
            encoding="utf-8",
        )
    references = _call(server, "inspect_action_references", {**base, "action_rid": 1001, "limit": 100})
    replaced = _call(server, "replace_prefab_reference", {
        **base, "action_rid": 1001, "prefab_index": 0,
        "target_prefab_guid": target_guid,
        "expected_current_prefab_guid": current_guid,
        "expected_payload_sha256": references["payload_sha256"],
    })
    assert replaced["after"]["guid"] == target_guid
    assert _call(server, "validate_scene", {"scene_path": str(scene)})["valid"] is True


def test_inspection_exposes_template_hash(main_scene_path: Path) -> None:
    target = _target(main_scene_path)
    result = _call(create_server(main_scene_path.parent), "inspect_action_list", {
        "scene_path": str(main_scene_path), "object_identifier": target[0],
        "component_identifier": target[1], "event_property": target[2],
    })
    assert len(result["actions"][0]["managed_reference_sha256"]) == 64
