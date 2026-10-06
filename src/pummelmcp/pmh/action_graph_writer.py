"""Transactional v0.5 writer for top-level Action graph add/delete/move."""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .action_graph import (
    ActionGraph,
    GraphFingerprintV2,
    require_safe_action_graph,
    validate_observed_rid_allocation,
)
from .action_schemas import CREATABLE_ACTION_CLASSES, validate_action_template
from .actions import (
    ACTION_EVENT_PROPERTIES,
    ActionEntry,
    ActionList,
    encode_action_varuint7,
    parse_action_payload,
)
from .errors import (
    ActionDependencyError,
    ActionFramingError,
    ActionGraphWriteError,
    ActionPayloadChangedError,
    ActionRidNotFoundError,
    ActionTemplateError,
    ActionValidationError,
)
from .json_spans import JsonNode, JsonSpanError, parse_json_spans
from .models import Component, Field, GameObject, Scene, SourceSpan
from .reader import PMHReader
from .writer import PMHScene


@dataclass(frozen=True, slots=True)
class ActionTemplateSource:
    object_guid: str
    hierarchy_path: str
    component_guid: str
    event_property: str
    source_rid: int
    source_file: str | None = None


@dataclass(frozen=True, slots=True)
class ActionTemplate:
    namespace: str
    class_name: str
    assembly: str
    type_tag: int
    source: ActionTemplateSource
    managed_reference_sha256: str
    action_item_raw: bytes
    refid_item_raw: bytes
    managed_reference_raw: bytes
    data: Mapping[str, Any]

    def summary(self) -> dict[str, Any]:
        return {
            "namespace": self.namespace,
            "class": self.class_name,
            "assembly": self.assembly,
            "type_tag": self.type_tag,
            "managed_reference_sha256": self.managed_reference_sha256,
            "source": asdict(self.source),
            "evidence_status": "CONFIRMED",
            "template_schema_validated": True,
        }


@dataclass(frozen=True, slots=True)
class ActionTemplateCatalog:
    templates: tuple[ActionTemplate, ...]

    def matching(self, namespace: str, class_name: str) -> tuple[ActionTemplate, ...]:
        return tuple(
            item for item in self.templates
            if item.namespace == namespace and item.class_name == class_name
        )


@dataclass(frozen=True, slots=True)
class ActionGraphValidationResult:
    passed: bool
    checks: Mapping[str, bool]


@dataclass(frozen=True, slots=True)
class ActionGraphChangeReport:
    operation: str
    object_name: str
    object_guid: str
    hierarchy_path: str
    component_guid: str
    event_property: str
    source_file: Path
    dry_run: bool
    backup_path: Path | None
    affected_rid: int
    old_index: int | None
    new_index: int | None
    action_count_before: int
    action_count_after: int
    reference_count_before: int
    reference_count_after: int
    payload_sha256_before: str
    payload_sha256_after: str
    root_json_sha256_before: str
    root_json_sha256_after: str
    old_property_length: int
    new_property_length: int
    bytes_added_or_removed: int
    graph_before: GraphFingerprintV2
    graph_after: GraphFingerprintV2
    validation: ActionGraphValidationResult
    template: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["object"] = result.pop("object_name")
        result["source_file"] = str(self.source_file)
        result["backup_path"] = str(self.backup_path) if self.backup_path else None
        result["graph_before"] = self.graph_before.to_dict()
        result["graph_after"] = self.graph_after.to_dict()
        return result


@dataclass(frozen=True, slots=True)
class _Target:
    obj: GameObject
    component: Component
    field: Field
    parsed: ActionList


