"""Same-scene whole-envelope replacement for observed ModProp references."""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from pathlib import Path

from .asset_references import parse_compact_asset_reference, parse_material_reference_list
from .errors import ConcurrentModificationError, UnsafeDuplicationError, WriterValidationError
from .validator import validate_scene
from .writer import PMHScene


def _sha(data: bytes) -> str: return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True, slots=True)
class AssetReferenceReplacementReport:
    safety_class: str
    source_file: str
    target_object_guid: str
    template_object_guid: str
    old_reference_guid: str
    new_reference_guid: str
    template_reference_sha256: str
    before_sha256: str
    after_sha256: str
    backup_path: str | None
    validation: dict

    def to_dict(self): return asdict(self)


def replace_prop_reference_from_template(scene_path: str | Path, target, template, *, expected_scene_hash: str, template_reference_sha256: str, backup: bool = True):
    loaded = PMHScene.load(scene_path)
    if loaded.source_sha256.casefold() != expected_scene_hash.casefold():
        raise ConcurrentModificationError("UNSAFE_STALE_SCENE")
    target_obj = loaded.get_object(target); template_obj = loaded.get_object(template)
    if target_obj.guid == template_obj.guid:
        raise UnsafeDuplicationError("TARGET_EQUALS_REFERENCE_TEMPLATE")
    for obj, label in ((target_obj,"target"),(template_obj,"template")):
        if tuple(c.type_name for c in obj.components) != ("ModTransform","ModProp"):
            raise UnsafeDuplicationError(f"UNSAFE_{label.upper()}_PROP_SHAPE")
        mats=parse_material_reference_list(obj.get_component("ModProp").get_field("customMaterials").raw)
        if mats.elements: raise UnsafeDuplicationError("UNSUPPORTED_MATERIAL_OVERRIDE")
    target_field=target_obj.get_component("ModProp").get_field("prop")
    template_field=template_obj.get_component("ModProp").get_field("prop")
    old=parse_compact_asset_reference(target_field.raw); new=parse_compact_asset_reference(template_field.raw)
    actual_template_hash=_sha(template_field.raw)
    if actual_template_hash.casefold()!=template_reference_sha256.casefold():
        raise ConcurrentModificationError("ASSET_REFERENCE_TEMPLATE_STALE")
    if target_field.source_span is None or target_field.source_span.length != 38:
        raise UnsafeDuplicationError("UNKNOWN_PROP_REFERENCE_BOUNDARY")
    patched=bytearray(loaded._original_bytes); span=target_field.source_span
    patched[span.start:span.end]=template_field.raw; output=bytes(patched)
    allowed=set(range(span.start,span.end))
    def validator(scene,data):
        changed={i for i,(a,b) in enumerate(zip(loaded._original_bytes,data,strict=True)) if a!=b}
        reparsed_target=next((o for o in scene.walk() if o.guid==target_obj.guid),None)
        reparsed_template=next((o for o in scene.walk() if o.guid==template_obj.guid),None)
        checks={
            "same_byte_length":len(data)==len(loaded._original_bytes),
            "parse_to_exact_eof":scene.fully_consumed,
            "scene_validation":validate_scene(scene).passed,
            "only_target_field_changed":changed<=allowed,
            "target_reference_matches_template":reparsed_target is not None and reparsed_target.get_component("ModProp").get_field("prop").raw==template_field.raw,
            "template_reference_unchanged":reparsed_template is not None and reparsed_template.get_component("ModProp").get_field("prop").raw==template_field.raw,
        }
        if not all(checks.values()): raise WriterValidationError("asset reference replacement validation failed")
        return {"passed":True,"checks":checks}
    result,bak=loaded._replace_with_validation(output,validator,backup=backup)
    return AssetReferenceReplacementReport("WHOLE_TYPED_PROP_REFERENCE_TEMPLATE_REPLACEMENT_CANDIDATE",str(loaded.source),target_obj.guid,template_obj.guid,old.guid,new.guid,actual_template_hash,loaded.source_sha256,_sha(output),str(bak) if bak else None,result)
