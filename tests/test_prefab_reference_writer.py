from __future__ import annotations

import hashlib
import json
import shutil
import struct
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import anyio
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

from pummelmcp.mcp_server import create_server
from pummelmcp.pmh import (
    ACTION_EVENT_PROPERTIES,
    ActionPayloadChangedError,
    AmbiguousPrefabReferenceTemplateError,
    NoValidatedPrefabReferenceTemplateError,
    PrefabReferenceAssetError,
    PrefabReferenceCurrentGuidMismatchError,
    PrefabReferenceTemplate,
    PrefabReferenceTemplateGroup,
    PrefabReferenceWriter,
    build_prefab_reference_template_catalog,
    inspect_action_entry_references,
    parse_action_payload,
    read_pmh,
)
from pummelmcp.pmh.action_writer import (
    _TokenReplacement,
    _apply_replacements,
    _reframe_reference_json,
)


def _target(path: Path) -> tuple[str, str, str, int]:
    for obj in read_pmh(path).walk():
        for component in obj.components:
            if component.type_name != "ModTrigger":
                continue
            for event in ACTION_EVENT_PROPERTIES:
                field = component.get_field(event)
                for action in parse_action_payload(field.raw).actions:
                    if action.type_info and action.type_info.class_name == "SpawnPrefabAction":
                        return obj.guid, component.guid, event, action.rid
    raise AssertionError("fixture has no SpawnPrefabAction")


def _action_state(path: Path):
    object_guid, component_guid, event, rid = _target(path)
    scene = read_pmh(path)
    obj = next(item for item in scene.walk() if item.guid == object_guid)
    component = next(item for item in obj.components if item.guid == component_guid)
    field = component.get_field(event)
    parsed = parse_action_payload(field.raw)
    action = next(item for item in parsed.actions if item.rid == rid)
    inspected = inspect_action_entry_references(action)
    prefabs = next(item for item in inspected.references if item.source_field == "m_prefabs")
    return obj, component, field, parsed, action, prefabs


@pytest.fixture(scope="module")
def prefab_mod(tmp_path_factory: pytest.TempPathFactory, main_scene_path: Path) -> Path:
    mod = tmp_path_factory.mktemp("prefab-template-mod") / "Joker"
    data = mod / "Data"
    prefabs_dir = mod / "Assets" / "Prefabs"
    data.mkdir(parents=True)
    prefabs_dir.mkdir(parents=True)
    scene_path = data / "MainScene.scene"
    shutil.copyfile(main_scene_path, scene_path)
    items = _action_state(main_scene_path)[5].items
    for item in items:
        metadata = item.raw_value["m_asset"]
        name = metadata["name"]
        (prefabs_dir / f"{name}.pfab").write_bytes(f"pfab:{name}".encode())
        (prefabs_dir / f"{name}.pfab.pmeta").write_text(
            json.dumps(metadata, separators=(",", ":")), encoding="utf-8"
        )
    return mod


def _clone_mod(prefab_mod: Path, tmp_path: Path) -> tuple[Path, Path]:
    target = tmp_path / "clone"
    shutil.copytree(prefab_mod, target)
    return target, target / "Data" / "MainScene.scene"


def _writer_args(scene: Path) -> tuple[str, str, str, int]:
    return _target(scene)


def _replace(
    scene: Path,
    mod: Path,
    *,
    index: int = 0,
    target_index: int = 1,
    dry_run: bool = False,
    by_path: bool = False,
):
    object_guid, component_guid, event, rid = _writer_args(scene)
    items = _action_state(scene)[5].items
    current_guid = items[index].normalized_value["guid"]
    target_guid = items[target_index].normalized_value["guid"]
    target_name = items[target_index].normalized_value["name"]
    kwargs: dict[str, Any] = {
        "expected_current_prefab_guid": current_guid,
        "expected_payload_sha256": hashlib.sha256(_action_state(scene)[2].raw).hexdigest(),
        "dry_run": dry_run,
    }
    if by_path:
        kwargs["target_prefab_relative_path"] = f"Assets/Prefabs/{target_name}.pfab"
    else:
        kwargs["target_prefab_guid"] = target_guid
    report = PrefabReferenceWriter(scene, mod).replace_prefab_reference(
        object_guid, component_guid, event, rid, index, **kwargs
    )
    return report, current_guid, target_guid


