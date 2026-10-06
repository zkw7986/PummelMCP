from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from pummelmcp.pmh import (
    ACTION_EVENT_PROPERTIES,
    AssetResolutionError,
    REFERENCE_SCHEMAS,
    build_asset_catalog,
    find_mod_root,
    inspect_action_entry_references,
    parse_action_payload,
    read_pmh,
)


def _varuint7(value: int) -> bytes:
    result = bytearray()
    while value >= 0x80:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value)
    return bytes(result)


def _synthetic_action(class_name: str, data: dict[str, Any]):
    ref = {
        "rid": 1000,
        "type": {
            "class": class_name,
            "ns": "ModSystem.Logic",
            "asm": "Assembly-CSharp",
        },
        "data": data,
    }
    root = {
        "m_type": 0,
        "m_actions": [{"rid": 1000}],
        "references": {"version": 2, "RefIds": [ref]},
    }
    root_raw = json.dumps(root, separators=(",", ":")).encode()
    data_raw = json.dumps(data, separators=(",", ":")).encode()
    payload = (
        b"\x00\x00"
        + _varuint7(len(root_raw))
        + root_raw
        + b"\x01\x00"
        + int(data["m_type"]).to_bytes(2, "little")
        + _varuint7(len(data_raw))
        + data_raw
    )
    return parse_action_payload(payload).actions[0]


def _all_real_actions(path: Path):
    for obj in read_pmh(path).walk():
        for component in obj.components:
            if component.type_name != "ModTrigger":
                continue
            for field in component.fields:
                if field.name not in ACTION_EVENT_PROPERTIES:
                    continue
                yield from parse_action_payload(field.raw).actions


def _real_action(path: Path, class_name: str):
    return next(
        action
        for action in _all_real_actions(path)
        if action.type_info is not None and action.type_info.class_name == class_name
    )


def _field(inspection, name: str):
    return next(item for item in inspection.references if item.source_field == name)


def test_spawn_prefab_reference_list_detected(main_scene_path: Path) -> None:
    inspected = inspect_action_entry_references(_real_action(main_scene_path, "SpawnPrefabAction"))
    assert _field(inspected, "m_prefabs").kind == "PrefabReferenceList"


def test_spawn_prefab_reference_count(main_scene_path: Path) -> None:
    inspected = inspect_action_entry_references(_real_action(main_scene_path, "SpawnPrefabAction"))
    assert len(_field(inspected, "m_prefabs").items) == 23


def test_spawn_prefab_reference_item_shape(main_scene_path: Path) -> None:
    item = _field(
        inspect_action_entry_references(_real_action(main_scene_path, "SpawnPrefabAction")),
        "m_prefabs",
    ).items[0]
    assert set(item.raw_value) == {"m_assetGUID", "m_asset"}
    assert isinstance(item.normalized_value["guid"], str)
    assert item.normalized_value["guid"] == item.normalized_value["asset_guid"]


def test_spawn_prefab_reference_source_span(main_scene_path: Path) -> None:
    action = _real_action(main_scene_path, "SpawnPrefabAction")
    field = _field(inspect_action_entry_references(action), "m_prefabs")
    raw = action.resolved_reference.raw_segment
    assert raw[field.source_span.start : field.source_span.end].startswith(b"[")
    guid_span = field.items[0].value_spans["$.m_assetGUID.serializedGuid"]
    assert raw[guid_span.start : guid_span.end].startswith(b'"')


def test_spawn_prefab_reference_unknown_fields_preserved() -> None:
    data = {
        "m_type": 576,
        "m_prefabs": [{
            "m_assetGUID": {"serializedGuid": "g"},
            "m_asset": {"guid": {"serializedGuid": "g"}, "futureAsset": 9},
            "futureRoot": {"x": 1},
        }],
    }
    inspected = inspect_action_entry_references(_synthetic_action("SpawnPrefabAction", data))
    item = _field(inspected, "m_prefabs").items[0]
    assert item.unknown_fields == {
        "futureRoot": {"x": 1},
        "m_asset.unknown": {"futureAsset": 9},
    }
    assert item.raw_value == data["m_prefabs"][0]


def test_spawn_prefab_reference_order_preserved(main_scene_path: Path) -> None:
    action = _real_action(main_scene_path, "SpawnPrefabAction")
    field = _field(inspect_action_entry_references(action), "m_prefabs")
    assert [item.normalized_value["guid"] for item in field.items] == [
        item["m_assetGUID"]["serializedGuid"] for item in action.fields["m_prefabs"]
    ]


