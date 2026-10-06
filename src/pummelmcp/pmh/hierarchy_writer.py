"""Oracle-constrained hierarchy mutations for Stage 11."""

from __future__ import annotations

import hashlib
import struct
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from .duplication_writer import (
    MAX_GUID_ATTEMPTS, STANDARD_TRANSFORM_FIELDS, _canonical_uuid4,
    _component_payload_length, _layout, _parent_child_count_offset,
    _validate_source_surface,
)
from .errors import ConcurrentModificationError, GuidAllocationError, UnsafeDuplicationError, WriterValidationError
from .models import GameObject, SourceSpan
from .reference_graph import build_reference_graph
from .writer import ObjectIdentifier, PMHScene


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _span_dict(span: SourceSpan) -> dict[str, int]:
    return {"start": span.start, "end": span.end, "length": span.length}


def _payload_span(scene, obj: GameObject) -> SourceSpan:
    _, spans, starts = _layout(scene)
    start = starts[obj.guid]
    end = start + sum(_component_payload_length(c) for c in obj.components)
    return SourceSpan(start, end - start)


def _resolve_destination(loaded: PMHScene, identifier: ObjectIdentifier | str) -> GameObject:
    return loaded.get_object(identifier)


def _standard_transform_only(obj: GameObject) -> None:
    if tuple(c.type_name for c in obj.components) != ("ModTransform",):
        raise UnsafeDuplicationError("UNSAFE_UNSUPPORTED_COMPONENT: expected ModTransform only")
    transform = obj.components[0]
    if obj.guid != transform.guid or tuple(f.name for f in transform.fields) != STANDARD_TRANSFORM_FIELDS:
        raise UnsafeDuplicationError("UNSAFE_INVALID_IDENTITY: non-standard ModTransform identity or fields")
    if transform.get_field("guid").value != transform.guid:
        raise UnsafeDuplicationError("UNSAFE_INVALID_IDENTITY: payload GUID differs")


def _require_reference_free(scene, nodes: list[GameObject]) -> None:
    owned = {x for obj in nodes for x in (obj.guid, *(c.guid for c in obj.components))}
    allowed = {"GameObjectIdentity", "ComponentIdentityProperty", "SerializedComponentMembership", "SerializedHierarchyChild"}
    graph = build_reference_graph(scene)
    bad = [e for e in graph.edges if (e.source.guid in owned or e.target.guid in owned) and e.reference_kind not in allowed]
    if bad:
        raise UnsafeDuplicationError("UNSAFE_SUBTREE_REFERENCE: approved source must be reference-free")


@dataclass(frozen=True, slots=True)
class HierarchyMovePlan:
    source_file: Path
    source_sha256: str
    safety_class: str
    source: dict[str, object]
    destination: dict[str, object]
    identity_policy: str
    component_identity_policy: str
    payload_policy: str
    transform_policy: str
    reference_policy: str
    mutations: tuple[dict[str, object], ...]

    def to_dict(self) -> dict[str, object]:
        out = asdict(self); out["source_file"] = str(self.source_file); out["dry_run"] = True; out["writer_available"] = True
        return out


@dataclass(frozen=True, slots=True)
class HierarchyMutationReport:
    source_file: Path
    before_sha256: str
    after_sha256: str
    source_guid: str
    destination_parent_guid: str
    sibling_index: int
    preorder_index: int
    safety_class: str
    backup_path: Path | None
    validation: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        out = asdict(self); out["source_file"] = str(self.source_file); out["backup_path"] = str(self.backup_path) if self.backup_path else None
        return out


