"""Stage 13: strict gameplay recipes composed from approved writers."""
from __future__ import annotations
import hashlib, json, math, shutil, tempfile, uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Mapping

from .errors import ConcurrentModificationError, UnsafeDuplicationError, WriterValidationError
from .reader import read_pmh
from .template_factory import create_from_template, list_object_templates, plan_create_from_template, template_fingerprint
from .validator import validate_scene
from .writer import PMHScene
from .action_graph_writer import ActionGraphWriter, build_action_template_catalog
from .action_writer import ActionFieldWriter
from .gameplay_rules import compile_trigger_rule, EVENT_ENABLED_FIELD

MAX_OBJECTS=64; MAX_ACTIONS=128; MAX_OPERATIONS=512
RECIPE_DEFINITIONS={
 "BUTTON_SPAWN_PREFAB":{"components":["ModTransform","ModBoxCollider","ModTrigger"],"action_types":["SpawnPrefabAction"],"availability":"AVAILABLE_WITH_REFERENCE_TEMPLATE"},
 "SCORE_PAD":{"components":["ModTransform","ModBoxCollider","ModTrigger"],"action_types":["ChangeScoreAction","SpawnEffectAction","PlaySoundAction"],"availability":"AVAILABLE_WITH_REFERENCE_TEMPLATES"},
 "DEATH_PENALTY_ZONE":{"components":["ModTransform","ModBoxCollider","ModTrigger"],"action_types":["ChangeScoreAction","KillAction"],"availability":"AVAILABLE_WITH_REFERENCE_TEMPLATES"},
 "FEEDBACK_CHECKPOINT":{"components":["ModTransform","ModBoxCollider","ModTrigger"],"action_types":["ShowMessageAction","SpawnEffectAction","PlaySoundAction"],"availability":"AVAILABLE_WITH_REFERENCE_TEMPLATES"},
 "SPAWN_POINT_SET":{"components":["ModTransform","ModPlayerSpawn"],"action_types":[],"availability":"AVAILABLE"},
 "TEXT_SIGN":{"components":["ModTransform","ModText"],"action_types":[],"availability":"AVAILABLE_TEMPLATE_TEXT_ONLY"},
 "LIGHT_SET":{"components":["ModTransform","ModLight"],"action_types":[],"availability":"AVAILABLE"},
 "PROP_LAYOUT":{"components":["ModTransform","ModProp"],"action_types":[],"availability":"AVAILABLE_CANDIDATE_EMPTY_MATERIALS"},
 "STATIC_TEMPLATE_BLOCK":{"components":["ModTransform"],"action_types":[],"availability":"AVAILABLE"},
 "CONFIGURABLE_TRIGGER":{"components":["ModTransform","ModBoxCollider","ModTrigger"],"action_types":[],"availability":"AVAILABLE_WITH_REFERENCE_TEMPLATES"},
}
TRIGGER_RECIPES=frozenset(name for name,definition in RECIPE_DEFINITIONS.items() if definition["action_types"] or name=="CONFIGURABLE_TRIGGER")
TOP_KEYS={"version","scene","destination_root","objects","layout","metadata"}
OBJECT_KEYS={"logical_id","recipe_type","template_id","destination_parent","transform","component_config","trigger_recipe","asset_bindings"}
TRANSFORM_KEYS={"position","rotation","scale"}; AXES={"x","y","z"}

def _sha(b:bytes)->str:return hashlib.sha256(b).hexdigest()
def _canonical(value)->bytes:return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
def _strict_keys(value,allowed,where):
 unknown=set(value)-allowed
 if unknown:raise UnsafeDuplicationError(f"UNKNOWN_GAMEPLAY_SPEC_FIELD: {where}: {sorted(unknown)}")

def _validate_transform(value):
 if not isinstance(value,dict):raise UnsafeDuplicationError("INVALID_TRANSFORM_SPEC")
 _strict_keys(value,TRANSFORM_KEYS,"transform")
 for name,axes in value.items():
  if not isinstance(axes,dict):raise UnsafeDuplicationError("INVALID_TRANSFORM_SPEC")
  _strict_keys(axes,AXES,name)
  if not axes or any(isinstance(v,bool) or not isinstance(v,(int,float)) for v in axes.values()):raise UnsafeDuplicationError("INVALID_TRANSFORM_SPEC")
  if any(not math.isfinite(float(v)) for v in axes.values()):raise UnsafeDuplicationError("INVALID_TRANSFORM_SPEC")

