"""Validated model and fingerprints for one top-level ActionList graph."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from .actions import ActionList
from .errors import ActionFramingError, DuplicateRidError


@dataclass(frozen=True, slots=True)
class ActionGraphNode:
    rid: int
    namespace: str | None
    class_name: str | None
    assembly: str | None
    type_tag: int | None
    segment_sha256: str | None
    outgoing_rids: tuple[int, ...]
    unknown_dependency_fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ActionGraph:
    ordered_rids: tuple[int, ...]
    nodes: tuple[ActionGraphNode, ...]
    reference_order: tuple[int, ...]
    incoming: tuple[tuple[int, tuple[int, ...]], ...]

    @classmethod
    def from_action_list(cls, parsed: ActionList) -> "ActionGraph":
        require_safe_action_graph(parsed)
        nodes = []
        incoming: dict[int, set[int]] = {item.rid: set() for item in parsed.references}
        known = set(incoming)
        for reference in parsed.references:
            outgoing, unknown = _dependency_scan(reference.data, known)
            for target in outgoing:
                incoming[target].add(reference.rid)
            nodes.append(
                ActionGraphNode(
                    rid=reference.rid,
                    namespace=reference.type_info.namespace,
                    class_name=reference.type_info.class_name,
                    assembly=reference.type_info.assembly,
                    type_tag=reference.type_tag,
                    segment_sha256=(
                        hashlib.sha256(reference.raw_segment).hexdigest()
                        if reference.raw_segment is not None
                        else None
                    ),
                    outgoing_rids=tuple(sorted(outgoing)),
                    unknown_dependency_fields=tuple(sorted(unknown)),
                )
            )
        return cls(
            ordered_rids=tuple(item.rid for item in parsed.actions if item.rid is not None),
            nodes=tuple(nodes),
            reference_order=tuple(item.rid for item in parsed.references),
            incoming=tuple((rid, tuple(sorted(sources))) for rid, sources in incoming.items()),
        )

    def node(self, rid: int) -> ActionGraphNode:
        return next(item for item in self.nodes if item.rid == rid)


@dataclass(frozen=True, slots=True)
class GraphFingerprintV2:
    ordered_rids: tuple[int, ...]
    references: tuple[tuple[int, str | None, str | None, str | None, int | None, str | None], ...]

    @classmethod
    def from_graph(cls, graph: ActionGraph) -> "GraphFingerprintV2":
        return cls(
            ordered_rids=graph.ordered_rids,
            references=tuple(
                (
                    node.rid,
                    node.namespace,
                    node.class_name,
                    node.assembly,
                    node.type_tag,
                    node.segment_sha256,
                )
                for node in graph.nodes
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ordered_rids": list(self.ordered_rids),
            "references": [
                {
                    "rid": item[0], "namespace": item[1], "class": item[2],
                    "assembly": item[3], "type_tag": item[4], "segment_sha256": item[5],
                }
                for item in self.references
            ],
        }


def require_safe_action_graph(parsed: ActionList) -> None:
    if not parsed.fully_consumed or parsed.unparsed_ranges:
        raise ActionFramingError("Action payload is not fully consumed")
    if parsed.duplicate_rids:
        raise DuplicateRidError(f"duplicate Action rid values: {parsed.duplicate_rids!r}")
    if parsed.dangling_rids or parsed.unreferenced_rids:
        raise ActionFramingError(
            f"Action graph has dangling/unreferenced rids: "
            f"{parsed.dangling_rids!r}/{parsed.unreferenced_rids!r}"
        )
    if len(parsed.actions) != len(parsed.references):
        raise ActionFramingError("Action and managed-reference counts differ")
    if len([s for s in parsed.segments if s.kind == "reference_json"]) != len(parsed.references):
        raise ActionFramingError("managed-reference segment count differs")
    if any(item.rid is None for item in parsed.actions):
        raise ActionFramingError("Action list contains null/non-integer rid")
    unsafe = [
        warning for warning in parsed.warnings
        if not warning.startswith("managed-reference cycles detected")
    ]
    reference_warnings = [
        warning for ref in parsed.references for warning in ref.warnings
        if warning != "reference JSON segment differs from RefIds data"
    ]
    if unsafe or reference_warnings:
        raise ActionFramingError(f"unsafe Action graph metadata: {unsafe + reference_warnings!r}")
    if any(
        ref.raw_segment is None or ref.segment_index is None or ref.type_tag is None
        or not isinstance(ref.data, Mapping) or ref.data.get("m_type") != ref.type_tag
        for ref in parsed.references
    ):
        raise ActionFramingError("managed-reference metadata is incomplete")


def validate_observed_rid_allocation(parsed: ActionList) -> int:
    """Return the next rid only for the corpus-confirmed contiguous local pattern."""
    require_safe_action_graph(parsed)
    actual = tuple(item.rid for item in parsed.actions)
    expected = tuple(range(1000, 1000 + len(actual)))
    if tuple(sorted(actual)) != expected or tuple(item.rid for item in parsed.references) != expected:
        raise ActionFramingError(
            "rid allocation is outside the confirmed property-local contiguous 1000+n pattern"
        )
    return 1000 + len(actual)


def _dependency_scan(value: Any, known_rids: set[int], path: str = "$") -> tuple[set[int], set[str]]:
    outgoing: set[int] = set()
    unknown: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            child = f"{path}.{key}"
            if key == "rid" and isinstance(item, int) and not isinstance(item, bool):
                if item in known_rids:
                    outgoing.add(item)
                else:
                    unknown.add(child)
            elif key.casefold().endswith(("ref", "reference", "rid")) and item not in (None, 0, ""):
                unknown.add(child)
            child_outgoing, child_unknown = _dependency_scan(item, known_rids, child)
            outgoing.update(child_outgoing)
            unknown.update(child_unknown)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            child_outgoing, child_unknown = _dependency_scan(item, known_rids, f"{path}[{index}]")
            outgoing.update(child_outgoing)
            unknown.update(child_unknown)
    return outgoing, unknown


def normalized_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()
