from __future__ import annotations
import copy,hashlib,json,shutil
from pathlib import Path
import pytest
from pummelmcp.pmh import ACTION_EVENT_PROPERTIES,encode_action_varuint7,read_pmh,register_object_template
from pummelmcp.pmh.errors import ConcurrentModificationError,UnsafeDuplicationError
from pummelmcp.pmh.minigame_planner import build_minigame,list_minigame_archetypes,plan_minigame,validate_minigame_spec
from pummelmcp.pmh.gameplay_composer import plan_gameplay_composition, compose_gameplay
from pummelmcp.pmh.actions import parse_action_payload

def _target_fields(type_tag):
 return {"m_type":type_tag,"m_version":0,"m_targetFlags":1,"m_targets":[]}

ACTION_DATA={
 "KillAction":_target_fields(64),
 "ChangeScoreAction":{**_target_fields(128),"m_operation":1,"m_value":10},
 "ChangeHealthAction":{**_target_fields(96),"m_operation":2,"m_value":10},
 "SetPlacementAction":_target_fields(480),
 "SpawnEffectAction":{**_target_fields(160),"m_effectType":1},
 "PlaySoundAction":{**_target_fields(544),"m_clip":{"m_assetGUID":{"serializedGuid":"951689b9-9095-4d7d-a7f1-2033c2214a91"},"m_asset":{"instanceID":188302}},"m_volume":1.0},
 "ShowMessageAction":{**_target_fields(1024),"m_messageTarget":2,"m_message":"Checkpoint!","m_duration":1.0},
}

def _action_payload(entries):
 refids=[];actions=[];tails=[]
 for index,(class_name,data) in enumerate(entries):
  rid=1000+index;actions.append({"rid":rid});refids.append({"rid":rid,"type":{"class":class_name,"ns":"ModSystem.Logic","asm":"Assembly-CSharp"},"data":data})
  raw=json.dumps(data,indent=4).encode();tails.append(int(data["m_type"]).to_bytes(2,"little")+encode_action_varuint7(len(raw))+raw)
 root={"m_type":0,"m_actions":actions,"references":{"version":2,"RefIds":refids}};root_raw=json.dumps(root,indent=4).encode()
 return b"\0\0"+encode_action_varuint7(len(root_raw))+root_raw+len(entries).to_bytes(2,"little")+b"".join(tails)

def seed_phase3_templates(scene):
 candidates=[]
 for obj in read_pmh(scene).walk():
  for component in obj.components:
   if component.type_name=="ModTrigger":
    candidates += [(obj,component,field) for field in component.fields if field.name in ACTION_EVENT_PROPERTIES and field.source_span is not None]
 _,_,field=candidates[-1];span=field.source_span;payload=_action_payload(list(ACTION_DATA.items()));raw=scene.read_bytes()
 scene.write_bytes(raw[:span.start-4]+len(payload).to_bytes(4,"little")+payload+raw[span.end:])

def setup(tmp_path):
 root=tmp_path/'Mod';(root/'Data').mkdir(parents=True);scene=root/'Data'/'MainScene.scene';shutil.copyfile(Path(__file__).parent/'fixtures'/'Stage15Baseline.scene',scene)
 s=read_pmh(scene);wanted={('ModTransform','ModBoxCollider','ModTrigger'):'button',('ModTransform','ModPlayerSpawn'):'spawn',('ModTransform','ModText'):'text',('ModTransform','ModLight'):'light'}
 for o in s.walk():
  shape=tuple(c.type_name for c in o.components)
  if shape in wanted and wanted[shape] not in {x['template_id'] for x in __import__('json').loads((root/'.pummelmcp-object-templates.json').read_text())['templates']} if (root/'.pummelmcp-object-templates.json').exists() else True:
   try:register_object_template(scene,o.guid,wanted[shape],catalog_root=root)
   except Exception:pass
 (root/'Assets'/'Prefabs').mkdir(parents=True);(root/'Assets'/'Prefabs'/'Prefab_0.pfab').write_bytes(b'pfab');(root/'Assets'/'Prefabs'/'Prefab_0.pfab.pmeta').write_text('{}')
 return root,scene
