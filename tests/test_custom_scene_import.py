from __future__ import annotations

import base64
import json
import shutil
from pathlib import Path

import pytest

from pummelmcp.pmh import (
    build_blender_scene_import,
    plan_blender_scene_import,
    read_pmh,
)
from pummelmcp.pmh.asset_references import (
    parse_compact_asset_reference,
    parse_material_reference_list,
)


PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@pytest.fixture
def import_case(tmp_path: Path, main_scene_path: Path):
    mod = tmp_path / "Mods" / "Casino"
    data = mod / "Data"
    data.mkdir(parents=True)
    scene = data / "MainScene.scene"
    shutil.copyfile(main_scene_path, scene)
    (data / "Meta.json").write_text("{}", encoding="utf-8")
    source = tmp_path / "BlenderExport"
    source.mkdir()
    (source / "table.obj").write_text(
        "mtllib table.mtl\nv 0 0 0\nv 1 0 0\nv 0 1 0\nvt 0 0\nvt 1 0\nvt 0 1\nusemtl Felt\nf 1/1 2/2 3/3\n",
        encoding="utf-8",
    )
    (source / "table.mtl").write_text("newmtl Felt\nmap_Kd felt.png\n", encoding="utf-8")
    (source / "felt.png").write_bytes(PNG_1PX)
    manifest = source / "pummel_scene.json"
    manifest.write_text(
        json.dumps(
            {
                "version": "0.1",
                "collection": "casino",
                "objects": [
                    {
                        "id": "table",
                        "obj": "table.obj",
                        "texture": "felt.png",
                        "name": "Imported Table",
                        "position": {"x": 1, "y": 2, "z": 3},
                        "rotation_degrees": {"y": 90},
                        "scale": {"x": 2, "y": 2, "z": 2},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    templates = Path(__file__).resolve().parents[1] / "StreamingAssets"
    if not templates.is_dir():
        pytest.skip("optional installed game material templates unavailable")
    return mod, scene, source, manifest, templates


def test_plan_and_build_textured_obj(import_case):
    mod, scene, source, manifest, templates = import_case
    plan = plan_blender_scene_import(
        scene,
        manifest,
        allowed_root=mod.parent,
        import_root=source,
        template_root=templates,
    )
    assert plan["objects"][0]["rotation"][1] == pytest.approx(1.57079632679)
    report = build_blender_scene_import(
        scene,
        manifest,
        plan["plan_sha256"],
        allowed_root=mod.parent,
        import_root=source,
        template_root=templates,
    )
    assert report["status"] == "BUILT_UNVERIFIED_IN_EDITOR"
    assert report["objects_placed"] == 1
    created = read_pmh(scene).find_game_object("Imported Table")
    prop = created.get_component("ModProp")
    prop_ref = parse_compact_asset_reference(prop.get_field("prop").raw)
    materials = parse_material_reference_list(prop.get_field("customMaterials").raw)
    assert prop_ref.guid == report["placements"][0]["prop_guid"]
    assert [item.guid for item in materials.elements] == report["placements"][0]["material_guids"]
    assert len(list((mod / "Assets" / "Props").glob("*.obj"))) == 1
    assert len(list((mod / "Assets" / "Textures").glob("*.png"))) == 1
    assert len(list((mod / "Assets" / "Materials").glob("*.pmat"))) == 1


def test_stale_plan_and_target_conflicts_fail(import_case):
    mod, scene, source, manifest, templates = import_case
    plan = plan_blender_scene_import(scene, manifest, allowed_root=mod.parent, import_root=source, template_root=templates)
    with pytest.raises(ValueError, match="STALE"):
        build_blender_scene_import(scene, manifest, "0" * 64, allowed_root=mod.parent, import_root=source, template_root=templates)
    build_blender_scene_import(scene, manifest, plan["plan_sha256"], allowed_root=mod.parent, import_root=source, template_root=templates)
    with pytest.raises(ValueError, match="already exist"):
        plan_blender_scene_import(scene, manifest, allowed_root=mod.parent, import_root=source, template_root=templates)