def build_action_template_catalog(scene: Scene) -> ActionTemplateCatalog:
    """Collect exact, source-backed top-level Action templates from one scene."""
    templates: list[ActionTemplate] = []
    for obj in scene.walk():
        for component in obj.components:
            if component.type_name != "ModTrigger":
                continue
            for field in component.fields:
                if field.name not in ACTION_EVENT_PROPERTIES:
                    continue
                try:
                    parsed = parse_action_payload(field.raw, source_property=field.name)
                    require_safe_action_graph(parsed)
                except Exception:
                    continue
                root = parsed.root_json_document
                if root is None or parsed.references_version != 2:
                    continue
                try:
                    actions_node = root.node_at(("m_actions",))
                    refids_node = root.node_at(("references", "RefIds"))
                except JsonSpanError:
                    continue
                if actions_node.kind != "array" or refids_node.kind != "array":
                    continue
                references = {item.rid: item for item in parsed.references}
                ref_positions = {item.rid: i for i, item in enumerate(parsed.references)}
                for action in parsed.actions:
                    reference = action.resolved_reference
                    if (
                        action.rid is None or reference is None
                        or reference.raw_segment is None or reference.type_tag is None
                        or not isinstance(reference.data, Mapping)
                    ):
                        continue
                    type_info = reference.type_info
                    if (
                        not type_info.namespace or not type_info.class_name
                        or not type_info.assembly
                    ):
                        continue
                    ref_index = ref_positions[action.rid]
                    templates.append(
                        ActionTemplate(
                            namespace=type_info.namespace,
                            class_name=type_info.class_name,
                            assembly=type_info.assembly,
                            type_tag=reference.type_tag,
                            source=ActionTemplateSource(
                                obj.guid, obj.hierarchy_path, component.guid,
                                field.name, action.rid,
                                str(scene.source) if scene.source is not None else None,
                            ),
                            managed_reference_sha256=_sha256(reference.raw_segment),
                            action_item_raw=_node_raw(root.raw, actions_node.items[action.index]),
                            refid_item_raw=_node_raw(root.raw, refids_node.items[ref_index]),
                            managed_reference_raw=reference.raw_segment,
                            data=reference.data,
                        )
                    )
    return ActionTemplateCatalog(tuple(templates))


