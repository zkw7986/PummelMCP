"""Read-only structural validation shared by application front ends."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .models import Scene


@dataclass(frozen=True, slots=True)
class SceneValidationResult:
    passed: bool
    checks: Mapping[str, bool]


def validate_scene(scene: Scene) -> SceneValidationResult:
    """Validate the structural invariants currently understood for PMH v1."""
    objects = list(scene.walk())
    components = [component for obj in objects for component in obj.components]
    fields = [field for component in components for field in component.fields]

    parent_links_ok = all(
        child.parent is obj for obj in objects for child in obj.children
    )
    spans_ok = all(
        field.source_span is not None
        and field.source_span.length == len(field.raw)
        and 0 <= field.source_span.start <= field.source_span.end <= scene.byte_length
        for field in fields
    )
    checks = {
        "magic": scene.magic == "PMH",
        "supported_version": scene.version == 1,
        "has_roots": scene.root_count > 0,
        "has_objects": scene.object_count == len(objects) and bool(objects),
        "component_count_consistent": scene.component_count == len(components),
        "parent_links_consistent": parent_links_ok,
        "property_source_spans_valid": spans_ok,
        "fully_consumed": scene.fully_consumed,
    }
    return SceneValidationResult(passed=all(checks.values()), checks=checks)
