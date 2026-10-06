"""Read-only controlled differential research for existing Action instances."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

from .actions import ACTION_EVENT_PROPERTIES, ActionEntry, parse_action_payload
from .errors import ActionNotFoundError, ActionRidNotFoundError
from .writer import PMHScene


def diff_action(
    before_scene: str | Path,
    after_scene: str | Path,
    object_identifier: str,
    component_identifier: str,
    event_property: str,
    *,
    action_rid: int | None = None,
    action_index: int | None = None,
) -> dict[str, Any]:
    """Compare the same existing Action in two scenes without writing either file."""
    if event_property not in ACTION_EVENT_PROPERTIES:
        raise ActionNotFoundError(f"unsupported event property {event_property!r}")
    if (action_rid is None) == (action_index is None):
        raise ActionNotFoundError("select exactly one of action_rid or action_index")
    before = _select(
        before_scene, object_identifier, component_identifier, event_property,
        action_rid=action_rid, action_index=action_index,
    )
    after = _select(
        after_scene, object_identifier, component_identifier, event_property,
        action_rid=action_rid, action_index=action_index,
    )
    before_action, before_hash = before
    after_action, after_hash = after
    before_type = before_action.type_info
    after_type = after_action.type_info
    before_fields = before_action.fields if isinstance(before_action.fields, Mapping) else {}
    after_fields = after_action.fields if isinstance(after_action.fields, Mapping) else {}
    names = list(dict.fromkeys([*before_fields.keys(), *after_fields.keys()]))
    changes = [
        {
            "field": name,
            "before": before_fields.get(name),
            "after": after_fields.get(name),
            "present_before": name in before_fields,
            "present_after": name in after_fields,
        }
        for name in names
        if (name in before_fields) != (name in after_fields)
        or before_fields.get(name) != after_fields.get(name)
    ]
    return {
        "selector": {
            "object": object_identifier,
            "component": component_identifier,
            "event_property": event_property,
            "action_rid": action_rid,
            "action_index": action_index,
        },
        "identity": {
            "before": _identity(before_action, before_type),
            "after": _identity(after_action, after_type),
            "unchanged": _identity(before_action, before_type)
            == _identity(after_action, after_type),
        },
        "payload_sha256": {"before": before_hash, "after": after_hash},
        "changes": changes,
        "changed_field_count": len(changes),
        "read_only": True,
    }


def _select(
    scene_path: str | Path,
    object_identifier: str,
    component_identifier: str,
    event_property: str,
    *,
    action_rid: int | None,
    action_index: int | None,
) -> tuple[ActionEntry, str]:
    loaded = PMHScene.load(scene_path)
    field = loaded.get_component(object_identifier, component_identifier).get_field(
        event_property
    )
    parsed = parse_action_payload(field.raw, source_property=event_property)
    if action_rid is not None:
        matches = [item for item in parsed.actions if item.rid == action_rid]
        if len(matches) != 1:
            raise ActionRidNotFoundError(
                f"Action rid {action_rid} is not uniquely available in {scene_path}"
            )
        action = matches[0]
    else:
        assert action_index is not None
        if isinstance(action_index, bool) or not 0 <= action_index < len(parsed.actions):
            raise ActionNotFoundError(
                f"Action index {action_index!r} is unavailable in {scene_path}"
            )
        action = parsed.actions[action_index]
    return action, hashlib.sha256(field.raw).hexdigest()


def _identity(action: ActionEntry, type_info: Any) -> dict[str, Any]:
    return {
        "rid": action.rid,
        "namespace": type_info.namespace if type_info else None,
        "class": type_info.class_name if type_info else None,
        "assembly": type_info.assembly if type_info else None,
        "type_tag": (
            action.resolved_reference.type_tag if action.resolved_reference else None
        ),
    }
