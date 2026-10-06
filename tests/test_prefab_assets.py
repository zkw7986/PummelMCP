from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import anyio
import pytest

from pummelmcp.mcp_server import create_server
from pummelmcp.pmh.errors import ConcurrentModificationError, UnsafeDuplicationError
from pummelmcp.pmh.prefab_assets import build_prefab_pack, plan_prefab_pack
from pummelmcp.pmh.reader import read_pfab
from pummelmcp.pmh.reader import read_pmh
from pummelmcp.pmh.validator import validate_scene


SOURCE = Path(__file__).resolve().parents[1] / "StreamingAssets" / "WorkshopTemplates" / "Minigames" / "Top Down Shooter"
SCORE_TEMPLATE = Path(__file__).resolve().parents[1] / "StreamingAssets" / "InbuiltMods" / "Aftershock Arena" / "Data" / "MainScene.scene"

pytestmark = pytest.mark.skipif(not SOURCE.is_dir(), reason="optional installed game prefab fixtures unavailable")


def setup(tmp_path):
    mod = tmp_path / "Shooter"
    shutil.copytree(SOURCE, mod)
    scene = mod / "Data" / "MainScene.scene"
    spec = {"version": "0.1", "scene": str(scene), "output_mod": "PrefabPack",
            "assets": [
                {"source": "Assets/Prefabs/SMG_Visual.pfab", "name": "CoinVisual"},
                {"source": "Assets/Prefabs/SMG_Item.pfab", "name": "CoinItem"},
            ]}
    return mod, scene, spec


def test_shipped_prefabs_parse_to_eof():
    paths = list((SOURCE.parents[2]).rglob("*.pfab"))
    assert len(paths) >= 10
    for path in paths:
        parsed = read_pfab(path)
        assert parsed.root_count == 1
        assert parsed.fully_consumed and validate_scene(parsed).passed


def test_prefab_pack_clones_assets_and_remaps_dependency(tmp_path):
    mod, scene, spec = setup(tmp_path)
    spawner = read_pmh(scene).find_game_object("WeaponSpawner")
    spec["assets"][0]["root_transform"] = {"scale": {"x": 1.5, "y": 1.5, "z": 1.5}}
    template = tmp_path / "ScoreTemplate.scene"
    shutil.copyfile(SCORE_TEMPLATE, template)
    spec["pickup_scores"] = [{"source_asset": "Assets/Prefabs/SMG_Item.pfab",
                              "value": 5, "template_scene": str(template)}]
    spec["spawner_bindings"] = [{"object_guid": spawner.guid,
                                 "source_asset": "Assets/Prefabs/SMG_Item.pfab"}]
    source = {p.relative_to(mod).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in mod.rglob("*") if p.is_file()}
    old_visual_guid = json.loads((mod / "Assets/Prefabs/SMG_Visual.pfab.pmeta").read_text())["guid"]["serializedGuid"]
    plan = plan_prefab_pack(scene, tmp_path, spec)
    result = build_prefab_pack(scene, tmp_path, spec, plan["plan_sha256"])
    assert result["status"] == "BUILD_PASS"
    output = Path(result["output_mod"])
    visual = output / "Assets/Prefabs/CoinVisual.pfab"
    item = output / "Assets/Prefabs/CoinItem.pfab"
    visual_guid = json.loads(Path(str(visual) + ".pmeta").read_text())["guid"]["serializedGuid"]
    assert visual_guid != old_visual_guid
    assert visual_guid.encode() in item.read_bytes()
    assert old_visual_guid.encode() not in item.read_bytes()
    assert b"9d9e1c6b-575a-415c-a2a9-ed96d2046edb" in visual.read_bytes()
    from pummelmcp.pmh.actions import parse_action_payload
    pickup = read_pfab(item).roots[0].get_component("ModItem").get_field("OnPickupTrigger")
    action = parse_action_payload(pickup.raw).actions[0]
    assert (action.type_info.class_name, action.fields["m_operation"],
            action.fields["m_value"], action.fields["m_targetFlags"]) == ("ChangeScoreAction", 1, 5, 1)
    new_item_guid = json.loads(Path(str(item) + ".pmeta").read_text())["guid"]["serializedGuid"]
    output_spawner = read_pmh(output / "Data/MainScene.scene").find_game_object("WeaponSpawner")
    assert all(new_item_guid.encode() in output_spawner.get_component("ModSpawner").get_field(name).raw
               for name in ("Prefabs", "PrefabGUIDs"))
    for path in (visual, item):
        assert read_pfab(path).fully_consumed and validate_scene(read_pfab(path)).passed
        assert read_pfab(path).roots[0].name == path.stem
    assert read_pfab(visual).roots[0].get_component("ModTransform").get_field("scale").value.x == 1.5
    assert source == {p.relative_to(mod).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in mod.rglob("*") if p.is_file()}


