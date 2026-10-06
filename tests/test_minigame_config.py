from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import anyio
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from pummelmcp.mcp_server import create_server
from pummelmcp.minigame_config import (
    MinigameConfigError,
    apply_minigame_config_update,
    get_minigame_config,
    plan_minigame_config_update,
)
from pummelmcp.pmh.errors import ConcurrentModificationError


SOURCE = Path(__file__).resolve().parents[1] / "src" / "pummelmcp" / "templates" / "minimal-third-person" / "Data"


@pytest.fixture
def mod(tmp_path: Path):
    data = tmp_path / "My Mod" / "Data"
    data.mkdir(parents=True)
    for name in ("MainScene.scene", "ModSettings.json", "MinigameDefinitionData.json"):
        shutil.copyfile(SOURCE / name, data / name)
    return data / "MainScene.scene", tmp_path


def test_config_read_and_atomic_write(mod):
    scene, root = mod
    settings = scene.parent / "ModSettings.json"
    original = settings.read_bytes()
    read = get_minigame_config(scene, root)
    assert read["settings"]["values"]["movement_speed"] == 6
    assert read["settings"]["values"]["end_conditions"] == ["timer"]
    updates = {"movement_speed": 8.5, "health_points": 75, "use_health": True,
               "rounds": 3, "end_conditions": ["timer", "obtain_points"],
               "round_duration_seconds": 90, "points_to_win": 10}
    plan = plan_minigame_config_update(scene, root, "settings", updates)
    assert settings.read_bytes() == original
    result = apply_minigame_config_update(scene, root, "settings", updates, plan["plan_sha256"])
    assert result["written"] is True
    assert Path(result["backup_path"]).read_bytes() == original
    after = get_minigame_config(scene, root)["settings"]["values"]
    assert after["end_conditions"] == ["timer", "obtain_points"]
    assert after["health_points"] == 75
    assert after["movement_speed"] == 8.5
    assert after["rounds"] == 3
    assert hashlib.sha256(settings.read_bytes()).hexdigest() == plan["after_sha256"]
    assert scene.read_bytes() == (SOURCE / "MainScene.scene").read_bytes()
    with pytest.raises(ConcurrentModificationError):
        apply_minigame_config_update(scene, root, "settings", updates, plan["plan_sha256"])


def test_details_and_invalid_rules_leave_source_unchanged(mod):
    scene, root = mod
    details = scene.parent / "MinigameDefinitionData.json"
    before = details.read_bytes()
    plan = plan_minigame_config_update(scene, root, "details", {"name": "夺旗赛", "description": "先夺旗者获胜", "min_players": 2, "max_players": 6})
    apply_minigame_config_update(scene, root, "details", {"name": "夺旗赛", "description": "先夺旗者获胜", "min_players": 2, "max_players": 6}, plan["plan_sha256"])
    assert b'"ScreenshotTextureGuid"' in details.read_bytes()
    assert get_minigame_config(scene, root)["details"]["values"]["name"] == "夺旗赛"
    assert before != details.read_bytes()
    current = details.read_bytes()
    with pytest.raises(MinigameConfigError):
        plan_minigame_config_update(scene, root, "details", {"min_players": 8, "max_players": 2})
    assert details.read_bytes() == current


@pytest.mark.parametrize("updates", [
    {"rounds": 0}, {"movement_speed": float("nan")}, {"health_points": True},
    {"end_conditions": ["timer", "timer"]}, {"end_conditions": ["unknown"]},
    {"raw_json": "{}"}, {"respawn_enabled": True, "end_conditions": ["remaining_players_alive"]},
])
def test_invalid_update_is_rejected(mod, updates):
    scene, root = mod
    before = (scene.parent / "ModSettings.json").read_bytes()
    with pytest.raises(MinigameConfigError):
        plan_minigame_config_update(scene, root, "settings", updates)
    assert (scene.parent / "ModSettings.json").read_bytes() == before


def test_mcp_config_tools_and_path_policy(mod):
    scene, root = mod
    server = create_server(root)
    async def call(name, arguments):
        return await server.call_tool(name, arguments)
    read = anyio.run(call, "get_minigame_config", {"scene_path": str(scene)})
    assert read.structured_content["settings"]["values"]["rounds"] == 1
    plan = anyio.run(call, "plan_minigame_config_update", {"scene_path": str(scene), "kind": "settings", "updates": {"rounds": 2}})
    assert not plan.is_error
    write = anyio.run(call, "apply_minigame_config_update", {"scene_path": str(scene), "kind": "settings", "updates": {"rounds": 2}, "expected_plan_sha256": plan.structured_content["plan_sha256"]})
    assert write.structured_content["written"] is True
    with pytest.raises(ToolError, match="outside PUMMELMCP_ALLOWED_ROOT"):
        anyio.run(call, "get_minigame_config", {"scene_path": str(SOURCE / "MainScene.scene")})
