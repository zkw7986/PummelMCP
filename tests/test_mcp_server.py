from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import sys
from pathlib import Path

import anyio
import pytest
from mcp import Client, StdioServerParameters
from mcp.server.mcpserver.exceptions import ToolError

from pummelmcp.mcp_server import ALLOWED_ROOT_ENV, create_server
from pummelmcp.pmh import read_pmh


EXPECTED_TOOLS = {
    "submit_gameplay_review", "approve_gameplay_review", "get_game_workflow_status",
    "authorize_workshop_directory", "list_mod_templates", "initialize_mod_from_template",
    "get_authoring_capabilities", "audit_minigame_archive", "get_authoring_donors",
    "plan_authored_minigame", "build_authored_minigame", "generate_card_minigame",
    "plan_prefab_pack", "build_prefab_pack",
    "plan_minigame_v2", "build_minigame_v2",
    "get_minigame_config", "plan_minigame_config_update", "apply_minigame_config_update",
    "get_builtin_asset_catalog_summary","search_builtin_assets","get_builtin_asset",
    "list_editor_assets","get_editor_asset","spawn_builtin_prop",
    "plan_blender_scene_import","build_blender_scene_import",
    "list_minigame_archetypes","plan_minigame","build_minigame",
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
    "get_transform",
    "set_transform",
    "set_component_property",
    "validate_scene",
}


def _call(server, name: str, arguments: dict):
    async def invoke():
        return await server.call_tool(name, arguments)

    return anyio.run(invoke)


def _structured(server, name: str, arguments: dict) -> dict:
    result = _call(server, name, arguments)
    assert not result.is_error
    assert isinstance(result.structured_content, dict)
    return result.structured_content


@pytest.fixture
def mcp_scene(tmp_path: Path, main_scene_path: Path) -> Path:
    directory = tmp_path / "允许 根目录"
    directory.mkdir()
    target = directory / "MainScene.scene"
    shutil.copyfile(main_scene_path, target)
    return target


def _field_raw(path: Path, property_name: str) -> bytes:
    return (
        read_pmh(path)
        .find_game_object("PlayerSpawn_0")
        .get_component("ModTransform")
        .get_field(property_name)
        .raw
    )


def test_mcp_server_lists_expected_tools(main_scene_path: Path) -> None:
    server = create_server(main_scene_path.parent)
    tools = anyio.run(server.list_tools)

    assert {tool.name for tool in tools} == EXPECTED_TOOLS
    assert len(tools) == len(EXPECTED_TOOLS)
    annotations = {tool.name: tool.annotations for tool in tools}
    assert annotations["get_scene_summary"].read_only_hint is True
    assert annotations["get_builtin_asset_catalog_summary"].read_only_hint is True
    assert annotations["search_builtin_assets"].read_only_hint is True
    assert annotations["get_builtin_asset"].read_only_hint is True
    assert annotations["get_scene_summary"].open_world_hint is False
    assert annotations["set_transform"].read_only_hint is False
    assert annotations["set_transform"].idempotent_hint is False
    assert annotations["get_component_property"].read_only_hint is True
    assert annotations["inspect_action_list"].read_only_hint is True
    assert annotations["inspect_action_list"].destructive_hint is False
    assert annotations["inspect_action_references"].read_only_hint is True
    assert annotations["inspect_action_references"].destructive_hint is False
    assert annotations["inspect_reference_graph"].read_only_hint is True
    assert annotations["inspect_object_references"].read_only_hint is True
    assert annotations["analyze_duplication_safety"].read_only_hint is True
    assert annotations["plan_gameobject_duplication"].read_only_hint is True
    assert annotations["duplicate_gameobject"].read_only_hint is False
    assert annotations["replace_prefab_reference"].read_only_hint is False
    assert annotations["set_action_field"].read_only_hint is False
    assert annotations["set_component_property"].read_only_hint is False


def test_mcp_get_scene_summary(main_scene_path: Path) -> None:
    data = _structured(
        create_server(main_scene_path.parent),
        "get_scene_summary",
        {"scene_path": str(main_scene_path)},
    )

    assert data == {
        "magic": "PMH",
        "version": 1,
        "roots": 41,
        "game_objects": 142,
        "components": 267,
        "file_size": 446703,
        "fully_consumed": True,
        "valid": True,
    }


def test_mcp_list_scene_objects(main_scene_path: Path) -> None:
    data = _structured(
        create_server(main_scene_path.parent),
        "list_scene_objects",
        {"scene_path": str(main_scene_path)},
    )

    assert data["total"] == 142
    assert data["limit"] == 100
    assert len(data["objects"]) == 100
    assert set(data["objects"][0]) == {"name", "guid", "path"}