def plan_gameobject_reparent(scene_path, source_identifier, destination_identifier) -> HierarchyMovePlan:
    loaded = PMHScene.load(scene_path); source = loaded.get_object(source_identifier); dest = _resolve_destination(loaded, destination_identifier)
    if source.parent is None: raise UnsafeDuplicationError("UNSAFE_ROOT_OBJECT: root reparent is forbidden")
    if source.children: raise UnsafeDuplicationError("UNSAFE_HAS_CHILDREN: subtree reparent is forbidden")
    if source.guid == dest.guid: raise UnsafeDuplicationError("UNSAFE_CYCLE: source equals destination")
    if any(x.guid == dest.guid for x in source.walk()): raise UnsafeDuplicationError("UNSAFE_CYCLE: destination is inside source subtree")
    shape = tuple(c.type_name for c in source.components)
    if shape == ("ModTransform",):
        _standard_transform_only(source); _require_reference_free(loaded.scene, [source])
        safety_class = "SAFE_TRANSFORM_ONLY_LEAF_REPARENT"
    elif shape == ("ModTransform", "ModBoxCollider", "ModTrigger"):
        _, _, duplication_class = _validate_source_surface(loaded, source, allow_known_components=True)
        if duplication_class != "SAFE_EMPTY_TRIGGER_BOXCOLLIDER_LEAF_DUPLICATION":
            raise UnsafeDuplicationError("UNSAFE_UNSUPPORTED_COMPONENT")
        safety_class = "SAFE_EMPTY_TRIGGER_BOXCOLLIDER_LEAF_REPARENT"
    else:
        raise UnsafeDuplicationError("UNSAFE_UNSUPPORTED_COMPONENT: reparent shape is not approved")
    if source.parent.guid == dest.guid: raise UnsafeDuplicationError("UNSAFE_NOOP: source already has destination parent")
    raw = loaded._original_bytes
    if loaded.source.read_bytes() != raw: raise UnsafeDuplicationError("UNSAFE_STALE_SCENE: source changed while planning")
    old = source.parent; payload = _payload_span(loaded.scene, source)
    objects = list(loaded.scene.walk()); dest_pre = dest.preorder_index + sum(1 for _ in dest.walk()) - (1 if source.preorder_index < dest.preorder_index else 0)
    return HierarchyMovePlan(
        loaded.source, _sha(raw), safety_class,
        {"guid":source.guid,"name":source.name,"old_parent":old.guid,"old_sibling":source.sibling_index,"old_preorder":source.preorder_index,"hierarchy_span":_span_dict(source.hierarchy_span),"object_index_span":_span_dict(source.index_span),"payload_span":_span_dict(payload)},
        {"new_parent":dest.guid,"existing_child_count":len(dest.children),"sibling_index":len(dest.children),"preorder_index":dest_pre},
        "PRESERVE", "PRESERVE", "PRESERVE_BYTE_EXACT", "PRESERVE_LOCAL_TRANSFORM", "NONE_PRESENT",
        ({"kind":"DECREMENT_OLD_PARENT_CHILD_COUNT","offset":_parent_child_count_offset(old)}, {"kind":"INCREMENT_NEW_PARENT_CHILD_COUNT","offset":_parent_child_count_offset(dest)}, {"kind":"MOVE_HIERARCHY_RECORD","span":_span_dict(source.hierarchy_span),"destination":dest.hierarchy_span.end}, {"kind":"MOVE_OBJECT_INDEX_RECORD","span":_span_dict(source.index_span),"destination_preorder":dest_pre}, {"kind":"MOVE_COMPONENT_PAYLOAD","span":_span_dict(payload),"destination_preorder":dest_pre})
    )


def _move(data: bytes, span: SourceSpan, destination: int) -> bytes:
    if span.start <= destination <= span.end: raise WriterValidationError("move destination overlaps source")
    chunk=data[span.start:span.end]
    if destination > span.end: return data[:span.start]+data[span.end:destination]+chunk+data[destination:]
    return data[:destination]+chunk+data[destination:span.start]+data[span.end:]


def _construct_reparent(loaded: PMHScene, plan: HierarchyMovePlan) -> bytes:
    raw=loaded._original_bytes; source=loaded.get_object(plan.source["guid"]); dest=loaded.get_object(plan.destination["new_parent"]); old=source.parent
    assert old and source.hierarchy_span and source.index_span and dest.hierarchy_span
    payload=_payload_span(loaded.scene,source); objects=list(loaded.scene.walk()); target_pre=plan.destination["preorder_index"]
    # Original insertion boundary is the object following destination's original subtree.
    original_boundary=target_pre + (1 if source.preorder_index < target_pre else 0)
    _,_,starts=_layout(loaded.scene)
    index_end,_,_= _layout(loaded.scene)
    index_dest=objects[original_boundary].index_span.start if original_boundary < len(objects) else index_end
    payload_dest=starts[objects[original_boundary].guid] if original_boundary < len(objects) else loaded.scene.byte_length
    patched=raw
    for off,value in ((_parent_child_count_offset(old),len(old.children)-1),(_parent_child_count_offset(dest),len(dest.children)+1)):
        patched=patched[:off]+struct.pack('<H',value)+patched[off+2:]
    patched=_move(patched,source.hierarchy_span,dest.hierarchy_span.end)
    patched=_move(patched,source.index_span,index_dest)
    patched=_move(patched,payload,payload_dest)
    return patched


