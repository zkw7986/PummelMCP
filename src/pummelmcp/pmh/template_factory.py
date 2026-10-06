"""Same-scene approved-template catalog and transactional factory."""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from .duplication_writer import duplicate_leaf, plan_leaf_duplication
from .errors import ConcurrentModificationError, UnsafeDuplicationError, WriterValidationError
from .hierarchy_writer import duplicate_subtree, plan_subtree_duplication, reparent_gameobject
from .models import GameObject
from .reader import read_pmh
from .validator import validate_scene
from .writer import PMHScene

CATALOG_NAME = ".pummelmcp-object-templates.json"
ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

def _sha(data: bytes) -> str: return hashlib.sha256(data).hexdigest()

def _field_hash(field) -> str: return _sha(field.raw)

def _node_fingerprint(obj: GameObject, root: GameObject) -> dict:
    rel=[]; cur=obj
    while cur is not root: rel.append(cur.sibling_index); cur=cur.parent
    rel.reverse()
    return {"relative_path":rel,"name":obj.name,"active":obj.active,"layer":obj.layer,"tag":obj.tag,
            "identity_relation":obj.guid==obj.components[0].guid if obj.components else False,
            "components":[{"type":c.type_name,"enabled":c.enabled,"fields":[{"name":f.name,"sha256":_field_hash(f)} for f in c.fields]} for c in obj.components],
            "children":[c.sibling_index for c in obj.children]}

def template_fingerprint(obj: GameObject) -> str:
    payload={"root_guid":obj.guid,"nodes":[_node_fingerprint(n,obj) for n in obj.walk()]}
    return _sha(json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode())

def _snapshot(obj: GameObject):
    return tuple((n.guid,n.name,n.parent.guid if n.parent else None,n.sibling_index,tuple((c.guid,c.type_name,c.enabled,tuple((f.name,f.raw) for f in c.fields)) for c in n.components)) for n in obj.walk())

def _catalog_path(root: Path) -> Path: return root / CATALOG_NAME

def _load(root: Path) -> dict:
    path=_catalog_path(root)
    if not path.exists(): return {"format_version":1,"templates":[]}
    data=json.loads(path.read_text(encoding="utf-8"))
    if data.get("format_version")!=1 or not isinstance(data.get("templates"),list): raise UnsafeDuplicationError("INVALID_TEMPLATE_CATALOG")
    return data

def _save(root: Path,data:dict):
    path=_catalog_path(root); temp=path.with_suffix(path.suffix+".tmp")
    temp.write_text(json.dumps(data,indent=2,sort_keys=True,ensure_ascii=False)+"\n",encoding="utf-8");temp.replace(path)

def _relative_scene(scene:Path,root:Path)->str:
    try:return scene.resolve(strict=True).relative_to(root.resolve(strict=True)).as_posix()
    except ValueError as exc:raise UnsafeDuplicationError("CATALOG_PATH_ESCAPE") from exc

def _entry(catalog_root:Path,template_id:str)->dict:
    matches=[x for x in _load(catalog_root)["templates"] if x["template_id"]==template_id]
    if len(matches)!=1: raise UnsafeDuplicationError("TEMPLATE_NOT_FOUND" if not matches else "DUPLICATE_TEMPLATE_ID")
    return matches[0]