def test_mcp_list_scene_objects_query_is_case_insensitive(
    main_scene_path: Path,
) -> None:
    data = _structured(
        create_server(main_scene_path.parent),
        "list_scene_objects",
        {"scene_path": str(main_scene_path), "query": "playerspawn_0"},
    )

    assert data["total"] == 1
    assert data["objects"][0]["name"] == "PlayerSpawn_0"


def test_mcp_list_scene_objects_pagination(main_scene_path: Path) -> None:
    server = create_server(main_scene_path.parent)
    first = _structured(
        server,
        "list_scene_objects",
        {"scene_path": str(main_scene_path), "offset": 10, "limit": 5},
    )
    second = _structured(
        server,
        "list_scene_objects",
        {"scene_path": str(main_scene_path), "offset": 15, "limit": 5},
    )

    assert first["total"] == second["total"] == 142
    assert len(first["objects"]) == len(second["objects"]) == 5
    assert {item["guid"] for item in first["objects"]}.isdisjoint(
        item["guid"] for item in second["objects"]
    )


def test_mcp_get_object(main_scene_path: Path) -> None:
    data = _structured(
        create_server(main_scene_path.parent),
        "get_object",
        {"scene_path": str(main_scene_path), "identifier": "PlayerSpawn_0"},
    )

    assert data["name"] == "PlayerSpawn_0"
    assert data["path"] == "/Player Spawnpoints/PlayerSpawn_0"
    assert data["parent"]["name"] == "Player Spawnpoints"
    assert {component["type"] for component in data["components"]} == {
        "ModTransform",
        "ModPlayerSpawn",
    }
    assert all("properties" not in component for component in data["components"])


def test_mcp_get_components(main_scene_path: Path) -> None:
    data = _structured(
        create_server(main_scene_path.parent),
        "get_components",
        {"scene_path": str(main_scene_path), "identifier": "PlayerSpawn_0"},
    )

    transform = next(item for item in data["components"] if item["type"] == "ModTransform")
    position = next(item for item in transform["properties"] if item["name"] == "position")
    assert position["decoded"]["x"] == 5.000002384185791
    assert position["raw_length"] == 12
    assert position["schema"] == "Vector3Float32"
    assert position["writable"] is True
    assert "raw" not in position


def test_mcp_get_components_uses_component_schema(main_scene_path: Path) -> None:
    data = _structured(
        create_server(main_scene_path.parent),
        "get_components",
        {"scene_path": str(main_scene_path), "identifier": "PlayerSpawn_0"},
    )
    spawn = next(item for item in data["components"] if item["type"] == "ModPlayerSpawn")
    radius = next(item for item in spawn["properties"] if item["name"] == "Radius")
    assert radius["decoded"] == 5.0
    assert radius["schema"] == "Float32LE"
    assert radius["writable"] is True
    assert radius["summarized"] is False


def test_mcp_get_component_property_by_type_and_guid(main_scene_path: Path) -> None:
    server = create_server(main_scene_path.parent)
    by_type = _structured(
        server,
        "get_component_property",
        {
            "scene_path": str(main_scene_path),
            "object_identifier": "PlayerSpawn_0",
            "component_identifier": "ModPlayerSpawn",
            "property_name": "SpawnUsageType",
        },
    )
    by_guid = _structured(
        server,
        "get_component_property",
        {
            "scene_path": str(main_scene_path),
            "object_identifier": "PlayerSpawn_0",
            "component_identifier": "84ce7c9d-70c1-482a-82d3-ea012902147b",
            "property_name": "SpawnUsageType",
        },
    )
    assert by_type == by_guid
    assert by_type["value"] == {"raw_value": 3, "name": None}
    assert by_type["schema"] == "Int32LE"
    assert by_type["writable"] is True


def test_mcp_set_component_property_and_dry_run(mcp_scene: Path) -> None:
    server = create_server(mcp_scene.parent)
    before = mcp_scene.read_bytes()
    dry = _structured(
        server,
        "set_component_property",
        {
            "scene_path": str(mcp_scene),
            "object_identifier": "PlayerSpawn_0",
            "component_identifier": "ModPlayerSpawn",
            "property_name": "Radius",
            "value": 9.0,
            "dry_run": True,
        },
    )
    assert dry["dry_run"] is True
    assert mcp_scene.read_bytes() == before

    changed = _structured(
        server,
        "set_component_property",
        {
            "scene_path": str(mcp_scene),
            "object_identifier": "PlayerSpawn_0",
            "component_identifier": "ModPlayerSpawn",
            "property_name": "Radius",
            "value": 9.0,
        },
    )
    assert changed["changes"]["value"]["after"] == 9.0
    assert changed["validation"]["passed"] is True