def test_prefab_template_catalog_built(prefab_mod: Path) -> None:
    catalog = build_prefab_reference_template_catalog(
        prefab_mod / "Data" / "MainScene.scene", prefab_mod
    )
    assert catalog.total_occurrences == 183
    assert len(catalog.groups) == 23


def test_prefab_templates_grouped_by_guid(prefab_mod: Path) -> None:
    catalog = build_prefab_reference_template_catalog(
        prefab_mod / "Data" / "MainScene.scene", prefab_mod
    )
    assert sum(group.occurrence_count for group in catalog.groups.values()) == 183
    assert all(group.guid == guid for guid, group in catalog.groups.items())
    assert all(len(group.variants) == 1 for group in catalog.groups.values())


def test_same_guid_variants_detected() -> None:
    template = PrefabReferenceTemplate(
        guid="g", relative_path="Assets/a.pfab", metadata_relative_path="Assets/a.pfab.pmeta",
        raw_bytes=b"{}", raw_value={}, normalized_value={}, unknown_fields={},
        reference_sha256="a" * 64, normalized_sha256="b" * 64,
        asset_sha256="c" * 64, metadata_sha256="d" * 64,
        evidence_status="CONFIRMED", sources=(),
    )
    other = PrefabReferenceTemplate(
        guid="g", relative_path=template.relative_path,
        metadata_relative_path=template.metadata_relative_path,
        raw_bytes=b"{ }", raw_value={}, normalized_value={}, unknown_fields={},
        reference_sha256="e" * 64, normalized_sha256=template.normalized_sha256,
        asset_sha256=template.asset_sha256, metadata_sha256=template.metadata_sha256,
        evidence_status="CONFIRMED", sources=(),
    )
    assert PrefabReferenceTemplateGroup("g", 2, (template, other)).ambiguous is True


def test_template_guid_matches_pmeta(prefab_mod: Path) -> None:
    catalog = build_prefab_reference_template_catalog(
        prefab_mod / "Data" / "MainScene.scene", prefab_mod
    )
    for guid, group in catalog.groups.items():
        template = group.variants[0]
        metadata = json.loads((prefab_mod / template.metadata_relative_path).read_text())
        assert metadata["guid"]["serializedGuid"] == guid


def test_template_resolves_pfab(prefab_mod: Path) -> None:
    catalog = build_prefab_reference_template_catalog(
        prefab_mod / "Data" / "MainScene.scene", prefab_mod
    )
    assert all(
        group.variants[0].validated
        and (prefab_mod / group.variants[0].relative_path).is_file()
        for group in catalog.groups.values()
    )


def test_missing_template_rejected(prefab_mod: Path, tmp_path: Path) -> None:
    mod, scene = _clone_mod(prefab_mod, tmp_path)
    asset = mod / "Assets" / "Prefabs" / "Unseen.pfab"
    asset.write_bytes(b"unseen")
    Path(str(asset) + ".pmeta").write_text(
        json.dumps({"name": "Unseen", "guid": {"serializedGuid": "unseen-guid"}, "tags": ["prefabs"]})
    )
    args = _writer_args(scene)
    with pytest.raises(NoValidatedPrefabReferenceTemplateError):
        PrefabReferenceWriter(scene, mod).replace_prefab_reference(
            *args, 0, target_prefab_relative_path="Assets/Prefabs/Unseen.pfab"
        )


def test_ambiguous_template_rejected(prefab_mod: Path, tmp_path: Path) -> None:
    mod, scene = _clone_mod(prefab_mod, tmp_path)
    writer = PrefabReferenceWriter(scene, mod)
    target_guid = _action_state(scene)[5].items[1].normalized_value["guid"]
    group = writer.catalog.groups[target_guid]
    variant = replace(
        group.variants[0],
        raw_bytes=group.variants[0].raw_bytes + b" ",
        reference_sha256="f" * 64,
    )
    writer.catalog = replace(
        writer.catalog,
        groups={
            **writer.catalog.groups,
            target_guid: replace(group, variants=(*group.variants, variant)),
        },
    )
    with pytest.raises(AmbiguousPrefabReferenceTemplateError):
        writer.replace_prefab_reference(
            *_writer_args(scene), 0, target_prefab_guid=target_guid
        )


