from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import anyio
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from pummelmcp.mcp_server import create_server
from pummelmcp.pmh import (
    ACTION_EVENT_PROPERTIES,
    ActionGraphWriter,
    ActionTemplateError,
    PMHScene,
    build_action_template_catalog,
    encode_action_varuint7,
    parse_action_payload,
    read_pmh,
)


CLIP_GUID = "951689b9-9095-4d7d-a7f1-2033c2214a91"


def _target_fields(type_tag: int) -> dict:
    return {
        "m_type": type_tag,
        "m_version": 0,
        "m_targetFlags": 1,
        "m_targets": [],
    }


ACTION_DATA = {
    "KillAction": _target_fields(64),
    "ChangeScoreAction": {
        **_target_fields(128),
        "m_operation": 1,
        "m_value": 10,
    },
    "SpawnEffectAction": {**_target_fields(160), "m_effectType": 1},
    "PositionAction": {
        **_target_fields(384),
        "m_space": 0,
        "m_operation": 0,
        "m_position": {"x": 1.0, "y": 2.0, "z": 3.0},
    },
    "RotationAction": {
        **_target_fields(416),
        "m_space": 0,
        "m_operation": 0,
        "m_rotation": {"x": 0.0, "y": 180.0, "z": 0.0},
    },
    "SetPlacementAction": _target_fields(480),
    "PlaySoundAction": {
        **_target_fields(544),
        "m_clip": {
            "m_assetGUID": {"serializedGuid": CLIP_GUID},
            "m_asset": {"instanceID": 188302},
        },
        "m_volume": 1.0,
    },
    "ShowMessageAction": {
        **_target_fields(1024),
        "m_messageTarget": 2,
        "m_message": "Try Again!",
        "m_duration": 1.0,
    },
}


def _payload(entries: list[tuple[str, dict]]) -> bytes:
    refids = []
    action_items = []
    tails = []
    for index, (class_name, data) in enumerate(entries):
        rid = 1000 + index
        action_items.append({"rid": rid})
        refids.append(
            {
                "rid": rid,
                "type": {
                    "class": class_name,
                    "ns": "ModSystem.Logic",
                    "asm": "Assembly-CSharp",
                },
                "data": data,
            }
        )
        raw = json.dumps(data, indent=4).encode()
        tails.append(
            int(data["m_type"]).to_bytes(2, "little")
            + encode_action_varuint7(len(raw))
            + raw
        )
    root = {
        "m_type": 0,
        "m_actions": action_items,
        "references": {"version": 2, "RefIds": refids},
    }
    root_raw = json.dumps(root, indent=4).encode()
    return (
        b"\0\0"
        + encode_action_varuint7(len(root_raw))
        + root_raw
        + len(entries).to_bytes(2, "little")
        + b"".join(tails)
    )


def _legacy_empty_payload() -> bytes:
    root = {"m_type": 0, "m_actions": [], "references": {"version": 1}}
    root_raw = json.dumps(root, indent=4).encode()
    return b"\0\0" + encode_action_varuint7(len(root_raw)) + root_raw + b"\0\0"


def _first_action_field(path: Path):
    for obj in read_pmh(path).walk():
        for component in obj.components:
            if component.type_name != "ModTrigger":
                continue
            for field in component.fields:
                if field.name in ACTION_EVENT_PROPERTIES and field.source_span is not None:
                    return obj, component, field
    raise AssertionError("no ModTrigger Action field")


def _replace_payload(path: Path, payload: bytes) -> tuple[str, str, str]:
    obj, component, field = _first_action_field(path)
    raw = path.read_bytes()
    span = field.source_span
    assert span is not None
    path.write_bytes(
        raw[: span.start - 4]
        + len(payload).to_bytes(4, "little")
        + payload
        + raw[span.end :]
    )
    return obj.guid, component.guid, field.name


def _make_generalized_scene(
    main_scene_path: Path, destination: Path, entries=None
) -> tuple[Path, tuple[str, str, str]]:
    shutil.copyfile(main_scene_path, destination)
    chosen = list(ACTION_DATA.items()) if entries is None else entries
    return destination, _replace_payload(destination, _payload(chosen))


def _parsed(path: Path, target: tuple[str, str, str]):
    field = PMHScene.load(path).get_component(target[0], target[1]).get_field(target[2])
    return field, parse_action_payload(field.raw)