def test_mcp_component_property_rejects_invalid_and_read_only(mcp_scene: Path) -> None:
    server = create_server(mcp_scene.parent)
    with pytest.raises(ToolError, match="ComponentPropertyNotFoundError"):
        _call(
            server,
            "set_component_property",
            {
                "scene_path": str(mcp_scene),
                "object_identifier": "PlayerSpawn_0",
                "component_identifier": "ModPlayerSpawn",
                "property_name": "unknown",
                "value": 1,
            },
        )
    with pytest.raises(ToolError, match="ComponentPropertyReadOnlyError"):
        _call(
            server,
            "set_component_property",
            {
                "scene_path": str(mcp_scene),
                "object_identifier": "WorldText",
                "component_identifier": "ModText",
                "property_name": "Text",
                "value": "new text",
            },
        )
    with pytest.raises(ToolError, match="ComponentWriteError"):
        _call(
            server,
            "set_component_property",
            {
                "scene_path": str(mcp_scene),
                "object_identifier": "PlayerSpawn_0",
                "component_identifier": "ModPlayerSpawn",
                "property_name": "SharedSpawn",
                "value": 1,
            },
        )


def test_mcp_component_tool_enforces_allowed_root(
    tmp_path: Path, main_scene_path: Path
) -> None:
    allowed = tmp_path / "allowed-components"
    allowed.mkdir()
    with pytest.raises(ToolError, match="outside PUMMELMCP_ALLOWED_ROOT"):
        _call(
            create_server(allowed),
            "get_component_property",
            {
                "scene_path": str(main_scene_path),
                "object_identifier": "PlayerSpawn_0",
                "component_identifier": "ModPlayerSpawn",
                "property_name": "Radius",
            },
        )


def test_mcp_get_transform(main_scene_path: Path) -> None:
    data = _structured(
        create_server(main_scene_path.parent),
        "get_transform",
        {"scene_path": str(main_scene_path), "identifier": "PlayerSpawn_0"},
    )

    assert data["object"]["name"] == "PlayerSpawn_0"
    assert data["position"]["x"] == 5.000002384185791
    assert data["rotation"] == {"x": 0.0, "y": 0.0, "z": -0.0}
    assert data["scale"] == {"x": 1.0, "y": 1.0, "z": 1.0}


def test_mcp_set_transform_position_x(mcp_scene: Path) -> None:
    before = _field_raw(mcp_scene, "position")
    data = _structured(
        create_server(mcp_scene.parent),
        "set_transform",
        {
            "scene_path": str(mcp_scene),
            "identifier": "PlayerSpawn_0",
            "position": {"x": 8.0},
        },
    )
    after = _field_raw(mcp_scene, "position")

    assert struct.unpack("<f", after[:4])[0] == 8.0
    assert after[4:] == before[4:]
    assert data["property_changes"]["position"]["x"]["before"] == 5.000002384185791
    assert data["property_changes"]["position"]["x"]["after"] == 8.0
    assert data["validation"]["passed"] is True
    assert Path(data["backup_path"]).is_file()


def test_mcp_set_transform_partial_update(mcp_scene: Path) -> None:
    before = _field_raw(mcp_scene, "position")

    _structured(
        create_server(mcp_scene.parent),
        "set_transform",
        {
            "scene_path": str(mcp_scene),
            "identifier": "PlayerSpawn_0",
            "position": {"y": -2.5},
        },
    )

    after = _field_raw(mcp_scene, "position")
    assert after[:4] == before[:4]
    assert struct.unpack("<f", after[4:8])[0] == -2.5
    assert after[8:] == before[8:]


def test_mcp_set_transform_dry_run(mcp_scene: Path) -> None:
    before = mcp_scene.read_bytes()

    data = _structured(
        create_server(mcp_scene.parent),
        "set_transform",
        {
            "scene_path": str(mcp_scene),
            "identifier": "PlayerSpawn_0",
            "position": {"x": 8.0},
            "dry_run": True,
        },
    )

    assert data["dry_run"] is True
    assert data["backup_path"] is None
    assert data["validation"]["passed"] is True
    assert mcp_scene.read_bytes() == before
    assert not list(mcp_scene.parent.glob("MainScene.scene.bak.*"))


def test_mcp_set_rotation(mcp_scene: Path) -> None:
    before = _field_raw(mcp_scene, "rotation")

    _structured(
        create_server(mcp_scene.parent),
        "set_transform",
        {
            "scene_path": str(mcp_scene),
            "identifier": "PlayerSpawn_0",
            "rotation": {"z": 90.0},
        },
    )

    after = _field_raw(mcp_scene, "rotation")
    assert after[:8] == before[:8]
    assert struct.unpack("<f", after[8:])[0] == 90.0