def test_spawn_prefab_reference_duplicate_preserved() -> None:
    item = {
        "m_assetGUID": {"serializedGuid": "same"},
        "m_asset": {"guid": {"serializedGuid": "same"}},
    }
    inspected = inspect_action_entry_references(
        _synthetic_action("SpawnPrefabAction", {"m_type": 576, "m_prefabs": [item, item]})
    )
    field = _field(inspected, "m_prefabs")
    assert len(field.items) == 2
    assert field.normalized_value["ordered_guids"] == ["same", "same"]
    assert field.normalized_value["duplicate_guids"] == ["same"]


def test_spawn_effect_reference_detected(main_scene_path: Path) -> None:
    inspected = inspect_action_entry_references(_real_action(main_scene_path, "SpawnEffectAction"))
    assert _field(inspected, "m_effectType").kind == "EffectIndexReference"


def test_spawn_effect_reference_fields(main_scene_path: Path) -> None:
    inspected = inspect_action_entry_references(_real_action(main_scene_path, "SpawnEffectAction"))
    assert {item.source_field for item in inspected.references} == {
        "m_targetFlags", "m_targets", "m_effectType"
    }
    assert _field(inspected, "m_effectType").normalized_value == {"raw_value": 3, "semantic": None}


def test_spawn_effect_unknown_fields_preserved() -> None:
    action = _synthetic_action(
        "SpawnEffectAction",
        {"m_type": 160, "m_effectType": 3, "futureRef": {"serializedGuid": "x", "z": 7}},
    )
    inspected = inspect_action_entry_references(action)
    unknown = _field(inspected, "futureRef")
    assert unknown.kind == "UnknownReference"
    assert unknown.raw_value == {"serializedGuid": "x", "z": 7}


def test_play_sound_reference_detected(main_scene_path: Path) -> None:
    inspected = inspect_action_entry_references(_real_action(main_scene_path, "PlaySoundAction"))
    assert _field(inspected, "m_clip").kind == "AudioReference"


def test_play_sound_reference_fields(main_scene_path: Path) -> None:
    inspected = inspect_action_entry_references(_real_action(main_scene_path, "PlaySoundAction"))
    clip = _field(inspected, "m_clip")
    assert clip.normalized_value == {
        "guid": "10a663ec-e461-4711-8db8-4aadbc86aeb5",
        "file_id": 107498,
        "path_id": 0,
    }


def test_audio_reference_read_only(main_scene_path: Path) -> None:
    clip = _field(
        inspect_action_entry_references(_real_action(main_scene_path, "PlaySoundAction")),
        "m_clip",
    )
    assert clip.writable is False
    assert clip.items[0].writable is False
    assert clip.evidence_status == "INFERRED"


def test_target_fields_detected(main_scene_path: Path) -> None:
    for class_name in ("SpawnPrefabAction", "SpawnEffectAction", "PlaySoundAction", "KillAction"):
        inspected = inspect_action_entry_references(_real_action(main_scene_path, class_name))
        expected = (
            "TransformReferenceList"
            if class_name == "SpawnPrefabAction"
            else "TargetSelectorList"
        )
        assert _field(inspected, "m_targets").kind == expected
        assert _field(inspected, "m_targetFlags").kind == "TargetFlags"


def test_target_flags_raw_value_preserved(main_scene_path: Path) -> None:
    action = _real_action(main_scene_path, "KillAction")
    flags = _field(inspect_action_entry_references(action), "m_targetFlags")
    assert flags.raw_value == action.fields["m_targetFlags"] == 1


def test_unknown_target_semantics_not_invented(main_scene_path: Path) -> None:
    inspected = inspect_action_entry_references(_real_action(main_scene_path, "KillAction"))
    assert _field(inspected, "m_targetFlags").normalized_value["semantic"] is None
    assert _field(inspected, "m_targetFlags").semantic_status == "UNKNOWN"
    assert _field(inspected, "m_targets").normalized_value["semantic"] is None


def test_target_reference_source_span(main_scene_path: Path) -> None:
    action = _real_action(main_scene_path, "KillAction")
    flags = _field(inspect_action_entry_references(action), "m_targetFlags")
    raw = action.resolved_reference.raw_segment
    assert raw[flags.source_span.start : flags.source_span.end] == b"1"


def _asset_mod(tmp_path: Path, main_scene_path: Path) -> tuple[Path, str]:
    mod = tmp_path / "mod"
    data = mod / "Data"
    prefabs = mod / "Assets" / "Prefabs"
    data.mkdir(parents=True)
    prefabs.mkdir(parents=True)
    scene = data / "MainScene.scene"
    scene.write_bytes(main_scene_path.read_bytes())
    guid = _real_action(main_scene_path, "SpawnPrefabAction").fields["m_prefabs"][0]["m_assetGUID"]["serializedGuid"]
    (prefabs / "Known.pfab").write_bytes(b"pfab-test")
    (prefabs / "Known.pfab.pmeta").write_text(
        json.dumps({"name": "Known", "guid": {"serializedGuid": guid}, "tags": ["prefabs"]}),
        encoding="utf-8",
    )
    return scene, guid