@pytest.mark.parametrize("class_name", list(ACTION_DATA))
def test_add_and_delete_round_trip_for_each_minigame_class(
    main_scene_path: Path, tmp_path: Path, class_name: str
) -> None:
    scene, target = _make_generalized_scene(
        main_scene_path, tmp_path / f"{class_name}.scene"
    )
    before_bytes = scene.read_bytes()
    before_field, _ = _parsed(scene, target)
    report = ActionGraphWriter(scene).add_action(
        *target,
        class_name,
        expected_payload_sha256=hashlib.sha256(before_field.raw).hexdigest(),
        backup=False,
    )
    after_field, after = _parsed(scene, target)
    added = next(item for item in after.actions if item.rid == 1008)
    assert added.type_info.class_name == class_name
    assert added.fields == ACTION_DATA[class_name]
    assert report.template["template_schema_validated"] is True
    assert report.validation.checks["new_reference_matches_template"] is True
    assert report.validation.checks["new_action_schema_valid"] is True

    ActionGraphWriter(scene).delete_action(
        *target,
        1008,
        expected_payload_sha256=hashlib.sha256(after_field.raw).hexdigest(),
        backup=False,
    )
    assert scene.read_bytes() == before_bytes


def test_invalid_template_shape_is_rejected_without_write(
    main_scene_path: Path, tmp_path: Path
) -> None:
    invalid = {**ACTION_DATA["KillAction"], "m_targets": [{"unexpected": True}]}
    scene, target = _make_generalized_scene(
        main_scene_path,
        tmp_path / "invalid.scene",
        [("KillAction", invalid)],
    )
    before = scene.read_bytes()
    with pytest.raises(ActionTemplateError, match="empty m_targets"):
        ActionGraphWriter(scene).add_action(*target, "KillAction", backup=False)
    assert scene.read_bytes() == before


def test_template_catalog_skips_legacy_v1_payload(
    main_scene_path: Path, tmp_path: Path
) -> None:
    scene = tmp_path / "Legacy.scene"
    shutil.copyfile(main_scene_path, scene)
    _replace_payload(scene, _legacy_empty_payload())
    catalog = build_action_template_catalog(read_pmh(scene))
    assert isinstance(catalog.templates, tuple)


def test_external_template_can_seed_empty_list_for_generalized_class(
    main_scene_path: Path, tmp_path: Path
) -> None:
    source, _ = _make_generalized_scene(
        main_scene_path,
        tmp_path / "Template.scene",
        [("ChangeScoreAction", ACTION_DATA["ChangeScoreAction"])],
    )
    target = tmp_path / "Target.scene"
    shutil.copyfile(main_scene_path, target)
    empty_target = _replace_payload(target, _payload([]))
    template = build_action_template_catalog(read_pmh(source)).matching(
        "ModSystem.Logic", "ChangeScoreAction"
    )[0]

    report = ActionGraphWriter(target).add_action(
        *empty_target,
        "ChangeScoreAction",
        template_sha256=template.managed_reference_sha256,
        template_scene_path=source,
        backup=False,
    )
    _, parsed = _parsed(target, empty_target)
    assert report.affected_rid == 1000
    assert parsed.actions[0].fields == ACTION_DATA["ChangeScoreAction"]


def test_mcp_adds_generalized_action(
    main_scene_path: Path, tmp_path: Path
) -> None:
    scene, target = _make_generalized_scene(
        main_scene_path, tmp_path / "Mcp.scene"
    )
    field, _ = _parsed(scene, target)
    server = create_server(tmp_path)

    async def invoke():
        return await server.call_tool(
            "add_action",
            {
                "scene_path": str(scene),
                "object_identifier": target[0],
                "component_identifier": target[1],
                "event_property": target[2],
                "action_class": "ChangeScoreAction",
                "expected_payload_sha256": hashlib.sha256(field.raw).hexdigest(),
            },
        )

    result = anyio.run(invoke)
    assert not result.is_error
    assert result.structured_content["template"]["class"] == "ChangeScoreAction"
    assert result.structured_content["validation"]["passed"] is True


def test_mcp_rejects_class_without_phase3_schema(
    main_scene_path: Path, tmp_path: Path
) -> None:
    scene, target = _make_generalized_scene(
        main_scene_path, tmp_path / "Unsupported.scene"
    )
    server = create_server(tmp_path)

    async def invoke():
        return await server.call_tool(
            "add_action",
            {
                "scene_path": str(scene),
                "object_identifier": target[0],
                "component_identifier": target[1],
                "event_property": target[2],
                "action_class": "WaitAction",
            },
        )

    with pytest.raises(ToolError, match="no Phase 3 creation schema"):
        anyio.run(invoke)