def test_mcp_set_scale(mcp_scene: Path) -> None:
    before = _field_raw(mcp_scene, "scale")

    _structured(
        create_server(mcp_scene.parent),
        "set_transform",
        {
            "scene_path": str(mcp_scene),
            "identifier": "PlayerSpawn_0",
            "scale": {"x": 1.5},
        },
    )

    after = _field_raw(mcp_scene, "scale")
    assert struct.unpack("<f", after[:4])[0] == 1.5
    assert after[4:] == before[4:]


def test_mcp_validate_scene(main_scene_path: Path) -> None:
    data = _structured(
        create_server(main_scene_path.parent),
        "validate_scene",
        {"scene_path": str(main_scene_path)},
    )

    assert data["valid"] is True
    assert data["fully_consumed"] is True
    assert data["errors"] == []
    assert (data["roots"], data["game_objects"], data["components"]) == (41, 142, 267)


def test_mcp_ambiguous_object(main_scene_path: Path) -> None:
    server = create_server(main_scene_path.parent)

    with pytest.raises(ToolError, match="AmbiguousObjectError"):
        _call(
            server,
            "get_object",
            {"scene_path": str(main_scene_path), "identifier": "LowPolyCube"},
        )


def test_mcp_missing_object(main_scene_path: Path) -> None:
    server = create_server(main_scene_path.parent)

    with pytest.raises(ToolError, match="ObjectNotFoundError"):
        _call(
            server,
            "get_transform",
            {"scene_path": str(main_scene_path), "identifier": "not-present"},
        )


def _scene_without_components() -> bytes:
    def str8(value: str) -> bytes:
        raw = value.encode()
        return bytes([len(raw)]) + raw

    hierarchy = str8("Root") + struct.pack("<Bi", 1, 0) + str8("Untagged") + b"\0\0"
    return str8("PMH") + struct.pack("<IH", 1, 1) + hierarchy + str8("guid") + struct.pack("<I", 0)


def test_mcp_missing_transform(tmp_path: Path) -> None:
    scene = tmp_path / "missing-transform.scene"
    scene.write_bytes(_scene_without_components())

    with pytest.raises(ToolError, match="TransformNotFoundError"):
        _call(
            create_server(tmp_path),
            "get_transform",
            {"scene_path": str(scene), "identifier": "Root"},
        )


def test_mcp_path_outside_allowed_root(
    tmp_path: Path, main_scene_path: Path
) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()

    with pytest.raises(ToolError, match="outside PUMMELMCP_ALLOWED_ROOT"):
        _call(
            create_server(allowed),
            "get_scene_summary",
            {"scene_path": str(main_scene_path)},
        )


def test_mcp_wrong_extension(tmp_path: Path, main_scene_path: Path) -> None:
    wrong = tmp_path / "MainScene.bin"
    shutil.copyfile(main_scene_path, wrong)

    with pytest.raises(ToolError, match=r"\.scene extension"):
        _call(
            create_server(tmp_path),
            "get_scene_summary",
            {"scene_path": str(wrong)},
        )


def test_mcp_missing_allowed_root_environment(
    monkeypatch: pytest.MonkeyPatch, main_scene_path: Path
) -> None:
    monkeypatch.delenv(ALLOWED_ROOT_ENV, raising=False)

    with pytest.raises(ToolError, match="PUMMELMCP_ALLOWED_ROOT is required"):
        _call(
            create_server(),
            "get_scene_summary",
            {"scene_path": str(main_scene_path)},
        )


def test_mcp_real_fixture_hash_unchanged(main_scene_path: Path) -> None:
    expected = "a0af6631df7e99d4f98d964122eca56ab66cb8beaad53d7217bc54efff89cde8"
    before = hashlib.sha256(main_scene_path.read_bytes()).hexdigest()

    _structured(
        create_server(main_scene_path.parent),
        "set_transform",
        {
            "scene_path": str(main_scene_path),
            "identifier": "PlayerSpawn_0",
            "position": {"x": 8.0},
            "dry_run": True,
        },
    )

    after = hashlib.sha256(main_scene_path.read_bytes()).hexdigest()
    assert before == after == expected


