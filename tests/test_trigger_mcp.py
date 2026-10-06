from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import anyio
import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

from pummelmcp.mcp_server import create_server
from pummelmcp.pmh import read_pmh


ACTION_PROPERTIES = (
    "OnHitActions",
    "OnEnterActions",
    "OnExitActions",
    "OnStayActions",
)


@pytest.fixture
def trigger_mcp_scene(tmp_path: Path, main_scene_path: Path) -> Path:
    target = tmp_path / "trigger-mcp.scene"
    shutil.copyfile(main_scene_path, target)
    return target


def _target(path: Path) -> tuple[str, str]:
    for obj in read_pmh(path).walk():
        for component in obj.components:
            if component.type_name == "ModTrigger":
                return obj.guid, component.guid
    raise AssertionError("real fixture contains no ModTrigger")


def _actions(path: Path, object_guid: str, component_guid: str) -> dict[str, bytes]:
    scene = read_pmh(path)
    obj = next(item for item in scene.walk() if item.guid == object_guid)
    component = next(item for item in obj.components if item.guid == component_guid)
    return {name: component.get_field(name).raw for name in ACTION_PROPERTIES}


def _call(server, name: str, arguments: dict):
    async def invoke():
        return await server.call_tool(name, arguments)

    return anyio.run(invoke)


def _structured(server, name: str, arguments: dict) -> dict:
    result = _call(server, name, arguments)
    assert not result.is_error
    assert isinstance(result.structured_content, dict)
    return result.structured_content


def test_mcp_tools_list_still_expected(main_scene_path: Path) -> None:
    tools = anyio.run(create_server(main_scene_path.parent).list_tools)
    assert {tool.name for tool in tools} >= {
        "plan_minigame_v2", "build_minigame_v2", "plan_prefab_pack", "build_prefab_pack",
        "get_minigame_config","plan_minigame_config_update","apply_minigame_config_update",
        "list_minigame_archetypes","plan_minigame","build_minigame",
        "get_builtin_asset_catalog_summary","search_builtin_assets","get_builtin_asset",
        "list_editor_assets","get_editor_asset","spawn_builtin_prop",
            "inspect_runtime_capabilities",
            "plan_playtest_session",
            "start_playtest_session",
            "collect_playtest_evidence",
            "evaluate_playtest",
            "list_gameplay_recipes",
            "plan_gameplay_composition",
            "compose_gameplay",
        "get_scene_summary",
        "list_scene_objects",
        "get_object",
        "get_components",
        "get_transform",
        "set_transform",
        "validate_scene",
        "get_component_property",
        "inspect_action_list",
            "inspect_action_references",
            "inspect_reference_graph",
            "inspect_object_references",
            "analyze_duplication_safety",
            "plan_gameobject_duplication",
            "duplicate_gameobject",
            "plan_gameobject_reparent",
            "reparent_gameobject",
            "plan_subtree_duplication",
            "duplicate_subtree",
            "register_object_template",
            "list_object_templates",
            "plan_create_from_template",
            "create_from_template",
        "replace_prefab_reference",
        "set_action_field",
        "add_action",
        "delete_action",
        "move_action",
        "set_component_property",
    }


def test_mcp_get_and_set_trigger_property(trigger_mcp_scene: Path) -> None:
    object_guid, component_guid = _target(trigger_mcp_scene)
    server = create_server(trigger_mcp_scene.parent)
    arguments = {
        "scene_path": str(trigger_mcp_scene),
        "object_identifier": object_guid,
        "component_identifier": component_guid,
        "property_name": "Radius",
    }
    before = _structured(server, "get_component_property", arguments)
    changed = _structured(
        server, "set_component_property", {**arguments, "value": 2.5}
    )
    after = _structured(server, "get_component_property", arguments)
    assert before["value"] == 0.5
    assert after["value"] == 2.5
    assert changed["changes"]["value"]["after"] == 2.5
    assert changed["validation"]["passed"] is True


def test_mcp_trigger_dry_run(trigger_mcp_scene: Path) -> None:
    object_guid, component_guid = _target(trigger_mcp_scene)
    before = hashlib.sha256(trigger_mcp_scene.read_bytes()).hexdigest()
    result = _structured(
        create_server(trigger_mcp_scene.parent),
        "set_component_property",
        {
            "scene_path": str(trigger_mcp_scene),
            "object_identifier": object_guid,
            "component_identifier": component_guid,
            "property_name": "OneUsePerPlayer",
            "value": True,
            "dry_run": True,
        },
    )
    assert result["dry_run"] is True
    assert result["backup_path"] is None
    assert hashlib.sha256(trigger_mcp_scene.read_bytes()).hexdigest() == before


def test_mcp_trigger_action_is_summarized_and_write_rejected(
    trigger_mcp_scene: Path,
) -> None:
    object_guid, component_guid = _target(trigger_mcp_scene)
    server = create_server(trigger_mcp_scene.parent)
    arguments = {
        "scene_path": str(trigger_mcp_scene),
        "object_identifier": object_guid,
        "component_identifier": component_guid,
        "property_name": "OnHitActions",
    }
    summary = _structured(server, "get_component_property", arguments)
    assert summary["schema"] == "ManagedReferencePayload"
    assert summary["raw_length"] == 47145
    assert summary["summarized"] is True
    assert summary["writable"] is False
    assert summary["value"] is None
    assert summary["sha256"] == hashlib.sha256(
        _actions(trigger_mcp_scene, object_guid, component_guid)["OnHitActions"]
    ).hexdigest()
    assert summary["suggestion"] == (
        "Use inspect_action_list for structured inspection."
    )
    with pytest.raises(
        ToolError, match="Action mutation is not supported in Trigger Writer v0.3"
    ):
        _call(server, "set_component_property", {**arguments, "value": {}})


def test_mcp_trigger_client_server_integration_preserves_actions(
    trigger_mcp_scene: Path,
) -> None:
    object_guid, component_guid = _target(trigger_mcp_scene)
    actions_before = _actions(trigger_mcp_scene, object_guid, component_guid)
    server = create_server(trigger_mcp_scene.parent)

    async def integration() -> dict:
        arguments = {
            "scene_path": str(trigger_mcp_scene),
            "object_identifier": object_guid,
            "component_identifier": component_guid,
            "property_name": "Radius",
        }
        async with Client(server) as client:
            components = await client.call_tool(
                "get_components",
                {"scene_path": str(trigger_mcp_scene), "identifier": object_guid},
            )
            before = await client.call_tool("get_component_property", arguments)
            changed = await client.call_tool(
                "set_component_property", {**arguments, "value": 2.5}
            )
            after = await client.call_tool("get_component_property", arguments)
            validation = await client.call_tool(
                "validate_scene", {"scene_path": str(trigger_mcp_scene)}
            )
        return {
            "components": components.structured_content,
            "before": before.structured_content,
            "changed": changed.structured_content,
            "after": after.structured_content,
            "validation": validation.structured_content,
        }

    result = anyio.run(integration)
    assert any(
        component["type"] == "ModTrigger"
        for component in result["components"]["components"]
    )
    assert result["before"]["value"] == 0.5
    assert result["changed"]["validation"]["passed"] is True
    assert result["after"]["value"] == 2.5
    assert result["validation"]["valid"] is True
    assert _actions(trigger_mcp_scene, object_guid, component_guid) == actions_before
