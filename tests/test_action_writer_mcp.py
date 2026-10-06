from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

from pummelmcp.mcp_server import create_server
from pummelmcp.pmh import ActionFieldWriter, parse_action_payload, read_pmh


@pytest.fixture
def action_mcp_scene(tmp_path: Path, main_scene_path: Path) -> Path:
    target = tmp_path / "action-mcp.scene"
    shutil.copyfile(main_scene_path, target)
    return target


def _target(path: Path) -> tuple[str, str]:
    for obj in read_pmh(path).walk():
        for component in obj.components:
            if component.type_name != "ModTrigger":
                continue
            parsed = parse_action_payload(component.get_field("OnHitActions").raw)
            if parsed.actions and parsed.actions[0].type_info.class_name == "SpawnPrefabAction":
                return obj.guid, component.guid
    raise AssertionError("fixture has no SpawnPrefabAction")


def _arguments(path: Path, field_name: str, value: Any) -> dict[str, Any]:
    object_guid, component_guid = _target(path)
    return {
        "scene_path": str(path),
        "object_identifier": object_guid,
        "component_identifier": component_guid,
        "event_property": "OnHitActions",
        "action_rid": 1000,
        "expected_class": "SpawnPrefabAction",
        "field_name": field_name,
        "value": value,
    }


def _call(server, name: str, arguments: dict[str, Any]):
    async def invoke():
        return await server.call_tool(name, arguments)

    return anyio.run(invoke)