def register_object_template(scene_path,source,template_id,*,catalog_root,expected_scene_hash=None,display_name=None):
    scene_path=Path(scene_path).resolve(strict=True);root=Path(catalog_root).resolve(strict=True)
    if not ID_RE.fullmatch(template_id): raise UnsafeDuplicationError("INVALID_TEMPLATE_ID")
    raw=scene_path.read_bytes()
    if expected_scene_hash and expected_scene_hash.casefold()!=_sha(raw): raise ConcurrentModificationError("UNSAFE_STALE_SCENE")
    loaded=PMHScene.load(scene_path);obj=loaded.get_object(source)
    if obj.children:
        plan=plan_subtree_duplication(scene_path,obj.guid);kind="SUBTREE";safety=plan.safety_class;policy="SOURCE_SAME_PARENT";allowed=["DUPLICATE_SUBTREE","SET_ROOT_LOCAL_TRANSFORM"]
    else:
        plan=plan_leaf_duplication(scene_path,obj.guid);kind="LEAF";safety=plan.safety_class
        policy="ARBITRARY_PARENT" if safety in {"SAFE_TRANSFORM_ONLY_LEAF_DUPLICATION", "SAFE_EMPTY_TRIGGER_BOXCOLLIDER_LEAF_DUPLICATION"} else "SOURCE_SAME_PARENT"
        allowed=["DUPLICATE_LEAF","SET_LOCAL_TRANSFORM"]+(["REPARENT_LEAF"] if policy=="ARBITRARY_PARENT" else [])
    catalog=_load(root)
    if any(x["template_id"]==template_id for x in catalog["templates"]): raise UnsafeDuplicationError("DUPLICATE_TEMPLATE_ID")
    nodes=list(obj.walk());entry={"template_id":template_id,"display_name":display_name or obj.name,"source_scene":_relative_scene(scene_path,root),"source_gameobject_guid":obj.guid,"source_hierarchy_path":obj.hierarchy_path,"template_kind":kind,"safety_class":safety,"template_fingerprint":template_fingerprint(obj),"component_shape":[c.type_name for c in obj.components],"subtree_shape":[{"relative_path":_node_fingerprint(n,obj)["relative_path"],"components":[c.type_name for c in n.components]} for n in nodes],"reference_inventory":[],"allowed_operations":allowed,"destination_policy":policy,"registered_scene_sha256":_sha(raw),"created_at":datetime.now(timezone.utc).isoformat()}
    catalog["templates"].append(entry);catalog["templates"].sort(key=lambda x:x["template_id"]);_save(root,catalog);return entry

def list_object_templates(scene_path,*,catalog_root):
    scene_path=Path(scene_path).resolve(strict=True);root=Path(catalog_root).resolve(strict=True);rel=_relative_scene(scene_path,root);out=[]
    scene=read_pmh(scene_path);by_guid={o.guid:o for o in scene.walk()}
    for e in _load(root)["templates"]:
        if e["source_scene"]!=rel: continue
        obj=by_guid.get(e["source_gameobject_guid"]);status="VALID" if obj and template_fingerprint(obj)==e["template_fingerprint"] else "STALE"
        out.append({**e,"fingerprint_status":status,"subtree_node_count":len(e["subtree_shape"])})
    return {"scene":str(scene_path),"templates":out,"count":len(out)}

@dataclass(frozen=True,slots=True)
class TemplatePlan:
    template_id:str;template_fingerprint:str;source:dict;kind:str;safety_class:str;destination:dict;transform_overrides:dict;duplicate_method:str;reparent_method:str|None;expected_created_gameobjects:int;expected_created_components:int;reference_policy:str;fingerprint_verification:str;transaction_steps:tuple[str,...]
    def to_dict(self):return asdict(self)

def plan_create_from_template(scene_path,template_id,*,catalog_root,destination_parent=None,position=None,rotation=None,scale=None,expected_scene_hash=None):
    scene_path=Path(scene_path).resolve(strict=True);root=Path(catalog_root).resolve(strict=True);raw=scene_path.read_bytes()
    if expected_scene_hash and expected_scene_hash.casefold()!=_sha(raw):raise ConcurrentModificationError("UNSAFE_STALE_SCENE")
    e=_entry(root,template_id)
    if e["source_scene"]!=_relative_scene(scene_path,root):raise UnsafeDuplicationError("CROSS_SCENE_TEMPLATE_UNSUPPORTED")
    loaded=PMHScene.load(scene_path);source=loaded.get_object(e["source_gameobject_guid"])
    if template_fingerprint(source)!=e["template_fingerprint"]:raise UnsafeDuplicationError("TEMPLATE_STALE")
    parent=source.parent
    if parent is None:raise UnsafeDuplicationError("UNSAFE_ROOT_OBJECT")
    dest=parent if destination_parent is None else loaded.get_object(destination_parent)
    if e["destination_policy"]=="SOURCE_SAME_PARENT" and dest.guid!=parent.guid:
        code="UNSUPPORTED_SUBTREE_REPARENT" if e["template_kind"]=="SUBTREE" else "UNSUPPORTED_TEMPLATE_DESTINATION"
        raise UnsafeDuplicationError(code)
    if e["template_kind"]=="LEAF":
        fresh=plan_leaf_duplication(scene_path,source.guid);count=1;components=len(source.components);dup="duplicate_leaf";rep="reparent_gameobject" if dest.guid!=parent.guid else None
    else:
        fresh=plan_subtree_duplication(scene_path,source.guid);nodes=list(source.walk());count=len(nodes);components=sum(len(n.components) for n in nodes);dup="duplicate_subtree";rep=None
    overrides={k:v for k,v in (("position",position),("rotation",rotation),("scale",scale)) if v is not None}
    steps=[dup]+([rep] if rep else [])+(["set_transform"] if overrides else [])+["validate_scene","single_atomic_replace"]
    return TemplatePlan(template_id,e["template_fingerprint"],{"guid":source.guid,"path":source.hierarchy_path,"scene":str(scene_path)},e["template_kind"],e["safety_class"],{"parent_guid":dest.guid,"parent_path":dest.hierarchy_path,"policy":e["destination_policy"]},overrides,dup,rep,count,components,"REFERENCE_FREE_OR_ORACLE_APPROVED_EMPTY_TRIGGER","MATCH",tuple(steps))