def spec(scene,level='PLAYABLE_PROTOTYPE'):
 return {'version':'0.1','title':'Three Button Challenge','archetype':'BUTTON_SPAWN_CHALLENGE','scene':str(scene),'gameplay_root':None,'objective':{'type':'TRIGGER_ALL_BUTTONS','enforcement':'INSTRUCTIONAL_OBJECTIVE_ONLY'},'players':{'spawn_count':2,'spawn_positions':[{'x':-3,'y':1,'z':5},{'x':3,'y':1,'z':5}],'template_id':'spawn'},'layout':{'button_origin':{'x':-5,'y':1,'z':10},'button_direction':{'x':1,'y':0,'z':0},'button_spacing':5},'elements':{'button_count':3,'button_template_id':'button','player_spawn_template_id':'spawn','text_template_id':'text','light_template_id':'light','prefab_name':'Prefab_0'},'rules':[],'visuals':{'text_position':{'x':0,'y':4,'z':8},'light_position':{'x':0,'y':6,'z':5}},'runtime_expectations':['SceneLoaded','NoRuntimeErrors','PrefabSpawned'],'completion_level':level}

def phase4_spec(scene,archetype):
 s=spec(scene);s['title']=archetype.replace('_',' ').title();s['archetype']=archetype
 s['elements']={'trigger_count':2,'trigger_template_id':'button','player_spawn_template_id':'spawn','text_template_id':'text','light_template_id':'light'}
 s['layout']={'trigger_origin':{'x':-3,'y':1,'z':10},'trigger_direction':{'x':1,'y':0,'z':0},'trigger_spacing':6}
 objectives={
  'SCORE_PAD_CHALLENGE':{'type':'COLLECT_SCORE_FROM_PADS','enforcement':'ACTION_ENFORCED_NO_WIN_CONDITION'},
  'DEATH_PENALTY_ARENA':{'type':'AVOID_DEATH_PENALTY_ZONES','enforcement':'ACTION_ENFORCED_NO_WIN_CONDITION'},
  'TRIGGER_FEEDBACK_COURSE':{'type':'TRIGGER_ALL_CHECKPOINTS','enforcement':'INSTRUCTIONAL_OBJECTIVE_ONLY'},
 }
 s['objective']=objectives[archetype];s['runtime_expectations']=['SceneLoaded','NoRuntimeErrors']
 return s
def test_registry_and_capability_audit():
 r=list_minigame_archetypes();available={x['id'] for x in r['archetypes'] if x['availability']=='AVAILABLE'}
 assert {'BUTTON_SPAWN_CHALLENGE','SCORE_PAD_CHALLENGE','DEATH_PENALTY_ARENA','TRIGGER_FEEDBACK_COURSE'}<=available
 assert any(x['id']=='SCORE_CONTROL' and x['status']=='PARTIAL' and x['required_action']=='ChangeScoreAction' for x in r['capability_report']['capabilities'])


def test_configurable_trigger_scores_differ_and_preserve_source(tmp_path):
 root,scene=setup(tmp_path);seed_phase3_templates(scene)
 before=scene.read_bytes()
 def trigger(i,value):
  return {'logical_id':f'pad{i}','recipe_type':'CONFIGURABLE_TRIGGER','template_id':'button',
   'transform':{'position':{'x':i*4,'y':1,'z':4}},
   'trigger_recipe':{'event_property':'OnEnterActions','condition':'always','actions':[
    {'type':'CHANGE_SCORE','target':'triggering_player','operation':'add','value':value}]}}
 spec={'version':'0.1','scene':str(scene),'destination_root':None,'objects':[trigger(1,3),trigger(2,7)],'metadata':{}}
 plan=plan_gameplay_composition(scene,root,spec)
 assert plan.expected_actions==2
 result=compose_gameplay(scene,root,spec,expected_plan_sha256=plan.plan_sha256)
 assert result['validation']['passed']
 values=[]
 for guid in result['created_objects'].values():
  obj=next(o for o in read_pmh(scene).walk() if o.guid==guid)
  field=obj.get_component('ModTrigger').get_field('OnEnterActions')
  action=parse_action_payload(field.raw).actions[0]
  values.append((action.fields['m_value'],action.fields['m_operation'],action.fields['m_targetFlags']))
 assert values==[(3,1,1),(7,1,1)]
 assert scene.read_bytes()!=before


def test_configurable_trigger_rejects_arbitrary_rule(tmp_path):
 root,scene=setup(tmp_path)
 item={'logical_id':'pad','recipe_type':'CONFIGURABLE_TRIGGER','template_id':'button',
  'trigger_recipe':{'event_property':'OnEnterActions','condition':'player_score_gt_5',
  'actions':[{'type':'CHANGE_SCORE','target':'triggering_player','operation':'add','value':1}]}}
 spec={'version':'0.1','scene':str(scene),'destination_root':None,'objects':[item],'metadata':{}}
 with pytest.raises(UnsafeDuplicationError,match='RULE_CONDITION_UNSUPPORTED'):
  plan_gameplay_composition(scene,root,spec)

