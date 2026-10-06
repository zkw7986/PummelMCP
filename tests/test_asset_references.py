from __future__ import annotations
import hashlib, shutil
from pathlib import Path
import pytest
from pummelmcp.pmh.asset_references import AssetReferenceError, parse_compact_asset_reference, parse_material_reference_list, resolve_asset_reference
from pummelmcp.pmh.asset_reference_writer import replace_prop_reference_from_template
from pummelmcp.pmh.duplication_writer import PROP_SHAPE, duplicate_leaf, plan_leaf_duplication
from pummelmcp.pmh.reader import read_pmh

def envelope(g="cf9fa6a9-3852-4dbe-803b-32cec4c94139"): return b"\x01\x24"+g.encode()

def test_strict_reference_and_material_list_framing():
    assert parse_compact_asset_reference(envelope()).guid.startswith("cf9")
    assert not parse_material_reference_list(b"\0\0\0\0").elements
    assert len(parse_material_reference_list(b"\1\0\0\0"+envelope()).elements)==1
    for raw in (envelope()+b"x", b"\0\x24"+envelope()[2:], b"\1\0\0\0"+envelope()+b"x"):
        with pytest.raises(AssetReferenceError):
            parse_material_reference_list(raw) if raw[:4] == b"\1\0\0\0" else parse_compact_asset_reference(raw)

def test_resolver_reports_builtin_unresolved(tmp_path):
    r=resolve_asset_reference(parse_compact_asset_reference(envelope()),tmp_path)
    assert r.status=="UNRESOLVED_BUILTIN" and not r.exists

def _props(scene): return [o for o in scene.walk() if tuple(c.type_name for c in o.components)==PROP_SHAPE]

def test_prop_candidate_duplicate_and_same_scene_whole_reference_swap(main_scene_path,tmp_path):
    p=tmp_path/"MainScene.scene";shutil.copyfile(main_scene_path,p)
    props=_props(read_pmh(p)); source=props[0]
    plan=plan_leaf_duplication(p,source.guid)
    assert plan.safety_class=="SAFE_PROP_LEAF_DUPLICATION_EMPTY_MATERIALS_CANDIDATE"
    report=duplicate_leaf(p,source.guid,backup=False)
    scene=read_pmh(p); duplicate=next(o for o in scene.walk() if o.guid==report.duplicate_guid)
    template=next(o for o in _props(scene) if o.get_component("ModProp").get_field("prop").raw != source.get_component("ModProp").get_field("prop").raw and o.get_component("ModProp").get_field("customMaterials").raw==b"\0\0\0\0")
    raw=template.get_component("ModProp").get_field("prop").raw
    before=p.read_bytes(); result=replace_prop_reference_from_template(p,duplicate.guid,template.guid,expected_scene_hash=hashlib.sha256(before).hexdigest(),template_reference_sha256=hashlib.sha256(raw).hexdigest(),backup=False)
    after=read_pmh(p); changed=next(o for o in after.walk() if o.guid==duplicate.guid)
    assert changed.get_component("ModProp").get_field("prop").raw==raw
    assert result.validation["passed"]