def test_reference_resolves_known_asset(tmp_path: Path, main_scene_path: Path) -> None:
    scene, guid = _asset_mod(tmp_path, main_scene_path)
    catalog, warnings = build_asset_catalog(scene.parents[1], tmp_path)
    action = _real_action(scene, "SpawnPrefabAction")
    item = _field(inspect_action_entry_references(action, asset_catalog=catalog), "m_prefabs").items[0]
    assert warnings == ()
    assert item.resolution.resolved is True
    assert item.resolution.guid == guid
    assert item.resolution.relative_path == "Assets/Prefabs/Known.pfab"


def test_unresolved_reference_is_valid(main_scene_path: Path) -> None:
    action = _real_action(main_scene_path, "PlaySoundAction")
    clip = _field(inspect_action_entry_references(action, asset_catalog={}), "m_clip")
    assert clip.items[0].resolution.resolved is False
    assert clip.items[0].resolution.status == "CONFIRMED"


def test_resolution_does_not_guess(tmp_path: Path, main_scene_path: Path) -> None:
    scene, _ = _asset_mod(tmp_path, main_scene_path)
    catalog, _ = build_asset_catalog(scene.parents[1], tmp_path)
    action = _synthetic_action(
        "SpawnPrefabAction",
        {"m_type": 576, "m_prefabs": [{
            "m_assetGUID": {"serializedGuid": "not-the-guid"},
            "m_asset": {"guid": {"serializedGuid": "not-the-guid"}, "name": "Known"},
        }]},
    )
    item = _field(inspect_action_entry_references(action, asset_catalog=catalog), "m_prefabs").items[0]
    assert item.resolution.resolved is False
    assert item.resolution.relative_path is None


def test_reference_path_cannot_escape_allowed_root(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    (allowed / "Assets").mkdir(parents=True)
    (outside / "Assets").mkdir(parents=True)
    scene = outside / "Main.scene"
    scene.write_bytes(b"x")
    with pytest.raises(AssetResolutionError, match="outside the allowed root"):
        find_mod_root(scene, allowed)
    with pytest.raises(AssetResolutionError, match="outside the allowed root"):
        build_asset_catalog(outside, allowed)


def test_missing_asset_returns_unresolved(main_scene_path: Path) -> None:
    action = _real_action(main_scene_path, "SpawnPrefabAction")
    item = _field(inspect_action_entry_references(action, asset_catalog={}), "m_prefabs").items[0]
    assert item.resolution.resolved is False
    assert "no exact metadata GUID match" in item.resolution.warnings


def test_asset_catalog_skips_malformed_and_oversized_metadata(tmp_path: Path) -> None:
    mod = tmp_path / "mod"
    assets = mod / "Assets"
    assets.mkdir(parents=True)
    (assets / "Malformed.pmeta").write_text("{not-json", encoding="utf-8")
    (assets / "Oversized.pmeta").write_bytes(b"x" * (1024 * 1024 + 1))
    catalog, warnings = build_asset_catalog(mod, tmp_path)
    assert catalog == {}
    assert any("malformed metadata" in warning for warning in warnings)
    assert any("oversized/non-file metadata" in warning for warning in warnings)


def test_all_action_reference_inspection_is_zero_mutation(main_scene_path: Path) -> None:
    scene_before = main_scene_path.read_bytes()
    payloads_before = [
        action.resolved_reference.raw_segment
        for action in _all_real_actions(main_scene_path)
        if action.resolved_reference is not None
    ]
    for action in _all_real_actions(main_scene_path):
        if action.resolved_reference is not None:
            inspect_action_entry_references(action)
    payloads_after = [
        action.resolved_reference.raw_segment
        for action in _all_real_actions(main_scene_path)
        if action.resolved_reference is not None
    ]
    assert payloads_after == payloads_before
    assert main_scene_path.read_bytes() == scene_before
    assert hashlib.sha256(scene_before).hexdigest() == "a0af6631df7e99d4f98d964122eca56ab66cb8beaad53d7217bc54efff89cde8"
    assert not list(main_scene_path.parent.glob("*.bak*"))


def test_reference_registry_is_entirely_read_only() -> None:
    assert REFERENCE_SCHEMAS
    assert all(schema.writable is False for schema in REFERENCE_SCHEMAS.values())