def _vec(value,name):
 if not isinstance(value,dict) or set(value)!={"x","y","z"}:raise UnsafeDuplicationError(f"INVALID_{name}_VECTOR")
 out={k:float(value[k]) for k in ("x","y","z")}
 if any(not math.isfinite(v) for v in out.values()):raise UnsafeDuplicationError(f"INVALID_{name}_VECTOR")
 return out

def resolve_layouts(spec):
 positions={}; blocks=spec.get("layout",[])
 if blocks is None:blocks=[]
 if not isinstance(blocks,list):raise UnsafeDuplicationError("INVALID_LAYOUT")
 known={x["logical_id"] for x in spec["objects"]}
 for block in blocks:
  if not isinstance(block,dict) or block.get("type") not in {"ROW","GRID"}:raise UnsafeDuplicationError("UNSUPPORTED_LAYOUT")
  ids=block.get("objects")
  if not isinstance(ids,list) or not ids or len(ids)!=len(set(ids)) or any(x not in known for x in ids) or any(x in positions for x in ids):raise UnsafeDuplicationError("INVALID_LAYOUT_OBJECTS")
  origin=_vec(block.get("origin"),"ORIGIN")
  if block["type"]=="ROW":
   if set(block)!={"type","objects","origin","direction","spacing"}:raise UnsafeDuplicationError("INVALID_ROW_LAYOUT")
   d=_vec(block["direction"],"DIRECTION"); spacing=block["spacing"]
   if all(v==0 for v in d.values()) or isinstance(spacing,bool) or not isinstance(spacing,(int,float)) or not math.isfinite(spacing) or spacing==0:raise UnsafeDuplicationError("INVALID_ROW_LAYOUT")
   for i,k in enumerate(ids):positions[k]={a:origin[a]+d[a]*float(spacing)*i for a in ("x","y","z")}
  else:
   if set(block)!={"type","objects","origin","rows","columns","row_spacing","column_spacing","row_direction","column_direction"}:raise UnsafeDuplicationError("INVALID_GRID_LAYOUT")
   rows,cols=block["rows"],block["columns"]
   if isinstance(rows,bool) or isinstance(cols,bool) or not isinstance(rows,int) or not isinstance(cols,int) or rows<1 or cols<1 or rows*cols!=len(ids):raise UnsafeDuplicationError("GRID_CAPACITY_MISMATCH")
   rd=_vec(block["row_direction"],"ROW_DIRECTION");cd=_vec(block["column_direction"],"COLUMN_DIRECTION");rs=block["row_spacing"];cs=block["column_spacing"]
   if all(v==0 for v in rd.values()) or all(v==0 for v in cd.values()) or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v==0 for v in (rs,cs)):raise UnsafeDuplicationError("INVALID_GRID_LAYOUT")
   for i,k in enumerate(ids):
    row,col=divmod(i,cols);positions[k]={a:origin[a]+rd[a]*float(rs)*row+cd[a]*float(cs)*col for a in ("x","y","z")}
 return positions

