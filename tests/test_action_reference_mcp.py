from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

from pummelmcp.mcp_server import create_server
from pummelmcp.pmh import ACTION_EVENT_PROPERTIES, parse_action_payload, read_pmh


def _call(server, name: str, arguments: dict[str, Any]):
    async def invoke():
        return await server.call_tool(name, arguments)

    return anyio.run(invoke)


def _structured(server, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = _call(server, name, arguments)
    assert not result.is_error
    assert isinstance(result.structured_content, dict)
    return result.structured_content


def _target(path: Path, class_name: str) -> tuple[str, str, str, int]:
    for obj in read_pmh(path).walk():
        for component in obj.components:
            if component.type_name != "ModTrigger":
                continue
            for field in component.fields:
                if field.name not in ACTION_EVENT_PROPERTIES:
                    continue
                for action in parse_action_payload(field.raw).actions:
                    if action.type_info and action.type_info.class_name == class_name:
                        return obj.guid, component.guid, field.name, action.rid
    raise AssertionError(f"fixture has no {class_name}")


def _arguments(path: Path, class_name: str) -> dict[str, Any]:
    object_guid, component_guid, event_property, rid = _target(path, class_name)
    return {
        "scene_path": str(path),
        "object_identifier": object_guid,
        "component_identifier": component_guid,
        "event_property": event_property,
        "action_rid": rid,
    }


def _reference(result: dict[str, Any], field: str) -> dict[str, Any]:
    return next(item for item in result["references"] if item["field"] == field)


def _contains_bytes_or_absolute_path(value: Any) -> bool:
    if isinstance(value, dict):
        if any(key in {"raw", "raw_bytes", "raw_segment", "raw_payload"} for key in value):
            return True
        return any(_contains_bytes_or_absolute_path(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_bytes_or_absolute_path(item) for item in value)
    if isinstance(value, str):
        return ":\\" in value or ":/" in value
    return False


def test_mcp_tools_list_reference_tool(main_scene_path: Path) -> None:
    tools = anyio.run(create_server(main_scene_path.parent).list_tools)
    tool = next(item for item in tools if item.name == "inspect_action_references")
    assert tool.annotations.read_only_hint is True
    assert tool.annotations.destructive_hint is False
    assert tool.annotations.idempotent_hint is True
    assert tool.annotations.open_world_hint is False


def test_mcp_inspect_spawn_prefab_references(main_scene_path: Path) -> None:
    result = _structured(
        create_server(main_scene_path.parent),
        "inspect_action_references",
        _arguments(main_scene_path, "SpawnPrefabAction"),
    )
    prefabs = _reference(result, "m_prefabs")
    assert result["action"]["class"] == "SpawnPrefabAction"
    assert prefabs["kind"] == "PrefabReferenceList"
    assert prefabs["count"] == 23
    assert prefabs["returned"] == 10
    assert prefabs["items"][0]["kind"] == "PrefabReference"
    assert len(prefabs["items"][0]["reference_sha256"]) == 64
    assert prefabs["items"][0]["value_spans"]["$.m_assetGUID.serializedGuid"]["length"] > 2
    assert result["writable"] is prefabs["writable"] is False
    assert not _contains_bytes_or_absolute_path(result)


def test_mcp_reference_pagination(main_scene_path: Path) -> None:
    arguments = {**_arguments(main_scene_path, "SpawnPrefabAction"), "offset": 20, "limit": 2}
    prefabs = _reference(
        _structured(create_server(main_scene_path.parent), "inspect_action_references", arguments),
        "m_prefabs",
    )
    assert prefabs["count"] == 23
    assert prefabs["offset"] == 20
    assert prefabs["returned"] == 2
    assert [item["index"] for item in prefabs["items"]] == [20, 21]
    assert prefabs["has_more"] is True


def test_mcp_play_sound_reference(main_scene_path: Path) -> None:
    result = _structured(
        create_server(main_scene_path.parent),
        "inspect_action_references",
        _arguments(main_scene_path, "PlaySoundAction"),
    )
    clip = _reference(result, "m_clip")
    assert clip["kind"] == "AudioReference"
    assert clip["normalized_value"]["guid"] == "10a663ec-e461-4711-8db8-4aadbc86aeb5"
    assert clip["items"][0]["resolution"]["resolved"] is False
    assert clip["semantic_status"] == "UNKNOWN"


def test_mcp_target_reference(main_scene_path: Path) -> None:
    result = _structured(
        create_server(main_scene_path.parent),
        "inspect_action_references",
        _arguments(main_scene_path, "KillAction"),
    )
    flags = _reference(result, "m_targetFlags")
    targets = _reference(result, "m_targets")
    assert flags["normalized_value"] == {"raw_value": 1, "semantic": None}
    assert targets["normalized_value"] == {"count": 0, "semantic": None}
    assert flags["scene_source_span"]["length"] == 1


def _varuint7(value: int) -> bytes:
    result = bytearray()
    while value >= 0x80:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value)
    return bytes(result)


def _action_payload(data: dict[str, Any]) -> bytes:
    ref = {
        "rid": 1000,
        "type": {"class": "FutureAction", "ns": "ModSystem.Logic", "asm": "Assembly-CSharp"},
        "data": data,
    }
    root = {"m_type": 0, "m_actions": [{"rid": 1000}], "references": {"version": 2, "RefIds": [ref]}}
    root_raw = json.dumps(root, separators=(",", ":")).encode()
    data_raw = json.dumps(data, separators=(",", ":")).encode()
    return (
        b"\0\0" + _varuint7(len(root_raw)) + root_raw + b"\1\0"
        + int(data["m_type"]).to_bytes(2, "little")
        + _varuint7(len(data_raw)) + data_raw
    )


def _replace_first_action_payload(source: Path, target: Path, payload: bytes) -> None:
    raw = source.read_bytes()
    scene = read_pmh(source)
    obj = next(item for item in scene.walk() if any(c.type_name == "ModTrigger" for c in item.components))
    component = next(c for c in obj.components if c.type_name == "ModTrigger")
    field = component.get_field("OnHitActions")
    start = field.source_span.start
    target.write_bytes(raw[: start - 4] + len(payload).to_bytes(4, "little") + payload + raw[field.source_span.end :])


def test_mcp_unknown_reference(tmp_path: Path, main_scene_path: Path) -> None:
    target = tmp_path / "unknown.scene"
    _replace_first_action_payload(
        main_scene_path,
        target,
        _action_payload({"m_type": 777, "futureReference": {"serializedGuid": "future", "unknown": 42}}),
    )
    arguments = _arguments(target, "FutureAction")
    result = _structured(create_server(tmp_path), "inspect_action_references", arguments)
    unknown = _reference(result, "futureReference")
    assert unknown["kind"] == "UnknownReference"
    assert unknown["items"][0]["unknown_fields"] == {"serializedGuid": "future", "unknown": 42}


def test_mcp_reference_tool_read_only(main_scene_path: Path) -> None:
    before = main_scene_path.read_bytes()
    result = _structured(
        create_server(main_scene_path.parent),
        "inspect_action_references",
        _arguments(main_scene_path, "SpawnPrefabAction"),
    )
    scene = read_pmh(main_scene_path)
    obj = next(item for item in scene.walk() if item.guid == result["object"]["guid"])
    component = next(item for item in obj.components if item.guid == result["component"]["guid"])
    assert result["payload_sha256"] == hashlib.sha256(
        component.get_field(result["event_property"]).raw
    ).hexdigest()
    assert main_scene_path.read_bytes() == before
    assert not list(main_scene_path.parent.glob("*.bak*"))


def test_mcp_allowed_root(main_scene_path: Path, tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="outside PUMMELMCP_ALLOWED_ROOT"):
        _call(
            create_server(tmp_path),
            "inspect_action_references",
            _arguments(main_scene_path, "SpawnPrefabAction"),
        )


def test_mcp_reference_exact_asset_resolution(tmp_path: Path, main_scene_path: Path) -> None:
    mod = tmp_path / "mod"
    data = mod / "Data"
    prefabs = mod / "Assets" / "Prefabs"
    data.mkdir(parents=True)
    prefabs.mkdir(parents=True)
    scene = data / "MainScene.scene"
    scene.write_bytes(main_scene_path.read_bytes())
    arguments = _arguments(scene, "SpawnPrefabAction")
    unresolved = _structured(create_server(mod), "inspect_action_references", arguments)
    guid = _reference(unresolved, "m_prefabs")["items"][0]["normalized_value"]["guid"]
    (prefabs / "Known.pfab").write_bytes(b"asset")
    (prefabs / "Known.pfab.pmeta").write_text(
        json.dumps({"name": "Known", "guid": {"serializedGuid": guid}, "tags": ["prefabs"]}),
        encoding="utf-8",
    )
    resolved = _structured(create_server(mod), "inspect_action_references", arguments)
    resolution = _reference(resolved, "m_prefabs")["items"][0]["resolution"]
    assert resolution["resolved"] is True
    assert resolution["relative_path"] == "Assets/Prefabs/Known.pfab"
    assert not _contains_bytes_or_absolute_path(resolved)


def test_official_client_reference_flow_is_zero_mutation(main_scene_path: Path) -> None:
    before = main_scene_path.read_bytes()
    arguments = _arguments(main_scene_path, "SpawnPrefabAction")
    server = create_server(main_scene_path.parent)

    async def integration() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        async with Client(server) as client:
            listed = await client.call_tool(
                "inspect_action_list",
                {key: value for key, value in arguments.items() if key != "action_rid"},
            )
            references = await client.call_tool("inspect_action_references", arguments)
            validation = await client.call_tool("validate_scene", {"scene_path": str(main_scene_path)})
        return listed.structured_content, references.structured_content, validation.structured_content

    listed, references, validation = anyio.run(integration)
    assert listed["actions"][0]["rid"] == references["action"]["rid"]
    assert _reference(references, "m_prefabs")["count"] == 23
    assert validation["valid"] is True
    assert main_scene_path.read_bytes() == before
