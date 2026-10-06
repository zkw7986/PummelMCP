"""Transactional Blender OBJ/texture import and scene placement.

The game itself imports OBJ, PNG/JPG and PMAT assets.  This module produces the
same Mod-local metadata envelopes observed from the official editor, derives a
textured material from a shipped editor template, and then uses the validated
PMH prop writer to place the imported prop in a Scene.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import struct
import tempfile
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

from .builtin_prop_writer import spawn_builtin_prop
from .editor_assets import PROP_REFERENCE_PREFIX, VerifiedPropReference
from .reader import read_pmh
from .validator import validate_scene

EXTERNAL_ASSET_LOCATION = "757b15db-4305-4406-b767-1ec002daa769"
VERSION = "0.1"
MAX_OBJECTS = 256
MAX_SOURCE_BYTES = 128 * 1024 * 1024
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _uuid4(seed: str) -> str:
    raw = bytearray(hashlib.sha256(seed.encode("utf-8")).digest()[:16])
    raw[6] = (raw[6] & 0x0F) | 0x40
    raw[8] = (raw[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(raw)))


def _contained(root: Path, relative: object, *, extensions: set[str], what: str) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise ValueError(f"{what} must be a non-empty relative path")
    text = relative.replace("\\", "/")
    candidate = (root / text).resolve(strict=True)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{what} escapes the configured import root") from exc
    if not candidate.is_file() or candidate.suffix.casefold() not in extensions:
        raise ValueError(f"{what} must be an existing {sorted(extensions)} file: {text}")
    if candidate.stat().st_size <= 0 or candidate.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError(f"{what} has an unsupported size: {text}")
    return candidate


def _validate_obj(path: Path) -> dict[str, int]:
    vertices = faces = 0
    materials: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="strict").splitlines():
        stripped = line.strip()
        if stripped.startswith("v "):
            vertices += 1
        elif stripped.startswith("f "):
            faces += 1
        elif stripped.startswith("usemtl "):
            name = stripped[7:].strip()
            if name and name not in materials:
                materials.append(name)
    if vertices < 3 or faces < 1:
        raise ValueError(f"OBJ has no usable triangle geometry: {path.name}")
    return {"vertices": vertices, "faces": faces, "material_slots": max(1, len(materials))}


def _validate_image(path: Path) -> None:
    head = path.read_bytes()[:16]
    ext = path.suffix.casefold()
    if ext == ".png" and not head.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError(f"PNG signature mismatch: {path.name}")
    if ext in {".jpg", ".jpeg"} and not head.startswith(b"\xff\xd8\xff"):
        raise ValueError(f"JPEG signature mismatch: {path.name}")


def _vec(value: object, default: Sequence[float], *, what: str) -> list[float]:
    if value is None:
        return list(default)
    if not isinstance(value, Mapping) or not set(value) <= {"x", "y", "z"}:
        raise ValueError(f"{what} must contain only x, y and z")
    result = [float(value.get(axis, default[index])) for index, axis in enumerate(("x", "y", "z"))]
    if not all(math.isfinite(item) for item in result):
        raise ValueError(f"{what} values must be finite")
    return result


def _find_material_template(template_root: Path) -> tuple[Path, dict[str, Any], bytes]:
    for meta in sorted(template_root.rglob("*.pmat.pmeta")):
        try:
            obj = json.loads(meta.read_text(encoding="utf-8"))
            data = bytes(obj["m_shaderData"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if b"\x08_MainTex\x04\x01\x24" in data and obj.get("m_shaderName"):
            return meta, obj, data
    raise ValueError("no verified PMAT template with a _MainTex slot was found")


def _replace_main_texture(template: bytes, texture_guid: str) -> bytes:
    marker = b"\x08_MainTex\x04\x01\x24"
    offset = template.find(marker)
    if offset < 0 or template.find(marker, offset + 1) >= 0:
        raise ValueError("PMAT template has no unique _MainTex reference")
    start = offset + len(marker)
    old = template[start : start + 36]
    if len(old) != 36:
        raise ValueError("PMAT template has a truncated _MainTex reference")
    result = template[:start] + texture_guid.encode("ascii") + template[start + 36 :]
    if len(result) != len(template):
        raise ValueError("PMAT texture patch changed serialized size")
    return result


def _meta_base(name: str, guid: str, tag: str, folder: str) -> dict[str, Any]:
    return {
        "enabled": True,
        "name": name,
        "guid": {"serializedGuid": guid},
        "tags": [tag],
        "assetTypeString": "",
        "atlasIndex": 2147483647,
        "atlasPtrIndex": 2147483647,
        "assetLocation": {"serializedGuid": EXTERNAL_ASSET_LOCATION},
        "assetFolder": folder,
    }


def _read_manifest(manifest_path: Path) -> dict[str, Any]:
    try:
        spec = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid Blender scene manifest: {exc}") from exc
    if not isinstance(spec, dict) or spec.get("version") != VERSION:
        raise ValueError(f"manifest version must be {VERSION!r}")
    if set(spec) - {"version", "collection", "objects"}:
        raise ValueError("manifest contains unknown top-level fields")
    collection = spec.get("collection")
    if not isinstance(collection, str) or not _SAFE_NAME.fullmatch(collection):
        raise ValueError("collection must use 1-64 ASCII letters, numbers, '_' or '-'")
    objects = spec.get("objects")
    if not isinstance(objects, list) or not 1 <= len(objects) <= MAX_OBJECTS:
        raise ValueError(f"objects must contain 1-{MAX_OBJECTS} entries")
    return spec


def plan_blender_scene_import(
    scene_path: str | Path,
    manifest_path: str | Path,
    *,
    allowed_root: str | Path,
    import_root: str | Path,
    template_root: str | Path,
) -> dict[str, Any]:
    scene = Path(scene_path).resolve(strict=True)
    allowed = Path(allowed_root).resolve(strict=True)
    imports = Path(import_root).resolve(strict=True)
    manifest = Path(manifest_path).resolve(strict=True)
    templates = Path(template_root).resolve(strict=True)
    scene.relative_to(allowed)
    manifest.relative_to(imports)
    spec = _read_manifest(manifest)
    source_root = manifest.parent
    mod_root = scene.parent.parent
    if not (mod_root / "Data" / "Meta.json").is_file():
        raise ValueError("scene is not inside a Pummel Party Mod directory")
    template_meta, template_obj, template_data = _find_material_template(templates)
    normalized: list[dict[str, Any]] = []
    ids: set[str] = set()
    sources: dict[str, dict[str, Any]] = {}
    targets: set[str] = set()
    collection = spec["collection"]
    for index, raw in enumerate(spec["objects"]):
        if not isinstance(raw, dict):
            raise ValueError(f"objects[{index}] must be an object")
        allowed_fields = {"id", "obj", "texture", "textures", "name", "parent", "position", "rotation_degrees", "scale", "collision_type", "shadow_casting_mode", "tint_color", "active"}
        if set(raw) - allowed_fields:
            raise ValueError(f"objects[{index}] contains unknown fields")
        logical_id = raw.get("id")
        if not isinstance(logical_id, str) or not _SAFE_NAME.fullmatch(logical_id) or logical_id in ids:
            raise ValueError(f"objects[{index}].id must be unique and filename-safe")
        ids.add(logical_id)
        obj_path = _contained(source_root, raw.get("obj"), extensions={".obj"}, what=f"objects[{index}].obj")
        obj_info = _validate_obj(obj_path)
        texture_values = raw.get("textures")
        if texture_values is None and raw.get("texture") is not None:
            texture_values = [raw["texture"]]
        if texture_values is None:
            texture_values = []
        if not isinstance(texture_values, list) or not all(isinstance(x, str) for x in texture_values):
            raise ValueError(f"objects[{index}].textures must be an array of paths")
        if texture_values and len(texture_values) != obj_info["material_slots"]:
            raise ValueError(
                f"objects[{index}] has {obj_info['material_slots']} OBJ material slot(s) but {len(texture_values)} texture(s)"
            )
        texture_paths = []
        for tex in texture_values:
            path = _contained(source_root, tex, extensions=_IMAGE_EXTENSIONS, what=f"objects[{index}].texture")
            _validate_image(path)
            texture_paths.append(path)
        rotation_degrees = _vec(raw.get("rotation_degrees"), (0, 0, 0), what=f"objects[{index}].rotation_degrees")
        entry = {
            "id": logical_id,
            "obj": obj_path.relative_to(source_root).as_posix(),
            "textures": [p.relative_to(source_root).as_posix() for p in texture_paths],
            "name": raw.get("name", logical_id),
            "parent": raw.get("parent", "/World"),
            "position": _vec(raw.get("position"), (0, 0, 0), what=f"objects[{index}].position"),
            "rotation": [math.radians(v) for v in rotation_degrees],
            "scale": _vec(raw.get("scale"), (1, 1, 1), what=f"objects[{index}].scale"),
            "collision_type": raw.get("collision_type", "mesh"),
            "shadow_casting_mode": raw.get("shadow_casting_mode", "on"),
            "tint_color": raw.get("tint_color"),
            "active": raw.get("active", True),
        }
        if not isinstance(entry["name"], str) or not entry["name"]:
            raise ValueError(f"objects[{index}].name must be a non-empty string")
        normalized.append(entry)
        for path in [obj_path, *texture_paths]:
            rel = path.relative_to(source_root).as_posix()
            sources.setdefault(rel, {"sha256": _sha(path.read_bytes()), "bytes": path.stat().st_size})
        mtl = obj_path.with_suffix(".mtl")
        if mtl.is_file():
            mtl.relative_to(imports)
            rel = mtl.relative_to(source_root).as_posix()
            sources.setdefault(rel, {"sha256": _sha(mtl.read_bytes()), "bytes": mtl.stat().st_size})
        obj_token = hashlib.sha256(entry["obj"].encode()).hexdigest()[:8]
        targets.add(f"Assets/Props/{collection}__{obj_path.stem}_{obj_token}.obj")
        for tex_path in texture_paths:
            tex_rel = tex_path.relative_to(source_root).as_posix()
            tex_token = hashlib.sha256(tex_rel.encode()).hexdigest()[:8]
            targets.add(f"Assets/Textures/{collection}__{tex_path.stem}_{tex_token}{tex_path.suffix.lower()}")
            targets.add(f"Assets/Materials/{collection}__{tex_path.stem}_{tex_token}.pmat")
    conflicts = sorted(path for path in targets if (mod_root / path).exists() or Path(str(mod_root / path) + ".pmeta").exists())
    if conflicts:
        raise ValueError("target assets already exist: " + ", ".join(conflicts[:8]))
    base = {
        "version": VERSION,
        "scene": str(scene),
        "scene_sha256": _sha(scene.read_bytes()),
        "manifest": str(manifest),
        "manifest_sha256": _sha(manifest.read_bytes()),
        "collection": collection,
        "objects": normalized,
        "sources": sources,
        "material_template": str(template_meta),
        "material_template_sha256": _sha(template_meta.read_bytes()),
        "material_shader": template_obj["m_shaderName"],
        "material_payload_sha256": _sha(template_data),
        "targets": sorted(targets),
    }
    base["plan_sha256"] = _sha(_canonical(base))
    return base


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    except BaseException:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise


def build_blender_scene_import(
    scene_path: str | Path,
    manifest_path: str | Path,
    expected_plan_sha256: str,
    *,
    allowed_root: str | Path,
    import_root: str | Path,
    template_root: str | Path,
) -> dict[str, Any]:
    plan = plan_blender_scene_import(scene_path, manifest_path, allowed_root=allowed_root, import_root=import_root, template_root=template_root)
    if plan["plan_sha256"] != expected_plan_sha256:
        raise ValueError("STALE_BLENDER_IMPORT_PLAN")
    scene = Path(scene_path).resolve(strict=True)
    manifest = Path(manifest_path).resolve(strict=True)
    source_root = manifest.parent
    mod_root = scene.parent.parent
    template_obj = json.loads(Path(plan["material_template"]).read_text(encoding="utf-8"))
    template_data = bytes(template_obj["m_shaderData"])
    staged_files: dict[Path, bytes] = {}
    prop_refs: dict[str, VerifiedPropReference] = {}
    material_guids: dict[str, str] = {}
    collection = plan["collection"]
    for obj in plan["objects"]:
        obj_rel = obj["obj"]
        obj_source = (source_root / obj_rel).resolve(strict=True)
        obj_token = hashlib.sha256(obj_rel.encode()).hexdigest()[:8]
        obj_name = f"{collection}__{obj_source.stem}_{obj_token}"
        obj_target = mod_root / "Assets" / "Props" / (obj_name + ".obj")
        prop_guid = _uuid4(plan["plan_sha256"] + ":prop:" + obj_rel)
        mesh_guid = _uuid4(plan["plan_sha256"] + ":mesh:" + obj_rel)
        obj_meta = _meta_base(obj_name, prop_guid, "props", "/Props/")
        obj_meta["atlasIndex"] = 0
        obj_meta["m_meshAsset"] = _meta_base(obj_name, mesh_guid, "meshes", "")
        obj_meta["m_meshAsset"]["m_recalculateNormals"] = False
        staged_files[obj_target] = obj_source.read_bytes()
        staged_files[Path(str(obj_target) + ".pmeta")] = _canonical(obj_meta)
        mtl = obj_source.with_suffix(".mtl")
        if mtl.is_file():
            staged_files[obj_target.with_suffix(".mtl")] = mtl.read_bytes()
        payload = PROP_REFERENCE_PREFIX + prop_guid.encode("ascii")
        prop_refs[obj_rel] = VerifiedPropReference(
            guid=prop_guid,
            payload=payload,
            payload_sha256=_sha(payload),
            asset_id=f"ModLocal/Assets/Props/{obj_name}.obj",
            asset_name=obj_name,
            asset_folder="Assets/Props/",
            catalog_sha256=_sha(_canonical(obj_meta) + obj_source.read_bytes()),
        )
        for tex_rel in obj["textures"]:
            if tex_rel in material_guids:
                continue
            tex_source = (source_root / tex_rel).resolve(strict=True)
            tex_token = hashlib.sha256(tex_rel.encode()).hexdigest()[:8]
            tex_name = f"{collection}__{tex_source.stem}_{tex_token}"
            tex_target = mod_root / "Assets" / "Textures" / (tex_name + tex_source.suffix.lower())
            texture_guid = _uuid4(plan["plan_sha256"] + ":texture:" + tex_rel)
            material_guid = _uuid4(plan["plan_sha256"] + ":material:" + tex_rel)
            tex_meta = _meta_base(tex_name, texture_guid, "textures", "/Textures/")
            tex_meta.update({"m_compression": 0, "m_filter": 1, "m_wrapMode": 0})
            staged_files[tex_target] = tex_source.read_bytes()
            staged_files[Path(str(tex_target) + ".pmeta")] = _canonical(tex_meta)
            material_data = _replace_main_texture(template_data, texture_guid)
            material_name = tex_name
            material_target = mod_root / "Assets" / "Materials" / (material_name + ".pmat")
            material_meta = _meta_base(material_name, material_guid, "materials", "/Materials/")
            material_meta.update({"m_shaderName": template_obj["m_shaderName"], "m_shaderData": list(material_data)})
            staged_files[material_target] = b"\x04PMAT"
            staged_files[Path(str(material_target) + ".pmeta")] = _canonical(material_meta)
            material_guids[tex_rel] = material_guid
    temp_scene = scene.with_name(f".{scene.name}.{uuid.uuid4().hex}.staging")
    shutil.copyfile(scene, temp_scene)
    placements = []
    try:
        for obj in plan["objects"]:
            mats = [material_guids[path] for path in obj["textures"]]
            report = spawn_builtin_prop(
                temp_scene,
                prop_refs[obj["obj"]],
                parent=obj["parent"],
                name=obj["name"],
                position=obj["position"],
                rotation=obj["rotation"],
                scale=obj["scale"],
                tint_color=obj["tint_color"],
                collision_type=obj["collision_type"],
                shadow_casting_mode=obj["shadow_casting_mode"],
                active=obj["active"],
                custom_material_guids=mats,
                backup=False,
            )
            placements.append({"id": obj["id"], "path": report["created"]["hierarchy_path"], "prop_guid": prop_refs[obj["obj"]].guid, "material_guids": mats})
        parsed = read_pmh(temp_scene)
        if not parsed.fully_consumed or not validate_scene(parsed).passed:
            raise ValueError("generated Scene failed PMH validation")
        committed: list[Path] = []
        try:
            for target, data in staged_files.items():
                if target.exists():
                    raise ValueError(f"target appeared after planning: {target}")
                _write_atomic(target, data)
                committed.append(target)
            backup = scene.with_name(scene.name + ".bak.blender-import")
            if not backup.exists():
                shutil.copy2(scene, backup)
            _write_atomic(scene, temp_scene.read_bytes())
        except BaseException:
            for path in reversed(committed):
                try:
                    path.unlink()
                except OSError:
                    pass
            raise
    finally:
        try:
            temp_scene.unlink()
        except OSError:
            pass
    return {
        "status": "BUILT_UNVERIFIED_IN_EDITOR",
        "plan_sha256": plan["plan_sha256"],
        "scene": str(scene),
        "scene_sha256": _sha(scene.read_bytes()),
        "asset_files_created": len(staged_files),
        "objects_placed": len(placements),
        "placements": placements,
        "backup_path": str(scene.with_name(scene.name + ".bak.blender-import")),
        "validation": {"pmh": True, "assets_present": all(path.is_file() for path in staged_files)},
    }