@pytest.fixture(scope="module")
def replacement_case(prefab_mod: Path, tmp_path_factory: pytest.TempPathFactory):
    mod, scene = _clone_mod(prefab_mod, tmp_path_factory.mktemp("replacement-case"))
    before = _action_state(scene)
    report, current_guid, target_guid = _replace(scene, mod)
    after = _action_state(scene)
    return mod, scene, before, after, report, current_guid, target_guid


def test_replace_prefab_reference(replacement_case) -> None:
    assert replacement_case[4].validation.passed
    assert replacement_case[4].no_op is False


def test_replace_prefab_by_guid(replacement_case) -> None:
    assert replacement_case[4].after["guid"] == replacement_case[6]


def test_replace_prefab_by_relative_path(prefab_mod: Path, tmp_path: Path) -> None:
    mod, scene = _clone_mod(prefab_mod, tmp_path)
    report, _, target_guid = _replace(scene, mod, by_path=True)
    assert report.after["guid"] == target_guid


def test_replace_preserves_list_length(replacement_case) -> None:
    assert len(replacement_case[2][5].items) == len(replacement_case[3][5].items) == 23


def test_replace_preserves_list_order(replacement_case) -> None:
    before = [item.normalized_value["guid"] for item in replacement_case[2][5].items]
    after = [item.normalized_value["guid"] for item in replacement_case[3][5].items]
    assert after == [replacement_case[6], *before[1:]]


def test_replace_preserves_other_items(replacement_case) -> None:
    before = replacement_case[2][5].items
    after = replacement_case[3][5].items
    assert all(a.raw_bytes == b.raw_bytes for a, b in zip(before[1:], after[1:], strict=True))


def test_replace_preserves_duplicates(replacement_case) -> None:
    after = [item.normalized_value["guid"] for item in replacement_case[3][5].items]
    assert after[0] == after[1] == replacement_case[6]
    assert len(after) == 23


def test_same_guid_is_noop(prefab_mod: Path, tmp_path: Path) -> None:
    mod, scene = _clone_mod(prefab_mod, tmp_path)
    before = scene.read_bytes()
    backups = set(scene.parent.glob("*.bak*"))
    items = _action_state(scene)[5].items
    args = _writer_args(scene)
    report = PrefabReferenceWriter(scene, mod).replace_prefab_reference(
        *args, 0, target_prefab_guid=items[0].normalized_value["guid"]
    )
    assert report.no_op is True
    assert report.backup_path is None
    assert scene.read_bytes() == before
    assert set(scene.parent.glob("*.bak*")) == backups


def test_variable_length_reference_replacement(replacement_case) -> None:
    report = replacement_case[4]
    assert report.bytes_added_or_removed == -2
    assert report.new_property_length == report.old_property_length - 2


def test_variable_length_reference_replacement_reverse(prefab_mod: Path, tmp_path: Path) -> None:
    mod, scene = _clone_mod(prefab_mod, tmp_path)
    first, original_guid, _ = _replace(scene, mod)
    args = _writer_args(scene)
    field = _action_state(scene)[2]
    second = PrefabReferenceWriter(scene, mod).replace_prefab_reference(
        *args, 0, target_prefab_guid=original_guid,
        expected_payload_sha256=hashlib.sha256(field.raw).hexdigest(),
    )
    assert first.bytes_added_or_removed == -2
    assert second.bytes_added_or_removed == 2


def test_prefab_replacement_7bit_length_boundary_crossing() -> None:
    data = {"m_type": 576, "m_prefabs": [{"m_assetGUID": {"serializedGuid": "a"}, "m_asset": {"guid": {"serializedGuid": "a"}}}]}
    ref = {"rid": 1, "type": {"class": "SpawnPrefabAction", "ns": "ModSystem.Logic", "asm": "A"}, "data": data}
    root = {"m_type": 0, "m_actions": [{"rid": 1}], "references": {"version": 2, "RefIds": [ref]}}
    root_raw = json.dumps(root, separators=(",", ":")).encode()
    data_raw = json.dumps(data, separators=(",", ":")).encode()
    def varint(value: int) -> bytes:
        result = bytearray()
        while value >= 128:
            result.append((value & 127) | 128); value >>= 7
        result.append(value); return bytes(result)
    payload = b"\0\0" + varint(len(root_raw)) + root_raw + b"\1\0" + (576).to_bytes(2, "little") + varint(len(data_raw)) + data_raw
    parsed = parse_action_payload(payload)
    action = parsed.actions[0]
    item = next(x for x in inspect_action_entry_references(action).references if x.source_field == "m_prefabs").items[0]
    old_prefix = next(x.raw for x in parsed.segments if x.kind == "reference_json_length")
    needed = max(1, 128 - len(data_raw))
    replacement = _TokenReplacement("item", item.source_span, item.raw_bytes, item.raw_bytes[:-1] + b',"padding":"' + b"x" * needed + b'"}', item.raw_value, None)
    patched_ref = _apply_replacements(action.resolved_reference.raw_segment, [replacement])
    patched_payload = _reframe_reference_json(parsed, 0, patched_ref)
    reparsed = parse_action_payload(patched_payload)
    new_prefix = next(x.raw for x in reparsed.segments if x.kind == "reference_json_length")
    assert len(old_prefix) == 1
    assert len(new_prefix) == 2


