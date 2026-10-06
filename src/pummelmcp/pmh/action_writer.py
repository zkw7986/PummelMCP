"""Safe source-span writer for approved fields of existing Action instances."""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from .action_schemas import (
    ActionFieldSchema,
    get_action_schema,
    validate_bool_json,
    validate_vector3_json,
)
from .actions import (
    ACTION_EVENT_PROPERTIES,
    ActionEntry,
    ActionList,
    encode_action_varuint7,
    parse_action_payload,
)
from .errors import (
    ActionClassMismatchError,
    ActionFramingError,
    ActionPayloadChangedError,
    ActionRidNotFoundError,
    ActionValidationError,
    AmbiguousActionError,
    DuplicateRidError,
    InvalidActionFieldValueError,
    ReadOnlyActionFieldError,
    UnknownActionFieldError,
    UnknownActionSchemaError,
)
from .json_spans import JsonNode, JsonSpanError, parse_json_spans
from .models import Component, Field, GameObject, Scene, SourceSpan
from .reader import PMHReader
from .writer import PMHScene


@dataclass(frozen=True, slots=True)
class GraphFingerprint:
    event_property: str
    action_rids: tuple[int | None, ...]
    references: tuple[tuple[int, str | None, str | None, str | None, int | None], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "event_property": self.event_property,
            "action_rids": list(self.action_rids),
            "references": [
                {
                    "rid": item[0],
                    "namespace": item[1],
                    "class": item[2],
                    "assembly": item[3],
                    "type_tag": item[4],
                }
                for item in self.references
            ],
        }


@dataclass(frozen=True, slots=True)
class ActionFieldMemberChange:
    before: Any
    after: Any
    json_source_start: int
    token_length_before: int
    token_length_after: int


@dataclass(frozen=True, slots=True)
class ActionValidationResult:
    passed: bool
    checks: Mapping[str, bool]


@dataclass(frozen=True, slots=True)
class ActionFieldChangeReport:
    object_name: str
    object_guid: str
    hierarchy_path: str
    component_guid: str
    event_property: str
    action_index: int
    action_rid: int
    namespace: str
    class_name: str
    field: str
    changes: Mapping[str, ActionFieldMemberChange]
    source_file: Path
    dry_run: bool
    backup_path: Path | None
    validation: ActionValidationResult
    payload_sha256_before: str
    payload_sha256_after: str
    managed_reference_sha256_before: str
    managed_reference_sha256_after: str
    root_json_sha256_before: str
    root_json_sha256_after: str
    old_property_length: int
    new_property_length: int
    bytes_added_or_removed: int
    graph_fingerprint_before: GraphFingerprint
    graph_fingerprint_after: GraphFingerprint

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["object"] = result.pop("object_name")
        result["action"] = {
            "index": result.pop("action_index"),
            "rid": result.pop("action_rid"),
            "namespace": result.pop("namespace"),
            "class": result.pop("class_name"),
        }
        result["source_file"] = str(self.source_file)
        if self.backup_path is not None:
            result["backup_path"] = str(self.backup_path)
        result["graph_fingerprint_before"] = self.graph_fingerprint_before.to_dict()
        result["graph_fingerprint_after"] = self.graph_fingerprint_after.to_dict()
        return result


@dataclass(frozen=True, slots=True)
class _TokenReplacement:
    member: str
    span: SourceSpan
    before_raw: bytes
    after_raw: bytes
    before: Any
    after: Any