class ActionGraphWriter:
    def __init__(self, path: str | Path) -> None:
        self.loaded = PMHScene.load(path)
        self.source = self.loaded.source
        self.original_bytes = self.loaded._original_bytes

    def add_action(
        self,
        object_identifier: str,
        component_identifier: str,
        event_property: str,
        action_class: str,
        *,
        insert_index: int | None = None,
        template_sha256: str | None = None,
        template_scene_path: str | Path | None = None,
        expected_payload_sha256: str | None = None,
        dry_run: bool = False,
        backup: bool = True,
    ) -> ActionGraphChangeReport:
        target = self._target(
            object_identifier, component_identifier, event_property,
            expected_payload_sha256,
        )
        namespace = "ModSystem.Logic"
        if (namespace, action_class) not in CREATABLE_ACTION_CLASSES:
            available = ", ".join(sorted(item[1] for item in CREATABLE_ACTION_CLASSES))
            raise ActionTemplateError(
                f"no Phase 3 creation schema for {namespace}.{action_class}; "
                f"supported classes: {available}"
            )
        new_rid = validate_observed_rid_allocation(target.parsed)
        index = len(target.parsed.actions) if insert_index is None else insert_index
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index <= len(target.parsed.actions):
            raise ActionGraphWriteError("insert_index is outside the existing Action list")
        template = self._select_template(
            target,
            namespace,
            action_class,
            template_sha256,
            template_scene_path,
        )
        action_item = _patch_top_level_rid(template.action_item_raw, template.source.source_rid, new_rid)
        refid_item = _patch_top_level_rid(template.refid_item_raw, template.source.source_rid, new_rid)
        new_root = _mutate_root_arrays(
            target.parsed,
            action_items=_insert(_root_items(target.parsed, ("m_actions",)), index, action_item),
            refid_items=_insert(
                _root_items(target.parsed, ("references", "RefIds")),
                len(target.parsed.references), refid_item,
            ),
            style_source=(template.action_item_raw, template.refid_item_raw),
        )
        tails = _tail_frames(target.parsed)
        tails.append(
            struct.pack("<H", template.type_tag)
            + encode_action_varuint7(len(template.managed_reference_raw))
            + template.managed_reference_raw,
        )
        new_payload = _rebuild_payload(target.parsed, new_root, tails)
        return self._commit(
            target, new_payload, operation="add", affected_rid=new_rid,
            old_index=None, new_index=index, dry_run=dry_run, backup=backup,
            template=template,
        )

    def delete_action(
        self,
        object_identifier: str,
        component_identifier: str,
        event_property: str,
        action_rid: int,
        *,
        expected_payload_sha256: str | None = None,
        dry_run: bool = False,
        backup: bool = True,
    ) -> ActionGraphChangeReport:
        target = self._target(
            object_identifier, component_identifier, event_property,
            expected_payload_sha256,
        )
        graph = ActionGraph.from_action_list(target.parsed)
        action = _resolve_action(target.parsed, action_rid)
        incoming = dict(graph.incoming).get(action_rid, ())
        unknown = [
            field for node in graph.nodes if node.rid != action_rid
            for field in node.unknown_dependency_fields
        ]
        if incoming or unknown:
            raise ActionDependencyError(
                f"cannot prove deletion safe; incoming={incoming!r}, unknown={unknown!r}"
            )
        ref_index = next(i for i, item in enumerate(target.parsed.references) if item.rid == action_rid)
        action_items = _root_items(target.parsed, ("m_actions",))
        ref_items = _root_items(target.parsed, ("references", "RefIds"))
        new_root = _mutate_root_arrays(
            target.parsed,
            action_items=action_items[: action.index] + action_items[action.index + 1 :],
            refid_items=ref_items[:ref_index] + ref_items[ref_index + 1 :],
        )
        tails = _tail_frames(target.parsed)
        del tails[ref_index]
        return self._commit(
            target, _rebuild_payload(target.parsed, new_root, tails),
            operation="delete", affected_rid=action_rid,
            old_index=action.index, new_index=None, dry_run=dry_run, backup=backup,
        )

    def move_action(
        self,
        object_identifier: str,
        component_identifier: str,
        event_property: str,
        action_rid: int,
        new_index: int,
        *,
        expected_payload_sha256: str | None = None,
        dry_run: bool = False,
        backup: bool = True,
    ) -> ActionGraphChangeReport:
        target = self._target(
            object_identifier, component_identifier, event_property,
            expected_payload_sha256,
        )
        action = _resolve_action(target.parsed, action_rid)
        count = len(target.parsed.actions)
        if isinstance(new_index, bool) or not isinstance(new_index, int) or not 0 <= new_index < count:
            raise ActionGraphWriteError("new_index is outside the existing Action list")
        action_items = _root_items(target.parsed, ("m_actions",))
        moved = action_items.pop(action.index)
        action_items.insert(new_index, moved)
        new_root = _mutate_root_arrays(
            target.parsed,
            action_items=action_items,
            refid_items=_root_items(target.parsed, ("references", "RefIds")),
        )
        return self._commit(
            target, _rebuild_payload(target.parsed, new_root, _tail_frames(target.parsed)),
            operation="move", affected_rid=action_rid,
            old_index=action.index, new_index=new_index, dry_run=dry_run, backup=backup,
        )

    def _target(
        self, object_identifier: str, component_identifier: str,
        event_property: str, expected_hash: str | None,
    ) -> _Target:
        from .actions import SUPPORTED_ACTION_EVENT_PROPERTIES
        if event_property not in SUPPORTED_ACTION_EVENT_PROPERTIES:
            raise ActionFramingError(f"unsupported event property {event_property!r}")
        obj = self.loaded.get_object(object_identifier)
        component = self.loaded.get_component(object_identifier, component_identifier)
        from .actions import ACTION_COMPONENT_EVENTS
        if event_property not in ACTION_COMPONENT_EVENTS.get(component.type_name, ()):
            raise ActionFramingError("Action property is unsupported for this component")
        field = component.get_field(event_property)
        if field.source_span is None:
            raise ActionFramingError("Action property has no dynamic source span")
        actual_hash = _sha256(field.raw)
        if expected_hash is not None:
            if len(expected_hash) != 64 or any(c not in "0123456789abcdefABCDEF" for c in expected_hash):
                raise ActionPayloadChangedError("expected_payload_sha256 must be 64 hexadecimal characters")
            if actual_hash != expected_hash.casefold():
                raise ActionPayloadChangedError(
                    "Action payload changed after inspection; inspect it again before writing"
                )
        parsed = parse_action_payload(field.raw, source_property=event_property, source_span=field.source_span)
        require_safe_action_graph(parsed)
        return _Target(obj, component, field, parsed)

    def _select_template(
        self, target: _Target, namespace: str, class_name: str,
        requested_hash: str | None, template_scene_path: str | Path | None,
    ) -> ActionTemplate:
        if template_scene_path is not None and requested_hash is None:
            raise ActionTemplateError(
                "external template selection requires template_sha256"
            )
        template_scene = (
            PMHScene.load(template_scene_path).scene
            if template_scene_path is not None
            else self.loaded.scene
        )
        catalog = build_action_template_catalog(template_scene)
        candidates = catalog.matching(namespace, class_name)
        if requested_hash is not None:
            candidates = tuple(item for item in candidates if item.managed_reference_sha256 == requested_hash.casefold())
        elif any(item.type_info and item.type_info.class_name == class_name for item in target.parsed.actions):
            local_rids = {item.rid for item in target.parsed.actions if item.type_info and item.type_info.class_name == class_name}
            candidates = tuple(
                item for item in candidates
                if item.source.component_guid == target.component.guid
                and item.source.event_property == target.field.name
                and item.source.source_rid in local_rids
            )
        unique: dict[str, ActionTemplate] = {}
        for item in candidates:
            unique.setdefault(item.managed_reference_sha256, item)
        if len(unique) != 1:
            raise ActionTemplateError(
                f"template selection requires one exact managed-reference hash; found {len(unique)} variants"
            )
        template = next(iter(unique.values()))
        validate_action_template(
            template.namespace,
            template.class_name,
            template.assembly,
            template.type_tag,
            template.data,
        )
        return template

    def _commit(
        self, target: _Target, new_payload: bytes, *, operation: str,
        affected_rid: int, old_index: int | None, new_index: int | None,
        dry_run: bool, backup: bool, template: ActionTemplate | None = None,
    ) -> ActionGraphChangeReport:
        span = target.field.source_span
        assert span is not None
        record_start = span.start - 4
        if self.original_bytes[record_start:span.start] != struct.pack("<I", len(target.field.raw)):
            raise ActionFramingError("PMH Action property length is not adjacent")
        patched = (
            self.original_bytes[:record_start] + struct.pack("<I", len(new_payload))
            + new_payload + self.original_bytes[span.end:]
        )
        before_graph = ActionGraph.from_action_list(target.parsed)
        if dry_run:
            reparsed_scene = PMHReader().read_bytes(patched, source=self.source)
            validation, parsed_after = self._validate(
                reparsed_scene, patched, target, new_payload, operation,
                affected_rid, old_index, new_index, record_start, template,
            )
            backup_path = None
        else:
            self.loaded._assert_source_unchanged()
            holder: list[ActionList] = []
            def validate_temp(scene: Scene, data: bytes) -> ActionGraphValidationResult:
                result, parsed = self._validate(
                    scene, data, target, new_payload, operation,
                    affected_rid, old_index, new_index, record_start, template,
                )
                holder.append(parsed)
                return result
            validation, backup_path = self.loaded._replace_with_validation(
                patched, validate_temp, backup=backup
            )
            parsed_after = holder[0]
        after_graph = ActionGraph.from_action_list(parsed_after)
        return ActionGraphChangeReport(
            operation=operation, object_name=target.obj.name, object_guid=target.obj.guid,
            hierarchy_path=target.obj.hierarchy_path, component_guid=target.component.guid,
            event_property=target.field.name, source_file=self.source, dry_run=dry_run,
            backup_path=backup_path, affected_rid=affected_rid, old_index=old_index,
            new_index=new_index, action_count_before=len(target.parsed.actions),
            action_count_after=len(parsed_after.actions),
            reference_count_before=len(target.parsed.references),
            reference_count_after=len(parsed_after.references),
            payload_sha256_before=_sha256(target.field.raw), payload_sha256_after=_sha256(new_payload),
            root_json_sha256_before=_sha256(target.parsed.root_segment.raw),
            root_json_sha256_after=_sha256(parsed_after.root_segment.raw),
            old_property_length=len(target.field.raw), new_property_length=len(new_payload),
            bytes_added_or_removed=len(new_payload)-len(target.field.raw),
            graph_before=GraphFingerprintV2.from_graph(before_graph),
            graph_after=GraphFingerprintV2.from_graph(after_graph),
            validation=validation,
            template=template.summary() if template is not None else None,
        )

    def _validate(
        self, scene: Scene, patched: bytes, target: _Target, new_payload: bytes,
        operation: str, rid: int, old_index: int | None, new_index: int | None,
        record_start: int, template: ActionTemplate | None,
    ) -> tuple[ActionGraphValidationResult, ActionList]:
        new_record_end = record_start + 4 + len(new_payload)
        checks: dict[str, bool] = {
            "magic_unchanged": scene.magic == self.loaded.scene.magic,
            "version_unchanged": scene.version == self.loaded.scene.version,
            "object_count_unchanged": scene.object_count == self.loaded.scene.object_count,
            "component_count_unchanged": scene.component_count == self.loaded.scene.component_count,
            "pmh_fully_consumed": scene.fully_consumed,
            "prefix_before_property_record_unchanged": patched[:record_start] == self.original_bytes[:record_start],
            "suffix_after_property_record_unchanged": patched[new_record_end:] == self.original_bytes[target.field.source_span.end:],
            "property_length_updated": patched[record_start:record_start+4] == struct.pack("<I",len(new_payload)),
        }
        obj = next((o for o in scene.walk() if o.guid == target.obj.guid), None)
        component = next((c for c in obj.components if c.guid == target.component.guid), None) if obj else None
        field = component.get_field(target.field.name) if component else None
        checks["target_property_payload_matches"] = field is not None and field.raw == new_payload
        checks["other_component_properties_unchanged"] = component is not None and [
            (f.name,f.raw) for f in component.fields if f.name != target.field.name
        ] == [(f.name,f.raw) for f in target.component.fields if f.name != target.field.name]
        try:
            after = parse_action_payload(field.raw if field else b"", source_property=target.field.name)
            graph_after = ActionGraph.from_action_list(after)
        except Exception as exc:
            raise ActionValidationError(f"patched Action graph cannot be reparsed: {exc}") from exc
        before = ActionGraph.from_action_list(target.parsed)
        checks["root_unrelated_bytes_unchanged"] = _root_unrelated_bytes(
            target.parsed
        ) == _root_unrelated_bytes(after)
        before_fp = GraphFingerprintV2.from_graph(before)
        after_fp = GraphFingerprintV2.from_graph(graph_after)
        checks["action_payload_fully_consumed"] = after.fully_consumed
        if operation == "add":
            expected_order = list(before.ordered_rids); expected_order.insert(new_index, rid)
            expected_refs = [*before.reference_order, rid]
            checks["expected_action_order_delta"] = graph_after.ordered_rids == tuple(expected_order)
            checks["expected_reference_membership_delta"] = graph_after.reference_order == tuple(expected_refs)
            added_actions = [a for a in after.actions if a.rid == rid]
            added_action = added_actions[0] if len(added_actions) == 1 else None
            added_reference = added_action.resolved_reference if added_action else None
            checks["new_rid_resolves"] = added_reference is not None
            checks["template_was_validated"] = template is not None
            checks["new_reference_matches_template"] = bool(
                template is not None
                and added_reference is not None
                and added_reference.type_info.namespace == template.namespace
                and added_reference.type_info.class_name == template.class_name
                and added_reference.type_info.assembly == template.assembly
                and added_reference.type_tag == template.type_tag
                and added_reference.raw_segment == template.managed_reference_raw
            )
            try:
                if added_reference is None or not isinstance(added_reference.data, Mapping):
                    raise ActionTemplateError("new Action has no managed-reference data")
                validate_action_template(
                    added_reference.type_info.namespace or "",
                    added_reference.type_info.class_name or "",
                    added_reference.type_info.assembly or "",
                    added_reference.type_tag if added_reference.type_tag is not None else -1,
                    added_reference.data,
                )
            except ActionTemplateError:
                checks["new_action_schema_valid"] = False
            else:
                checks["new_action_schema_valid"] = True
            checks["existing_reference_bytes_unchanged"] = all(
                graph_after.node(n.rid).segment_sha256 == n.segment_sha256 for n in before.nodes
            )
        elif operation == "delete":
            checks["expected_action_order_delta"] = graph_after.ordered_rids == tuple(x for x in before.ordered_rids if x != rid)
            checks["expected_reference_membership_delta"] = graph_after.reference_order == tuple(x for x in before.reference_order if x != rid)
            checks["deleted_rid_absent"] = rid not in graph_after.ordered_rids and rid not in graph_after.reference_order
            checks["remaining_reference_bytes_unchanged"] = all(
                graph_after.node(n.rid).segment_sha256 == n.segment_sha256 for n in before.nodes if n.rid != rid
            )
        else:
            expected_order = list(before.ordered_rids); value=expected_order.pop(old_index); expected_order.insert(new_index,value)
            checks["expected_action_order_delta"] = graph_after.ordered_rids == tuple(expected_order)
            checks["refids_unchanged"] = graph_after.reference_order == before.reference_order
            checks["managed_reference_bytes_unchanged"] = before_fp.references == after_fp.references
        passed = all(checks.values())
        result = ActionGraphValidationResult(passed, checks)
        if not passed:
            raise ActionValidationError(
                "patched Action graph validation failed: "
                + ", ".join(name for name, ok in checks.items() if not ok)
            )
        return result, after