def create_from_template(scene_path,template_id,*,catalog_root,destination_parent=None,position=None,rotation=None,scale=None,expected_scene_hash=None,backup=True,failure_injector:Callable[[str],None]|None=None):
    scene_path=Path(scene_path).resolve(strict=True);original=PMHScene.load(scene_path);before=original.source_sha256
    if expected_scene_hash and expected_scene_hash.casefold()!=before:raise ConcurrentModificationError("UNSAFE_STALE_SCENE")
    plan=plan_create_from_template(scene_path,template_id,catalog_root=catalog_root,destination_parent=destination_parent,position=position,rotation=rotation,scale=scale,expected_scene_hash=before)
    source=original.get_object(plan.source["guid"]);source_snapshot=_snapshot(source)
    with tempfile.TemporaryDirectory(prefix="pummelmcp-template-") as td:
        temp=Path(td)/scene_path.name;temp.write_bytes(original._original_bytes)
        if plan.kind=="LEAF":
            preview=plan_leaf_duplication(temp,source.guid);values=iter(uuid.UUID(x.new_guid) for x in preview.source.components);rep=duplicate_leaf(temp,source.guid,backup=False,guid_factory=lambda:next(values));created_root=rep.duplicate_guid;created_nodes=[created_root];created_components=list(preview.destination.component_guids.values())
        else:
            preview=plan_subtree_duplication(temp,source.guid);values=iter(uuid.UUID(preview.gameobject_mappings[n["guid"]]) for n in preview.ordered_nodes);rep=duplicate_subtree(temp,source.guid,backup=False,guid_factory=lambda:next(values));created_root=rep["duplicate_root_guid"];created_nodes=list(preview.gameobject_mappings.values());created_components=list(preview.component_mappings.values())
        if failure_injector:failure_injector("after_duplicate")
        if plan.reparent_method:reparent_gameobject(temp,created_root,plan.destination["parent_guid"],backup=False)
        if failure_injector:failure_injector("after_reparent")
        if plan.transform_overrides:
            PMHScene.load(temp).set_transform(created_root,**plan.transform_overrides)
        if failure_injector:failure_injector("after_transform")
        final=read_pmh(temp);validation=validate_scene(final)
        retained=next((o for o in final.walk() if o.guid==source.guid),None)
        if not validation.passed or retained is None or _snapshot(retained)!=source_snapshot:raise WriterValidationError("TEMPLATE_SOURCE_MUTATED_OR_FINAL_VALIDATION_FAILED")
        final_raw=temp.read_bytes()
        def final_validate(scene,data):
            checks={"parse_to_exact_eof":scene.fully_consumed,"scene_validation":validate_scene(scene).passed,"source_preserved":_snapshot(next(o for o in scene.walk() if o.guid==source.guid))==source_snapshot,"created_root_present":any(o.guid==created_root for o in scene.walk())}
            if not all(checks.values()):raise WriterValidationError("template final validation failed")
            return {"passed":True,"checks":checks}
        validation_result,bak=original._replace_with_validation(final_raw,final_validate,backup=backup)
    return {"template_id":template_id,"template_fingerprint":plan.template_fingerprint,"source_guid":source.guid,"created_root_guid":created_root,"created_node_guids":created_nodes,"created_component_guids":created_components,"destination_parent":plan.destination,"transaction_before_sha256":before,"transaction_after_sha256":_sha(final_raw),"backup_path":str(bak) if bak else None,"validation":validation_result}