def validate_gameplay_spec(spec:Mapping)->dict:
 if not isinstance(spec,dict):raise UnsafeDuplicationError("INVALID_GAMEPLAY_SPEC")
 _strict_keys(spec,TOP_KEYS,"root")
 if spec.get("version")!="0.1":raise UnsafeDuplicationError("UNSUPPORTED_GAMEPLAY_SPEC_VERSION")
 if not isinstance(spec.get("scene"),str):raise UnsafeDuplicationError("INVALID_SCENE")
 objects=spec.get("objects")
 if not isinstance(objects,list) or not objects:raise UnsafeDuplicationError("EMPTY_COMPOSITION")
 if len(objects)>MAX_OBJECTS:raise UnsafeDuplicationError("COMPOSITION_LIMIT_EXCEEDED")
 ids=[]
 for i,item in enumerate(objects):
  if not isinstance(item,dict):raise UnsafeDuplicationError("INVALID_OBJECT_SPEC")
  _strict_keys(item,OBJECT_KEYS,f"objects[{i}]")
  for key in ("logical_id","recipe_type","template_id"):
   if not isinstance(item.get(key),str) or not item[key]:raise UnsafeDuplicationError(f"INVALID_{key.upper()}")
  if item["recipe_type"] not in RECIPE_DEFINITIONS:raise UnsafeDuplicationError("RECIPE_CAPABILITY_MISSING")
  ids.append(item["logical_id"])
  if "transform" in item:_validate_transform(item["transform"])
  if item.get("component_config") not in (None,{}):raise UnsafeDuplicationError("ARBITRARY_COMPONENT_CONFIGURATION_FORBIDDEN")
  trigger=item.get("trigger_recipe")
  if trigger not in (None,{}):
   if item["recipe_type"] not in TRIGGER_RECIPES or not isinstance(trigger,dict):raise UnsafeDuplicationError("INVALID_TRIGGER_RECIPE")
   if item["recipe_type"]=="CONFIGURABLE_TRIGGER":compile_trigger_rule(trigger)
   elif set(trigger)!={"event_property"} or trigger["event_property"] not in EVENT_ENABLED_FIELD:raise UnsafeDuplicationError("INVALID_TRIGGER_RECIPE")
  if item.get("asset_bindings") not in (None,{}):raise UnsafeDuplicationError("ARBITRARY_ASSET_BINDING_FORBIDDEN")
  if item["recipe_type"] in TRIGGER_RECIPES and not item.get("trigger_recipe"):raise UnsafeDuplicationError("TRIGGER_RECIPE_REQUIRED")
 if len(ids)!=len(set(ids)):raise UnsafeDuplicationError("DUPLICATE_LOGICAL_ID")
 resolve_layouts(spec)
 action_count=sum(len(_action_specs(x)) for x in objects)
 if action_count>MAX_ACTIONS or len(objects)+action_count>MAX_OPERATIONS:raise UnsafeDuplicationError("COMPOSITION_LIMIT_EXCEEDED")
 return {"valid":True,"object_count":len(objects),"spec_sha256":_sha(_canonical(spec))}


def _action_specs(item):
 if item["recipe_type"]=="CONFIGURABLE_TRIGGER":
  return compile_trigger_rule(item["trigger_recipe"])["actions"]
 return [{"class":name,"fields":{}} for name in RECIPE_DEFINITIONS[item["recipe_type"]]["action_types"]]

def list_gameplay_recipes()->dict:
 return {"version":"0.1","recipes":[{"recipe_type":k,"schema_version":"0.1","required_components":v["components"],"allowed_action_types":v["action_types"],"availability":v["availability"],"transform_space":"DESTINATION_PARENT_LOCAL"} for k,v in RECIPE_DEFINITIONS.items()],"limits":{"objects":MAX_OBJECTS,"actions":MAX_ACTIONS,"operations":MAX_OPERATIONS}}

@dataclass(frozen=True,slots=True)
class GameplayCompositionPlan:
 scene_sha256:str;spec_sha256:str;plan_sha256:str;safety_class:str;objects:tuple[dict,...];operations:tuple[dict,...];template_fingerprints:dict;expected_created_objects:int;expected_created_components:int;expected_actions:int;source_protection_targets:tuple[str,...];human_summary:dict
 def to_dict(self):return asdict(self)