def _resolve_action(parsed: ActionList, rid: int) -> ActionEntry:
    matches = [item for item in parsed.actions if item.rid == rid]
    if len(matches) != 1:
        raise ActionRidNotFoundError(f"Action rid {rid} is not uniquely available")
    return matches[0]


def _node_raw(raw: bytes, node: JsonNode) -> bytes:
    return raw[node.span.start:node.span.end]


def _root_items(parsed: ActionList, path: tuple[str, ...]) -> list[bytes]:
    document = parsed.root_json_document
    if document is None:
        raise ActionFramingError("root JSON has no source spans")
    node = document.node_at(path)
    if node.kind != "array":
        raise ActionFramingError(f"root JSON path {path!r} is not an array")
    return [_node_raw(document.raw, item) for item in node.items]


def _patch_top_level_rid(raw: bytes, before: int, after: int) -> bytes:
    document = parse_json_spans(raw)
    node = document.root.member("rid")
    if node.value != before or node.kind != "number":
        raise ActionTemplateError("template rid token is not exact")
    return raw[:node.span.start] + str(after).encode("ascii") + raw[node.span.end:]


def _insert(items: list[bytes], index: int, item: bytes) -> list[bytes]:
    result = list(items); result.insert(index,item); return result


def _render_array(original_raw: bytes, node: JsonNode, items: list[bytes], fallback_item: bytes | None = None) -> bytes:
    if not items:
        return b"[]"
    if node.items:
        prefix = original_raw[node.span.start+1:node.items[0].span.start]
        suffix = original_raw[node.items[-1].span.end:node.span.end-1]
        if len(node.items) >= 2:
            sep = original_raw[node.items[0].span.end:node.items[1].span.start]
        else:
            sep = b"," + prefix
    else:
        # All observed serializer roots use four-space indentation, with array items
        # nested two levels below the root. This path is admitted only with a real
        # source-backed template item, never arbitrary JSON.
        if fallback_item is None:
            raise ActionTemplateError("empty array insertion requires a source template")
        prefix, suffix, sep = b"\n        ", b"\n    ", b",\n        "
    return b"[" + prefix + sep.join(items) + suffix + b"]"


