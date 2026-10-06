"""Dual raw/semantic Scene identity for Stage 14 sessions."""
from __future__ import annotations
import dataclasses, hashlib, json, math
from pathlib import Path
from typing import Any
from .models import DecodeConfidence, Vector2, Vector3, Vector4, Color4
from .reader import read_pmh
from .validator import validate_scene

NORMALIZATION_TOLERANCE = 1.2e-6
ACTION_FIELDS = frozenset({"OnHitActions","OnEnterActions","OnExitActions","OnStayActions"})

def _sha(data: bytes)->str:return hashlib.sha256(data).hexdigest()
def _value(v:Any)->Any:
    if dataclasses.is_dataclass(v): return {f.name:_value(getattr(v,f.name)) for f in dataclasses.fields(v)}
    if isinstance(v,(list,tuple)): return [_value(x) for x in v]
    if isinstance(v,dict): return {str(k):_value(v[k]) for k in sorted(v)}
    if isinstance(v,float):
        if not math.isfinite(v): return repr(v)
        return {"float32_hex":float(v).hex()}
    return v

def semantic_scene_document(path:str|Path)->dict[str,Any]:
    scene=read_pmh(path); validation=validate_scene(scene)
    if not scene.fully_consumed or not validation.passed: raise ValueError("SCENE_IDENTITY_PARSE_FAILED")
    objects=[]
    for o in scene.walk():
        components=[]
        for c in o.components:
            fields=[]
            for f in c.fields:
                approved=(c.type_name=="ModTransform" and f.name=="position" and o.hierarchy_path.startswith("/Player Spawnpoints/PlayerSpawn_"))
                if f.name in ACTION_FIELDS or f.confidence is DecodeConfidence.UNKNOWN:
                    payload={"raw_sha256":_sha(f.raw),"byte_length":len(f.raw)}
                elif approved and isinstance(f.value,Vector3):
                    # Field-specific canonicalization of the sole documented Editor normalization.
                    payload={"normalized_vector3":[round(x,4) for x in (f.value.x,f.value.y,f.value.z)]}
                else: payload={"semantic":_value(f.value)}
                fields.append({"name":f.name,"payload":payload,"normalization":"PLAYERSPAWN_POSITION_FLOAT32" if approved else "NONE"})
            components.append({"type":c.type_name,"guid":c.guid,"enabled":c.enabled,"fields":fields})
        objects.append({"guid":o.guid,"name":o.name,"active":o.active,"layer":o.layer,"tag":o.tag,
          "parent":o.parent.guid if o.parent else None,"sibling":o.sibling_index,"preorder":o.preorder_index,
          "children":[x.guid for x in o.children],"components":components})
    return {"format":{"magic":scene.magic,"version":scene.version},"counts":{"objects":scene.object_count,"components":scene.component_count},"objects":objects}

def semantic_scene_fingerprint(path:str|Path)->str:
    raw=json.dumps(semantic_scene_document(path),sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
    return _sha(raw)

def classify_scene_identity(planned_path:str|Path,observed_path:str|Path)->dict[str,Any]:
    p,o=Path(planned_path),Path(observed_path); pr,orr=p.read_bytes(),o.read_bytes()
    pf,of=semantic_scene_fingerprint(p),semantic_scene_fingerprint(o)
    approved,changes=_approved_normalization_only(p,o)
    if pr==orr: status="EXACT_SCENE_MATCH"
    elif pf==of and approved: status="SEMANTIC_SCENE_MATCH"
    else: status="STALE_PLAYTEST_SCENE"
    return {"status":status,"planned_raw_sha256":_sha(pr),"observed_raw_sha256":_sha(orr),
      "planned_semantic_fingerprint":pf,"observed_semantic_fingerprint":of,
      "raw_changed":pr!=orr,"semantic_changed":pf!=of,"normalization_changes":changes}

def _approved_normalization_only(planned:Path,observed:Path)->tuple[bool,list[dict[str,Any]]]:
    a,b=read_pmh(planned),read_pmh(observed); ao={o.guid:o for o in a.walk()};bo={o.guid:o for o in b.walk()};changes=[]
    if list(ao)!=list(bo): return False,changes
    for gid,left in ao.items():
        right=bo[gid]
        if (left.name,left.active,left.layer,left.tag,left.parent.guid if left.parent else None,left.sibling_index,left.preorder_index,[x.guid for x in left.children]) != (right.name,right.active,right.layer,right.tag,right.parent.guid if right.parent else None,right.sibling_index,right.preorder_index,[x.guid for x in right.children]): return False,changes
        if [(c.type_name,c.guid,c.enabled) for c in left.components] != [(c.type_name,c.guid,c.enabled) for c in right.components]: return False,changes
        for lc,rc in zip(left.components,right.components):
            if [f.name for f in lc.fields] != [f.name for f in rc.fields]: return False,changes
            for lf,rf in zip(lc.fields,rc.fields):
                if lf.raw==rf.raw: continue
                allowed=lc.type_name=="ModTransform" and lf.name=="position" and left.hierarchy_path.startswith("/Player Spawnpoints/PlayerSpawn_") and isinstance(lf.value,Vector3) and isinstance(rf.value,Vector3)
                if not allowed:return False,changes
                delta={axis:getattr(rf.value,axis)-getattr(lf.value,axis) for axis in ("x","y","z")}
                if any(abs(x)>NORMALIZATION_TOLERANCE for x in delta.values()):return False,changes
                changes.append({"object_guid":gid,"path":left.hierarchy_path,"component_guid":lc.guid,"field":"position","delta":delta,"classification":"KNOWN_EDITOR_NORMALIZATION"})
    return True,changes
