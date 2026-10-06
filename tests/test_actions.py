from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from pummelmcp.pmh import ActionPayloadError, parse_action_payload, read_pmh


ACTION_PROPERTIES = (
    "OnHitActions",
    "OnEnterActions",
    "OnExitActions",
    "OnStayActions",
)


def _varuint7(value: int) -> bytes:
    encoded = bytearray()
    while value >= 0x80:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def _payload(root: dict[str, Any], *, trailing: bytes = b"") -> bytes:
    root_raw = json.dumps(root, separators=(",", ":")).encode()
    refids = root.get("references", {}).get("RefIds", [])
    result = bytearray(b"\x00\x00" + _varuint7(len(root_raw)) + root_raw)
    result.extend(len(refids).to_bytes(2, "little"))
    for refid in refids:
        data_raw = json.dumps(refid["data"], separators=(",", ":")).encode()
        result.extend(int(refid["data"]["m_type"]).to_bytes(2, "little"))
        result.extend(_varuint7(len(data_raw)))
        result.extend(data_raw)
    result.extend(trailing)
    return bytes(result)


def _root(
    actions: list[dict[str, Any]], refids: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "m_type": 0,
        "m_actions": actions,
        "references": {"version": 2, "RefIds": refids},
    }


def _ref(rid: int, class_name: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "rid": rid,
        "type": {"class": class_name, "ns": "Example.Actions", "asm": "Tests"},
        "data": data,
    }


def _first_trigger(path: Path):
    for obj in read_pmh(path).walk():
        for component in obj.components:
            if component.type_name == "ModTrigger":
                return obj, component
    raise AssertionError("fixture contains no ModTrigger")


def test_action_payload_outer_framing(main_scene_path: Path) -> None:
    _, component = _first_trigger(main_scene_path)
    parsed = parse_action_payload(component.get_field("OnHitActions").raw)
    kinds = [item.kind for item in parsed.segments]
    assert parsed.prefix_u16 == 0
    assert kinds[:4] == [
        "outer_prefix_u16",
        "root_json_length",
        "root_json",
        "reference_segment_count",
    ]


def test_action_payload_length_prefix(main_scene_path: Path) -> None:
    _, component = _first_trigger(main_scene_path)
    parsed = parse_action_payload(component.get_field("OnHitActions").raw)
    assert parsed.segments[1].raw == b"\x90\xde\x01"
    assert parsed.segments[1].value == 28_432


def test_action_payload_segments(main_scene_path: Path) -> None:
    _, component = _first_trigger(main_scene_path)
    raw = component.get_field("OnHitActions").raw
    parsed = parse_action_payload(raw)
    assert parsed.fully_consumed is True
    assert parsed.parsed_ranges[0].start == 0
    assert parsed.parsed_ranges[0].end == len(raw)
    assert parsed.unparsed_ranges == ()
    assert parsed.segments[-1].kind == "reference_json"
    assert parsed.segments[-1].source_span.end == len(raw)


def test_action_parser_retains_root_and_reference_json_source_spans(
    main_scene_path: Path,
) -> None:
    _, component = _first_trigger(main_scene_path)
    parsed = parse_action_payload(component.get_field("OnHitActions").raw)
    assert parsed.root_json_document is not None
    actions_node = parsed.root_json_document.root.member("m_actions")
    assert parsed.root_segment.raw[actions_node.span.start] == ord("[")
    reference = parsed.references[0]
    assert reference.json_document is not None
    bool_node = reference.json_document.root.member("m_spawnAtPosition")
    assert reference.raw_segment[bool_node.span.start : bool_node.span.end] == b"false"


def test_action_payload_full_consumption_or_reports_gaps(
    main_scene_path: Path,
) -> None:
    _, component = _first_trigger(main_scene_path)
    raw = component.get_field("OnHitActions").raw
    parsed = parse_action_payload(raw)
    with_gap = parse_action_payload(raw + b"unknown")
    assert parsed.fully_consumed is True
    assert parsed.unparsed_ranges == ()
    assert with_gap.fully_consumed is False
    assert with_gap.unparsed_ranges[0].start == len(raw)


def test_action_payload_reports_unparsed_gap() -> None:
    parsed = parse_action_payload(_payload(_root([], []), trailing=b"unknown"))
    assert parsed.fully_consumed is False
    assert parsed.unparsed_ranges[0].length == 7
    assert parsed.raw_payload[parsed.unparsed_ranges[0].start :] == b"unknown"


