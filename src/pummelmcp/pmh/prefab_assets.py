"""Template-grounded PMH Prefab asset cloning in a staged Mod copy."""

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
from typing import Any

from .action_graph_writer import ActionGraphWriter, build_action_template_catalog
from .action_writer import ActionFieldWriter
from .actions import parse_action_payload
from .errors import ConcurrentModificationError, UnsafeDuplicationError, WriterValidationError
from .reader import read_pfab, read_pmh
from .validator import validate_scene
from .writer import PMHScene

_UUID = re.compile(rb"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _()-]{0,63}$")
_ZERO = "00000000-0000-0000-0000-000000000000"
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _source(scene_path: str | Path, allowed_root: str | Path) -> tuple[Path, Path, Path]:
    scene = Path(scene_path).resolve(strict=True)
    allowed = Path(allowed_root).resolve(strict=True)
    try:
        scene.relative_to(allowed)
    except ValueError as exc:
        raise UnsafeDuplicationError("PREFAB_SCENE_OUTSIDE_ALLOWED_ROOT") from exc
    if scene.parent.name != "Data" or scene.suffix.lower() != ".scene":
        raise UnsafeDuplicationError("PREFAB_SOURCE_REQUIRES_DATA_SCENE")
    mod = scene.parent.parent
    if mod == allowed:
        raise UnsafeDuplicationError("PREFAB_OUTPUT_ROOT_MUST_CONTAIN_SOURCE_MOD")
    return scene, mod, allowed


def _asset_path(mod: Path, relative: str) -> Path:
    if type(relative) is not str or not relative.startswith("Assets/Prefabs/") or not relative.endswith(".pfab"):
        raise UnsafeDuplicationError("INVALID_PREFAB_SOURCE_PATH")
    try:
        path = (mod / relative).resolve(strict=True)
    except FileNotFoundError as exc:
        raise UnsafeDuplicationError("PREFAB_SOURCE_MISSING") from exc
    try:
        path.relative_to((mod / "Assets" / "Prefabs").resolve(strict=True))
    except ValueError as exc:
        raise UnsafeDuplicationError("PREFAB_SOURCE_PATH_ESCAPE") from exc
    if not path.is_file():
        raise UnsafeDuplicationError("PREFAB_SOURCE_NOT_FILE")
    return path


def _metadata(path: Path) -> tuple[dict, bytes, str]:
    meta_path = Path(str(path) + ".pmeta")
    try:
        raw = meta_path.read_bytes()
    except FileNotFoundError as exc:
        raise UnsafeDuplicationError("PREFAB_METADATA_MISSING") from exc
    try:
        value = json.loads(raw)
        guid = value["guid"]["serializedGuid"]
        if type(guid) is not str or str(uuid.UUID(guid)) != guid.lower():
            raise ValueError("invalid GUID")
    except (ValueError, KeyError, TypeError) as exc:
        raise UnsafeDuplicationError("INVALID_PREFAB_METADATA") from exc
    return value, raw, guid.lower()


def _transform(value: Any) -> dict:
    if not isinstance(value, dict) or not value or set(value) - {"position", "rotation", "scale"}:
        raise UnsafeDuplicationError("INVALID_PREFAB_ROOT_TRANSFORM")
    result = {}
    for field, vector in value.items():
        if not isinstance(vector, dict) or set(vector) != {"x", "y", "z"}:
            raise UnsafeDuplicationError("INVALID_PREFAB_ROOT_TRANSFORM")
        if any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 10000
               or (field == "scale" and v <= 0) for v in vector.values()):
            raise UnsafeDuplicationError("INVALID_PREFAB_ROOT_TRANSFORM")
        result[field] = {axis: float(vector[axis]) for axis in ("x", "y", "z")}
    return result


def _empty_v2_trigger(scene_model) -> bytes:
    for obj in scene_model.walk():
        for component in obj.components:
            if component.type_name != "ModTrigger":
                continue
            for field in component.fields:
                if field.name not in {"OnHitActions", "OnEnterActions", "OnExitActions", "OnStayActions"}:
                    continue
                parsed = parse_action_payload(field.raw)
                if (parsed.fully_consumed and not parsed.actions and not parsed.references
                        and parsed.references_version == 2
                        and parsed.root_json_document.node_at(("m_type",)).value == 0):
                    return field.raw
    raise UnsafeDuplicationError("EMPTY_V2_TRIGGER_TEMPLATE_UNAVAILABLE")