def reparent_gameobject(scene_path, source_identifier, destination_identifier, *, expected_scene_hash=None, backup=True) -> HierarchyMutationReport:
    loaded=PMHScene.load(scene_path); before=loaded.source_sha256
    if expected_scene_hash and expected_scene_hash.casefold()!=before: raise ConcurrentModificationError("UNSAFE_STALE_SCENE: expected_scene_hash does not match")
    plan=plan_gameobject_reparent(loaded.source,source_identifier,destination_identifier)
    patched=_construct_reparent(loaded,plan)
    original={o.guid:(o.name,o.parent.guid if o.parent else None,[(c.guid,[(f.name,f.raw) for f in c.fields]) for c in o.components]) for o in loaded.scene.walk()}
    def validate(scene,data):
        moved=next((o for o in scene.walk() if o.guid==plan.source['guid']),None); ids=[o.guid for o in scene.walk()]
        checks={"parse_to_exact_eof":scene.fully_consumed,"counts_preserved":(scene.object_count,scene.component_count)==(loaded.scene.object_count,loaded.scene.component_count),"guids_preserved":set(ids)==set(original),"new_parent":bool(moved and moved.parent and moved.parent.guid==plan.destination['new_parent']),"append_to_destination":bool(moved and moved.sibling_index==plan.destination['sibling_index']),"expected_preorder":bool(moved and moved.preorder_index==plan.destination['preorder_index']),"payload_preserved":bool(moved and [(c.guid,[(f.name,f.raw) for f in c.fields]) for c in moved.components]==original[moved.guid][2])}
        if not all(checks.values()): raise WriterValidationError('reparent validation failed: '+','.join(k for k,v in checks.items() if not v))
        return {"passed":True,"checks":checks}
    validation,backup_path=loaded._replace_with_validation(patched,validate,backup=backup)
    return HierarchyMutationReport(loaded.source,before,_sha(patched),plan.source['guid'],plan.destination['new_parent'],plan.destination['sibling_index'],plan.destination['preorder_index'],plan.safety_class,backup_path,validation)


@dataclass(frozen=True, slots=True)
class SubtreeClonePlan:
    source_file: Path; source_sha256: str; safety_class: str; source_root: dict[str,object]; ordered_nodes: tuple[dict[str,object],...]; gameobject_mappings: dict[str,str]; component_mappings: dict[str,str]; hierarchy_reconstruction: tuple[dict[str,object],...]; destination: dict[str,object]; reference_inventory: tuple[object,...]; unknown_inventory: tuple[object,...]
    def to_dict(self):
        out=asdict(self);out['source_file']=str(self.source_file);out['dry_run']=True;out['writer_available']=True;return out


def _allocate(scene,count,guid_factory,max_attempts=MAX_GUID_ATTEMPTS):
    occupied={x.casefold() for o in scene.walk() for x in (o.guid,*(c.guid for c in o.components))}; out=[]
    for _ in range(count):
        value=None
        for _ in range(max_attempts):
            candidate=_canonical_uuid4(guid_factory())
            if candidate and candidate.casefold() not in occupied: value=candidate;occupied.add(candidate.casefold());break
        if value is None: raise GuidAllocationError("could not allocate collision-free canonical UUID v4")
        out.append(value)
    return out


def plan_subtree_duplication(scene_path, identifier, *, guid_factory:Callable[[],object]=uuid.uuid4) -> SubtreeClonePlan:
    loaded=PMHScene.load(scene_path); root=loaded.get_object(identifier)
    if root.parent is None: raise UnsafeDuplicationError("UNSAFE_ROOT_OBJECT: root subtree duplication is forbidden")
    nodes=list(root.walk())
    for node in nodes: _standard_transform_only(node)
    _require_reference_free(loaded.scene,nodes)
    new=_allocate(loaded.scene,len(nodes),guid_factory); mapping=dict(zip((n.guid for n in nodes),new,strict=True))
    return SubtreeClonePlan(loaded.source,_sha(loaded._original_bytes),"SAFE_REFERENCE_FREE_TRANSFORM_ONLY_SUBTREE_DUPLICATION",{"guid":root.guid,"name":root.name,"parent":root.parent.guid,"preorder_range":[root.preorder_index,root.preorder_index+len(nodes)-1],"hierarchy_span":_span_dict(root.hierarchy_span)},tuple({"guid":n.guid,"new_guid":mapping[n.guid],"parent":n.parent.guid if n.parent else None,"new_parent":mapping.get(n.parent.guid,n.parent.guid) if n.parent else None,"sibling":n.sibling_index,"preorder":n.preorder_index,"components":[c.type_name for c in n.components]} for n in nodes),mapping,mapping.copy(),tuple({"source":n.guid,"duplicate":mapping[n.guid],"parent":mapping.get(n.parent.guid,n.parent.guid)} for n in nodes),{"outer_parent":root.parent.guid,"sibling_index":len(root.parent.children),"preorder_index":root.parent.preorder_index+sum(1 for _ in root.parent.walk())},(),())