@pytest.mark.parametrize(
    "raw, message",
    [
        (b"\x00", "outer_prefix_u16 length"),
        (b"\x00\x00\x80", "truncated 7-bit length prefix"),
        (b"\x00\x00\x05{}", "root_json length"),
        (b"\x00\x00\x01\xff\x00\x00", "not valid UTF-8"),
        (b"\x00\x00\x01x\x00\x00", "not valid bounded JSON"),
    ],
)
def test_action_payload_rejects_truncated_or_malformed(
    raw: bytes, message: str
) -> None:
    with pytest.raises(ActionPayloadError, match=message):
        parse_action_payload(raw)


def test_action_payload_truncated() -> None:
    with pytest.raises(ActionPayloadError, match="truncated 7-bit length prefix"):
        parse_action_payload(b"\x00\x00\x80")


def test_action_payload_invalid_length() -> None:
    with pytest.raises(ActionPayloadError, match="root_json length"):
        parse_action_payload(b"\x00\x00\x05{}")


def test_action_payload_malformed_utf8() -> None:
    with pytest.raises(ActionPayloadError, match="not valid UTF-8"):
        parse_action_payload(b"\x00\x00\x01\xff\x00\x00")


def test_action_payload_malformed_json() -> None:
    with pytest.raises(ActionPayloadError, match="not valid bounded JSON"):
        parse_action_payload(b"\x00\x00\x03NaN\x00\x00")


def test_parse_action_list_root() -> None:
    root = _root(
        [{"rid": 1001}, {"rid": 1000}],
        [
            _ref(1000, "FirstAction", {"m_type": 7, "value": "first"}),
            _ref(1001, "SecondAction", {"m_type": 8, "value": "second"}),
        ],
    )
    parsed = parse_action_payload(_payload(root))
    assert parsed.m_type == 0
    assert parsed.references_version == 2
    assert len(parsed.actions) == len(parsed.references) == 2
    assert [item.rid for item in parsed.actions] == [1001, 1000]
    assert [item.type_info.class_name for item in parsed.actions] == [
        "SecondAction",
        "FirstAction",
    ]


def test_action_order_preserved() -> None:
    parsed = parse_action_payload(
        _payload(
            _root(
                [{"rid": 2}, {"rid": 1}],
                [_ref(1, "First", {"m_type": 1}), _ref(2, "Second", {"m_type": 2})],
            )
        )
    )
    assert [item.rid for item in parsed.actions] == [2, 1]


def test_action_count() -> None:
    parsed = parse_action_payload(_payload(_root([{"rid": 1}], [_ref(1, "A", {"m_type": 1})])))
    assert len(parsed.actions) == 1


def test_action_rid_resolution() -> None:
    parsed = parse_action_payload(_payload(_root([{"rid": 9}], [_ref(9, "A", {"m_type": 1})])))
    assert parsed.actions[0].resolved_reference is parsed.references[0]


def test_reference_count() -> None:
    parsed = parse_action_payload(_payload(_root([], [_ref(9, "A", {"m_type": 1})])))
    assert len(parsed.references) == 1


def test_duplicate_dangling_and_unreferenced_rids_are_reported() -> None:
    root = _root(
        [{"rid": 1000}, {"rid": 9999}],
        [
            _ref(1000, "A", {"m_type": 1}),
            _ref(1000, "Duplicate", {"m_type": 2}),
            _ref(2000, "Unused", {"m_type": 3}),
        ],
    )
    parsed = parse_action_payload(_payload(root))
    assert parsed.duplicate_rids == (1000,)
    assert parsed.dangling_rids == (1000, 9999)
    assert parsed.unreferenced_rids == (2000,)
    assert parsed.actions[0].resolved_reference is None


def test_dangling_rid_detected() -> None:
    parsed = parse_action_payload(_payload(_root([{"rid": 99}], [])))
    assert parsed.dangling_rids == (99,)


def test_duplicate_rid_detected() -> None:
    refs = [_ref(1, "A", {"m_type": 1}), _ref(1, "B", {"m_type": 2})]
    assert parse_action_payload(_payload(_root([], refs))).duplicate_rids == (1,)


def test_unreferenced_refid_reported() -> None:
    parsed = parse_action_payload(_payload(_root([], [_ref(7, "A", {"m_type": 1})])))
    assert parsed.unreferenced_rids == (7,)


def test_unknown_action_class_and_fields_are_preserved() -> None:
    data = {"m_type": 321, "unknown_scalar": 42, "unknown_object": {"x": [1, 2]}}
    parsed = parse_action_payload(
        _payload(_root([{"rid": 44}], [_ref(44, "NeverSeenAction", data)]))
    )
    action = parsed.actions[0]
    assert action.type_info.class_name == "NeverSeenAction"
    assert action.type_info.namespace == "Example.Actions"
    assert action.fields == data
    assert action.resolved_reference.raw_segment is not None


