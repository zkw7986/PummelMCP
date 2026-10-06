"""Lossless, read-only inspection of framed ModTrigger Action payloads."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .errors import PMHError
from .json_spans import JsonDocument, JsonSpanError, parse_json_spans
from .models import SourceSpan


ACTION_EVENT_PROPERTIES = frozenset(
    {"OnHitActions", "OnEnterActions", "OnExitActions", "OnStayActions"}
)
ITEM_ACTION_EVENT_PROPERTIES = frozenset({"OnPickupTrigger"})
SUPPORTED_ACTION_EVENT_PROPERTIES = ACTION_EVENT_PROPERTIES | ITEM_ACTION_EVENT_PROPERTIES
ACTION_COMPONENT_EVENTS = {
    "ModTrigger": frozenset({"OnHitActions", "OnEnterActions", "OnExitActions", "OnStayActions"}),
    "ModItem": ITEM_ACTION_EVENT_PROPERTIES,
}
MAX_ACTION_PAYLOAD_BYTES = 16 * 1024 * 1024
MAX_ACTION_PARSE_DEPTH = 8
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 100_000
MAX_REFERENCE_COUNT = 10_000


class ActionPayloadError(PMHError, ValueError):
    """An Action payload violates the bounded framing or JSON contract."""


class ActionInspectionError(PMHError, ValueError):
    """A requested Action inspection target is invalid."""


@dataclass(frozen=True, slots=True)
class ActionPayloadSegment:
    kind: str
    source_span: SourceSpan
    raw: bytes
    value: Any = None
    index: int | None = None
    json_document: JsonDocument | None = None


@dataclass(frozen=True, slots=True)
class ActionTypeInfo:
    class_name: str | None
    namespace: str | None
    assembly: str | None
    raw: Any


@dataclass(frozen=True, slots=True)
class ManagedReference:
    rid: int
    type_info: ActionTypeInfo
    data: Any
    raw_refid: Mapping[str, Any]
    segment_index: int | None
    type_tag: int | None
    source_span: SourceSpan | None
    raw_segment: bytes | None
    json_document: JsonDocument | None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ActionEntry:
    index: int
    rid: int | None
    raw: Any
    resolved_reference: ManagedReference | None
    nested_action_lists: tuple[ActionList, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def type_info(self) -> ActionTypeInfo | None:
        return (
            self.resolved_reference.type_info
            if self.resolved_reference is not None
            else None
        )

    @property
    def fields(self) -> Any:
        return self.resolved_reference.data if self.resolved_reference else None


@dataclass(frozen=True, slots=True)
class ActionList:
    source_property: str | None
    source_span: SourceSpan | None
    raw_payload: bytes
    prefix_u16: int | None
    root_json: Mapping[str, Any]
    root_segment: ActionPayloadSegment | None
    root_json_document: JsonDocument | None
    m_type: Any
    actions: tuple[ActionEntry, ...]
    references_version: Any
    references: tuple[ManagedReference, ...]
    segments: tuple[ActionPayloadSegment, ...]
    parsed_ranges: tuple[SourceSpan, ...]
    unparsed_ranges: tuple[SourceSpan, ...]
    fully_consumed: bool
    duplicate_rids: tuple[int, ...] = ()
    dangling_rids: tuple[int, ...] = ()
    unreferenced_rids: tuple[int, ...] = ()
    reference_cycles: tuple[tuple[int, ...], ...] = ()
    warnings: tuple[str, ...] = ()
    depth: int = 0
    embedded: bool = False

    @property
    def raw_length(self) -> int:
        return len(self.raw_payload)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.raw_payload).hexdigest()


class _Cursor:
    def __init__(self, raw: bytes) -> None:
        self.raw = raw
        self.offset = 0
        self.segments: list[ActionPayloadSegment] = []

    def read_u16(self, kind: str, *, index: int | None = None) -> int:
        start = self.offset
        raw = self.read_exact(2, kind)
        value = int.from_bytes(raw, "little", signed=False)
        self.segments.append(
            ActionPayloadSegment(kind, SourceSpan(start, 2), raw, value, index)
        )
        return value

    def read_varuint7(self, kind: str, *, index: int | None = None) -> int:
        start = self.offset
        value = 0
        shift = 0
        for _ in range(5):
            if self.offset >= len(self.raw):
                raise ActionPayloadError(f"truncated 7-bit length prefix at {start}")
            byte = self.raw[self.offset]
            self.offset += 1
            if shift == 28 and byte > 0x0F:
                raise ActionPayloadError(f"invalid 7-bit length prefix at {start}")
            value |= (byte & 0x7F) << shift
            if byte & 0x80 == 0:
                encoded = self.raw[start : self.offset]
                self.segments.append(
                    ActionPayloadSegment(
                        kind,
                        SourceSpan(start, len(encoded)),
                        encoded,
                        value,
                        index,
                    )
                )
                return value
            shift += 7
        raise ActionPayloadError(f"invalid 7-bit length prefix at {start}")

    def read_exact(self, length: int, label: str) -> bytes:
        if length < 0 or self.offset + length > len(self.raw):
            raise ActionPayloadError(
                f"{label} length {length} exceeds remaining payload "
                f"{len(self.raw) - self.offset} at {self.offset}"
            )
        result = self.raw[self.offset : self.offset + length]
        self.offset += length
        return result

    def read_json(
        self, length: int, kind: str, *, index: int | None = None
    ) -> tuple[Any, ActionPayloadSegment]:
        if length > MAX_ACTION_PAYLOAD_BYTES:
            raise ActionPayloadError(f"{kind} length {length} exceeds safety limit")
        start = self.offset
        raw = self.read_exact(length, kind)
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ActionPayloadError(f"{kind} is not valid UTF-8") from exc
        try:
            value = json.loads(text, parse_constant=_reject_json_constant)
        except (ValueError, RecursionError) as exc:
            raise ActionPayloadError(f"{kind} is not valid bounded JSON: {exc}") from exc
        _validate_json_limits(value)
        try:
            json_document = parse_json_spans(raw)
        except JsonSpanError as exc:
            raise ActionPayloadError(f"{kind} has unsafe JSON token spans: {exc}") from exc
        if json_document.root.value != value:
            raise ActionPayloadError(f"{kind} span parse differs from logical JSON parse")
        segment = ActionPayloadSegment(
            kind, SourceSpan(start, length), raw, value, index, json_document
        )
        self.segments.append(segment)
        return value, segment


def parse_action_payload(
    payload: bytes,
    *,
    source_property: str | None = None,
    source_span: SourceSpan | None = None,
    max_depth: int = MAX_ACTION_PARSE_DEPTH,
) -> ActionList:
    """Parse one framed Action payload without modifying or normalizing it."""
    raw = bytes(payload)
    if len(raw) > MAX_ACTION_PAYLOAD_BYTES:
        raise ActionPayloadError(
            f"Action payload length {len(raw)} exceeds {MAX_ACTION_PAYLOAD_BYTES} bytes"
        )
    if not 0 <= max_depth <= MAX_ACTION_PARSE_DEPTH:
        raise ActionInspectionError(
            f"max_depth must be between 0 and {MAX_ACTION_PARSE_DEPTH}"
        )

    cursor = _Cursor(raw)
    prefix_u16 = cursor.read_u16("outer_prefix_u16")
    root_length = cursor.read_varuint7("root_json_length")
    root, root_segment = cursor.read_json(root_length, "root_json")
    if not isinstance(root, dict):
        raise ActionPayloadError("ActionList root JSON must be an object")

    reference_segment_count = cursor.read_u16("reference_segment_count")
    if reference_segment_count > MAX_REFERENCE_COUNT:
        raise ActionPayloadError(
            f"reference segment count {reference_segment_count} exceeds safety limit"
        )
    tail_segments: list[tuple[int, Any, ActionPayloadSegment]] = []
    for index in range(reference_segment_count):
        type_tag = cursor.read_u16("reference_type_tag", index=index)
        length = cursor.read_varuint7("reference_json_length", index=index)
        value, segment = cursor.read_json(length, "reference_json", index=index)
        tail_segments.append((type_tag, value, segment))

    unparsed = (
        (SourceSpan(cursor.offset, len(raw) - cursor.offset),)
        if cursor.offset < len(raw)
        else ()
    )
    return _build_action_list(
        root,
        source_property=source_property,
        source_span=source_span,
        raw_payload=raw,
        prefix_u16=prefix_u16,
        root_segment=root_segment,
        segments=tuple(cursor.segments),
        unparsed_ranges=unparsed,
        tail_segments=tail_segments,
        max_depth=max_depth,
        depth=0,
        embedded=False,
    )


def encode_action_varuint7(value: int) -> bytes:
    """Encode one non-negative 32-bit Action framing length."""
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFFFFFF:
        raise ActionPayloadError("7-bit length must be an unsigned 32-bit integer")
    result = bytearray()
    while value >= 0x80:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value)
    return bytes(result)


def _build_action_list(
    root: Mapping[str, Any],
    *,
    source_property: str | None,
    source_span: SourceSpan | None,
    raw_payload: bytes,
    prefix_u16: int | None,
    root_segment: ActionPayloadSegment | None,
    segments: tuple[ActionPayloadSegment, ...],
    unparsed_ranges: tuple[SourceSpan, ...],
    tail_segments: Sequence[tuple[int, Any, ActionPayloadSegment]],
    max_depth: int,
    depth: int,
    embedded: bool,
) -> ActionList:
    warnings: list[str] = []
    actions_raw = root.get("m_actions", [])
    if not isinstance(actions_raw, list):
        warnings.append("m_actions is not a list")
        actions_raw = []
    references_root = root.get("references", {})
    if not isinstance(references_root, dict):
        warnings.append("references is not an object")
        references_root = {}
    refids_raw = references_root.get("RefIds", [])
    if not isinstance(refids_raw, list):
        warnings.append("references.RefIds is not a list")
        refids_raw = []
    if len(refids_raw) > MAX_REFERENCE_COUNT:
        raise ActionPayloadError(
            f"RefIds count {len(refids_raw)} exceeds safety limit"
        )
    if not embedded and len(tail_segments) != len(refids_raw):
        warnings.append(
            f"reference segment count {len(tail_segments)} does not match "
            f"RefIds count {len(refids_raw)}"
        )

    references: list[ManagedReference] = []
    seen_rids: set[int] = set()
    duplicate_rids: set[int] = set()
    for index, item in enumerate(refids_raw):
        ref_warnings: list[str] = []
        if not isinstance(item, dict):
            warnings.append(f"RefIds[{index}] is not an object")
            continue
        rid = item.get("rid")
        if isinstance(rid, bool) or not isinstance(rid, int):
            warnings.append(f"RefIds[{index}].rid is not an integer")
            continue
        if rid in seen_rids:
            duplicate_rids.add(rid)
        seen_rids.add(rid)
        type_raw = item.get("type")
        if not isinstance(type_raw, dict):
            type_raw = {}
            ref_warnings.append("type metadata is not an object")
        type_info = ActionTypeInfo(
            class_name=_optional_string(type_raw.get("class")),
            namespace=_optional_string(type_raw.get("ns")),
            assembly=_optional_string(type_raw.get("asm")),
            raw=item.get("type"),
        )
        root_data = item.get("data")
        data = root_data
        type_tag: int | None = None
        segment: ActionPayloadSegment | None = None
        if index < len(tail_segments):
            type_tag, tail_data, segment = tail_segments[index]
            if tail_data != root_data:
                ref_warnings.append("reference JSON segment differs from RefIds data")
            # The separately framed segment is the writable serialized instance.
            # The root RefIds data remains a losslessly preserved graph snapshot.
            data = tail_data
            data_type = data.get("m_type") if isinstance(data, dict) else None
            if data_type != type_tag:
                ref_warnings.append(
                    f"reference type tag {type_tag} differs from data.m_type {data_type!r}"
                )
        references.append(
            ManagedReference(
                rid=rid,
                type_info=type_info,
                data=data,
                raw_refid=item,
                segment_index=index if segment is not None else None,
                type_tag=type_tag,
                source_span=segment.source_span if segment else None,
                raw_segment=segment.raw if segment else None,
                json_document=segment.json_document if segment else None,
                warnings=tuple(ref_warnings),
            )
        )

    unique_references = {
        reference.rid: reference
        for reference in references
        if reference.rid not in duplicate_rids
    }
    action_entries: list[ActionEntry] = []
    action_rids: list[int] = []
    for index, item in enumerate(actions_raw):
        entry_warnings: list[str] = []
        rid = item.get("rid") if isinstance(item, dict) else None
        if rid is not None and (isinstance(rid, bool) or not isinstance(rid, int)):
            entry_warnings.append("action rid is not an integer or null")
            rid = None
        if isinstance(rid, int):
            action_rids.append(rid)
        resolved = unique_references.get(rid) if isinstance(rid, int) else None
        if isinstance(rid, int) and resolved is None:
            entry_warnings.append(f"action rid {rid} is dangling or duplicated")
        nested = (
            _discover_nested_action_lists(
                resolved.data,
                max_depth=max_depth,
                action_depth=depth,
                ancestors=set(),
                path=f"action[{index}]",
            )
            if resolved is not None
            else ()
        )
        action_entries.append(
            ActionEntry(index, rid, item, resolved, nested, tuple(entry_warnings))
        )

    dangling = sorted({rid for rid in action_rids if rid not in unique_references})
    unreferenced = sorted(
        reference.rid for reference in references if reference.rid not in action_rids
    )
    cycles = _find_reference_cycles(references)
    if duplicate_rids:
        warnings.append(f"duplicate RefIds rid values: {sorted(duplicate_rids)!r}")
    if dangling:
        warnings.append(f"dangling action rid values: {dangling!r}")
    if unreferenced:
        warnings.append(f"unreferenced RefIds rid values: {unreferenced!r}")
    if cycles:
        warnings.append(f"managed-reference cycles detected: {cycles!r}")

    parsed_ranges = _merge_ranges(segment.source_span for segment in segments)
    return ActionList(
        source_property=source_property,
        source_span=source_span,
        raw_payload=raw_payload,
        prefix_u16=prefix_u16,
        root_json=root,
        root_segment=root_segment,
        root_json_document=root_segment.json_document if root_segment else None,
        m_type=root.get("m_type"),
        actions=tuple(action_entries),
        references_version=references_root.get("version"),
        references=tuple(references),
        segments=segments,
        parsed_ranges=parsed_ranges,
        unparsed_ranges=unparsed_ranges,
        fully_consumed=not unparsed_ranges,
        duplicate_rids=tuple(sorted(duplicate_rids)),
        dangling_rids=tuple(dangling),
        unreferenced_rids=tuple(unreferenced),
        reference_cycles=cycles,
        warnings=tuple(warnings),
        depth=depth,
        embedded=embedded,
    )


def _discover_nested_action_lists(
    value: Any,
    *,
    max_depth: int,
    action_depth: int,
    ancestors: set[int],
    path: str,
) -> tuple[ActionList, ...]:
    if action_depth >= max_depth:
        return ()
    if isinstance(value, (dict, list)):
        identity = id(value)
        if identity in ancestors:
            return ()
        ancestors = {*ancestors, identity}
    found: list[ActionList] = []
    if isinstance(value, dict):
        if isinstance(value.get("m_actions"), list) and isinstance(
            value.get("references"), dict
        ):
            found.append(
                _build_action_list(
                    value,
                    source_property=path,
                    source_span=None,
                    raw_payload=b"",
                    prefix_u16=None,
                    root_segment=None,
                    segments=(),
                    unparsed_ranges=(),
                    tail_segments=(),
                    max_depth=max_depth,
                    depth=action_depth + 1,
                    embedded=True,
                )
            )
            return tuple(found)
        for key, item in value.items():
            found.extend(
                _discover_nested_action_lists(
                    item,
                    max_depth=max_depth,
                    action_depth=action_depth,
                    ancestors=ancestors,
                    path=f"{path}.{key}",
                )
            )
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(
                _discover_nested_action_lists(
                    item,
                    max_depth=max_depth,
                    action_depth=action_depth,
                    ancestors=ancestors,
                    path=f"{path}[{index}]",
                )
            )
    return tuple(found)


def _find_reference_cycles(
    references: Sequence[ManagedReference],
) -> tuple[tuple[int, ...], ...]:
    known = {reference.rid for reference in references}
    graph = {
        reference.rid: _collect_rid_links(reference.data) & known
        for reference in references
    }
    cycles: set[tuple[int, ...]] = set()

    completed: set[int] = set()
    for start in graph:
        if start in completed:
            continue
        path = [start]
        positions = {start: 0}
        stack = [(start, iter(graph.get(start, ()) ))]
        while stack:
            node, targets = stack[-1]
            try:
                target = next(targets)
            except StopIteration:
                stack.pop()
                completed.add(node)
                positions.pop(node, None)
                path.pop()
                continue
            if target in positions:
                core = tuple(path[positions[target] :])
                rotations = [core[index:] + core[:index] for index in range(len(core))]
                canonical = min(rotations)
                cycles.add(canonical + (canonical[0],))
            elif target not in completed:
                positions[target] = len(path)
                path.append(target)
                stack.append((target, iter(graph.get(target, ()))))
    return tuple(sorted(cycles))


def _collect_rid_links(value: Any, *, _seen: set[int] | None = None) -> set[int]:
    seen = set() if _seen is None else _seen
    if isinstance(value, (dict, list)):
        identity = id(value)
        if identity in seen:
            return set()
        seen.add(identity)
    result: set[int] = set()
    if isinstance(value, dict):
        rid = value.get("rid")
        if not isinstance(rid, bool) and isinstance(rid, int):
            result.add(rid)
        for item in value.values():
            result.update(_collect_rid_links(item, _seen=seen))
    elif isinstance(value, list):
        for item in value:
            result.update(_collect_rid_links(item, _seen=seen))
    return result


def _validate_json_limits(value: Any) -> None:
    nodes = 0

    def visit(item: Any, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > MAX_JSON_NODES:
            raise ActionPayloadError(
                f"JSON node count exceeds safety limit {MAX_JSON_NODES}"
            )
        if depth > MAX_JSON_DEPTH:
            raise ActionPayloadError(
                f"JSON depth exceeds safety limit {MAX_JSON_DEPTH}"
            )
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ActionPayloadError("JSON object key is not a string")
                visit(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                visit(child, depth + 1)

    visit(value, 0)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant {value!r}")


def _merge_ranges(ranges: Iterable[SourceSpan]) -> tuple[SourceSpan, ...]:
    ordered = sorted(ranges, key=lambda item: item.start)
    if not ordered:
        return ()
    merged = [ordered[0]]
    for item in ordered[1:]:
        previous = merged[-1]
        if previous.end == item.start:
            merged[-1] = SourceSpan(previous.start, previous.length + item.length)
        else:
            merged.append(item)
    return tuple(merged)


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) else None