def _check(replacement_case, name: str) -> None:
    assert replacement_case[4].validation.checks[name] is True


def test_root_json_unchanged(replacement_case) -> None:
    _check(replacement_case, "root_json_bytes_unchanged")


def test_m_actions_unchanged(replacement_case) -> None:
    _check(replacement_case, "m_actions_bytes_unchanged")


def test_rids_unchanged(replacement_case) -> None:
    _check(replacement_case, "rids_unchanged")


def test_refids_unchanged(replacement_case) -> None:
    _check(replacement_case, "refids_unchanged")


def test_action_identity_unchanged(replacement_case) -> None:
    _check(replacement_case, "action_identity_unchanged")


def test_non_target_references_unchanged(replacement_case) -> None:
    _check(replacement_case, "non_target_reference_hashes_unchanged")


def test_other_event_payloads_unchanged(replacement_case) -> None:
    _check(replacement_case, "other_event_payloads_unchanged")


def test_scalar_action_fields_unchanged(replacement_case) -> None:
    _check(replacement_case, "scalar_action_fields_unchanged")


def test_prefab_list_only_target_index_changes(replacement_case) -> None:
    _check(replacement_case, "prefab_list_only_target_index_changes")


def test_graph_fingerprint_unchanged(replacement_case) -> None:
    _check(replacement_case, "graph_fingerprint_unchanged")


def test_scene_suffix_content_unchanged(replacement_case) -> None:
    _check(replacement_case, "suffix_after_property_record_unchanged")


def test_stale_payload_hash_rejected(prefab_mod: Path, tmp_path: Path) -> None:
    mod, scene = _clone_mod(prefab_mod, tmp_path)
    args = _writer_args(scene)
    target = _action_state(scene)[5].items[1].normalized_value["guid"]
    with pytest.raises(ActionPayloadChangedError):
        PrefabReferenceWriter(scene, mod).replace_prefab_reference(
            *args, 0, target_prefab_guid=target, expected_payload_sha256="0" * 64
        )


def test_expected_current_guid_mismatch_rejected(prefab_mod: Path, tmp_path: Path) -> None:
    mod, scene = _clone_mod(prefab_mod, tmp_path)
    args = _writer_args(scene)
    target = _action_state(scene)[5].items[1].normalized_value["guid"]
    with pytest.raises(PrefabReferenceCurrentGuidMismatchError):
        PrefabReferenceWriter(scene, mod).replace_prefab_reference(
            *args, 0, target_prefab_guid=target,
            expected_current_prefab_guid="stale-guid",
        )


def test_scene_not_overwritten_on_conflict(prefab_mod: Path, tmp_path: Path) -> None:
    mod, scene = _clone_mod(prefab_mod, tmp_path)
    before = scene.read_bytes()
    args = _writer_args(scene)
    target = _action_state(scene)[5].items[1].normalized_value["guid"]
    with pytest.raises(ActionPayloadChangedError):
        PrefabReferenceWriter(scene, mod).replace_prefab_reference(
            *args, 0, target_prefab_guid=target, expected_payload_sha256="f" * 64
        )
    assert scene.read_bytes() == before


def test_asset_hashes_unchanged(prefab_mod: Path, tmp_path: Path) -> None:
    mod, scene = _clone_mod(prefab_mod, tmp_path)
    assets = sorted(path for path in (mod / "Assets").rglob("*") if path.is_file())
    before = {path.relative_to(mod): hashlib.sha256(path.read_bytes()).hexdigest() for path in assets}
    _replace(scene, mod)
    after = {path.relative_to(mod): hashlib.sha256(path.read_bytes()).hexdigest() for path in assets}
    assert after == before


