from __future__ import annotations
import hashlib, shutil
from pathlib import Path
import pytest
from pummelmcp.pmh import read_pmh, register_object_template, plan_leaf_duplication
from pummelmcp.pmh.errors import ConcurrentModificationError, UnsafeDuplicationError
from pummelmcp.pmh.gameplay_composer import compose_gameplay, list_gameplay_recipes, plan_gameplay_composition, validate_gameplay_spec, resolve_layouts
from pummelmcp.mcp_server import create_server

def setup_mod(tmp_path,main_scene_path):
 root=tmp_path/'Mod';data=root/'Data';data.mkdir(parents=True);scene=data/'MainScene.scene';shutil.copyfile(main_scene_path,scene)
 s=read_pmh(scene)
 candidates=[]
 for o in s.walk():
  shape=[c.type_name for c in o.components]
  if o.parent and not o.children and shape in (["ModTransform","ModPlayerSpawn"],["ModTransform","ModLight"],["ModTransform","ModText"],["ModTransform","ModProp"]):
   try: plan_leaf_duplication(scene,o.guid)
   except UnsafeDuplicationError: continue
   candidates.append(o)
 for i,o in enumerate(candidates[:5]):register_object_template(scene,o.guid,f't{i}',catalog_root=root)
 return root,scene,candidates[:5]

def spec(scene,items):return {"version":"0.1","scene":str(scene),"destination_root":None,"objects":items,"metadata":{"purpose":"test"}}
def item(i,t,pos):return {"logical_id":f'o{i}',"recipe_type":t,"template_id":f't{i}',"transform":{"position":{"x":pos,"y":1,"z":0}}}

def test_strict_schema_and_registry():
 recipes=list_gameplay_recipes()["recipes"]
 assert len(recipes)==10
 assert {"SCORE_PAD","DEATH_PENALTY_ZONE","FEEDBACK_CHECKPOINT"}<={x["recipe_type"] for x in recipes}
 with pytest.raises(UnsafeDuplicationError,match="UNKNOWN_GAMEPLAY_SPEC_FIELD"):validate_gameplay_spec({"version":"0.1","scene":"x","objects":[{"logical_id":"x","recipe_type":"LIGHT_SET","template_id":"t","raw":1}]})
 with pytest.raises(UnsafeDuplicationError,match="DUPLICATE_LOGICAL_ID"):validate_gameplay_spec({"version":"0.1","scene":"x","objects":[{"logical_id":"x","recipe_type":"LIGHT_SET","template_id":"a"},{"logical_id":"x","recipe_type":"LIGHT_SET","template_id":"b"}]})

def test_row_and_grid_are_deterministic_and_strict():
 objects=[{"logical_id":x,"recipe_type":"LIGHT_SET","template_id":"t"} for x in "abcd"]
 base={"version":"0.1","scene":"x","objects":objects}
 row={**base,"layout":[{"type":"ROW","objects":["a","b","c"],"origin":{"x":-5,"y":1,"z":10},"direction":{"x":1,"y":0,"z":0},"spacing":5}]}
 assert resolve_layouts(row)=={"a":{"x":-5.0,"y":1.0,"z":10.0},"b":{"x":0.0,"y":1.0,"z":10.0},"c":{"x":5.0,"y":1.0,"z":10.0}}
 grid={**base,"layout":[{"type":"GRID","objects":["a","b","c","d"],"origin":{"x":0,"y":0,"z":0},"rows":2,"columns":2,"row_spacing":3,"column_spacing":4,"row_direction":{"x":0,"y":0,"z":1},"column_direction":{"x":1,"y":0,"z":0}}]}
 assert resolve_layouts(grid)["d"]=={"x":4.0,"y":0.0,"z":3.0}
 with pytest.raises(UnsafeDuplicationError,match="GRID_CAPACITY_MISMATCH"):resolve_layouts({**base,"layout":[{**grid["layout"][0],"columns":3}]})