class ActionFieldWriter:
    """Modify one registered field without reserializing any JSON document."""

    def __init__(self, path: str | Path) -> None:
        self.loaded = PMHScene.load(path)
        self.source = self.loaded.source
        self.original_bytes = self.loaded._original_bytes

    def set_action_field(
        self,
        object_identifier: str,
        component_identifier: str,
        event_property: str,
        action_rid: int,
        expected_class: str,
        field_name: str,
        value: Any,
        *,
        dry_run: bool = False,
        expected_payload_sha256: str | None = None,
        backup: bool = True,
    ) -> ActionFieldChangeReport:
        from .actions import SUPPORTED_ACTION_EVENT_PROPERTIES
        if event_property not in SUPPORTED_ACTION_EVENT_PROPERTIES:
            allowed = ", ".join(sorted(SUPPORTED_ACTION_EVENT_PROPERTIES))
            raise ActionFramingError(f"event_property must be one of: {allowed}")
        if isinstance(action_rid, bool) or not isinstance(action_rid, int):
            raise ActionRidNotFoundError("action_rid must be an integer")
        if not isinstance(expected_class, str) or not expected_class:
            raise ActionClassMismatchError("expected_class must be a non-empty string")
        if not isinstance(field_name, str) or not field_name:
            raise UnknownActionFieldError("field_name must be a non-empty string")

        obj = self.loaded.get_object(object_identifier)
        component = self.loaded.get_component(object_identifier, component_identifier)
        from .actions import ACTION_COMPONENT_EVENTS
        if event_property not in ACTION_COMPONENT_EVENTS.get(component.type_name, ()):
            raise ActionFramingError(
                f"Action property is unsupported for {component.type_name!r}"
            )
        try:
            field = component.get_field(event_property)
        except Exception as exc:
            raise ActionFramingError(
                f"event property {event_property!r} is not present on {component.guid!r}"
            ) from exc
        if field.source_span is None:
            raise ActionFramingError("Action property has no dynamic PMH source span")

        payload_hash = _sha256(field.raw)
        if expected_payload_sha256 is not None:
            if not _valid_sha256(expected_payload_sha256):
                raise ActionPayloadChangedError(
                    "expected_payload_sha256 must be 64 hexadecimal characters"
                )
            if payload_hash != expected_payload_sha256.casefold():
                raise ActionPayloadChangedError(
                    "Action payload changed after inspection; inspect it again before writing"
                )

        try:
            parsed = parse_action_payload(
                field.raw, source_property=event_property, source_span=field.source_span
            )
        except Exception as exc:
            raise ActionFramingError(f"Action payload framing failed: {exc}") from exc
        self._require_safe_graph(parsed)
        action = self._resolve_action(parsed, action_rid)
        type_info = action.type_info
        if type_info is None or action.resolved_reference is None:
            raise ActionRidNotFoundError(f"Action rid {action_rid} is unresolved")
        if type_info.class_name != expected_class:
            raise ActionClassMismatchError(
                f"Action rid {action_rid} is {type_info.class_name!r}, not expected {expected_class!r}"
            )
        schema = get_action_schema(type_info.namespace, type_info.class_name)
        if schema is None:
            raise UnknownActionSchemaError(
                f"Action {type_info.namespace}.{type_info.class_name} is read-only; no v0.4b schema"
            )
        field_schema = schema.get(field_name)
        if field_schema is None:
            raise UnknownActionFieldError(
                f"field {field_name!r} is not registered for {schema.namespace}.{schema.class_name}"
            )
        if not field_schema.writable:
            detail = f" {field_schema.note}" if field_schema.note else ""
            raise ReadOnlyActionFieldError(
                f"{schema.class_name}.{field_name} is read-only in v0.4b.{detail}"
            )

        reference = action.resolved_reference
        if reference.raw_segment is None or reference.source_span is None or reference.segment_index is None:
            raise ActionFramingError("target Action has no separately framed reference JSON")
        try:
            document = reference.json_document or parse_json_spans(reference.raw_segment)
            target_node = document.root.member(field_name)
        except JsonSpanError as exc:
            raise UnknownActionFieldError(
                f"registered field {field_name!r} is not uniquely present: {exc}"
            ) from exc
        replacements = self._plan_replacements(
            reference.raw_segment, target_node, field_schema, field_name, value
        )
        patched_reference = _apply_replacements(reference.raw_segment, replacements)
        # Reparse the exact patched document before it is admitted to PMH framing.
        parse_json_spans(patched_reference)

        new_payload = _reframe_reference_json(
            parsed, reference.segment_index, patched_reference
        )

        property_span = field.source_span
        record_start = property_span.start - 4
        if record_start < 0 or self.original_bytes[record_start:property_span.start] != struct.pack(
            "<I", len(field.raw)
        ):
            raise ActionFramingError("PMH Action property length field is not adjacent or valid")
        old_record_end = property_span.end
        patched_bytes = (
            self.original_bytes[:record_start]
            + struct.pack("<I", len(new_payload))
            + new_payload
            + self.original_bytes[old_record_end:]
        )

        context = _ValidationContext(
            obj=obj,
            component=component,
            field=field,
            parsed=parsed,
            action=action,
            field_name=field_name,
            requested=value,
            replacements=tuple(replacements),
            patched_reference=patched_reference,
            new_payload=new_payload,
            record_start=record_start,
            old_record_end=old_record_end,
        )
        if dry_run:
            reparsed = PMHReader().read_bytes(patched_bytes, source=self.source)
            validation, parsed_after = self._validate(reparsed, patched_bytes, context)
            backup_path = None
        else:
            self.loaded._assert_source_unchanged()
            after_holder: list[ActionList] = []

            def validate_temp(reparsed: Scene, data: bytes) -> ActionValidationResult:
                result, after = self._validate(reparsed, data, context)
                after_holder.append(after)
                return result

            validation, backup_path = self.loaded._replace_with_validation(
                patched_bytes, validate_temp, backup=backup
            )
            parsed_after = after_holder[0]

        after_action = self._resolve_action(parsed_after, action_rid)
        after_reference = after_action.resolved_reference
        assert after_reference is not None and after_reference.raw_segment is not None
        type_namespace = type_info.namespace or ""
        type_class = type_info.class_name or ""
        changes = {
            item.member: ActionFieldMemberChange(
                before=item.before,
                after=item.after,
                json_source_start=item.span.start,
                token_length_before=len(item.before_raw),
                token_length_after=len(item.after_raw),
            )
            for item in replacements
        }
        return ActionFieldChangeReport(
            object_name=obj.name,
            object_guid=obj.guid,
            hierarchy_path=obj.hierarchy_path,
            component_guid=component.guid,
            event_property=event_property,
            action_index=action.index,
            action_rid=action_rid,
            namespace=type_namespace,
            class_name=type_class,
            field=field_name,
            changes=changes,
            source_file=self.source,
            dry_run=dry_run,
            backup_path=backup_path,
            validation=validation,
            payload_sha256_before=payload_hash,
            payload_sha256_after=_sha256(new_payload),
            managed_reference_sha256_before=_sha256(reference.raw_segment),
            managed_reference_sha256_after=_sha256(after_reference.raw_segment),
            root_json_sha256_before=_sha256(parsed.root_segment.raw),
            root_json_sha256_after=_sha256(parsed_after.root_segment.raw),
            old_property_length=len(field.raw),
            new_property_length=len(new_payload),
            bytes_added_or_removed=len(new_payload) - len(field.raw),
            graph_fingerprint_before=_graph_fingerprint(parsed, event_property),
            graph_fingerprint_after=_graph_fingerprint(parsed_after, event_property),
        )

    @staticmethod
    def _require_safe_graph(parsed: ActionList) -> None:
        if not parsed.fully_consumed or parsed.unparsed_ranges:
            raise ActionFramingError("Action payload is not fully consumed")
        if parsed.duplicate_rids:
            raise DuplicateRidError(f"duplicate Action rid values: {parsed.duplicate_rids!r}")
        if parsed.dangling_rids:
            raise ActionFramingError(f"dangling Action rid values: {parsed.dangling_rids!r}")
        reference_segments = [
            item for item in parsed.segments if item.kind == "reference_json"
        ]
        if len(reference_segments) != len(parsed.references):
            raise ActionFramingError(
                "managed-reference metadata and framed segment counts differ"
            )
        unsafe_warnings = [
            warning
            for warning in parsed.warnings
            if not warning.startswith("unreferenced RefIds rid values")
        ]
        unsafe_reference_warnings = [
            warning
            for reference in parsed.references
            for warning in reference.warnings
            if warning != "reference JSON segment differs from RefIds data"
        ]
        if unsafe_warnings or unsafe_reference_warnings:
            raise ActionFramingError(
                f"Action graph metadata is unsafe: {unsafe_warnings + unsafe_reference_warnings!r}"
            )
        if any(
            reference.segment_index is None
            or reference.source_span is None
            or reference.raw_segment is None
            or not isinstance(reference.data, Mapping)
            or reference.data.get("m_type") != reference.type_tag
            for reference in parsed.references
        ):
            raise ActionFramingError("managed-reference segment metadata is incomplete")

    @staticmethod
    def _resolve_action(parsed: ActionList, rid: int) -> ActionEntry:
        matches = [item for item in parsed.actions if item.rid == rid]
        if not matches:
            raise ActionRidNotFoundError(f"Action rid {rid} was not found")
        if len(matches) != 1:
            raise AmbiguousActionError(f"Action rid {rid} identifies {len(matches)} entries")
        return matches[0]

    @staticmethod
    def _plan_replacements(
        raw: bytes,
        node: JsonNode,
        schema: ActionFieldSchema,
        field_name: str,
        value: Any,
    ) -> list[_TokenReplacement]:
        if schema.logical_type in {"Int32Json", "OperationJson", "TargetFlagsJson", "MessageJson", "DurationJson"}:
            kind = schema.logical_type
            if kind == "Int32Json":
                valid = type(value) is int and -(2**31) <= value < 2**31
            elif kind == "OperationJson":
                valid = type(value) is int and value in range(5)
            elif kind == "TargetFlagsJson":
                valid = type(value) is int and value in (1, 2)
            elif kind == "MessageJson":
                valid = type(value) is str and 0 < len(value) <= 256
            else:
                valid = type(value) in (int, float) and 0.5 <= value <= 1200
            if not valid:
                raise InvalidActionFieldValueError(f"invalid {field_name} value for {kind}")
            expected_kind = "string" if kind == "MessageJson" else "number"
            if node.kind != expected_kind or (expected_kind == "number" and isinstance(node.value, bool)):
                raise InvalidActionFieldValueError(f"existing {field_name} has the wrong JSON token type")
            before_raw = raw[node.span.start : node.span.end]
            after_raw = (
                json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                if kind == "MessageJson" else _encode_json_number(value, before_raw)
            )
            return [_TokenReplacement("value", node.span, before_raw, after_raw, node.value, value)]
        if schema.logical_type == "BoolJson":
            validated = validate_bool_json(value)
            if node.kind != "bool":
                raise InvalidActionFieldValueError(
                    f"existing {field_name} token is not a JSON boolean"
                )
            after_raw = b"true" if validated else b"false"
            return [
                _TokenReplacement(
                    "value",
                    node.span,
                    raw[node.span.start : node.span.end],
                    after_raw,
                    node.value,
                    validated,
                )
            ]
        if schema.logical_type == "Vector3Json":
            requested = validate_vector3_json(value)
            if node.kind != "object":
                raise InvalidActionFieldValueError(
                    f"existing {field_name} token is not a JSON object"
                )
            result = []
            for axis, after in requested.items():
                try:
                    child = node.member(axis)
                except JsonSpanError as exc:
                    raise UnknownActionFieldError(
                        f"existing {field_name}.{axis} token is unavailable: {exc}"
                    ) from exc
                if child.kind != "number" or isinstance(child.value, bool):
                    raise InvalidActionFieldValueError(
                        f"existing {field_name}.{axis} token is not a JSON number"
                    )
                before_raw = raw[child.span.start : child.span.end]
                result.append(
                    _TokenReplacement(
                        axis,
                        child.span,
                        before_raw,
                        _encode_json_number(after, before_raw),
                        child.value,
                        after,
                    )
                )
            return result
        raise InvalidActionFieldValueError(
            f"unsupported writable logical type {schema.logical_type!r}"
        )

    def _validate(
        self,
        reparsed: Scene,
        patched: bytes,
        context: _ValidationContext,
    ) -> tuple[ActionValidationResult, ActionList]:
        new_record_end = context.record_start + 4 + len(context.new_payload)
        checks: dict[str, bool] = {
            "magic_unchanged": reparsed.magic == self.loaded.scene.magic,
            "version_unchanged": reparsed.version == self.loaded.scene.version,
            "root_count_unchanged": reparsed.root_count == self.loaded.scene.root_count,
            "object_count_unchanged": reparsed.object_count == self.loaded.scene.object_count,
            "component_count_unchanged": reparsed.component_count == self.loaded.scene.component_count,
            "pmh_fully_consumed": reparsed.fully_consumed,
            "prefix_before_property_record_unchanged": patched[: context.record_start]
            == self.original_bytes[: context.record_start],
            "suffix_after_property_record_unchanged": patched[new_record_end:]
            == self.original_bytes[context.old_record_end :],
            "property_length_updated": patched[context.record_start : context.record_start + 4]
            == struct.pack("<I", len(context.new_payload)),
        }
        target_objects = [item for item in reparsed.walk() if item.guid == context.obj.guid]
        checks["target_object_guid_unchanged"] = len(target_objects) == 1
        new_component: Component | None = None
        if len(target_objects) == 1:
            matches = [
                item
                for item in target_objects[0].components
                if item.guid == context.component.guid and item.type_name == context.component.type_name
            ]
            checks["target_component_guid_unchanged"] = len(matches) == 1
            if len(matches) == 1:
                new_component = matches[0]
        else:
            checks["target_component_guid_unchanged"] = False
        new_field: Field | None = None
        if new_component is not None:
            matches = [item for item in new_component.fields if item.name == context.field.name]
            if len(matches) == 1:
                new_field = matches[0]
        checks["target_property_exists"] = new_field is not None
        checks["target_property_payload_matches"] = (
            new_field is not None and new_field.raw == context.new_payload
        )
        checks["other_component_properties_unchanged"] = (
            new_component is not None
            and [(item.name, item.raw) for item in new_component.fields if item.name != context.field.name]
            == [(item.name, item.raw) for item in context.component.fields if item.name != context.field.name]
        )
        checks["other_event_payloads_unchanged"] = (
            new_component is not None
            and all(
                new_component.get_field(name).raw == context.component.get_field(name).raw
                for name in ACTION_EVENT_PROPERTIES
                if name != context.field.name and name in {field.name for field in context.component.fields}
            )
        )
        try:
            parsed_after = parse_action_payload(
                new_field.raw if new_field is not None else b"",
                source_property=context.field.name,
                source_span=new_field.source_span if new_field else None,
            )
        except Exception as exc:
            raise ActionValidationError(f"patched Action payload cannot be reparsed: {exc}") from exc
        checks["action_payload_fully_consumed"] = parsed_after.fully_consumed
        checks["action_unparsed_ranges_empty"] = not parsed_after.unparsed_ranges
        checks["root_json_bytes_unchanged"] = (
            parsed_after.root_segment.raw == context.parsed.root_segment.raw
        )
        checks["m_actions_bytes_unchanged"] = checks["root_json_bytes_unchanged"]
        before_graph = _graph_fingerprint(context.parsed, context.field.name)
        after_graph = _graph_fingerprint(parsed_after, context.field.name)
        checks["action_order_unchanged"] = before_graph.action_rids == after_graph.action_rids
        checks["rids_unchanged"] = before_graph.action_rids == after_graph.action_rids
        checks["refids_unchanged"] = tuple(item[0] for item in before_graph.references) == tuple(
            item[0] for item in after_graph.references
        )
        checks["namespace_class_assembly_unchanged"] = tuple(
            item[1:4] for item in before_graph.references
        ) == tuple(item[1:4] for item in after_graph.references)
        checks["type_tags_unchanged"] = tuple(item[4] for item in before_graph.references) == tuple(
            item[4] for item in after_graph.references
        )
        checks["graph_fingerprint_unchanged"] = before_graph == after_graph
        target_index = context.action.resolved_reference.segment_index
        checks["non_target_reference_hashes_unchanged"] = all(
            before.raw_segment == after.raw_segment
            for index, (before, after) in enumerate(
                zip(context.parsed.references, parsed_after.references, strict=True)
            )
            if index != target_index
        ) if len(context.parsed.references) == len(parsed_after.references) else False
        after_action = self._resolve_action(parsed_after, context.action.rid)
        after_reference = after_action.resolved_reference
        checks["target_reference_exact_patch"] = (
            after_reference is not None
            and after_reference.raw_segment == context.patched_reference
        )
        checks["target_field_logical_value"] = _requested_value_matches(
            after_action.fields, context.field_name, context.requested
        )
        checks["target_only_requested_tokens_changed"] = _patches_reconstruct(
            context.action.resolved_reference.raw_segment,
            context.patched_reference,
            context.replacements,
        )
        passed = all(checks.values())
        result = ActionValidationResult(passed, checks)
        if not passed:
            failed = ", ".join(name for name, ok in checks.items() if not ok)
            raise ActionValidationError(f"patched Action validation failed: {failed}")
        return result, parsed_after


