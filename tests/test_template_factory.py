from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

import pytest

from pummelmcp.mcp_server import create_server
from pummelmcp.pmh import (
    ConcurrentModificationError, PMHScene, UnsafeDuplicationError,
    create_from_template, list_object_templates, plan_create_from_template,
    read_pmh, register_object_template,
)

ORACLES=Path(os.environ.get("PUMMELMCP_ORACLE_ROOT","."))

@pytest.fixture
def mod(tmp_path):
    if not (ORACLES/"09_subtree_before.scene").exists():pytest.skip("Stage 11 Oracle unavailable")
    root=tmp_path/"Mod";data=root/"Data";data.mkdir(parents=True);scene=data/"MainScene.scene";shutil.copyfile(ORACLES/"09_subtree_before.scene",scene);return root,scene

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def register_all(root,scene):
    empty=next(o for o in read_pmh(scene).walk() if o.name=="ChildLeaf" and o.parent and o.parent.name=="Parent")
    button=next(o for o in read_pmh(scene).walk() if o.guid=="a08ec153-338d-4bc8-9196-0344654c98cf")
    register_object_template(scene,empty.guid,"empty_leaf",catalog_root=root)
    register_object_template(scene,button.guid,"button_empty",catalog_root=root)
    register_object_template(scene,"/OuterParent/Cluster","simple_cluster",catalog_root=root)

def test_registration_is_scene_read_only_and_capabilities_are_narrow(mod):
    root,scene=mod;before=sha(scene);register_all(root,scene);assert sha(scene)==before
    entries={x["template_id"]:x for x in list_object_templates(scene,catalog_root=root)["templates"]}
    assert entries["empty_leaf"]["destination_policy"]=="ARBITRARY_PARENT"
    assert entries["button_empty"]["destination_policy"]=="SOURCE_SAME_PARENT"
    assert entries["simple_cluster"]["destination_policy"]=="SOURCE_SAME_PARENT"
    assert all(x["fingerprint_status"]=="VALID" for x in entries.values())

def test_duplicate_id_and_stale_registration_hash_rejected(mod):
    root,scene=mod;obj=next(o for o in read_pmh(scene).walk() if o.name=="ChildLeaf")
    register_object_template(scene,obj.guid,"empty_leaf",catalog_root=root)
    with pytest.raises(UnsafeDuplicationError,match="DUPLICATE_TEMPLATE_ID"):register_object_template(scene,obj.guid,"empty_leaf",catalog_root=root)
    with pytest.raises(ConcurrentModificationError,match="UNSAFE_STALE_SCENE"):register_object_template(scene,obj.guid,"other",catalog_root=root,expected_scene_hash="0"*64)

def test_empty_leaf_cross_parent_transaction_and_transform(mod):
    root,scene=mod;register_all(root,scene);source=next(x for x in list_object_templates(scene,catalog_root=root)["templates"] if x["template_id"]=="empty_leaf");before_source=source["template_fingerprint"]
    plan=plan_create_from_template(scene,"empty_leaf",catalog_root=root,destination_parent="/Root/ParentB",position={"x":5.0,"y":6.0,"z":7.0})
    assert plan.reparent_method=="reparent_gameobject"
    result=create_from_template(scene,"empty_leaf",catalog_root=root,destination_parent="/Root/ParentB",position={"x":5.0,"y":6.0,"z":7.0})
    out=read_pmh(scene);created=next(o for o in out.walk() if o.guid==result["created_root_guid"])
    assert created.parent.name=="ParentB" and created.sibling_index==3
    assert created.get_component("ModTransform").get_field("position").value.x==5.0
    assert next(x for x in list_object_templates(scene,catalog_root=root)["templates"] if x["template_id"]=="empty_leaf")["template_fingerprint"]==before_source

def test_button_same_parent_and_subtree_factory(mod):
    root,scene=mod;register_all(root,scene)
    with pytest.raises(UnsafeDuplicationError,match="UNSUPPORTED_TEMPLATE_DESTINATION"):plan_create_from_template(scene,"button_empty",catalog_root=root,destination_parent="/Root/ParentB")
    button=create_from_template(scene,"button_empty",catalog_root=root,position={"x":8.0})
    b=next(o for o in read_pmh(scene).walk() if o.guid==button["created_root_guid"]);assert [c.type_name for c in b.components]==["ModTransform","ModBoxCollider","ModTrigger"]
    with pytest.raises(UnsafeDuplicationError,match="UNSUPPORTED_SUBTREE_REPARENT"):plan_create_from_template(scene,"simple_cluster",catalog_root=root,destination_parent="/Root/ParentB")
    cluster=create_from_template(scene,"simple_cluster",catalog_root=root,position={"x":12.0})
    c=next(o for o in read_pmh(scene).walk() if o.guid==cluster["created_root_guid"]);assert c.parent.name=="OuterParent" and [x.name for x in c.children]==["LeafA","LeafB"] and c.get_component("ModTransform").get_field("position").value.x==12.0

def test_template_mutation_is_detected(mod):
    root,scene=mod;register_all(root,scene);entry=next(x for x in list_object_templates(scene,catalog_root=root)["templates"] if x["template_id"]=="empty_leaf")
    PMHScene.load(scene).set_transform(entry["source_gameobject_guid"],position={"x":99.0})
    with pytest.raises(UnsafeDuplicationError,match="TEMPLATE_STALE"):create_from_template(scene,"empty_leaf",catalog_root=root)

def test_rollback_discards_temp_transaction(mod):
    root,scene=mod;register_all(root,scene);before=scene.read_bytes()
    def fail(stage):
        if stage=="after_reparent":raise RuntimeError("injected")
    with pytest.raises(RuntimeError,match="injected"):create_from_template(scene,"empty_leaf",catalog_root=root,destination_parent="/Root/ParentB",failure_injector=fail)
    assert scene.read_bytes()==before

def test_same_scene_and_missing_template_rejected(mod):
    root,scene=mod;register_all(root,scene)
    with pytest.raises(UnsafeDuplicationError,match="TEMPLATE_NOT_FOUND"):plan_create_from_template(scene,"missing",catalog_root=root)
    other=scene.parent/"Other.scene";shutil.copyfile(scene,other)
    with pytest.raises(UnsafeDuplicationError,match="CROSS_SCENE_TEMPLATE_UNSUPPORTED"):plan_create_from_template(other,"empty_leaf",catalog_root=root)

def test_server_has_32_tools(mod):
    root,_=mod;tools=create_server(root)._tool_manager._tools;assert len(tools)==43
    assert {"register_object_template","list_object_templates","plan_create_from_template","create_from_template"}<=set(tools)
