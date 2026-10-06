"""Read-only structural comparison for manually captured Ctrl+D oracle pairs."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from .models import GameObject
from .reader import read_pmh


@dataclass(frozen=True, slots=True)
class RawDiffRange:
    operation: str
    before_start: int
    before_end: int
    after_start: int
    after_end: int


@dataclass(frozen=True, slots=True)
class ExistingObjectChange:
    guid: str
    path: str
    changes: tuple[str, ...]
    classification: str


@dataclass(frozen=True, slots=True)
class DuplicationOracleReport:
    before_path: Path
    after_path: Path
    before_sha256: str
    after_sha256: str
    before_counts: tuple[int, int, int]
    after_counts: tuple[int, int, int]
    source_name: str
    source_guids_before: tuple[str, ...]
    source_guids_after: tuple[str, ...]
    added_object_guids: tuple[str, ...]
    removed_object_guids: tuple[str, ...]
    added_component_guids: tuple[str, ...]
    removed_component_guids: tuple[str, ...]
    existing_object_changes: tuple[ExistingObjectChange, ...]
    raw_diff_ranges: tuple[RawDiffRange, ...]

    @property
    def fully_parsed(self) -> bool:
        return True

    @property
    def single_object_duplicate_candidate(self) -> bool:
        return (
            len(self.source_guids_before) == 1
            and len(self.added_object_guids) == 1
            and not self.removed_object_guids
        )

    @property
    def known_editor_save_noise(self) -> tuple[ExistingObjectChange, ...]:
        return tuple(
            item
            for item in self.existing_object_changes
            if item.classification == "KNOWN_EDITOR_SAVE_NOISE"
        )

    @property
    def semantic_existing_object_changes(self) -> tuple[ExistingObjectChange, ...]:
        return tuple(
            item
            for item in self.existing_object_changes
            if item.classification != "KNOWN_EDITOR_SAVE_NOISE"
        )


def analyze_duplication_oracle(
    before_path: str | Path,
    after_path: str | Path,
    *,
    source_name: str,
    source_guid: str | None = None,
) -> DuplicationOracleReport:
    """Compare two scenes structurally, retaining only hashes and bounded ranges."""
    before_file = Path(before_path)
    after_file = Path(after_path)
    before_raw = before_file.read_bytes()
    after_raw = after_file.read_bytes()
    before = read_pmh(before_file)
    after = read_pmh(after_file)
    if not before.fully_consumed or not after.fully_consumed:
        raise ValueError("oracle scenes must parse to exact EOF")

    before_objects = {item.guid: item for item in before.walk()}
    after_objects = {item.guid: item for item in after.walk()}
    if source_guid is not None:
        selected = before_objects.get(source_guid)
        if selected is None or selected.name != source_name:
            raise ValueError("source_guid does not identify source_name in before scene")
    before_components = {
        item.guid for obj in before.walk() for item in obj.components
    }
    after_components = {
        item.guid for obj in after.walk() for item in obj.components
    }
    existing_changes = tuple(
        change
        for guid in before_objects.keys() & after_objects.keys()
        if (change := _existing_change(before_objects[guid], after_objects[guid]))
        is not None
    )
    return DuplicationOracleReport(
        before_path=before_file,
        after_path=after_file,
        before_sha256=_sha256(before_raw),
        after_sha256=_sha256(after_raw),
        before_counts=(before.root_count, before.object_count, before.component_count),
        after_counts=(after.root_count, after.object_count, after.component_count),
        source_name=source_name,
        source_guids_before=tuple(
            item.guid
            for item in before.walk()
            if item.name == source_name
            and (source_guid is None or item.guid == source_guid)
        ),
        source_guids_after=tuple(
            item.guid
            for item in after.walk()
            if item.name == source_name
            and (source_guid is None or item.guid == source_guid)
        ),
        added_object_guids=tuple(after_objects.keys() - before_objects.keys()),
        removed_object_guids=tuple(before_objects.keys() - after_objects.keys()),
        added_component_guids=tuple(after_components - before_components),
        removed_component_guids=tuple(before_components - after_components),
        existing_object_changes=existing_changes,
        raw_diff_ranges=_raw_diff_ranges(before_raw, after_raw),
    )


def _existing_change(
    before: GameObject, after: GameObject
) -> ExistingObjectChange | None:
    changes: list[str] = []
    if (before.name, before.active, before.layer, before.tag) != (
        after.name,
        after.active,
        after.layer,
        after.tag,
    ):
        changes.append("gameobject_fields")
    if (before.parent.guid if before.parent else None) != (
        after.parent.guid if after.parent else None
    ):
        changes.append("parent")
    if [item.guid for item in before.children] != [
        item.guid for item in after.children
    ]:
        changes.append("children")
    if [(item.type_name, item.guid, item.enabled) for item in before.components] != [
        (item.type_name, item.guid, item.enabled) for item in after.components
    ]:
        changes.append("component_index")
    before_by_guid = {item.guid: item for item in before.components}
    after_by_guid = {item.guid: item for item in after.components}
    for guid in before_by_guid.keys() & after_by_guid.keys():
        left = before_by_guid[guid]
        right = after_by_guid[guid]
        if [(item.name, item.raw) for item in left.fields] != [
            (item.name, item.raw) for item in right.fields
        ]:
            changes.append(f"component_payload:{left.type_name}")
    if not changes:
        return None
    classification = (
        "KNOWN_EDITOR_SAVE_NOISE"
        if _is_player_spawn_position_only_change(before, after, changes)
        else "SEMANTIC_CHANGE"
    )
    return ExistingObjectChange(
        before.guid, before.hierarchy_path, tuple(changes), classification
    )


def _is_player_spawn_position_only_change(
    before: GameObject, after: GameObject, changes: list[str]
) -> bool:
    if not before.hierarchy_path.startswith("/Player Spawnpoints/PlayerSpawn_"):
        return False
    if changes != ["component_payload:ModTransform"]:
        return False
    before_by_guid = {item.guid: item for item in before.components}
    after_by_guid = {item.guid: item for item in after.components}
    field_changes: list[tuple[str, str]] = []
    for guid in before_by_guid.keys() & after_by_guid.keys():
        left = before_by_guid[guid]
        right = after_by_guid[guid]
        right_fields = {item.name: item for item in right.fields}
        for field in left.fields:
            other = right_fields.get(field.name)
            if other is not None and field.raw != other.raw:
                field_changes.append((left.type_name, field.name))
    return field_changes == [("ModTransform", "position")]


def _raw_diff_ranges(before: bytes, after: bytes) -> tuple[RawDiffRange, ...]:
    matcher = SequenceMatcher(None, before, after, autojunk=False)
    return tuple(
        RawDiffRange(operation, i1, i2, j1, j2)
        for operation, i1, i2, j1, j2 in matcher.get_opcodes()
        if operation != "equal"
    )


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()