def plan_prefab_pack(scene_path: str | Path, allowed_root: str | Path, spec: dict[str, Any]) -> dict[str, Any]:
    """Plan a new Mod with cloned Prefabs and remapped same-pack dependencies."""
    scene, mod, allowed = _source(scene_path, allowed_root)
    required = {"version", "scene", "output_mod", "assets"}
    if not isinstance(spec, dict) or not required <= set(spec) or set(spec) - required - {"spawner_bindings", "pickup_scores"} or spec["version"] != "0.1":
        raise UnsafeDuplicationError("INVALID_PREFAB_PACK_SPEC")
    if type(spec["scene"]) is not str or Path(spec["scene"]).resolve(strict=False) != scene:
        raise UnsafeDuplicationError("PREFAB_PACK_SCENE_MISMATCH")
    output_name = spec["output_mod"]
    if type(output_name) is not str or not _NAME.fullmatch(output_name) or output_name.strip() != output_name or output_name.upper() in _RESERVED:
        raise UnsafeDuplicationError("INVALID_PREFAB_OUTPUT_MOD")
    output = (allowed / output_name).resolve(strict=False)
    if output.parent != allowed or output.exists() or output == mod:
        raise UnsafeDuplicationError("PREFAB_OUTPUT_ALREADY_EXISTS_OR_UNSAFE")
    assets = spec["assets"]
    if not isinstance(assets, list) or not 1 <= len(assets) <= 16:
        raise UnsafeDuplicationError("INVALID_PREFAB_ASSET_LIST")
    sources, names, records = set(), set(), []
    for item in assets:
        if not isinstance(item, dict) or set(item) not in ({"source", "name"}, {"source", "name", "root_transform"}):
            raise UnsafeDuplicationError("INVALID_PREFAB_ASSET_SPEC")
        name = item["name"]
        if type(name) is not str or not _NAME.fullmatch(name) or name.strip() != name or name.upper() in _RESERVED:
            raise UnsafeDuplicationError("INVALID_PREFAB_ASSET_NAME")
        path = _asset_path(mod, item["source"])
        if path in sources or name.casefold() in names or (mod / "Assets" / "Prefabs" / (name + ".pfab")).exists():
            raise UnsafeDuplicationError("DUPLICATE_PREFAB_SOURCE_OR_OUTPUT")
        sources.add(path); names.add(name.casefold())
        parsed = read_pfab(path)
        if not parsed.fully_consumed or not validate_scene(parsed).passed or parsed.root_count != 1:
            raise UnsafeDuplicationError("UNSUPPORTED_PREFAB_GRAMMAR")
        transform = _transform(item["root_transform"]) if "root_transform" in item else None
        if transform is not None and len([c for c in parsed.roots[0].components if c.type_name == "ModTransform"]) != 1:
            raise UnsafeDuplicationError("PREFAB_ROOT_TRANSFORM_UNAVAILABLE")
        metadata, meta_raw, asset_guid = _metadata(path)
        internal = {o.guid.lower() for o in parsed.walk()} | {c.guid.lower() for o in parsed.walk() for c in o.components}
        all_guids = {m.group().decode().lower() for m in _UUID.finditer(path.read_bytes())}
        external = all_guids - internal - {_ZERO}
        inherited_props = {m.group().decode().lower() for obj in parsed.walk()
                           for component in obj.components if component.type_name == "ModProp"
                           for field in component.fields if field.name == "prop"
                           for m in _UUID.finditer(field.raw)}
        records.append({"source": item["source"], "name": name, "asset_guid": asset_guid,
                        "internal_guids": sorted(internal), "external_guids": sorted(external),
                        "inherited_builtin_prop_guids": sorted(inherited_props),
                        "pfab_sha256": _sha(path.read_bytes()), "pmeta_sha256": _sha(meta_raw),
                        "root_transform": transform})
    # Every external Prefab reference must resolve to metadata in this Mod.
    catalog = {}
    for meta_path in (mod / "Assets").rglob("*.pfab.pmeta"):
        _, _, guid = _metadata(Path(str(meta_path)[:-6]))
        if guid in catalog:
            raise UnsafeDuplicationError("DUPLICATE_PREFAB_ASSET_GUID")
        catalog[guid] = meta_path
    for record in records:
        if any(guid not in catalog and guid not in record["inherited_builtin_prop_guids"]
               for guid in record["external_guids"]):
            raise UnsafeDuplicationError("UNRESOLVED_PREFAB_DEPENDENCY")
    dependency_fingerprints = {}
    for guid in sorted({guid for record in records for guid in record["external_guids"] if guid in catalog}):
        meta_path = catalog[guid]
        dependency_path = Path(str(meta_path)[:-6])
        if not dependency_path.is_file():
            raise UnsafeDuplicationError("PREFAB_DEPENDENCY_ASSET_MISSING")
        dependency_fingerprints[guid] = {"asset_sha256": _sha(dependency_path.read_bytes()),
                                         "metadata_sha256": _sha(meta_path.read_bytes())}
    bindings = spec.get("spawner_bindings", [])
    if not isinstance(bindings, list) or len(bindings) > 16:
        raise UnsafeDuplicationError("INVALID_SPAWNER_BINDINGS")
    scene_model = read_pmh(scene)
    by_source = {row["source"]: row for row in records}
    planned_bindings = []
    bound_spawners = set()
    for binding in bindings:
        if not isinstance(binding, dict) or set(binding) != {"object_guid", "source_asset"}:
            raise UnsafeDuplicationError("INVALID_SPAWNER_BINDING")
        source_asset = binding["source_asset"]
        if type(source_asset) is not str or source_asset not in by_source:
            raise UnsafeDuplicationError("SPAWNER_SOURCE_NOT_IN_PACK")
        if type(binding["object_guid"]) is not str:
            raise UnsafeDuplicationError("INVALID_SPAWNER_GUID")
        if binding["object_guid"] in bound_spawners:
            raise UnsafeDuplicationError("DUPLICATE_SPAWNER_BINDING")
        bound_spawners.add(binding["object_guid"])
        matches = [obj for obj in scene_model.walk() if obj.guid == binding["object_guid"]]
        if len(matches) != 1:
            raise UnsafeDuplicationError("SPAWNER_OBJECT_NOT_FOUND")
        components = [component for component in matches[0].components if component.type_name == "ModSpawner"]
        if len(components) != 1:
            raise UnsafeDuplicationError("SPAWNER_COMPONENT_NOT_FOUND")
        old_guid = by_source[source_asset]["asset_guid"].encode()
        for field_name in ("Prefabs", "PrefabGUIDs"):
            field = components[0].get_field(field_name)
            if field.raw.count(old_guid) != 1 or field.source_span is None:
                raise UnsafeDuplicationError("SPAWNER_REFERENCE_SHAPE_UNSUPPORTED")
        planned_bindings.append({"object_guid": binding["object_guid"], "source_asset": source_asset,
                                 "old_asset_guid": old_guid.decode()})
    scores = spec.get("pickup_scores", [])
    if not isinstance(scores, list) or len(scores) > 16:
        raise UnsafeDuplicationError("INVALID_PICKUP_SCORES")
    planned_scores = []
    scored_assets = set()
    for score in scores:
        if not isinstance(score, dict) or set(score) != {"source_asset", "value", "template_scene"}:
            raise UnsafeDuplicationError("INVALID_PICKUP_SCORE")
        source_asset = score["source_asset"]
        value = score["value"]
        if type(source_asset) is not str or source_asset not in by_source or source_asset in scored_assets:
            raise UnsafeDuplicationError("PICKUP_SCORE_SOURCE_UNAVAILABLE")
        scored_assets.add(source_asset)
        if type(value) is not int or not 1 <= value <= 1000:
            raise UnsafeDuplicationError("PICKUP_SCORE_VALUE_INVALID")
        item = read_pfab(_asset_path(mod, source_asset))
        component = item.roots[0].get_component("ModItem")
        pickup = parse_action_payload(component.get_field("OnPickupTrigger").raw)
        if pickup.actions or not pickup.fully_consumed:
            raise UnsafeDuplicationError("PICKUP_TRIGGER_NOT_EMPTY")
        template_path_raw = score["template_scene"]
        if type(template_path_raw) is not str:
            raise UnsafeDuplicationError("PICKUP_TEMPLATE_PATH_INVALID")
        template_path = Path(template_path_raw).resolve(strict=True)
        if template_path.suffix.lower() != ".scene" or not template_path.is_relative_to(allowed):
            raise UnsafeDuplicationError("PICKUP_TEMPLATE_OUTSIDE_ALLOWED_ROOT")
        template_scene = read_pmh(template_path)
        empty_payload = _empty_v2_trigger(template_scene)
        variants = build_action_template_catalog(template_scene).matching("ModSystem.Logic", "ChangeScoreAction")
        hashes = {variant.managed_reference_sha256 for variant in variants}
        if len(hashes) != 1:
            raise UnsafeDuplicationError("PICKUP_SCORE_TEMPLATE_UNAVAILABLE")
        planned_scores.append({"source_asset": source_asset, "value": value, "template_scene": str(template_path),
                               "template_scene_sha256": _sha(template_path.read_bytes()),
                               "template_sha256": next(iter(hashes)),
                               "empty_payload_sha256": _sha(empty_payload)})
    payload = {"scene_sha256": _sha(scene.read_bytes()), "spec": spec, "assets": records,
               "dependencies": dependency_fingerprints,
               "pickup_scores": planned_scores, "output": str(output)}
    return {"status": "SAFE_TO_BUILD", "plan_sha256": _sha(_canonical(payload)),
            "output_mod": str(output), "assets": records, "spawner_bindings": planned_bindings,
            "pickup_scores": planned_scores,
            "runtime_status": "NOT_RUN"}