def test_pfab_never_modified(prefab_mod: Path, tmp_path: Path) -> None:
    mod, scene = _clone_mod(prefab_mod, tmp_path)
    before = {path: path.read_bytes() for path in (mod / "Assets").rglob("*.pfab")}
    _replace(scene, mod)
    assert all(path.read_bytes() == raw for path, raw in before.items())


def test_pmeta_never_modified(prefab_mod: Path, tmp_path: Path) -> None:
    mod, scene = _clone_mod(prefab_mod, tmp_path)
    before = {path: path.read_bytes() for path in (mod / "Assets").rglob("*.pmeta")}
    _replace(scene, mod)
    assert all(path.read_bytes() == raw for path, raw in before.items())


def test_asset_path_escape_rejected(prefab_mod: Path, tmp_path: Path) -> None:
    mod, scene = _clone_mod(prefab_mod, tmp_path)
    outside = tmp_path / "outside.pfab"
    outside.write_bytes(b"outside")
    Path(str(outside) + ".pmeta").write_text(json.dumps({"guid": {"serializedGuid": "x"}}))
    with pytest.raises(PrefabReferenceAssetError):
        PrefabReferenceWriter(scene, tmp_path).replace_prefab_reference(
            *_writer_args(scene), 0, target_prefab_relative_path="../../outside.pfab"
        )


def test_cross_mod_reference_rejected(prefab_mod: Path, tmp_path: Path) -> None:
    mod, scene = _clone_mod(prefab_mod, tmp_path)
    other = tmp_path / "other" / "Assets" / "x.pfab"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"other")
    Path(str(other) + ".pmeta").write_text(json.dumps({"guid": {"serializedGuid": "x"}}))
    with pytest.raises(PrefabReferenceAssetError):
        PrefabReferenceWriter(scene, tmp_path).replace_prefab_reference(
            *_writer_args(scene), 0,
            target_prefab_relative_path="../other/Assets/x.pfab",
        )


def test_unresolved_target_rejected(prefab_mod: Path, tmp_path: Path) -> None:
    mod, scene = _clone_mod(prefab_mod, tmp_path)
    target = _action_state(scene)[5].items[1].normalized_value["guid"]
    target_name = _action_state(scene)[5].items[1].normalized_value["name"]
    (mod / "Assets" / "Prefabs" / f"{target_name}.pfab").unlink()
    with pytest.raises(PrefabReferenceAssetError):
        PrefabReferenceWriter(scene, mod).replace_prefab_reference(
            *_writer_args(scene), 0, target_prefab_guid=target
        )


def test_external_template_requires_exact_reference_hash(
    prefab_mod: Path, tmp_path: Path
) -> None:
    mod, scene = _clone_mod(prefab_mod, tmp_path)
    template_scene = mod / "Data" / "Template.scene"
    shutil.copyfile(scene, template_scene)
    target = _action_state(scene)[5].items[1].normalized_value["guid"]
    with pytest.raises(PrefabReferenceAssetError, match="requires exact"):
        PrefabReferenceWriter(scene, mod).replace_prefab_reference(
            *_writer_args(scene), 0,
            target_prefab_guid=target,
            template_scene_path=template_scene,
        )


def test_external_template_uses_pinned_reference_and_reports_provenance(
    prefab_mod: Path, tmp_path: Path
) -> None:
    mod, scene = _clone_mod(prefab_mod, tmp_path)
    template_scene = mod / "Data" / "Template.scene"
    shutil.copyfile(scene, template_scene)
    target = _action_state(scene)[5].items[1].normalized_value["guid"]
    template = build_prefab_reference_template_catalog(template_scene, mod).get(
        target
    ).variants[0]
    report = PrefabReferenceWriter(scene, mod).replace_prefab_reference(
        *_writer_args(scene), 0,
        target_prefab_guid=target,
        template_scene_path=template_scene,
        template_reference_sha256=template.reference_sha256,
    )
    assert report.template_source.source_file == template_scene.resolve()
    assert report.validation.passed is True