def plan_gameplay_composition(scene_path,catalog_root,spec)->GameplayCompositionPlan:
 validation=validate_gameplay_spec(spec); scene_path=Path(scene_path).resolve(strict=True)
 if Path(spec["scene"]).resolve(strict=False)!=scene_path:raise UnsafeDuplicationError("SPEC_SCENE_MISMATCH")
 raw=scene_path.read_bytes(); entries={x["template_id"]:x for x in list_object_templates(scene_path,catalog_root=catalog_root)["templates"]}
 planned=[];ops=[];fingerprints={};components=0;layout_positions=resolve_layouts(spec)
 for item in spec["objects"]:
  entry=entries.get(item["template_id"])
  if not entry or entry["fingerprint_status"]!="VALID":raise UnsafeDuplicationError("COMPOSER_TEMPLATE_UNSUPPORTED")
  expected=RECIPE_DEFINITIONS[item["recipe_type"]]["components"]
  shape=entry["component_shape"]
  if item["recipe_type"]!="STATIC_TEMPLATE_BLOCK" and shape!=expected:raise UnsafeDuplicationError("RECIPE_TEMPLATE_CAPABILITY_MISMATCH")
  action_specs=_action_specs(item);action_types=[x["class"] for x in action_specs]
  action_hashes={}
  if action_types:
   catalog=build_action_template_catalog(read_pmh(scene_path))
   for action_class in action_types:
    variants=catalog.matching("ModSystem.Logic",action_class)
    hashes={x.managed_reference_sha256 for x in variants}
    if len(hashes)!=1:raise UnsafeDuplicationError(f"RECIPE_CAPABILITY_MISSING: exact {action_class} template unavailable or ambiguous")
    action_hashes[action_class]=next(iter(hashes))
    if action_class=="ShowMessageAction" and item["recipe_type"]=="CONFIGURABLE_TRIGGER":
     if any(x.data.get("m_messageTarget")!=2 for x in variants):raise UnsafeDuplicationError("RULE_MESSAGE_TARGET_TEMPLATE_UNSUPPORTED")
  transform=dict(item.get("transform",{}))
  if item["logical_id"] in layout_positions:transform={**transform,"position":layout_positions[item["logical_id"]]}
  p=plan_create_from_template(scene_path,item["template_id"],catalog_root=catalog_root,destination_parent=item.get("destination_parent"),position=transform.get("position"),rotation=transform.get("rotation"),scale=transform.get("scale"))
  row={"logical_id":item["logical_id"],"recipe_type":item["recipe_type"],"template_id":item["template_id"],"destination":p.destination,"transform":transform,"expected_objects":p.expected_created_gameobjects,"expected_components":p.expected_created_components}
  planned.append(row);components+=p.expected_created_components;fingerprints[item["template_id"]]=entry["template_fingerprint"]
  ops.append({"operation":"CREATE_FROM_TEMPLATE","logical_id":item["logical_id"],"template_id":item["template_id"]})
  if item.get("trigger_recipe"):
   for action in action_specs:
    ops.append({"operation":"ADD_ACTION","logical_id":item["logical_id"],"event_property":item["trigger_recipe"]["event_property"],"action_class":action["class"],"template_sha256":action_hashes[action["class"]],"fields":action["fields"]})
 payload={"scene_sha256":_sha(raw),"spec_sha256":validation["spec_sha256"],"objects":planned,"operations":ops,"template_fingerprints":fingerprints}
 plan_hash=_sha(_canonical(payload))
 actions=sum(len(_action_specs(x)) for x in spec["objects"])
 return GameplayCompositionPlan(payload["scene_sha256"],validation["spec_sha256"],plan_hash,"SAFE_TO_COMPOSE",tuple(planned),tuple(ops),fingerprints,sum(x["expected_objects"] for x in planned),components,actions,tuple(sorted(fingerprints)),{"will_create":len(planned),"will_add_actions":actions,"recipes":[x["recipe_type"] for x in planned],"unsupported":[]})