def test_five_object_atomic_composition_and_rollback(tmp_path,main_scene_path):
 root,scene,objects=setup_mod(tmp_path,main_scene_path)
 recipes={tuple(c.type_name for c in o.components):r for o,r in []}
 types=[]
 for o in objects:
  shape=tuple(c.type_name for c in o.components)
  types.append({('ModTransform','ModPlayerSpawn'):'SPAWN_POINT_SET',('ModTransform','ModLight'):'LIGHT_SET',('ModTransform','ModText'):'TEXT_SIGN',('ModTransform','ModProp'):'PROP_LAYOUT'}[shape])
 s=spec(scene,[item(i,types[i],i*3) for i in range(5)])
 plan=plan_gameplay_composition(scene,root,s);before=scene.read_bytes()
 result=compose_gameplay(scene,root,s,expected_plan_sha256=plan.plan_sha256,backup=False)
 assert len(result['created_objects'])==5 and result['validation']['passed']
 clean=tmp_path/'rollback.scene';clean.write_bytes(before); (root/'Data'/'MainScene.scene').write_bytes(before)
 plan=plan_gameplay_composition(scene,root,s); before_hash=hashlib.sha256(scene.read_bytes()).hexdigest()
 with pytest.raises(RuntimeError):compose_gameplay(scene,root,s,expected_plan_sha256=plan.plan_sha256,backup=False,failure_injector=lambda stage: (_ for _ in ()).throw(RuntimeError('injected')) if stage=='after_object_3' else None)
 assert hashlib.sha256(scene.read_bytes()).hexdigest()==before_hash
 with pytest.raises(ConcurrentModificationError):compose_gameplay(scene,root,s,expected_plan_sha256='0'*64,backup=False)

def test_server_has_composer_tools(main_scene_path,tmp_path):
 root,_,_=setup_mod(tmp_path,main_scene_path);tools=create_server(root)._tool_manager._tools
 assert {'list_gameplay_recipes','plan_gameplay_composition','compose_gameplay'}<=set(tools)

def test_stage13d_three_button_action_recipe_and_rollback(tmp_path):
 root=tmp_path/'Stage13D';data=root/'Data';data.mkdir(parents=True);scene=data/'MainScene.scene';shutil.copyfile(Path(__file__).parent/'fixtures'/'Stage13DActionTemplate.scene',scene)
 register_object_template(scene,'3976dc48-7d5e-4db7-a297-40102971c7ab','button',catalog_root=root)
 register_object_template(scene,'d4b76d25-9d72-4772-9364-554c6c159404','spawn',catalog_root=root)
 buttons=[{'logical_id':f'b{i}','recipe_type':'BUTTON_SPAWN_PREFAB','template_id':'button','trigger_recipe':{'event_property':'OnEnterActions'}} for i in range(3)]
 spawns=[{'logical_id':f's{i}','recipe_type':'SPAWN_POINT_SET','template_id':'spawn','transform':{'position':{'x':-3+6*i,'y':1,'z':5}}} for i in range(2)]
 value=spec(scene,buttons+spawns);value['layout']=[{'type':'ROW','objects':['b0','b1','b2'],'origin':{'x':-5,'y':1,'z':10},'direction':{'x':1,'y':0,'z':0},'spacing':5}]
 plan=plan_gameplay_composition(scene,root,value);before=scene.read_bytes()
 assert plan.expected_actions==3 and [x['transform']['position']['x'] for x in plan.objects[:3]]==[-5,0,5]
 with pytest.raises(RuntimeError):compose_gameplay(scene,root,value,expected_plan_sha256=plan.plan_sha256,backup=False,failure_injector=lambda stage: (_ for _ in ()).throw(RuntimeError('action fail')) if stage=='after_action_2' else None)
 assert scene.read_bytes()==before
 result=compose_gameplay(scene,root,value,expected_plan_sha256=plan.plan_sha256,backup=False)
 assert len(result['created_actions'])==3 and all(x['class']=='SpawnPrefabAction' and x['rid']==1000 for x in result['created_actions'])