def _mutate_root_arrays(
    parsed: ActionList, *, action_items: list[bytes], refid_items: list[bytes],
    style_source: tuple[bytes, bytes] | None = None,
) -> bytes:
    document = parsed.root_json_document
    if document is None:
        raise ActionFramingError("root JSON has no source spans")
    actions_node = document.node_at(("m_actions",))
    refs_node = document.node_at(("references", "RefIds"))
    replacements = [
        (actions_node.span, _render_array(document.raw, actions_node, action_items, style_source[0] if style_source else None)),
        (refs_node.span, _render_array(document.raw, refs_node, refid_items, style_source[1] if style_source else None)),
    ]
    result = document.raw
    for span, value in sorted(replacements, key=lambda x:x[0].start, reverse=True):
        result = result[:span.start] + value + result[span.end:]
    parse_json_spans(result)
    return result


def _tail_frames(parsed: ActionList) -> list[bytes]:
    result=[]
    for index, reference in enumerate(parsed.references):
        type_segment=next(s for s in parsed.segments if s.kind=="reference_type_tag" and s.index==index)
        if reference.source_span is None:
            raise ActionFramingError("reference has no framed source span")
        result.append(parsed.raw_payload[type_segment.source_span.start:reference.source_span.end])
    return result


def _rebuild_payload(parsed: ActionList, root: bytes, tails: Iterable[bytes]) -> bytes:
    tail_list=list(tails)
    outer=next(s for s in parsed.segments if s.kind=="outer_prefix_u16").raw
    return outer + encode_action_varuint7(len(root)) + root + struct.pack("<H",len(tail_list)) + b"".join(tail_list)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _root_unrelated_bytes(parsed: ActionList) -> tuple[bytes, bytes, bytes]:
    document = parsed.root_json_document
    if document is None:
        raise ActionFramingError("root JSON has no source spans")
    first = document.node_at(("m_actions",)).span
    second = document.node_at(("references", "RefIds")).span
    if first.start > second.start:
        first, second = second, first
    return (
        document.raw[:first.start],
        document.raw[first.end:second.start],
        document.raw[second.end:],
    )