@dataclass(frozen=True, slots=True)
class _ValidationContext:
    obj: GameObject
    component: Component
    field: Field
    parsed: ActionList
    action: ActionEntry
    field_name: str
    requested: Any
    replacements: tuple[_TokenReplacement, ...]
    patched_reference: bytes
    new_payload: bytes
    record_start: int
    old_record_end: int


def _graph_fingerprint(parsed: ActionList, event_property: str) -> GraphFingerprint:
    return GraphFingerprint(
        event_property=event_property,
        action_rids=tuple(item.rid for item in parsed.actions),
        references=tuple(
            (
                item.rid,
                item.type_info.namespace,
                item.type_info.class_name,
                item.type_info.assembly,
                item.type_tag,
            )
            for item in parsed.references
        ),
    )


def _reframe_reference_json(
    parsed: ActionList, reference_index: int, patched_reference: bytes
) -> bytes:
    reference = parsed.references[reference_index]
    length_segment = next(
            (
                item
                for item in parsed.segments
                if item.kind == "reference_json_length"
                and item.index == reference_index
            ),
            None,
        )
    if (
        reference.source_span is None
        or length_segment is None
        or length_segment.source_span.end != reference.source_span.start
    ):
        raise ActionFramingError("target reference length prefix is missing or non-adjacent")
    internal_start = length_segment.source_span.start
    internal_end = reference.source_span.end
    new_internal = encode_action_varuint7(len(patched_reference)) + patched_reference
    return parsed.raw_payload[:internal_start] + new_internal + parsed.raw_payload[internal_end:]


