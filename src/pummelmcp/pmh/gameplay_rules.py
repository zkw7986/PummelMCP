"""Typed, deliberately small Trigger rule language for minigame composition."""

from __future__ import annotations

import math
from typing import Any

from .errors import UnsafeDuplicationError


EVENT_ENABLED_FIELD = {
    "OnHitActions": "TriggerOnHit",
    "OnEnterActions": "TriggerOnEnter",
    "OnExitActions": "TriggerOnExit",
    "OnStayActions": "TriggerOnStay",
}
# SimpleModPlayer emits collision HitEvent(Source=player, Receiver=ModTrigger).
# Player-affecting Trigger rules therefore admit only the triggering player.
TARGET = {"triggering_player": 1}
OPERATION = {"set": 0, "add": 1, "subtract": 2, "multiply": 3, "divide": 4}
ACTION_CLASS = {
    "CHANGE_SCORE": "ChangeScoreAction",
    "CHANGE_HEALTH": "ChangeHealthAction",
    "KILL": "KillAction",
    "SET_PLACEMENT": "SetPlacementAction",
    "SHOW_MESSAGE": "ShowMessageAction",
}


def compile_trigger_rule(rule: Any) -> dict[str, Any]:
    """Reject unknown semantics and compile only assembly-backed scalar changes."""
    if not isinstance(rule, dict) or set(rule) - {"event_property", "condition", "interval_seconds", "actions"}:
        raise UnsafeDuplicationError("INVALID_TRIGGER_RULE_FIELDS")
    event = rule.get("event_property")
    if type(event) is not str or event not in EVENT_ENABLED_FIELD:
        raise UnsafeDuplicationError("UNSUPPORTED_TRIGGER_EVENT")
    condition = rule.get("condition")
    if type(condition) is not str or condition not in {"always", "once_per_player", "once_global"}:
        raise UnsafeDuplicationError("RULE_CONDITION_UNSUPPORTED")
    if event == "OnStayActions":
        interval = rule.get("interval_seconds")
        if type(interval) not in (int, float) or not math.isfinite(interval) or not 0.1 <= interval <= 60:
            raise UnsafeDuplicationError("INVALID_STAY_INTERVAL")
    elif "interval_seconds" in rule:
        raise UnsafeDuplicationError("INTERVAL_REQUIRES_STAY_EVENT")
    actions = rule.get("actions")
    if not isinstance(actions, list) or not 1 <= len(actions) <= 16:
        raise UnsafeDuplicationError("INVALID_RULE_ACTION_SEQUENCE")
    compiled = []
    for action in actions:
        if not isinstance(action, dict) or type(action.get("type")) is not str or action["type"] not in ACTION_CLASS:
            raise UnsafeDuplicationError("RULE_ACTION_UNSUPPORTED")
        kind = action["type"]
        expected = {"type", "target"}
        if kind in {"CHANGE_SCORE", "CHANGE_HEALTH"}:
            expected |= {"operation", "value"}
        elif kind == "SHOW_MESSAGE":
            expected |= {"message", "duration_seconds"}
        if set(action) != expected:
            raise UnsafeDuplicationError("INVALID_RULE_ACTION_FIELDS")
        if type(action["target"]) is not str or action["target"] not in TARGET:
            raise UnsafeDuplicationError("RULE_TARGET_UNSUPPORTED")
        fields: dict[str, Any] = {"m_targetFlags": TARGET[action["target"]]}
        if kind in {"CHANGE_SCORE", "CHANGE_HEALTH"}:
            if type(action["operation"]) is not str or action["operation"] not in OPERATION:
                raise UnsafeDuplicationError("RULE_OPERATION_UNSUPPORTED")
            value = action["value"]
            if type(value) is not int or not -1000 <= value <= 1000:
                raise UnsafeDuplicationError("RULE_VALUE_OUT_OF_RANGE")
            if action["operation"] == "divide" and value == 0:
                raise UnsafeDuplicationError("RULE_DIVIDE_BY_ZERO")
            fields.update(m_operation=OPERATION[action["operation"]], m_value=value)
        if kind == "SHOW_MESSAGE":
            message, duration = action["message"], action["duration_seconds"]
            if type(message) is not str or not 1 <= len(message) <= 256:
                raise UnsafeDuplicationError("RULE_MESSAGE_INVALID")
            if type(duration) not in (int, float) or not math.isfinite(duration) or not 0.5 <= duration <= 1200:
                raise UnsafeDuplicationError("RULE_DURATION_INVALID")
            fields.update(m_message=message, m_duration=float(duration))
        compiled.append({"class": ACTION_CLASS[kind], "fields": fields})
    return {
        "event_property": event,
        "enable_field": EVENT_ENABLED_FIELD[event],
        "one_use_per_player": rule["condition"] == "once_per_player",
        "disable_after_triggered": rule["condition"] == "once_global",
        "interval_seconds": float(rule["interval_seconds"]) if event == "OnStayActions" else None,
        "actions": compiled,
    }
