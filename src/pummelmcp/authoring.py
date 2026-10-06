"""Experimental, source-backed authoring into NEW Mods only.

Donor archives are data, never executable instructions. This compiler builds a new
hierarchy, component membership, action lists and Prefabs; it does not patch an
existing scene or claim official-editor/runtime verification.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import struct
import tempfile
import uuid
import zipfile
from collections import Counter
from typing import Any

from .pmh.actions import parse_action_payload
from .pmh.action_writer import encode_action_varuint7
from .pmh.reader import PMHReader
from .pmh.schemas import get_property_schema, Vector3Float32, Utf8String
from .pmh.validator import validate_scene
from .minigame_config import _patched

EVENTS = {
    "ModTrigger": ("OnHitActions", "OnEnterActions", "OnExitActions", "OnStayActions"),
    "ModItem": ("OnPickupTrigger", "OnDropTrigger", "OnUseTrigger", "OnWhileHeldTrigger"),
}
COMPONENTS = {"ModProp", "ModBoxCollider", "ModPlayerSpawn", "ModTrigger", "ModLight", "ModText", "ModItem", "ModLogic"}
PLAYER_EVENTS = {"hitTrigger", "weaponHitTrigger", "tickTrigger", "onDeathTrigger", "onRespawnTrigger", "onFirstSpawnTrigger", "onRoundEndTrigger"}
ACTION_FIELDS = {
    "SetActiveAction": {"m_newState": "active_state"},
    "ChangeScoreAction": {"m_operation": "operation", "m_value": "int"},
    "ChangeHealthAction": {"m_operation": "operation", "m_value": "int"},
    "KillAction": {}, "SetPlacementAction": {}, "GiveMinigameItemAction": {},
    "ShowMessageAction": {"m_message": "text", "m_messageTarget": "message_target", "m_duration": "seconds"},
    "WaitAction": {"m_seconds": "interval"},
    "PositionAction": {"m_space": "space", "m_operation": "operation", "m_position": "vector"},
    "ScaleAction": {"m_operation": "operation", "m_scale": "vector"},
    "ModifyPlayerAction": {"m_playerActionType": "player_mode", "m_movementSpeed": "positive", "m_gravity": "vector"},
    "StunPlayerAction": {"m_stunDuration": "seconds"},
    "SpawnEffectAction": {"m_effectType": "effect"},
    "PlaySoundAction": {"m_volume": "volume"},
    "ChangeVelocityAction": {"m_speed": "positive", "m_customDir": "vector",
        "m_operation": "operation", "m_space": "space", "m_localTarget": "local_target",
        "m_velocityDir": "velocity_direction"},
    "SetPlayerVisualAction": {"m_recolorPrefabUsingPlayerColor": "bool",
        "m_verticalLookTargetChildIndex": "player_visual_index", "m_movementRotationTargetChildIndex": "player_visual_index"},
    "SpawnPrefabAction": {"m_spawnAtPosition": "bool", "m_position": "vector", "m_rotation": "vector",
        "m_parentToTarget": "bool", "m_targetPositionOffset": "vector", "m_targetRotationOffset": "vector"},
}
ACTION_TYPES = {"WaitAction": 32, "KillAction": 64, "ChangeHealthAction": 96, "ChangeScoreAction": 128,
    "SpawnEffectAction": 160, "ScaleAction": 256, "SetPlacementAction": 480, "ModifyPlayerAction": 512,
    "PlaySoundAction": 544, "SpawnPrefabAction": 576, "ChangeVelocityAction": 832,
    "PositionAction": 384, "ShowMessageAction": 1024, "StunPlayerAction": 1088,
    "SetActiveAction": 320, "SetPlayerVisualAction": 1248, "GiveMinigameItemAction": 1056}
GUID_RE = re.compile(rb"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
SAFE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def fail(message):
    raise ValueError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def keys(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        fail(f"INVALID_FIELDS: required={sorted(required)}, optional={sorted(optional)}")


def safe_name(value):
    if not isinstance(value, str) or not SAFE.fullmatch(value) or value.upper() in RESERVED:
        fail("UNSAFE_NAME: use an ASCII letter followed by letters, digits, _ or -")
    return value


def contained(path, roots):
    resolved = Path(path).resolve(strict=True)
    if not any(resolved.is_relative_to(Path(root).resolve(strict=True)) for root in roots):
        fail("SOURCE_OUTSIDE_READ_ROOTS")
    return resolved


def parse_single_timer(raw):
    """Only the observed single, version-2 ModLogic Timer array is authorable."""
    if raw[:4] != struct.pack("<I", 1): fail("ONLY_ONE_LOGIC_TIMER_SUPPORTED")
    parsed = parse_action_payload(raw[4:])
    if (not parsed.fully_consumed or parsed.prefix_u16 != 96 or parsed.m_type != 96
            or parsed.references_version != 2
            or set(parsed.root_json) != {"m_type", "m_actions", "m_interval", "references"}):
        fail("UNSUPPORTED_LOGIC_TIMER_TEMPLATE")
    return parsed


class Donor:
    """Bounded ZIP reader; no extraction, scripts, or path interpretation by OS."""
    def __init__(self, path, read_roots):
        self.path = contained(path, read_roots)
        if self.path.stat().st_size > 256 * 1024 * 1024:
            fail("ARCHIVE_TOO_LARGE")
        raw = self.path.read_bytes()
        if len(raw) > 256 * 1024 * 1024:
            fail("ARCHIVE_TOO_LARGE")
        self.digest = sha(raw)
        import io
        self.files = {}
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            if len(infos) > 5000 or sum(i.file_size for i in infos) > 512 * 1024 * 1024:
                fail("ARCHIVE_LIMIT_EXCEEDED")
            seen = set()
            for info in infos:
                name = info.orig_filename
                parts = PurePosixPath(name).parts
                if (not parts or name.startswith("/") or "\\" in name or any(ch in name for ch in ':<>"|?*\x00')
                        or any(p in ("..", ".") or p.endswith((".", " ")) or p.split('.')[0].upper() in RESERVED for p in parts)
                        or stat.S_ISLNK(info.external_attr >> 16)):
                    fail("UNSAFE_ARCHIVE_MEMBER")
                if info.is_dir():
                    continue
                if name.casefold() in seen:
                    fail("DUPLICATE_ARCHIVE_MEMBER")
                seen.add(name.casefold())
                self.files[name] = archive.read(info)
        scenes = [n for n in self.files if n.endswith("/Data/MainScene.scene")]
        if len(scenes) != 1:
            fail("ARCHIVE_REQUIRES_ONE_MAIN_SCENE")
        self.prefix = scenes[0][:-len("Data/MainScene.scene")]
        if any(not n.startswith(self.prefix) for n in self.files):
            fail("MULTIPLE_ARCHIVE_ROOTS")
        self.files = {n[len(self.prefix):]: b for n, b in self.files.items()}
        self.models, self.actions, self.asset_meta = {}, {}, {}
        self.internal_ids = set()
        for name, data in self.files.items():
            if name.endswith((".scene", ".pfab")):
                model = (PMHReader().read_bytes(data) if name.endswith(".scene") else PMHReader().read_prefab_bytes(data))
                if not model.fully_consumed or not validate_scene(model).passed:
                    fail("INVALID_DONOR_PMH: " + name)
                self.models[name] = model
                for obj in model.walk():
                    self.internal_ids.add(obj.guid.lower())
                    for component in obj.components:
                        self.internal_ids.add(component.guid.lower())
                        for field in component.fields:
                            if field.name in EVENTS.get(component.type_name, ()):
                                parsed = parse_action_payload(field.raw)
                                for ref in parsed.references:
                                    self.actions.setdefault(ref.type_info.class_name, copy.deepcopy(dict(ref.raw_refid)))
                            elif component.type_name == "ModLogic" and field.name == "Triggers":
                                try:
                                    timer = parse_single_timer(field.raw)
                                except ValueError:
                                    continue
                                for ref in timer.references:
                                    self.actions.setdefault(ref.type_info.class_name, copy.deepcopy(dict(ref.raw_refid)))
            elif name.endswith(".pmeta"):
                meta = json.loads(data)
                guid = str(uuid.UUID(meta["guid"]["serializedGuid"]))
                if guid in self.asset_meta or name[:-6] not in self.files:
                    fail("INVALID_ASSET_METADATA")
                self.asset_meta[guid] = (name[:-6], meta)
        # Player-level action templates are stored in ModSettings JSON.
        settings = json.loads(self.files["Data/ModSettings.json"])
        for ref in settings.get("references", {}).get("RefIds", []):
            self.actions.setdefault(ref["type"]["class"], copy.deepcopy(ref))

    def component(self, donor, kind):
        keys(donor, {"file", "object"})
        if donor["file"] not in self.models:
            fail("DONOR_FILE_NOT_FOUND")
        matches = [o for o in self.models[donor["file"]].walk() if donor["object"] in (o.guid, o.name, o.hierarchy_path)]
        if len(matches) != 1: fail("DONOR_OBJECT_MISSING_OR_AMBIGUOUS")
        return matches[0].get_component(kind)

    def audit(self):
        components, actions, records = Counter(), Counter(), []
        for name, model in self.models.items():
            local = Counter(c.type_name for o in model.walk() for c in o.components)
            components.update(local)
            for obj in model.walk():
                for component in obj.components:
                    for field in component.fields:
                        if field.name in EVENTS.get(component.type_name, ()):
                            actions.update(r.type_info.class_name for r in parse_action_payload(field.raw).references)
            records.append({"file": name, "objects": model.object_count, "components": dict(local)})
        return {"archive_sha256": self.digest, "files": len(self.files), "pmh_files": records,
                "components": dict(components), "actions": dict(actions),
                "available_action_templates": sorted(self.actions),
                "authorable_components": sorted(COMPONENTS | {"ModTransform"}),
                "authorable_actions": sorted(set(self.actions) & ACTION_FIELDS.keys()),
                "runtime_status": "NOT_RUN", "evidence": "SOURCE_BACKED_EXPERIMENTAL"}


def validate_action_value(kind, value):
    enums = {"operation": range(5), "space": range(2), "message_target": (1, 2, 3), "player_mode": (0, 1), "effect": range(5), "active_state": (0, 1),
             "local_target": range(3), "velocity_direction": range(9)}
    if kind in enums:
        if type(value) is not int or value not in enums[kind]: fail("INVALID_ACTION_ENUM")
    elif kind == "bool":
        if type(value) is not bool: fail("INVALID_ACTION_BOOL")
    elif kind == "int":
        if type(value) is not int or not -10000 <= value <= 10000: fail("INVALID_ACTION_INTEGER")
    elif kind == "player_visual_index":
        if type(value) is not int or not -1 <= value <= 1024: fail("INVALID_PLAYER_VISUAL_CHILD_INDEX")
    elif kind == "text":
        if not isinstance(value, str) or not 1 <= len(value) <= 1024: fail("INVALID_ACTION_TEXT")
    elif kind == "vector":
        Vector3Float32.encode(value)
        if any(abs(v) > 10000 for v in value.values()): fail("ACTION_VECTOR_OUT_OF_RANGE")
    elif kind == "interval":
        keys(value, {"valueType", "min", "max"})
        if type(value["valueType"]) is not int or value["valueType"] not in (0, 1): fail("INVALID_INTERVAL_MODE")
        validate_action_value("seconds", value["min"]); validate_action_value("seconds", value["max"])
        if value["max"] < value["min"]: fail("INVALID_INTERVAL_RANGE")
    else:
        upper = 1 if kind == "volume" else 3600
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= upper:
            fail("INVALID_ACTION_NUMBER")


def action_document(actions, donor, prefabs, object_targets=None):
    if not isinstance(actions, list) or len(actions) > 64: fail("INVALID_ACTION_LIST")
    refs = []
    for index, action in enumerate(actions):
        keys(action, {"type"}, {"target", "fields", "prefabs", "prefab", "targets"})
        kind = action["type"]
        if type(kind) is not str or kind not in ACTION_FIELDS or kind not in donor.actions: fail("ACTION_TEMPLATE_UNAVAILABLE: " + str(kind))
        ref = copy.deepcopy(donor.actions[kind]); data = ref["data"]
        expected_version = 1 if kind == "SetPlayerVisualAction" else 0
        if (ref["type"] != {"class": kind, "ns": "ModSystem.Logic", "asm": "Assembly-CSharp"}
                or data.get("m_type") != ACTION_TYPES[kind] or data.get("m_version") != expected_version):
            fail("UNSUPPORTED_ACTION_TEMPLATE_IDENTITY")
        ref["rid"] = 1000 + index
        if kind == "SetActiveAction":
            if action.get("target") != "objects" or not isinstance(object_targets, dict): fail("OBJECT_TARGET_CONTEXT_REQUIRED")
            names = action.get("targets")
            if not isinstance(names, list) or not 1 <= len(names) <= 64 or any(type(n) is not str or n not in object_targets for n in names):
                fail("UNBOUND_OBJECT_TARGET")
            candidates = data.get("m_targets", [])
            if not candidates: fail("SET_ACTIVE_REFERENCE_TEMPLATE_REQUIRED")
            reference = copy.deepcopy(candidates[0])
            if (set(reference) != {"m_assetGUID", "m_asset"} or set(reference["m_asset"]) != {"m_FileID", "m_PathID"}
                    or reference["m_asset"]["m_PathID"] != 0 or type(reference["m_asset"]["m_FileID"]) is not int):
                fail("UNSUPPORTED_COMPONENT_REFERENCE_ENVELOPE")
            original = reference["m_assetGUID"]["serializedGuid"]
            if not any(c.type_name == "ModTransform" and c.guid == original for m in donor.models.values() for o in m.walk() for c in o.components):
                fail("SET_ACTIVE_DONOR_TARGET_NOT_TRANSFORM")
            data["m_targetFlags"] = 4
            data["m_targets"] = []
            for name in names:
                bound = copy.deepcopy(reference)
                bound["m_assetGUID"]["serializedGuid"] = object_targets[name]
                data["m_targets"].append(bound)
            if set(action.get("fields", {})) != {"m_newState"}: fail("EXPLICIT_SET_ACTIVE_STATE_REQUIRED")
        elif data.get("m_targets") or "targets" in action:
            fail("ADVANCED_TARGET_TEMPLATE_UNSUPPORTED")
        elif "m_targetFlags" in data:
            target = action.get("target")
            if target not in ("source", "receiver", "both", "none"): fail("EXPLICIT_ACTION_TARGET_REQUIRED")
            data["m_targetFlags"] = {"source": 1, "receiver": 2, "both": 3, "none": 0}[target]
        elif "target" in action: fail("ACTION_HAS_NO_TARGET")
        fields = action.get("fields", {})
        if not isinstance(fields, dict) or set(fields) - ACTION_FIELDS[kind].keys(): fail("UNSUPPORTED_ACTION_FIELDS")
        for key, value in fields.items():
            validate_action_value(ACTION_FIELDS[kind][key], value)
            data[key] = value
        if data.get("m_operation") == 4 and data.get("m_value") == 0: fail("DIVISION_BY_ZERO")
        if kind == "SpawnPrefabAction":
            names = action.get("prefabs")
            if not isinstance(names, list) or not 1 <= len(names) <= 128: fail("PREFAB_POOL_REQUIRED")
            if any(type(n) is not str or n not in prefabs for n in names): fail("UNBOUND_PREFAB")
            # Duplicate symbols are intentional equal-probability slots (weights).
            data["m_prefabs"] = [{"m_assetGUID": {"serializedGuid": prefabs[n]["guid"]["serializedGuid"]},
                                  "m_asset": copy.deepcopy(prefabs[n])} for n in names]
        elif "prefabs" in action: fail("PREFABS_REQUIRE_SPAWN_ACTION")
        if kind == "SetPlayerVisualAction":
            if "prefab" not in action:
                fail("PLAYER_VISUAL_PREFAB_BINDING_REQUIRED")
            visual = action["prefab"]
            if visual is None:
                data["m_prefab"] = {"m_assetGUID": {"serializedGuid": "00000000-0000-0000-0000-000000000000"},
                                    "m_asset": {"m_FileID": 0, "m_PathID": 0}}
            elif type(visual) is str and visual in prefabs:
                data["m_prefab"] = {"m_assetGUID": {"serializedGuid": prefabs[visual]["guid"]["serializedGuid"]},
                                    "m_asset": copy.deepcopy(prefabs[visual])}
            else:
                fail("UNBOUND_PLAYER_VISUAL_PREFAB")
        elif kind == "GiveMinigameItemAction":
            name = action.get("prefab")
            if type(name) is not str or name not in prefabs:
                fail("UNBOUND_ITEM_PREFAB")
            data["m_item"] = {"m_assetGUID": {"serializedGuid": prefabs[name]["guid"]["serializedGuid"]},
                              "m_asset": copy.deepcopy(prefabs[name])}
        elif "prefab" in action:
            fail("PREFAB_BINDING_REQUIRES_PLAYER_VISUAL_ACTION")
        refs.append(ref)
    return {"m_type": 0, "m_actions": [{"rid": r["rid"]} for r in refs], "references": {"version": 2, "RefIds": refs}}


def frame_actions(document, *, prefix_type=0):
    # Native OnEnableTrigger (40) uses the same MODLOGICTRIGGER framing.
    if prefix_type not in (0, 40, 96) or document["m_type"] != prefix_type: fail("UNSUPPORTED_ACTION_FRAME_TYPE")
    root = canonical(document)
    # ModReflectionTypes loads the following binary array as Actions, replacing
    # the JSON m_actions array. Registry order is not execution order.
    registry = {ref["rid"]: ref for ref in document["references"]["RefIds"]}
    refs = [registry[entry["rid"]] for entry in document["m_actions"]]
    tails = []
    for ref in refs:
        raw = canonical(ref["data"])
        tails.append(struct.pack("<H", ref["data"]["m_type"]) + encode_action_varuint7(len(raw)) + raw)
    result = struct.pack("<H", prefix_type) + encode_action_varuint7(len(root)) + root + struct.pack("<H", len(tails)) + b"".join(tails)
    parsed = parse_action_payload(result)
    if not parsed.fully_consumed or parsed.dangling_rids or parsed.duplicate_rids or parsed.reference_cycles:
        fail("INVALID_COMPILED_ACTION_GRAPH")
    return result


def encode_scene(nodes, prefab=False):
    """Encode complete observed PMH grammar in hierarchy/index/payload order."""
    by_id = {node["id"]: node for node in nodes}
    children = {node["id"]: [] for node in nodes}; roots = []
    for node in nodes:
        parent = node.get("parent")
        if parent is None: roots.append(node)
        elif parent not in by_id: fail("UNKNOWN_PARENT")
        else: children[parent].append(node)
    if not roots or prefab and len(roots) != 1: fail("INVALID_ROOT_COUNT")
    ordered, visiting = [], set()
    def walk(node):
        if node["id"] in visiting: fail("HIERARCHY_CYCLE")
        visiting.add(node["id"]); ordered.append(node)
        for child in children[node["id"]]: walk(child)
    for root in roots: walk(root)
    if len(ordered) != len(nodes): fail("HIERARCHY_CYCLE")
    s = Utf8String.encode
    header = b"\x03PMH" + struct.pack("<I", 1) + (b"" if prefab else struct.pack("<H", len(roots)))
    hierarchy = b"".join(s(n["name"]) + bytes([n["active"]]) + struct.pack("<i", n["layer"]) + s(n["tag"]) + struct.pack("<H", len(children[n["id"]])) for n in ordered)
    indices, payloads = [], []
    for node in ordered:
        cs = node["components"]
        indices.append(s(node["guid"]) + struct.pack("<I", len(cs)) + b"".join(s(c["type"]) + s(c["guid"]) + b"\x01" for c in cs))
        for c in cs:
            payloads.append(struct.pack("<H", len(c["fields"])) + b"".join(s(k) + struct.pack("<I", len(v)) + v for k, v in c["fields"].items()))
    result = header + hierarchy + b"".join(indices) + b"".join(payloads)
    model = (PMHReader().read_prefab_bytes if prefab else PMHReader().read_bytes)(result)
    if not model.fully_consumed or not validate_scene(model).passed: fail("COMPILED_SCENE_INVALID")
    return result


class Compiler:
    def __init__(self, donor, spec):
        self.donor, self.spec = donor, spec
        self.namespace = uuid.uuid5(uuid.NAMESPACE_URL, donor.digest + sha(canonical(spec)))
        self.asset_remap = {}
        for guid, (name, _) in donor.asset_meta.items():
            if not name.endswith(".pfab"): self.asset_remap[guid] = self.guid("asset:" + name)
        self.prefabs, self.files = {}, {}

    def guid(self, name):
        return str(uuid.uuid5(self.namespace, name))

    def remap(self, raw):
        return GUID_RE.sub(lambda m: self.asset_remap.get(m.group().decode().lower(), m.group().decode()).encode(), raw)

    def compile_nodes(self, nodes, scope):
        if not isinstance(nodes, list) or not 1 <= len(nodes) <= 512: fail("INVALID_NODE_LIST")
        object_targets = {safe_name(n["id"]): self.guid(scope + ":" + n["id"]) for n in nodes}
        result, used = [], set()
        for node in nodes:
            keys(node, {"id", "components"}, {"name", "parent", "position", "rotation", "scale", "active", "layer", "tag"})
            symbol = safe_name(node["id"])
            if symbol in used: fail("DUPLICATE_NODE_ID")
            used.add(symbol)
            identity = self.guid(scope + ":" + symbol)
            transform = {key: Vector3Float32.encode(node.get(key, dict.fromkeys(("x", "y", "z"), default))) for key, default in (("position", 0), ("rotation", 0), ("scale", 1))}
            if any(v <= 0 for v in struct.unpack("<fff", transform["scale"])): fail("NONPOSITIVE_SCALE")
            transform["guid"] = Utf8String.encode(identity)
            components = [{"type": "ModTransform", "guid": identity, "fields": transform}]
            if not isinstance(node["components"], list) or len(node["components"]) > 8: fail("INVALID_COMPONENT_LIST")
            types = set()
            for component in node["components"]:
                keys(component, {"type", "donor"}, {"properties", "events", "item_visual", "logic_timer"})
                kind = component["type"]
                if type(kind) is not str or kind not in COMPONENTS or kind in types: fail("UNSUPPORTED_OR_DUPLICATE_COMPONENT")
                types.add(kind)
                source = self.donor.component(component["donor"], kind)
                fields = {f.name: f.raw for f in source.fields}
                component_guid = self.guid(scope + ":" + symbol + ":" + kind)
                fields["guid"] = Utf8String.encode(component_guid)
                properties = component.get("properties", {})
                if not isinstance(properties, dict): fail("INVALID_PROPERTIES")
                for key, value in properties.items():
                    schema = get_property_schema(kind, key)
                    if kind == "ModText" and key == "Text" and key in fields:
                        fields[key] = Utf8String.encode(value)
                        continue
                    if key not in fields or schema is None or schema.codec is None or not schema.writable:
                        fail("UNSUPPORTED_COMPONENT_PROPERTY: " + kind + "." + key)
                    fields[key] = schema.codec.patch(fields[key], value).raw
                events = component.get("events", {})
                if not isinstance(events, dict) or set(events) - set(EVENTS.get(kind, ())): fail("UNSUPPORTED_EVENT")
                for event in EVENTS.get(kind, ()):
                    if event not in fields: fail("DONOR_EVENT_MISSING")
                    fields[event] = frame_actions(action_document(events.get(event, []), self.donor, self.prefabs, object_targets))
                    if kind == "ModTrigger": fields[event.replace("Actions", "").replace("On", "TriggerOn", 1)] = bytes([bool(events.get(event))])
                if kind == "ModTrigger" and events.get("OnHitActions") and any(events.get(e) for e in EVENTS[kind][1:]):
                    fail("HIT_AND_COLLISION_EVENTS_ARE_EXCLUSIVE_IN_ENGINE")
                if kind == "ModLogic":
                    if any(c["type"] == "ModTrigger" for c in node["components"]): fail("LOGIC_AND_TRIGGER_CANNOT_SHARE_OBJECT")
                    timer = component.get("logic_timer")
                    keys(timer, {"interval_seconds", "actions"})
                    original = parse_single_timer(fields["Triggers"])
                    seconds = timer["interval_seconds"]
                    validate_action_value("seconds", seconds)
                    if seconds < .1: fail("TIMER_INTERVAL_TOO_SHORT")
                    doc = action_document(timer["actions"], self.donor, self.prefabs, object_targets)
                    doc["m_type"] = original.m_type
                    doc["m_interval"] = {"valueType": 0, "min": seconds, "max": seconds}
                    fields["Triggers"] = struct.pack("<I", 1) + frame_actions(doc, prefix_type=96)
                    parse_single_timer(fields["Triggers"])
                elif "logic_timer" in component: fail("TIMER_REQUIRES_MODLOGIC")
                if kind == "ModItem":
                    visual = component.get("item_visual")
                    if visual not in self.prefabs: fail("ITEM_VISUAL_BINDING_REQUIRED")
                    guid = self.prefabs[visual]["guid"]["serializedGuid"]
                    fields["ItemPrefab"] = b"\x01" + Utf8String.encode(guid)
                    fields["ItemPrefabGUID"] = Utf8String.encode(guid)
                    if fields.get("DroppedItemPrefab") != b"\x00": fail("CUSTOM_DROP_PREFAB_NOT_SUPPORTED")
                elif "item_visual" in component: fail("ITEM_VISUAL_REQUIRES_ITEM")
                # No inherited reference may retain an identity belonging to the donor scene.
                for key, raw in fields.items():
                    if key == "guid": continue
                    refs = {m.group().decode().lower() for m in GUID_RE.finditer(raw)}
                    if refs & self.donor.internal_ids: fail("UNBOUND_DONOR_OBJECT_REFERENCE")
                    old_prefabs = {g for g, (n, _) in self.donor.asset_meta.items() if n.endswith(".pfab")}
                    if refs & old_prefabs: fail("UNBOUND_DONOR_PREFAB_REFERENCE")
                components.append({"type": kind, "guid": component_guid, "fields": {k: self.remap(v) for k, v in fields.items()}})
            active, layer, tag = node.get("active", True), node.get("layer", 0), node.get("tag", "Untagged")
            if type(active) is not bool or type(layer) is not int or not 0 <= layer <= 31 or not isinstance(tag, str): fail("INVALID_NODE_FLAGS")
            result.append({"id": symbol, "parent": node.get("parent"), "name": node.get("name", symbol), "guid": identity,
                           "active": active, "layer": layer, "tag": tag, "components": components})
        return result

    def compile(self):
        spec = self.spec
        keys(spec, {"version", "output_mod", "title", "description", "min_players", "max_players", "settings", "scene", "prefabs"}, {"player_events", "player_tick_seconds"})
        if spec["version"] != "0.3": fail("UNSUPPORTED_AUTHORING_VERSION")
        safe_name(spec["output_mod"])
        if not isinstance(spec["prefabs"], list) or len(spec["prefabs"]) > 128: fail("INVALID_PREFABS")
        meta_templates = [meta for _, (path, meta) in self.donor.asset_meta.items() if path.endswith(".pfab")]
        if spec["prefabs"] and not meta_templates: fail("PREFAB_METADATA_TEMPLATE_MISSING")
        for prefab in spec["prefabs"]:
            keys(prefab, {"id", "nodes"})
            name = safe_name(prefab["id"])
            if name.casefold() in {n.casefold() for n in self.prefabs}: fail("DUPLICATE_PREFAB_ID")
            meta = copy.deepcopy(meta_templates[0]); meta["name"] = name
            meta["guid"]["serializedGuid"] = self.guid("prefab:" + name)
            self.prefabs[name] = meta
        # Asset recursion is unsupported. Detect it before serializing any file.
        dependencies = {p["id"]: set() for p in spec["prefabs"]}
        for prefab in spec["prefabs"]:
            for node in prefab["nodes"]:
                for component in node["components"]:
                    if "item_visual" in component: dependencies[prefab["id"]].add(component["item_visual"])
                    for actions in component.get("events", {}).values():
                        for action in actions:
                            dependencies[prefab["id"]].update(action.get("prefabs", []))
                            if isinstance(action.get("prefab"), str):
                                dependencies[prefab["id"]].add(action["prefab"])
        complete, active = set(), set()
        def visit(name):
            if name not in dependencies: fail("UNBOUND_PREFAB")
            if name in active: fail("PREFAB_DEPENDENCY_CYCLE")
            if name in complete: return
            active.add(name)
            for child in dependencies[name]: visit(child)
            active.remove(name); complete.add(name)
        for name in dependencies: visit(name)
        for scope in [spec["scene"], *(p["nodes"] for p in spec["prefabs"])]:
            for node in scope:
                for component in node["components"]:
                    for actions in component.get("events", {}).values():
                        for action in actions:
                            if action.get("type") == "GiveMinigameItemAction":
                                item = next((p for p in spec["prefabs"] if p["id"] == action.get("prefab")), None)
                                root = next((n for n in item["nodes"] if n.get("parent") is None), None) if item else None
                                if root is None or not any(c["type"] == "ModItem" for c in root["components"]):
                                    fail("GIVE_ITEM_REQUIRES_MODITEM_ON_PREFAB_ROOT")
        for prefab in spec["prefabs"]:
            name = prefab["id"]
            self.files[f"Assets/Prefabs/{name}.pfab"] = encode_scene(self.compile_nodes(prefab["nodes"], "prefab:" + name), True)
            self.files[f"Assets/Prefabs/{name}.pfab.pmeta"] = canonical(self.prefabs[name])
        scene_nodes = self.compile_nodes(spec["scene"], "scene")
        if not any(c["type"] == "ModPlayerSpawn" for n in scene_nodes for c in n["components"]): fail("PLAYER_SPAWN_REQUIRED")
        self.files["Data/MainScene.scene"] = encode_scene(scene_nodes)
        # Copy visual/audio resources only, never the donor scene or Prefab logic.
        allowed_assets = {".png", ".jpg", ".jpeg", ".ogg", ".wav", ".obj", ".mtl", ".pmat", ".fbx"}
        for name, raw in self.donor.files.items():
            if name.startswith("Assets/") and "/Prefabs/" not in name:
                base = name[:-6] if name.endswith(".pmeta") else name
                if Path(base).suffix.lower() not in allowed_assets: continue
                if name.endswith(".pmeta"):
                    meta = json.loads(self.remap(raw))
                    # PMAT shader data encodes texture GUIDs as ASCII bytes
                    # inside a JSON integer array. Remap that nested payload
                    # as well as the metadata object's plain GUID strings.
                    if "m_shaderData" in meta:
                        meta["m_shaderData"] = list(self.remap(bytes(meta["m_shaderData"])))
                    self.files[name] = canonical(meta)
                else:
                    self.files[name] = self.remap(raw) if name.endswith((".pmat", ".mtl")) else raw
        for name in ("Lighting.json", "PostProcessing.data", "Preview.jpg", "ModSettings.json", "MinigameDefinitionData.json", "Meta.json"):
            path = "Data/" + name
            if path in self.donor.files: self.files[path] = self.remap(self.donor.files[path])
        settings = json.loads(self.files["Data/ModSettings.json"])
        # Do not silently inherit player-level gameplay from the donor.
        for value in settings.get("SimpleModPlayerSettings", {}).values():
            if isinstance(value, dict) and "m_actions" in value: value["m_actions"] = []
        settings["references"] = {"version": 2, "RefIds": []}
        player_events = spec.get("player_events", {})
        if not isinstance(player_events, dict) or set(player_events) - PLAYER_EVENTS: fail("UNSUPPORTED_PLAYER_EVENT")
        next_rid = 1000
        for event, actions in player_events.items():
            if event not in settings["SimpleModPlayerSettings"]: fail("DONOR_PLAYER_EVENT_MISSING")
            document = action_document(actions, self.donor, self.prefabs)
            entries = []
            for ref in document["references"]["RefIds"]:
                ref["rid"] = next_rid
                entries.append({"rid": next_rid}); next_rid += 1
                settings["references"]["RefIds"].append(ref)
            settings["SimpleModPlayerSettings"][event]["m_actions"] = entries
        if "player_tick_seconds" in spec:
            value = spec["player_tick_seconds"]
            validate_action_value("seconds", value)
            if value < .1: fail("PLAYER_TICK_INTERVAL_TOO_SHORT")
            settings["SimpleModPlayerSettings"]["tickTrigger"]["m_interval"] = {"valueType": 0, "min": value, "max": value}
        self.files["Data/ModSettings.json"] = self.remap(canonical(settings))
        meta = json.loads(self.files["Data/Meta.json"]); meta["Name"] = spec["title"]
        self.files["Data/Meta.json"] = canonical(meta)
        # Required even for local editing. Generate a fresh unpublished record
        # rather than inheriting a donor's Workshop publishing identity.
        self.files["Data/WorkshopItem.json"] = canonical({
            "Version": 0, "title": spec["title"], "description": spec["description"],
            "language": "english", "metaData": "", "visibility": 2,
            "tags": ["Minigame"], "isInternal": False, "previewFiles": [],
            "publishedFileId": 256,
            "previewImageGuid": {"serializedGuid": "00000000-0000-0000-0000-000000000000"},
        })
        details = {"name": spec["title"], "description": spec["description"], "min_players": spec["min_players"], "max_players": spec["max_players"]}
        for kind, name, updates in (("settings", "ModSettings.json", spec["settings"]), ("details", "MinigameDefinitionData.json", details)):
            self.files["Data/" + name], _ = _patched(self.files["Data/" + name], kind, updates)
        if not isinstance(spec["settings"], dict) or not {"rounds", "round_duration_seconds", "end_conditions", "placement_condition"} <= spec["settings"].keys():
            fail("EXPLICIT_ROUNDS_DURATION_ENDING_PLACEMENT_REQUIRED")
        if "timer" not in spec["settings"]["end_conditions"]:
            fail("AUTHORING_REQUIRES_TIMER_FALLBACK")
        self.files["authoring-spec.json"] = canonical(spec)
        # New object identities must be unique across every generated PMH file.
        identities = set()
        for name, raw in self.files.items():
            if not name.endswith((".scene", ".pfab")): continue
            model = (PMHReader().read_bytes if name.endswith(".scene") else PMHReader().read_prefab_bytes)(raw)
            for obj in model.walk():
                own = [obj.guid] + [c.guid for c in obj.components if c.type_name != "ModTransform"]
                if len(set(own)) != len(own) or identities.intersection(own): fail("DUPLICATE_COMPILED_IDENTITY")
                identities.update(own)
        return self.files


def authoring_capabilities():
    return {"version": "0.3", "components": sorted(COMPONENTS | {"ModTransform"}),
            "actions": ACTION_FIELDS, "events": EVENTS, "player_events": sorted(PLAYER_EVENTS),
            "bindings": ["Item -> visual Prefab", "SpawnPrefabAction -> weighted Prefab pool", "SetPlayerVisualAction -> local Prefab or null/native visual", "PositionAction -> world/local player position", "local visual/audio asset remapping", "SetActiveAction -> local Transform objects"],
            "scene": ["new hierarchy", "component composition from donor defaults", "new identities", "transform", "colliders", "lights", "spawn points"],
            "unsupported": ["arbitrary scripts/variables/conditions", "explicit component targets except local Transform SetActive", "arbitrary component types", "new artwork without asset donors", "automatic game playtest"],
            "spec_contract": {
                "required": {"version": "0.3", "output_mod": "ASCII identifier", "title": "string", "description": "string",
                    "min_players": "1..8", "max_players": "1..8", "settings": "existing minigame settings API; rounds, round_duration_seconds, end_conditions including timer, placement_condition required",
                    "scene": "node[]", "prefabs": "[{id, nodes: node[]}]"},
                "optional": {"player_events": "player event-name -> action[]; merged into one managed-reference table", "player_tick_seconds": "0.1..3600"},
                "node": {"id": "unique ASCII identifier", "components": "component[]", "optional": ["name", "parent", "position", "rotation", "scale", "active", "layer", "tag"]},
                "component": {"type": "supported component", "donor": {"file": "archive-relative .scene/.pfab", "object": "unique object GUID/name/path"},
                    "optional": {"properties": "existing writable component fields", "events": "event-name -> action[]", "item_visual": "required prefab id for ModItem", "logic_timer": "required for ModLogic: {interval_seconds: 0.1..3600, actions: action[]}; one source-backed version-2 Timer; cannot share an object with ModTrigger"}},
                "action": {"type": "Action class", "target": "source|receiver|both|none; objects for SetActiveAction; omit for WaitAction", "fields": "typed fields listed in actions",
                    "targets": "SetActiveAction only: local node-id[]; m_newState 0=enabled, 1=disabled; modern source-backed Transform reference required",
                    "prefabs": "required prefab-id[] for SpawnPrefabAction; duplicate slots express random weights",
                    "prefab": "SetPlayerVisualAction: local prefab-id or null/native; GiveMinigameItemAction: local prefab-id with ModItem on root"}},
            "runtime_status": "NOT_RUN", "evidence": "SOURCE_BACKED_EXPERIMENTAL"}


def plan_authoring(archive_path, read_roots, output_root, spec, *, allow_output_in_read_roots=False):
    root = Path(output_root).resolve(strict=True)
    # An explicitly authorized WorkshopMods root may also contain read-only donor
    # Mods. Generated destinations remain fresh, distinct child directories.
    if not allow_output_in_read_roots and any(root.is_relative_to(Path(r).resolve(strict=True)) for r in read_roots):
        fail("OUTPUT_INSIDE_READ_ONLY_ROOT")
    donor = Donor(archive_path, read_roots)
    compiler = Compiler(donor, spec); files = compiler.compile()
    output = root / safe_name(spec["output_mod"])
    if output.exists() or output.is_symlink(): fail("OUTPUT_ALREADY_EXISTS")
    manifest = {n: sha(b) for n, b in sorted(files.items())}
    digest = sha(canonical({"archive": donor.digest, "spec": spec, "output": str(output), "files": manifest}))
    return {"status": "EXPERIMENTAL_READY", "plan_sha256": digest, "archive_sha256": donor.digest,
            "output_mod": str(output), "file_sha256": manifest, "objects": len(spec["scene"]), "prefabs": len(spec["prefabs"]),
            "runtime_status": "NOT_RUN", "completion": "AUTHORED_UNVERIFIED",
            "limitations": ["Donor component defaults and built-in assets retained; engine asset loading not verified",
                            "Visual/audio resources reused, not newly modeled", "No official Editor save/reload or multiplayer playtest"]}, files


def build_authoring(archive_path, read_roots, output_root, spec, expected_plan_sha256, *, allow_output_in_read_roots=False):
    plan, files = plan_authoring(archive_path, read_roots, output_root, spec,
                                 allow_output_in_read_roots=allow_output_in_read_roots)
    if plan["plan_sha256"] != expected_plan_sha256: fail("STALE_AUTHORING_PLAN")
    root = Path(output_root).resolve(strict=True)
    stage = Path(tempfile.mkdtemp(prefix=".authoring-", dir=root))
    try:
        for name, raw in files.items():
            path = stage / name
            if not path.resolve().is_relative_to(stage): fail("OUTPUT_PATH_ESCAPE")
            path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
        # Source hash is rechecked after all work, before publication.
        if sha(Path(archive_path).read_bytes()) != plan["archive_sha256"]: fail("DONOR_CHANGED_DURING_BUILD")
        output = Path(plan["output_mod"])
        if output.exists(): fail("OUTPUT_ALREADY_EXISTS")
        report = {**plan, "status": "BUILD_PASS", "file_sha256": {str(p.relative_to(stage)).replace('\\', '/'): sha(p.read_bytes()) for p in stage.rglob('*') if p.is_file()}}
        (stage / "authoring-report.json").write_bytes(canonical(report))
        os.rename(stage, output)
        return report
    finally:
        if stage.exists(): shutil.rmtree(stage)