@pytest.mark.parametrize(('archetype','action_sequence'),[
 ('SCORE_PAD_CHALLENGE',('ChangeScoreAction','SpawnEffectAction','PlaySoundAction')),
 ('DEATH_PENALTY_ARENA',('ChangeScoreAction','KillAction')),
 ('TRIGGER_FEEDBACK_COURSE',('ShowMessageAction','SpawnEffectAction','PlaySoundAction')),
])
def test_phase4_archetypes_plan_and_build(tmp_path,archetype,action_sequence):
 root,scene=setup(tmp_path);seed_phase3_templates(scene);s=phase4_spec(scene,archetype)
 p=plan_minigame(scene,root,s)
 assert p.status=='SAFE_TO_BUILD' and p.gameplay_plan['expected_actions']==2*len(action_sequence)
 result=build_minigame(scene,root,s,expected_plan_hash=p.plan_sha256)
 assert result['build_result']=='BUILD_PASS'
 assert [x['class'] for x in result['created_actions']]==list(action_sequence)*2

def test_phase4_archetype_fails_closed_without_action_templates(tmp_path):
 root,scene=setup(tmp_path);s=phase4_spec(scene,'SCORE_PAD_CHALLENGE')
 with pytest.raises(UnsafeDuplicationError,match='exact ChangeScoreAction template unavailable'):
  plan_minigame(scene,root,s)
def test_valid_plan_benchmark(tmp_path):
 root,scene=setup(tmp_path);p=plan_minigame(scene,root,spec(scene));assert p.status=='SAFE_TO_BUILD' and p.gameplay_plan['expected_actions']==3 and len(p.gameplay_spec['objects'])==7
def test_unknown_archetype_rejected(tmp_path):
 root,scene=setup(tmp_path);s=spec(scene);s['archetype']='UNKNOWN'
 with pytest.raises(UnsafeDuplicationError,match='UNKNOWN_MINIGAME_ARCHETYPE'):plan_minigame(scene,root,s)
def test_full_minigame_reports_gaps(tmp_path):
 root,scene=setup(tmp_path);p=plan_minigame(scene,root,spec(scene,'FULL_MINIGAME'));assert p.status=='BLOCKED' and set(p.capability_gaps)=={'ROUND_CONTROL','SCORE_CONTROL','WIN_CONDITION'}
def test_invalid_player_layout_rules(tmp_path):
 root,scene=setup(tmp_path)
 for mutate,error in [(lambda s:s['players'].__setitem__('spawn_count',3),'INVALID_PLAYER_COUNT'),(lambda s:s['layout'].__setitem__('button_spacing',0),'INVALID_LAYOUT'),(lambda s:s.__setitem__('rules',[{'script':'x'}]),'UNSUPPORTED_RULES')]:
  s=spec(scene);mutate(s)
  with pytest.raises(UnsafeDuplicationError,match=error):validate_minigame_spec(s)
def test_missing_template_and_asset(tmp_path):
 root,scene=setup(tmp_path);s=spec(scene);s['elements']['text_template_id']='missing'
 with pytest.raises(UnsafeDuplicationError):plan_minigame(scene,root,s)
 s=spec(scene);(root/'Assets'/'Prefabs'/'Prefab_0.pfab').unlink()
 with pytest.raises(UnsafeDuplicationError,match='MINIGAME_ASSET_MISSING'):plan_minigame(scene,root,s)
def test_build_whole_prototype_and_duplicate_rejected(tmp_path):
 root,scene=setup(tmp_path);s=spec(scene);p=plan_minigame(scene,root,s);r=build_minigame(scene,root,s,expected_plan_hash=p.plan_sha256);assert r['build_result']=='BUILD_PASS' and len(r['created_objects'])==7 and len(r['created_actions'])==3
 with pytest.raises(UnsafeDuplicationError,match='MINIGAME_ALREADY_APPLIED'):build_minigame(scene,root,s,expected_plan_hash=plan_minigame(scene,root,s).plan_sha256)
def test_plan_stale_and_scene_stale(tmp_path):
 root,scene=setup(tmp_path);s=spec(scene);p=plan_minigame(scene,root,s)
 with pytest.raises(ConcurrentModificationError):build_minigame(scene,root,s,expected_plan_hash='0'*64)
 scene.write_bytes(scene.read_bytes()+b'x')
 with pytest.raises(Exception):build_minigame(scene,root,s,expected_plan_hash=p.plan_sha256)