def _apply_replacements(raw: bytes, replacements: list[_TokenReplacement]) -> bytes:
    ordered = sorted(replacements, key=lambda item: item.span.start)
    for previous, current in zip(ordered, ordered[1:]):
        if previous.span.end > current.span.start:
            raise ActionFramingError("requested JSON token spans overlap")
    result = raw
    for item in reversed(ordered):
        if result[item.span.start : item.span.end] != item.before_raw:
            raise ActionFramingError("JSON token source span no longer matches parsed bytes")
        result = result[: item.span.start] + item.after_raw + result[item.span.end :]
    return result


def _patches_reconstruct(
    before: bytes | None,
    after: bytes,
    replacements: tuple[_TokenReplacement, ...],
) -> bool:
    return before is not None and _apply_replacements(before, list(replacements)) == after


def _encode_json_number(value: float | int, original: bytes) -> bytes:
    try:
        token = json.dumps(value, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise InvalidActionFieldValueError(f"invalid JSON number {value!r}") from exc
    original_text = original.decode("ascii")
    if "." in original_text and "." not in token and "e" not in token.casefold():
        token += ".0"
    encoded = token.encode("ascii")
    try:
        parsed = json.loads(encoded.decode("ascii"))
    except json.JSONDecodeError as exc:  # pragma: no cover - json.dumps is authoritative
        raise InvalidActionFieldValueError("encoded number is not valid JSON") from exc
    if float(parsed) != float(value):
        raise InvalidActionFieldValueError("numeric value cannot be represented exactly enough")
    return encoded


def _requested_value_matches(data: Any, field_name: str, requested: Any) -> bool:
    if not isinstance(data, Mapping) or field_name not in data:
        return False
    actual = data[field_name]
    if isinstance(requested, Mapping):
        return isinstance(actual, Mapping) and all(
            axis in actual and float(actual[axis]) == float(value)
            for axis, value in requested.items()
        )
    return actual is requested if isinstance(requested, bool) else actual == requested


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _valid_sha256(value: str) -> bool:
    return len(value) == 64 and all(item in "0123456789abcdefABCDEF" for item in value)