def _structured(server, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = _call(server, name, arguments)
    assert not result.is_error
    assert isinstance(result.structured_content, dict)
    return result.structured_content


def test_mcp_tools_list_has_set_action_field(main_scene_path: Path) -> None:
    tools = anyio.run(create_server(main_scene_path.parent).list_tools)
    tool = next(item for item in tools if item.name == "set_action_field")
    assert tool.annotations.read_only_hint is False
    assert tool.annotations.idempotent_hint is False
    assert "cannot create, delete, copy, or reorder Actions" in tool.description


def test_mcp_set_action_field(action_mcp_scene: Path) -> None:
    result = _structured(
        create_server(action_mcp_scene.parent),
        "set_action_field",
        _arguments(action_mcp_scene, "m_spawnAtPosition", True),
    )
    assert result["action"]["class"] == "SpawnPrefabAction"
    assert result["changes"]["value"]["after"] is True
    assert result["validation"]["passed"] is True
    assert result["bytes_added_or_removed"] == -1


def test_mcp_set_action_field_partial_vector(action_mcp_scene: Path) -> None:
    result = _structured(
        create_server(action_mcp_scene.parent),
        "set_action_field",
        _arguments(action_mcp_scene, "m_position", {"x": 5.0}),
    )
    assert set(result["changes"]) == {"x"}
    inspected = _structured(
        create_server(action_mcp_scene.parent),
        "inspect_action_list",
        {
            "scene_path": str(action_mcp_scene),
            "object_identifier": _target(action_mcp_scene)[0],
            "component_identifier": _target(action_mcp_scene)[1],
            "event_property": "OnHitActions",
        },
    )
    assert inspected["actions"][0]["fields"]["m_position"] == {
        "x": 5.0,
        "y": 0.0,
        "z": 0.0,
    }


def test_mcp_action_dry_run(action_mcp_scene: Path) -> None:
    before = hashlib.sha256(action_mcp_scene.read_bytes()).hexdigest()
    result = _structured(
        create_server(action_mcp_scene.parent),
        "set_action_field",
        {**_arguments(action_mcp_scene, "m_spawnAtPosition", True), "dry_run": True},
    )
    assert result["dry_run"] is True
    assert result["backup_path"] is None
    assert hashlib.sha256(action_mcp_scene.read_bytes()).hexdigest() == before


def test_mcp_action_stale_hash(action_mcp_scene: Path) -> None:
    object_guid, component_guid = _target(action_mcp_scene)
    server = create_server(action_mcp_scene.parent)
    inspected = _structured(
        server,
        "inspect_action_list",
        {
            "scene_path": str(action_mcp_scene),
            "object_identifier": object_guid,
            "component_identifier": component_guid,
            "event_property": "OnHitActions",
        },
    )
    ActionFieldWriter(action_mcp_scene).set_action_field(
        object_guid,
        component_guid,
        "OnHitActions",
        1000,
        "SpawnPrefabAction",
        "m_position",
        {"x": 5.0},
    )
    changed_hash = hashlib.sha256(action_mcp_scene.read_bytes()).hexdigest()
    with pytest.raises(ToolError, match="changed after inspection"):
        _call(
            server,
            "set_action_field",
            {
                **_arguments(action_mcp_scene, "m_rotation", {"z": 45.0}),
                "expected_payload_sha256": inspected["sha256"],
            },
        )
    assert hashlib.sha256(action_mcp_scene.read_bytes()).hexdigest() == changed_hash


def test_mcp_readonly_field_rejected(action_mcp_scene: Path) -> None:
    with pytest.raises(ToolError, match="Prefab/reference mutation"):
        _call(
            create_server(action_mcp_scene.parent),
            "set_action_field",
            _arguments(action_mcp_scene, "m_prefabs", []),
        )


def test_mcp_graph_unchanged(action_mcp_scene: Path) -> None:
    result = _structured(
        create_server(action_mcp_scene.parent),
        "set_action_field",
        _arguments(action_mcp_scene, "m_rotation", {"z": 45.0}),
    )
    assert result["graph_fingerprint_before"] == result["graph_fingerprint_after"]
    assert result["validation"]["checks"]["graph_fingerprint_unchanged"] is True


def test_mcp_validate_after_action_write(action_mcp_scene: Path) -> None:
    server = create_server(action_mcp_scene.parent)
    _structured(
        server,
        "set_action_field",
        _arguments(action_mcp_scene, "m_parentToTarget", True),
    )
    validation = _structured(
        server, "validate_scene", {"scene_path": str(action_mcp_scene)}
    )
    assert validation["valid"] is True
    assert validation["fully_consumed"] is True


def test_mcp_action_writer_official_client_integration(action_mcp_scene: Path) -> None:
    object_guid, component_guid = _target(action_mcp_scene)
    before_component = next(
        component
        for obj in read_pmh(action_mcp_scene).walk()
        if obj.guid == object_guid
        for component in obj.components
        if component.guid == component_guid
    )
    other_events = {
        name: before_component.get_field(name).raw
        for name in ("OnEnterActions", "OnExitActions", "OnStayActions")
    }
    server = create_server(action_mcp_scene.parent)

    async def integration() -> dict[str, Any]:
        async with Client(server) as client:
            before = await client.call_tool(
                "inspect_action_list",
                {
                    "scene_path": str(action_mcp_scene),
                    "object_identifier": object_guid,
                    "component_identifier": component_guid,
                    "event_property": "OnHitActions",
                },
            )
            changed = await client.call_tool(
                "set_action_field",
                {
                    **_arguments(action_mcp_scene, "m_spawnAtPosition", True),
                    "expected_payload_sha256": before.structured_content["sha256"],
                },
            )
            after = await client.call_tool(
                "inspect_action_list",
                {
                    "scene_path": str(action_mcp_scene),
                    "object_identifier": object_guid,
                    "component_identifier": component_guid,
                    "event_property": "OnHitActions",
                },
            )
            validation = await client.call_tool(
                "validate_scene", {"scene_path": str(action_mcp_scene)}
            )
        return {
            "before": before.structured_content,
            "changed": changed.structured_content,
            "after": after.structured_content,
            "validation": validation.structured_content,
        }

    result = anyio.run(integration)
    assert result["before"]["actions"][0]["fields"]["m_spawnAtPosition"] is False
    assert result["after"]["actions"][0]["fields"]["m_spawnAtPosition"] is True
    assert result["changed"]["graph_fingerprint_before"] == result["changed"]["graph_fingerprint_after"]
    assert result["validation"]["valid"] is True
    after_component = next(
        component
        for obj in read_pmh(action_mcp_scene).walk()
        if obj.guid == object_guid
        for component in obj.components
        if component.guid == component_guid
    )
    assert {
        name: after_component.get_field(name).raw for name in other_events
    } == other_events