def test_mcp_mutation_only_changes_allowed_span(mcp_scene: Path) -> None:
    before = mcp_scene.read_bytes()
    field = (
        read_pmh(mcp_scene)
        .find_game_object("PlayerSpawn_0")
        .get_component("ModTransform")
        .get_field("position")
    )
    assert field.source_span is not None

    _structured(
        create_server(mcp_scene.parent),
        "set_transform",
        {
            "scene_path": str(mcp_scene),
            "identifier": "PlayerSpawn_0",
            "position": {"x": 8.0},
        },
    )

    after = mcp_scene.read_bytes()
    differences = {i for i, (a, b) in enumerate(zip(before, after)) if a != b}
    allowed = set(range(field.source_span.start, field.source_span.start + 4))
    assert differences
    assert differences <= allowed


def test_mcp_large_component_payload_is_summarized(main_scene_path: Path) -> None:
    data = _structured(
        create_server(main_scene_path.parent),
        "get_components",
        {
            "scene_path": str(main_scene_path),
            "identifier": "303604ff-82c9-4d7b-bbf9-f5c08ec1ec00",
        },
    )
    trigger = next(item for item in data["components"] if item["type"] == "ModTrigger")
    action = next(item for item in trigger["properties"] if item["name"] == "OnHitActions")

    assert action["raw_length"] == 47145
    assert action["decoded"] is None
    assert "raw" not in action
    assert len(json.dumps(data)) < 10_000


def test_mcp_no_stdout_pollution(capsys: pytest.CaptureFixture, main_scene_path: Path) -> None:
    server = create_server(main_scene_path.parent)
    anyio.run(server.list_tools)
    _structured(
        server,
        "get_scene_summary",
        {"scene_path": str(main_scene_path)},
    )

    captured = capsys.readouterr()
    assert captured.out == ""


def test_mcp_client_server_integration_flow(
    mcp_scene: Path,
) -> None:
    server = create_server(mcp_scene.parent)

    async def integration() -> dict:
        async with Client(server) as client:
            listed = await client.list_tools()
            before = await client.call_tool(
                "get_transform",
                {"scene_path": str(mcp_scene), "identifier": "PlayerSpawn_0"},
            )
            changed = await client.call_tool(
                "set_transform",
                {
                    "scene_path": str(mcp_scene),
                    "identifier": "PlayerSpawn_0",
                    "position": {"x": 8.0},
                },
            )
            after = await client.call_tool(
                "get_transform",
                {"scene_path": str(mcp_scene), "identifier": "PlayerSpawn_0"},
            )
            validation = await client.call_tool(
                "validate_scene", {"scene_path": str(mcp_scene)}
            )
            return {
                "tools": [tool.name for tool in listed.tools],
                "before": before.structured_content,
                "changed": changed.structured_content,
                "after": after.structured_content,
                "validation": validation.structured_content,
            }

    result = anyio.run(integration)
    assert set(result["tools"]) == EXPECTED_TOOLS
    assert result["before"]["position"]["x"] == 5.000002384185791
    assert result["changed"]["validation"]["passed"] is True
    assert result["after"]["position"]["x"] == 8.0
    assert result["validation"]["valid"] is True


def test_mcp_component_client_server_integration_flow(mcp_scene: Path) -> None:
    server = create_server(mcp_scene.parent)

    async def integration() -> dict:
        arguments = {
            "scene_path": str(mcp_scene),
            "object_identifier": "cf70476f-7eb5-4a5e-bf31-ddc9d0cf3e64",
            "component_identifier": "ModLight",
            "property_name": "intensity",
        }
        async with Client(server) as client:
            before = await client.call_tool("get_component_property", arguments)
            changed = await client.call_tool(
                "set_component_property", {**arguments, "value": 2.5}
            )
            after = await client.call_tool("get_component_property", arguments)
            validation = await client.call_tool(
                "validate_scene", {"scene_path": str(mcp_scene)}
            )
        return {
            "before": before.structured_content,
            "changed": changed.structured_content,
            "after": after.structured_content,
            "validation": validation.structured_content,
        }

    result = anyio.run(integration)
    assert result["before"]["value"] == 1.0
    assert result["changed"]["validation"]["passed"] is True
    assert result["after"]["value"] == 2.5
    assert result["validation"]["valid"] is True


def test_mcp_stdio_server_protocol_has_no_stdout_pollution(
    main_scene_path: Path,
) -> None:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "pummelmcp.mcp_server"],
        cwd=Path(__file__).parents[1],
        env={**os.environ, ALLOWED_ROOT_ENV: str(main_scene_path.parent)},
    )

    async def stdio_roundtrip() -> set[str]:
        async with Client(parameters, mode="legacy") as client:
            listed = await client.list_tools()
            return {tool.name for tool in listed.tools}

    assert anyio.run(stdio_roundtrip) == EXPECTED_TOOLS