def test_nested_action_list_and_max_depth() -> None:
    nested = _root(
        [{"rid": 2}], [_ref(2, "InnerAction", {"m_type": 2, "answer": 42})]
    )
    outer = _root(
        [{"rid": 1}],
        [_ref(1, "ContainerAction", {"m_type": 1, "nested": {"value": nested}})],
    )
    with_nested = parse_action_payload(_payload(outer), max_depth=1)
    without_nested = parse_action_payload(_payload(outer), max_depth=0)
    found = with_nested.actions[0].nested_action_lists
    assert len(found) == 1
    assert found[0].depth == 1
    assert found[0].actions[0].type_info.class_name == "InnerAction"
    assert without_nested.actions[0].nested_action_lists == ()


def test_reference_cycle_detected() -> None:
    root = _root(
        [{"rid": 1}],
        [
            _ref(1, "A", {"m_type": 1, "next": {"rid": 2}}),
            _ref(2, "B", {"m_type": 2, "next": {"rid": 1}}),
        ],
    )
    assert parse_action_payload(_payload(root)).reference_cycles == ((1, 2, 1),)


def test_spawn_prefab_action_typed_view_preserves_unknown_fields(
    main_scene_path: Path,
) -> None:
    _, component = _first_trigger(main_scene_path)
    action = parse_action_payload(component.get_field("OnHitActions").raw).actions[0]
    assert action.type_info.namespace == "ModSystem.Logic"
    assert action.type_info.class_name == "SpawnPrefabAction"
    assert action.fields["m_type"] == 576
    assert len(action.fields["m_prefabs"]) == 23
    assert "m_targetRotationOffset" in action.fields


def _spawn_prefab(main_scene_path: Path):
    _, component = _first_trigger(main_scene_path)
    return parse_action_payload(component.get_field("OnHitActions").raw).actions[0]


def test_spawn_prefab_action_detected(main_scene_path: Path) -> None:
    assert _spawn_prefab(main_scene_path).resolved_reference is not None


def test_spawn_prefab_namespace(main_scene_path: Path) -> None:
    assert _spawn_prefab(main_scene_path).type_info.namespace == "ModSystem.Logic"


def test_spawn_prefab_class(main_scene_path: Path) -> None:
    assert _spawn_prefab(main_scene_path).type_info.class_name == "SpawnPrefabAction"


def test_spawn_prefab_fields_inspected(main_scene_path: Path) -> None:
    assert len(_spawn_prefab(main_scene_path).fields["m_prefabs"]) == 23


def test_unknown_spawn_prefab_fields_preserved(main_scene_path: Path) -> None:
    assert "m_targetRotationOffset" in _spawn_prefab(main_scene_path).fields


def test_all_fixture_actions_consume_and_inspection_is_zero_mutation(
    main_scene_path: Path,
) -> None:
    scene_before = main_scene_path.read_bytes()
    payloads_before: list[tuple[bytes, str]] = []
    for obj in read_pmh(main_scene_path).walk():
        for component in obj.components:
            if component.type_name != "ModTrigger":
                continue
            for name in ACTION_PROPERTIES:
                raw = component.get_field(name).raw
                payloads_before.append((raw, hashlib.sha256(raw).hexdigest()))
                assert parse_action_payload(raw, source_property=name).fully_consumed

    assert len(payloads_before) == 40
    assert main_scene_path.read_bytes() == scene_before
    assert hashlib.sha256(scene_before).hexdigest() == (
        "a0af6631df7e99d4f98d964122eca56ab66cb8beaad53d7217bc54efff89cde8"
    )
    payloads_after = [
        component.get_field(name).raw
        for obj in read_pmh(main_scene_path).walk()
        for component in obj.components
        if component.type_name == "ModTrigger"
        for name in ACTION_PROPERTIES
    ]
    assert payloads_after == [item[0] for item in payloads_before]
    assert [hashlib.sha256(item).hexdigest() for item in payloads_after] == [
        item[1] for item in payloads_before
    ]


def test_real_action_catalog_contains_all_observed_classes(
    main_scene_path: Path,
) -> None:
    classes = {
        (action.type_info.namespace, action.type_info.class_name)
        for obj in read_pmh(main_scene_path).walk()
        for component in obj.components
        if component.type_name == "ModTrigger"
        for name in ACTION_PROPERTIES
        for action in parse_action_payload(component.get_field(name).raw).actions
        if action.type_info is not None
    }
    assert classes == {
        ("ModSystem.Logic", "SpawnPrefabAction"),
        ("ModSystem.Logic", "SpawnEffectAction"),
        ("ModSystem.Logic", "PlaySoundAction"),
        ("ModSystem.Logic", "KillAction"),
    }