def _patch_guid_slots(block:bytearray,block_start:int,spans:list[tuple[SourceSpan,str]]):
    for span,value in spans:
        rel=span.start-block_start
        if span.length!=37 or block[rel]!=36: raise WriterValidationError("identity slot is not canonical str8 UUID")
        block[rel+1:rel+37]=value.encode('ascii')


def _construct_subtree(loaded,plan):
    raw=loaded._original_bytes; root=loaded.get_object(plan.source_root['guid']); nodes=list(root.walk()); parent=root.parent; assert parent and root.hierarchy_span
    objects=list(loaded.scene.walk()); index_end,comp_spans,starts=_layout(loaded.scene); insert_pre=plan.destination['preorder_index']
    index_dest=objects[insert_pre].index_span.start if insert_pre<len(objects) else index_end; payload_dest=starts[objects[insert_pre].guid] if insert_pre<len(objects) else loaded.scene.byte_length
    hierarchy=raw[root.hierarchy_span.start:root.hierarchy_span.end]
    index_start=nodes[0].index_span.start; index_end_src=nodes[-1].index_span.end; index=bytearray(raw[index_start:index_end_src]); index_slots=[]
    payload_start=comp_spans[nodes[0].components[0].guid].start; payload_end=comp_spans[nodes[-1].components[-1].guid].end; payload=bytearray(raw[payload_start:payload_end]); payload_slots=[]
    for n in nodes:
        index_slots.append((n.identity_span,plan.gameobject_mappings[n.guid]))
        for c in n.components:
            index_slots.append((c.identity_span,plan.component_mappings[c.guid])); payload_slots.append((c.get_field('guid').source_span,plan.component_mappings[c.guid]))
    _patch_guid_slots(index,index_start,index_slots);_patch_guid_slots(payload,payload_start,payload_slots)
    ops=[(_parent_child_count_offset(parent),2,struct.pack('<H',len(parent.children)+1)),(parent.hierarchy_span.end,0,hierarchy),(index_dest,0,bytes(index)),(payload_dest,0,bytes(payload))]
    patched=bytearray(raw)
    for off,length,repl in sorted(ops,reverse=True): patched[off:off+length]=repl
    return bytes(patched)


def duplicate_subtree(scene_path,identifier,*,expected_scene_hash=None,backup=True,guid_factory:Callable[[],object]=uuid.uuid4):
    loaded=PMHScene.load(scene_path); before=loaded.source_sha256
    if expected_scene_hash and expected_scene_hash.casefold()!=before: raise ConcurrentModificationError("UNSAFE_STALE_SCENE: expected_scene_hash does not match")
    plan=plan_subtree_duplication(loaded.source,identifier,guid_factory=guid_factory);patched=_construct_subtree(loaded,plan);source=loaded.get_object(plan.source_root['guid']);count=len(plan.ordered_nodes)
    def validate(scene,data):
        dup=next((o for o in scene.walk() if o.guid==plan.gameobject_mappings[source.guid]),None); checks={"parse_to_exact_eof":scene.fully_consumed,"object_count":scene.object_count==loaded.scene.object_count+count,"component_count":scene.component_count==loaded.scene.component_count+count,"duplicate_root":bool(dup and dup.parent and dup.parent.guid==plan.destination['outer_parent']),"append_root":bool(dup and dup.sibling_index==plan.destination['sibling_index']),"node_mapping":all(any(o.guid==g for o in scene.walk()) for g in plan.gameobject_mappings.values()),"ownership":all(o.guid==o.components[0].guid for o in scene.walk() if o.guid in plan.gameobject_mappings.values()),"source_preserved":all(any(o.guid==n['guid'] for o in scene.walk()) for n in plan.ordered_nodes)}
        if not all(checks.values()): raise WriterValidationError('subtree validation failed: '+','.join(k for k,v in checks.items() if not v))
        return {"passed":True,"checks":checks}
    validation,bak=loaded._replace_with_validation(patched,validate,backup=backup)
    return {"source_file":str(loaded.source),"before_sha256":before,"after_sha256":_sha(patched),"source_root":plan.source_root,"duplicate_root_guid":plan.gameobject_mappings[source.guid],"node_count":count,"safety_class":plan.safety_class,"backup_path":str(bak) if bak else None,"validation":validation}