def test_external_template_outside_current_mod_rejected(
    prefab_mod: Path, tmp_path: Path
) -> None:
    mod, scene = _clone_mod(prefab_mod, tmp_path)
    outside = tmp_path / "outside.scene"
    shutil.copyfile(scene, outside)
    target = _action_state(scene)[5].items[1].normalized_value["guid"]
    with pytest.raises(PrefabReferenceAssetError, match="escapes"):
        PrefabReferenceWriter(scene, tmp_path).replace_prefab_reference(
            *_writer_args(scene), 0,
            target_prefab_guid=target,
            template_scene_path=outside,
            template_reference_sha256="0" * 64,
        )


def _mcp_call(server, name: str, arguments: dict[str, Any]):
    async def invoke():
        return await server.call_tool(name, arguments)

    return anyio.run(invoke)


def _mcp_structured(server, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = _mcp_call(server, name, arguments)
    assert not result.is_error
    assert isinstance(result.structured_content, dict)
    return result.structured_content


def _mcp_arguments(scene: Path, *, index: int = 0, target_index: int = 1) -> dict[str, Any]:
    object_guid, component_guid, event, rid = _writer_args(scene)
    state = _action_state(scene)
    items = state[5].items
    return {
        "scene_path": str(scene),
        "object_identifier": object_guid,
        "component_identifier": component_guid,
        "event_property": event,
        "action_rid": rid,
        "prefab_index": index,
        "target_prefab_guid": items[target_index].normalized_value["guid"],
        "expected_current_prefab_guid": items[index].normalized_value["guid"],
        "expected_payload_sha256": hashlib.sha256(state[2].raw).hexdigest(),
    }


def test_mcp_tools_list_contains_replace_prefab_reference(prefab_mod: Path) -> None:
    tools = anyio.run(create_server(prefab_mod).list_tools)
    tool = next(item for item in tools if item.name == "replace_prefab_reference")
    assert tool.annotations.read_only_hint is False
    assert tool.annotations.destructive_hint is False
    assert "EXISTING SpawnPrefabAction.m_prefabs item" in tool.description
    assert "cannot add, remove, resize, or reorder" in tool.description


def test_mcp_replace_prefab_reference(prefab_mod: Path, tmp_path: Path) -> None:
    mod, scene = _clone_mod(prefab_mod, tmp_path)
    arguments = _mcp_arguments(scene)
    result = _mcp_structured(
        create_server(mod), "replace_prefab_reference", arguments
    )
    assert result["after"]["guid"] == arguments["target_prefab_guid"]
    assert result["validation"]["passed"] is True


def test_mcp_replace_by_relative_path(prefab_mod: Path, tmp_path: Path) -> None:
    mod, scene = _clone_mod(prefab_mod, tmp_path)
    arguments = _mcp_arguments(scene)
    target_name = _action_state(scene)[5].items[1].normalized_value["name"]
    arguments.pop("target_prefab_guid")
    arguments["target_prefab_relative_path"] = f"Assets/Prefabs/{target_name}.pfab"
    result = _mcp_structured(
        create_server(mod), "replace_prefab_reference", arguments
    )
    assert result["after"]["relative_path"] == f"Assets/Prefabs/{target_name}.pfab"


def test_mcp_prefab_dry_run(prefab_mod: Path, tmp_path: Path) -> None:
    mod, scene = _clone_mod(prefab_mod, tmp_path)
    before = scene.read_bytes()
    arguments = {**_mcp_arguments(scene), "dry_run": True}
    result = _mcp_structured(
        create_server(mod), "replace_prefab_reference", arguments
    )
    assert result["dry_run"] is True
    assert result["backup_path"] is None
    assert scene.read_bytes() == before


def test_mcp_stale_hash_rejected(prefab_mod: Path, tmp_path: Path) -> None:
    mod, scene = _clone_mod(prefab_mod, tmp_path)
    arguments = {**_mcp_arguments(scene), "expected_payload_sha256": "0" * 64}
    with pytest.raises(ToolError, match="ActionPayloadChangedError"):
        _mcp_call(create_server(mod), "replace_prefab_reference", arguments)


def test_mcp_current_guid_mismatch(prefab_mod: Path, tmp_path: Path) -> None:
    mod, scene = _clone_mod(prefab_mod, tmp_path)
    arguments = {**_mcp_arguments(scene), "expected_current_prefab_guid": "stale"}
    with pytest.raises(ToolError, match="PrefabReferenceCurrentGuidMismatchError"):
        _mcp_call(create_server(mod), "replace_prefab_reference", arguments)


def test_mcp_missing_template_rejected(prefab_mod: Path, tmp_path: Path) -> None:
    mod, scene = _clone_mod(prefab_mod, tmp_path)
    asset = mod / "Assets" / "Prefabs" / "Unseen.pfab"
    asset.write_bytes(b"unseen")
    Path(str(asset) + ".pmeta").write_text(
        json.dumps({"guid": {"serializedGuid": "unseen"}, "tags": ["prefabs"]})
    )
    arguments = _mcp_arguments(scene)
    arguments.pop("target_prefab_guid")
    arguments["target_prefab_relative_path"] = "Assets/Prefabs/Unseen.pfab"
    with pytest.raises(ToolError, match="NoValidatedPrefabReferenceTemplateError"):
        _mcp_call(create_server(mod), "replace_prefab_reference", arguments)


def test_mcp_validate_after_prefab_replace(prefab_mod: Path, tmp_path: Path) -> None:
    mod, scene = _clone_mod(prefab_mod, tmp_path)
    server = create_server(mod)
    _mcp_structured(server, "replace_prefab_reference", _mcp_arguments(scene))
    validation = _mcp_structured(
        server, "validate_scene", {"scene_path": str(scene)}
    )
    assert validation["valid"] is True


def test_mcp_prefab_reference_integration(prefab_mod: Path, tmp_path: Path) -> None:
    mod, scene = _clone_mod(prefab_mod, tmp_path)
    before_assets = {
        path.relative_to(mod): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (mod / "Assets").rglob("*")
        if path.is_file()
    }
    object_guid, component_guid, event, rid = _writer_args(scene)
    server = create_server(mod)

    async def integration():
        async with Client(server) as client:
            listed = await client.call_tool(
                "inspect_action_list",
                {
                    "scene_path": str(scene),
                    "object_identifier": object_guid,
                    "component_identifier": component_guid,
                    "event_property": event,
                },
            )
            before = await client.call_tool(
                "inspect_action_references",
                {
                    "scene_path": str(scene),
                    "object_identifier": object_guid,
                    "component_identifier": component_guid,
                    "event_property": event,
                    "action_rid": rid,
                    "limit": 100,
                },
            )
            prefab_field = next(
                item for item in before.structured_content["references"]
                if item["field"] == "m_prefabs"
            )
            changed = await client.call_tool(
                "replace_prefab_reference",
                {
                    "scene_path": str(scene),
                    "object_identifier": object_guid,
                    "component_identifier": component_guid,
                    "event_property": event,
                    "action_rid": rid,
                    "prefab_index": 0,
                    "target_prefab_guid": prefab_field["items"][1]["normalized_value"]["guid"],
                    "expected_current_prefab_guid": prefab_field["items"][0]["normalized_value"]["guid"],
                    "expected_payload_sha256": before.structured_content["payload_sha256"],
                },
            )
            after = await client.call_tool(
                "inspect_action_references",
                {
                    "scene_path": str(scene),
                    "object_identifier": object_guid,
                    "component_identifier": component_guid,
                    "event_property": event,
                    "action_rid": rid,
                    "limit": 100,
                },
            )
            validation = await client.call_tool(
                "validate_scene", {"scene_path": str(scene)}
            )
        return listed, before, changed, after, validation

    listed, before, changed, after, validation = anyio.run(integration)
    before_field = next(item for item in before.structured_content["references"] if item["field"] == "m_prefabs")
    after_field = next(item for item in after.structured_content["references"] if item["field"] == "m_prefabs")
    assert listed.structured_content["actions"][0]["rid"] == rid
    assert len(before_field["items"]) == len(after_field["items"]) == 23
    assert after_field["items"][0]["normalized_value"]["guid"] == before_field["items"][1]["normalized_value"]["guid"]
    assert all(before_field["items"][i]["reference_sha256"] == after_field["items"][i]["reference_sha256"] for i in range(1, 23))
    assert changed.structured_content["validation"]["checks"]["graph_fingerprint_unchanged"] is True
    assert validation.structured_content["valid"] is True
    after_assets = {
        path.relative_to(mod): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (mod / "Assets").rglob("*")
        if path.is_file()
    }
    assert after_assets == before_assets