def test_prefab_pack_mcp_and_stale_plan(tmp_path):
    from test_workflow import proposal
    workshop = tmp_path / "WorkshopMods"
    workshop.mkdir()
    _, scene, spec = setup(workshop)
    server = create_server(workshop)

    async def call(name, arguments):
        return await server.call_tool(name, arguments)

    anyio.run(call, "authorize_workshop_directory", {"workshop_directory": str(workshop), "user_granted_write_permission": True})
    review = anyio.run(call, "submit_gameplay_review", proposal(workshop / spec["output_mod"]))
    anyio.run(call, "approve_gameplay_review", {"review_sha256": review.structured_content["review_sha256"], "user_approved": True, "user_reply": "Approve this test plan"})
    plan = anyio.run(call, "plan_prefab_pack", {"scene_path": str(scene), "spec": spec})
    assert not plan.is_error
    built = anyio.run(call, "build_prefab_pack", {"scene_path": str(scene), "spec": spec,
                                                "expected_plan_sha256": plan.structured_content["plan_sha256"]})
    assert not built.is_error
    assert len(built.structured_content["assets"]) == 2
    assert built.structured_content["runtime_status"] == "NOT_RUN"


def test_prefab_pack_rejects_missing_dependency_and_stale_source(tmp_path):
    mod, scene, spec = setup(tmp_path)
    (mod / "Assets/Prefabs/SMG_Visual.pfab.pmeta").unlink()
    with pytest.raises(UnsafeDuplicationError):
        plan_prefab_pack(scene, tmp_path, spec)
    assert not (tmp_path / "PrefabPack").exists()
    mod, scene, spec = setup(tmp_path / "other")
    plan = plan_prefab_pack(scene, mod.parent, spec)
    path = mod / "Assets/Prefabs/SMG_Item.pfab.pmeta"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ConcurrentModificationError):
        build_prefab_pack(scene, mod.parent, spec, plan["plan_sha256"])
    assert not (mod.parent / "PrefabPack").exists()


def test_prefab_pack_failure_cleans_staged_mod(tmp_path, monkeypatch):
    import pummelmcp.pmh.prefab_assets as assets

    mod, scene, spec = setup(tmp_path)
    plan = plan_prefab_pack(scene, tmp_path, spec)
    original_reader = assets.read_pfab

    def fail_second_clone(path):
        if Path(path).name == "CoinItem.pfab":
            raise RuntimeError("injected prefab validation failure")
        return original_reader(path)

    monkeypatch.setattr(assets, "read_pfab", fail_second_clone)
    with pytest.raises(RuntimeError, match="injected prefab validation failure"):
        build_prefab_pack(scene, tmp_path, spec, plan["plan_sha256"])
    assert not (tmp_path / "PrefabPack").exists()
    assert not list(tmp_path.glob(".pummelmcp-prefab-build-*"))
    assert (mod / "Assets/Prefabs/SMG_Item.pfab").is_file()


def test_prefab_pack_rejects_unproven_pickup_score(tmp_path):
    _, scene, spec = setup(tmp_path)
    spec["pickup_scores"] = [{"source_asset": "Assets/Prefabs/SMG_Item.pfab",
                              "value": 5, "template_scene": str(scene)}]
    with pytest.raises(UnsafeDuplicationError, match="TRIGGER_TEMPLATE_UNAVAILABLE|SCORE_TEMPLATE_UNAVAILABLE"):
        plan_prefab_pack(scene, tmp_path, spec)
    assert not (tmp_path / "PrefabPack").exists()


def test_prefab_pack_hash_binds_uncloned_dependency(tmp_path):
    mod, scene, spec = setup(tmp_path)
    spec["assets"] = [spec["assets"][1]]
    plan = plan_prefab_pack(scene, tmp_path, spec)
    dependency = mod / "Assets/Prefabs/SMG_Visual.pfab.pmeta"
    dependency.write_bytes(dependency.read_bytes() + b" ")
    with pytest.raises(ConcurrentModificationError):
        build_prefab_pack(scene, tmp_path, spec, plan["plan_sha256"])
    assert not (tmp_path / "PrefabPack").exists()
