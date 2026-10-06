from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from pathlib import Path

import pytest

from pummelmcp.mcp_server import create_server
from pummelmcp.pmh import (
    ConcurrentModificationError, UnsafeDuplicationError, duplicate_subtree,
    plan_gameobject_reparent, plan_subtree_duplication, read_pmh,
    reparent_gameobject,
)

ROOT = Path(os.environ.get("PUMMELMCP_ORACLE_ROOT", "."))
SHA08 = ("3556725c02a44e7b06290d044652260ef600fdcfa1a3c2f355cc80d939d6b859", "b2cabc28a430df0f76ed040b4163e876fb795f9c8c4437fb5c59e1420d442f25")
SHA09 = ("a08c21897908d315ea881729a482ac7544129c527192808e4581c3f71a0c6665", "128dd234a8813ec7d1ec32e53f7cd4d531c3e817f0e3931e0f3819ef737fc98b")

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()

@pytest.fixture(scope="module", autouse=True)
def require_oracles():
    if not (ROOT / "08_leaf_reparent_before.scene").exists(): pytest.skip("Stage 11 Oracles unavailable")

def test_oracles_are_hash_pinned_and_exact_eof():
    for names, hashes in [(("08_leaf_reparent_before.scene","08_leaf_reparent_after.scene"),SHA08),(("09_subtree_before.scene","09_subtree_after.scene"),SHA09)]:
        paths=[ROOT/n for n in names]; assert tuple(map(sha,paths))==hashes
        assert all(read_pmh(p).fully_consumed for p in paths); assert tuple(map(sha,paths))==hashes

def test_08_proves_append_identity_and_local_transform_semantics():
    before=read_pmh(ROOT/"08_leaf_reparent_before.scene");after=read_pmh(ROOT/"08_leaf_reparent_after.scene")
    src=before.find_game_object("MoveLeaf"); moved=next(o for o in after.walk() if o.guid==src.guid)
    assert (before.object_count,before.component_count)==(after.object_count,after.component_count)==(59,104)
    assert [x.name for x in src.parent.children]==["BeforeSibling","MoveLeaf","AfterSibling"]
    assert [x.name for x in moved.parent.children]==["DestA","DestB","MoveLeaf"]
    assert moved.guid==moved.components[0].guid==src.components[0].guid
    for field in ("position","rotation","scale"):
        a=src.components[0].get_field(field).value;b=moved.components[0].get_field(field).value
        assert max(abs(getattr(a,k)-getattr(b,k)) for k in "xyz") < 0.00001

def test_reparent_plan_and_writer_structural_equivalence(tmp_path):
    target=tmp_path/"x.scene";shutil.copyfile(ROOT/"08_leaf_reparent_before.scene",target)
    plan=plan_gameobject_reparent(target,"/Root/ParentA/MoveLeaf","/Root/ParentB")
    assert plan.safety_class=="SAFE_TRANSFORM_ONLY_LEAF_REPARENT"; assert plan.destination["sibling_index"]==2;assert plan.destination["preorder_index"]==58
    rep=reparent_gameobject(target,plan.source["guid"],plan.destination["new_parent"],expected_scene_hash=SHA08[0])
    generated=read_pmh(target); official=read_pmh(ROOT/"08_leaf_reparent_after.scene"); moved=next(o for o in generated.walk() if o.guid==plan.source["guid"]); expected=next(o for o in official.walk() if o.guid==plan.source["guid"])
    assert rep.validation["passed"] and (moved.parent.guid,moved.sibling_index,moved.preorder_index)==(expected.parent.guid,expected.sibling_index,expected.preorder_index)
    assert target.read_bytes()!= (ROOT/"08_leaf_reparent_after.scene").read_bytes()

@pytest.mark.parametrize("source,dest,code",[("/Root","/Root/ParentB","UNSAFE_ROOT_OBJECT"),("/Root/ParentA","/Root/ParentB","UNSAFE_HAS_CHILDREN"),("/Root/ParentA/MoveLeaf","/Root/ParentA/MoveLeaf","UNSAFE_CYCLE")])
def test_reparent_rejects_unsafe_shapes(source,dest,code):
    with pytest.raises(UnsafeDuplicationError,match=code): plan_gameobject_reparent(ROOT/"08_leaf_reparent_before.scene",source,dest)

def test_reparent_rejects_stale_hash(tmp_path):
    p=tmp_path/"x.scene";shutil.copyfile(ROOT/"08_leaf_reparent_before.scene",p)
    with pytest.raises(ConcurrentModificationError,match="UNSAFE_STALE_SCENE"): reparent_gameobject(p,"/Root/ParentA/MoveLeaf","/Root/ParentB",expected_scene_hash="0"*64)

def test_09_oracle_and_fixed_uuid_byte_equivalence(tmp_path):
    before=read_pmh(ROOT/"09_subtree_before.scene");after=read_pmh(ROOT/"09_subtree_after.scene");old={o.guid for o in before.walk()};added=[o for o in after.walk() if o.guid not in old]
    assert [(o.name,o.sibling_index) for o in added]==[("Cluster",2),("LeafA",0),("LeafB",1)]
    p=tmp_path/"x.scene";shutil.copyfile(ROOT/"09_subtree_before.scene",p);values=iter(uuid.UUID(o.guid) for o in added);plan=plan_subtree_duplication(p,"/OuterParent/Cluster",guid_factory=lambda:next(values));values=iter(uuid.UUID(o.guid) for o in added);report=duplicate_subtree(p,plan.source_root["guid"],guid_factory=lambda:next(values),expected_scene_hash=SHA09[0])
    assert report["validation"]["passed"] and p.read_bytes()==(ROOT/"09_subtree_after.scene").read_bytes()

def test_subtree_root_and_unsupported_component_rejected():
    with pytest.raises(UnsafeDuplicationError,match="UNSAFE_ROOT_OBJECT"): plan_subtree_duplication(ROOT/"09_subtree_before.scene","/OuterParent")
    with pytest.raises(UnsafeDuplicationError,match="UNSAFE_UNSUPPORTED_COMPONENT"): plan_subtree_duplication(ROOT/"09_subtree_before.scene","a08ec153-338d-4bc8-9196-0344654c98cf")

def test_mcp_surface_retains_stage11_tools():
    tools=create_server(ROOT)._tool_manager._tools
    assert len(tools)==43
    assert {"plan_gameobject_reparent","reparent_gameobject","plan_subtree_duplication","duplicate_subtree"} <= set(tools)
