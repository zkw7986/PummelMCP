from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

from pummelmcp.mcp_server import create_server
from pummelmcp.pmh import read_pmh


def _target(path: Path, object_name: str) -> tuple[str, str]:
    obj = next(item for item in read_pmh(path).walk() if item.name == object_name)
    component = next(item for item in obj.components if item.type_name == "ModTrigger")
    return obj.guid, component.guid


def _call(server, name: str, arguments: dict[str, Any]):
    async def invoke():
        return await server.call_tool(name, arguments)

    return anyio.run(invoke)


def _structured(server, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = _call(server, name, arguments)
    assert not result.is_error
    assert isinstance(result.structured_content, dict)
    return result.structured_content


def _contains_raw_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(key in {"raw", "raw_payload", "raw_segment"} for key in value) or any(
            _contains_raw_key(item) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_raw_key(item) for item in value)
    return False


def test_mcp_tools_list_contains_inspect_action_list(main_scene_path: Path) -> None:
    tools = anyio.run(create_server(main_scene_path.parent).list_tools)
    tool = next(item for item in tools if item.name == "inspect_action_list")
    assert tool.annotations.read_only_hint is True
    assert tool.annotations.destructive_hint is False
    assert tool.annotations.idempotent_hint is True
    assert tool.annotations.open_world_hint is False


def test_mcp_action_tool_is_read_only(main_scene_path: Path) -> None:
    tools = anyio.run(create_server(main_scene_path.parent).list_tools)
    tool = next(item for item in tools if item.name == "inspect_action_list")
    assert tool.annotations.read_only_hint is True
    assert tool.annotations.destructive_hint is False


def test_mcp_inspect_on_hit_actions(main_scene_path: Path) -> None:
    object_guid, component_guid = _target(main_scene_path, "DrawButton_01")
    result = _structured(
        create_server(main_scene_path.parent),
        "inspect_action_list",
        {
            "scene_path": str(main_scene_path),
            "object_identifier": object_guid,
            "component_identifier": component_guid,
            "event_property": "OnHitActions",
        },
    )
    assert result["raw_length"] == 47_145
    assert result["sha256"] == (
        "d2ffca2b3f59d3ebd47c2b8a99729a90d35a17f7fa1a23d43448d4802ffca198"
    )
    assert result["fully_consumed"] is True
    assert result["unparsed_ranges"] == []
    assert result["action_count"] == result["reference_summary"]["count"] == 1
    action = result["actions"][0]
    assert action["index"] == 0
    assert action["rid"] == 1000
    assert action["namespace"] == "ModSystem.Logic"
    assert action["class"] == "SpawnPrefabAction"
    assert action["field_schema"]["m_spawnAtPosition"]["writable"] is True
    assert action["field_schema"]["m_prefabs"]["writable"] is False
    assert action["fields"]["m_prefabs"] == {
        **action["fields"]["m_prefabs"],
        "type": "list",
        "count": 23,
        "truncated": True,
    }
    assert not _contains_raw_key(result)


def test_mcp_spawn_prefab_detected(main_scene_path: Path) -> None:
    object_guid, component_guid = _target(main_scene_path, "DrawButton_01")
    result = _structured(
        create_server(main_scene_path.parent),
        "inspect_action_list",
        {
            "scene_path": str(main_scene_path),
            "object_identifier": object_guid,
            "component_identifier": component_guid,
            "event_property": "OnHitActions",
        },
    )
    assert result["actions"][0]["class"] == "SpawnPrefabAction"


def test_mcp_action_payload_hash(main_scene_path: Path) -> None:
    object_guid, component_guid = _target(main_scene_path, "DrawButton_01")
    result = _structured(
        create_server(main_scene_path.parent),
        "inspect_action_list",
        {
            "scene_path": str(main_scene_path),
            "object_identifier": object_guid,
            "component_identifier": component_guid,
            "event_property": "OnHitActions",
        },
    )
    assert result["sha256"] == "d2ffca2b3f59d3ebd47c2b8a99729a90d35a17f7fa1a23d43448d4802ffca198"


def test_mcp_action_order(main_scene_path: Path) -> None:
    object_guid, component_guid = _target(
        main_scene_path, "Base Wall Window Low 01 Glass"
    )
    server = create_server(main_scene_path.parent)
    arguments = {
        "scene_path": str(main_scene_path),
        "object_identifier": object_guid,
        "component_identifier": component_guid,
        "event_property": "OnHitActions",
        "limit": 1,
    }
    first = _structured(server, "inspect_action_list", arguments)
    second = _structured(server, "inspect_action_list", {**arguments, "offset": 1})
    assert first["action_count"] == second["action_count"] == 2
    assert first["actions"][0]["class"] == "SpawnEffectAction"
    assert second["actions"][0]["class"] == "PlaySoundAction"
    assert [first["actions"][0]["index"], second["actions"][0]["index"]] == [0, 1]


def test_mcp_action_pagination(main_scene_path: Path) -> None:
    object_guid, component_guid = _target(main_scene_path, "Base Wall Window Low 01 Glass")
    server = create_server(main_scene_path.parent)
    base = {
        "scene_path": str(main_scene_path),
        "object_identifier": object_guid,
        "component_identifier": component_guid,
        "event_property": "OnHitActions",
        "limit": 1,
    }
    assert _structured(server, "inspect_action_list", base)["returned"] == 1
    assert _structured(server, "inspect_action_list", {**base, "offset": 2})["returned"] == 0


def test_inspect_action_list_validates_event_component_and_allowed_root(
    main_scene_path: Path, tmp_path: Path
) -> None:
    object_guid, component_guid = _target(main_scene_path, "DrawButton_01")
    arguments = {
        "scene_path": str(main_scene_path),
        "object_identifier": object_guid,
        "component_identifier": component_guid,
        "event_property": "Radius",
    }
    with pytest.raises(ToolError, match="event_property must be one of"):
        _call(create_server(main_scene_path.parent), "inspect_action_list", arguments)
    with pytest.raises(ToolError, match="outside PUMMELMCP_ALLOWED_ROOT"):
        _call(create_server(tmp_path), "inspect_action_list", arguments)

    non_trigger = read_pmh(main_scene_path).find_game_object("PlayerSpawn_0")
    transform = non_trigger.get_component("ModTransform")
    with pytest.raises(ToolError, match="component must be ModTrigger"):
        _call(
            create_server(main_scene_path.parent),
            "inspect_action_list",
            {
                **arguments,
                "object_identifier": non_trigger.guid,
                "component_identifier": transform.guid,
                "event_property": "OnHitActions",
            },
        )


def test_mcp_action_invalid_event_property(main_scene_path: Path) -> None:
    object_guid, component_guid = _target(main_scene_path, "DrawButton_01")
    with pytest.raises(ToolError, match="event_property must be one of"):
        _call(
            create_server(main_scene_path.parent),
            "inspect_action_list",
            {
                "scene_path": str(main_scene_path),
                "object_identifier": object_guid,
                "component_identifier": component_guid,
                "event_property": "Radius",
            },
        )


def test_mcp_action_outside_allowed_root(main_scene_path: Path, tmp_path: Path) -> None:
    object_guid, component_guid = _target(main_scene_path, "DrawButton_01")
    with pytest.raises(ToolError, match="outside PUMMELMCP_ALLOWED_ROOT"):
        _call(
            create_server(tmp_path),
            "inspect_action_list",
            {
                "scene_path": str(main_scene_path),
                "object_identifier": object_guid,
                "component_identifier": component_guid,
                "event_property": "OnHitActions",
            },
        )


def test_official_client_action_inspection_flow_is_zero_mutation(
    main_scene_path: Path,
) -> None:
    object_guid, component_guid = _target(main_scene_path, "DrawButton_01")
    before = hashlib.sha256(main_scene_path.read_bytes()).hexdigest()
    server = create_server(main_scene_path.parent)

    async def integration() -> dict[str, Any]:
        async with Client(server) as client:
            components = await client.call_tool(
                "get_components",
                {"scene_path": str(main_scene_path), "identifier": object_guid},
            )
            inspected = await client.call_tool(
                "inspect_action_list",
                {
                    "scene_path": str(main_scene_path),
                    "object_identifier": object_guid,
                    "component_identifier": component_guid,
                    "event_property": "OnHitActions",
                },
            )
            validation = await client.call_tool(
                "validate_scene", {"scene_path": str(main_scene_path)}
            )
        return {
            "components": components.structured_content,
            "inspected": inspected.structured_content,
            "validation": validation.structured_content,
        }

    result = anyio.run(integration)
    assert any(
        item["type"] == "ModTrigger" for item in result["components"]["components"]
    )
    assert result["inspected"]["actions"][0]["class"] == "SpawnPrefabAction"
    assert result["validation"]["valid"] is True
    assert hashlib.sha256(main_scene_path.read_bytes()).hexdigest() == before