def build_prefab_pack(scene_path: str | Path, allowed_root: str | Path, spec: dict[str, Any], expected_plan_sha256: str) -> dict[str, Any]:
    plan = plan_prefab_pack(scene_path, allowed_root, spec)
    if type(expected_plan_sha256) is not str or expected_plan_sha256.casefold() != plan["plan_sha256"]:
        raise ConcurrentModificationError("PREFAB_PACK_PLAN_STALE")
    scene, mod, allowed = _source(scene_path, allowed_root)
    output = Path(plan["output_mod"])
    stage = Path(tempfile.mkdtemp(prefix=".pummelmcp-prefab-build-", dir=allowed))
    staged_mod = stage / "Mod"
    try:
        def ignore(directory, names):
            return {stage.name} if Path(directory).resolve() == mod and stage.name in names else set()
        shutil.copytree(mod, staged_mod, ignore=ignore)
        asset_map = {row["asset_guid"]: str(uuid.uuid4()) for row in plan["assets"]}
        scores_by_source = {score["source_asset"]: score for score in plan["pickup_scores"]}
        created = []
        for row in plan["assets"]:
            src = _asset_path(staged_mod, row["source"])
            target = staged_mod / "Assets" / "Prefabs" / (row["name"] + ".pfab")
            raw = src.read_bytes()
            replacements = {guid: str(uuid.uuid4()) for guid in row["internal_guids"]}
            replacements.update({guid: new for guid, new in asset_map.items() if guid in row["external_guids"]})
            transformed = _UUID.sub(lambda match: replacements.get(match.group().decode().lower(), match.group().decode()).encode(), raw)
            # The Prefab grammar has exactly one root starting at offset 8.
            old_name_size = transformed[8]
            new_name = row["name"].encode("ascii")
            transformed = transformed[:8] + bytes([len(new_name)]) + new_name + transformed[9 + old_name_size:]
            if row["root_transform"]:
                temporary_scene = stage / "prefab-edit.scene"
                temporary_scene.write_bytes(transformed[:8] + struct.pack("<H", 1) + transformed[8:])
                root_guid = read_pfab(src).roots[0].guid.lower()
                fresh_root_guid = replacements[root_guid]
                PMHScene.load(temporary_scene).set_transform(fresh_root_guid, **row["root_transform"], backup=False)
                scene_bytes = temporary_scene.read_bytes()
                transformed = scene_bytes[:8] + scene_bytes[10:]
                temporary_scene.unlink()
            target.write_bytes(transformed)
            score = scores_by_source.get(row["source"])
            if score:
                temporary_scene = stage / "item-score.scene"
                temporary_scene.write_bytes(transformed[:8] + struct.pack("<H", 1) + transformed[8:])
                fresh_root_guid = replacements[read_pfab(src).roots[0].guid.lower()]
                empty_payload = _empty_v2_trigger(read_pmh(score["template_scene"]))
                if _sha(empty_payload) != score["empty_payload_sha256"]:
                    raise ConcurrentModificationError("PICKUP_TRIGGER_TEMPLATE_CHANGED")
                item_field = read_pmh(temporary_scene).roots[0].get_component("ModItem").get_field("OnPickupTrigger")
                record_start = item_field.source_span.start - 4
                before = temporary_scene.read_bytes()
                temporary_scene.write_bytes(before[:record_start] + struct.pack("<I", len(empty_payload))
                                            + empty_payload + before[item_field.source_span.end:])
                added = ActionGraphWriter(temporary_scene).add_action(
                    fresh_root_guid, "ModItem", "OnPickupTrigger", "ChangeScoreAction",
                    template_sha256=score["template_sha256"],
                    template_scene_path=score["template_scene"], backup=False)
                for field_name, value in (("m_targetFlags", 1), ("m_operation", 1), ("m_value", score["value"])):
                    ActionFieldWriter(temporary_scene).set_action_field(
                        fresh_root_guid, "ModItem", "OnPickupTrigger", added.affected_rid,
                        "ChangeScoreAction", field_name, value, backup=False)
                changed = temporary_scene.read_bytes()
                transformed = changed[:8] + changed[10:]
                target.write_bytes(transformed)
                temporary_scene.unlink()
            meta, _, _ = _metadata(src)
            meta["name"] = row["name"]
            meta["guid"]["serializedGuid"] = asset_map[row["asset_guid"]]
            Path(str(target) + ".pmeta").write_bytes(_canonical(meta))
            parsed = read_pfab(target)
            if not parsed.fully_consumed or not validate_scene(parsed).passed:
                raise WriterValidationError("STAGED_PREFAB_INVALID")
            if parsed.roots[0].name != row["name"]:
                raise WriterValidationError("STAGED_PREFAB_NAME_MISMATCH")
            if score:
                actions = parse_action_payload(parsed.roots[0].get_component("ModItem").get_field("OnPickupTrigger").raw).actions
                if len(actions) != 1 or actions[0].fields.get("m_value") != score["value"] or actions[0].fields.get("m_targetFlags") != 1:
                    raise WriterValidationError("STAGED_PICKUP_SCORE_MISMATCH")
            if any(guid.encode() in transformed.lower() for guid in row["internal_guids"]):
                raise WriterValidationError("PREFAB_OLD_INTERNAL_GUID_REMAINS")
            created.append({"path": str(output / target.relative_to(staged_mod)), "guid": asset_map[row["asset_guid"]],
                            "source": row["source"], "sha256": _sha(transformed)})
        staged_scene = staged_mod / scene.relative_to(mod)
        staged_scene_model = read_pmh(staged_scene)
        scene_raw = staged_scene.read_bytes()
        edits = []
        for binding in plan["spawner_bindings"]:
            obj = next(obj for obj in staged_scene_model.walk() if obj.guid == binding["object_guid"])
            component = obj.get_component("ModSpawner")
            old_guid = binding["old_asset_guid"].encode()
            new_guid = asset_map[binding["old_asset_guid"]].encode()
            for field_name in ("Prefabs", "PrefabGUIDs"):
                field = component.get_field(field_name)
                if field.raw.count(old_guid) != 1:
                    raise ConcurrentModificationError("STAGED_SPAWNER_REFERENCE_CHANGED")
                start = field.source_span.start
                edits.append((start, field.raw.replace(old_guid, new_guid)))
        for start, value in edits:
            scene_raw = scene_raw[:start] + value + scene_raw[start + len(value):]
        if edits:
            staged_scene.write_bytes(scene_raw)
            parsed_scene = read_pmh(staged_scene)
            if not parsed_scene.fully_consumed or not validate_scene(parsed_scene).passed:
                raise WriterValidationError("STAGED_SPAWNER_SCENE_INVALID")
        if plan_prefab_pack(scene, allowed, spec)["plan_sha256"] != plan["plan_sha256"] or output.exists():
            raise ConcurrentModificationError("PREFAB_PACK_SOURCE_CHANGED")
        os.rename(staged_mod, output)
        return {"status": "BUILD_PASS", "output_mod": str(output), "assets": created,
                "spawner_bindings": plan["spawner_bindings"],
                "plan_sha256": plan["plan_sha256"], "runtime_status": "NOT_RUN", "completion": "ASSETS_UNVERIFIED"}
    finally:
        if stage.resolve(strict=False).parent == allowed and stage.name.startswith(".pummelmcp-prefab-build-"):
            shutil.rmtree(stage, ignore_errors=True)