def compose_gameplay(scene_path,catalog_root,spec,*,expected_plan_sha256,backup=True,failure_injector:Callable[[str],None]|None=None):
 scene_path=Path(scene_path).resolve(strict=True); root=Path(catalog_root).resolve(strict=True)
 plan=plan_gameplay_composition(scene_path,root,spec)
 if plan.plan_sha256.casefold()!=expected_plan_sha256.casefold():raise ConcurrentModificationError("GAMEPLAY_PLAN_STALE")
 original=PMHScene.load(scene_path); before=original._original_bytes
 source_scene=read_pmh(scene_path); protected={tid:template_fingerprint(next(o for o in source_scene.walk() if o.guid==next(e for e in list_object_templates(scene_path,catalog_root=root)["templates"] if e["template_id"]==tid)["source_gameobject_guid"])) for tid in plan.source_protection_targets}
 with tempfile.TemporaryDirectory(prefix="pummelmcp-composer-") as td:
  temp_root=Path(td)/root.name; shutil.copytree(root,temp_root); rel=scene_path.relative_to(root); temp_scene=temp_root/rel
  mapping={};components={};actions=[]
  resolved={x["logical_id"]:x["transform"] for x in plan.objects}
  for index,item in enumerate(spec["objects"]):
   transform=resolved[item["logical_id"]]
   result=create_from_template(temp_scene,item["template_id"],catalog_root=temp_root,destination_parent=item.get("destination_parent"),position=transform.get("position"),rotation=transform.get("rotation"),scale=transform.get("scale"),backup=False)
   mapping[item["logical_id"]]=result["created_root_guid"];components[item["logical_id"]]=result["created_component_guids"]
   if item.get("trigger_recipe"):
    trigger=item["trigger_recipe"]
    if item["recipe_type"]=="CONFIGURABLE_TRIGGER":
     compiled=compile_trigger_rule(trigger)
     PMHScene.load(temp_scene).set_component_property(result["created_root_guid"],"ModTrigger",compiled["enable_field"],True,backup=False)
     PMHScene.load(temp_scene).set_component_property(result["created_root_guid"],"ModTrigger","OneUsePerPlayer",compiled["one_use_per_player"],backup=False)
     PMHScene.load(temp_scene).set_component_property(result["created_root_guid"],"ModTrigger","DisableAfterTriggered",compiled["disable_after_triggered"],backup=False)
     if compiled["interval_seconds"] is not None:PMHScene.load(temp_scene).set_component_property(result["created_root_guid"],"ModTrigger","StayTriggerInterval",compiled["interval_seconds"],backup=False)
    for operation in (x for x in plan.operations if x["operation"]=="ADD_ACTION" and x["logical_id"]==item["logical_id"]):
     action_class=operation["action_class"]
     report=ActionGraphWriter(temp_scene).add_action(result["created_root_guid"],"ModTrigger",trigger["event_property"],action_class,template_sha256=operation["template_sha256"],backup=False)
     for field_name,value in operation["fields"].items():
      ActionFieldWriter(temp_scene).set_action_field(result["created_root_guid"],"ModTrigger",trigger["event_property"],report.affected_rid,action_class,field_name,value,backup=False)
     actions.append({"logical_id":item["logical_id"],"rid":report.affected_rid,"event_property":report.event_property,"class":action_class,"template":report.template,"fields":operation["fields"]})
     if failure_injector:failure_injector(f"after_action_{len(actions)}")
   if failure_injector:failure_injector(f"after_object_{index+1}")
  final=read_pmh(temp_scene); final_raw=temp_scene.read_bytes()
  entries={x["template_id"]:x for x in list_object_templates(temp_scene,catalog_root=temp_root)["templates"]}
  source_ok=all(entries[t]["template_fingerprint"]==protected[t] and entries[t]["fingerprint_status"]=="VALID" for t in protected)
  if not validate_scene(final).passed or not final.fully_consumed or not source_ok:raise WriterValidationError("COMPOSITION_FINAL_VALIDATION_FAILED")
  if failure_injector:failure_injector("before_commit")
  def validator(scene,data):
   checks={"parse_to_exact_eof":scene.fully_consumed,"scene_validation":validate_scene(scene).passed,"created_mapping_complete":all(any(o.guid==g for o in scene.walk()) for g in mapping.values()),"source_templates_preserved":source_ok,"unique_guids":len({c.guid for o in scene.walk() for c in o.components})==sum(len(o.components) for o in scene.walk())}
   if not all(checks.values()):raise WriterValidationError("COMPOSITION_COMMIT_VALIDATION_FAILED")
   return {"passed":True,"checks":checks}
  validation,bak=original._replace_with_validation(final_raw,validator,backup=backup)
 return {"composition_id":str(uuid.uuid4()),"spec_sha256":plan.spec_sha256,"plan_sha256":plan.plan_sha256,"before_scene_sha256":_sha(before),"after_scene_sha256":_sha(final_raw),"backup_path":str(bak) if bak else None,"created_objects":mapping,"created_components":components,"created_actions":actions,"bound_references":[x["template"] for x in actions],"validation":validation,"source_preservation":source_ok,"transaction":"COMMITTED_ATOMICALLY"}
