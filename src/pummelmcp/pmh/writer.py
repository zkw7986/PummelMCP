"""Safe minimal-patch writer for existing Transform and allowlisted fields."""

from __future__ import annotations

import hashlib
import os
import stat
import struct
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from .errors import (
    AmbiguousObjectError,
    AmbiguousComponentError,
    ComponentNotFoundError,
    ComponentPropertyNotFoundError,
    ComponentPropertyReadOnlyError,
    ComponentWriteError,
    ConcurrentModificationError,
    ObjectNotFoundError,
    PMHLookupError,
    TransformNotFoundError,
    TransformWriteError,
    UnknownComponentSchemaError,
    WriterValidationError,
)
from .models import Component, Field, GameObject, Scene, Vector3
from .reader import PMHReader
from .schemas import CodecPatch, SchemaValueError, get_component_schema


_AXES = ("x", "y", "z")
_TRANSFORM_PROPERTIES = ("position", "rotation", "scale")


@dataclass(frozen=True, slots=True)
class ObjectIdentifier:
    """Exactly one safe way to identify an existing GameObject."""

    guid: str | None = None
    hierarchy_path: str | None = None
    unique_name: str | None = None

    def __post_init__(self) -> None:
        supplied = sum(
            value is not None
            for value in (self.guid, self.hierarchy_path, self.unique_name)
        )
        if supplied != 1:
            raise ValueError("ObjectIdentifier requires exactly one identifier")

    @classmethod
    def by_guid(cls, guid: str) -> ObjectIdentifier:
        return cls(guid=guid)

    @classmethod
    def by_path(cls, hierarchy_path: str) -> ObjectIdentifier:
        return cls(hierarchy_path=hierarchy_path)

    @classmethod
    def by_name(cls, unique_name: str) -> ObjectIdentifier:
        return cls(unique_name=unique_name)

    @classmethod
    def parse(cls, value: str) -> ObjectIdentifier:
        """Parse the public CLI/API shorthand: path, UUID GUID, or unique name."""
        if value.startswith("/"):
            return cls.by_path(value)
        try:
            uuid.UUID(value)
        except (ValueError, AttributeError):
            return cls.by_name(value)
        return cls.by_guid(value)


@dataclass(frozen=True, slots=True)
class AxisChange:
    before: Any
    after: Any
    source_start: int
    source_length: int = 4


@dataclass(frozen=True, slots=True)
class ValidationResult:
    passed: bool
    checks: Mapping[str, bool]
    changed_offsets: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ChangeReport:
    object_name: str
    guid: str
    hierarchy_path: str
    component: str
    component_guid: str
    property_changes: Mapping[str, Mapping[str, AxisChange]]
    source_file: Path
    dry_run: bool
    backup_path: Path | None
    validation: ValidationResult
    bytes_changed: int

    @property
    def changes(self) -> Mapping[str, AxisChange]:
        """Return axis changes for a single-property report."""
        property_name = self.property
        return self.property_changes[property_name] if property_name else {}

    @property
    def property(self) -> str | None:
        """Return the property name for a single-property change report."""
        if len(self.property_changes) != 1:
            return None
        return next(iter(self.property_changes))

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["object"] = result.pop("object_name")
        result["object_guid"] = result["guid"]
        result["source_file"] = str(self.source_file)
        if self.backup_path is not None:
            result["backup_path"] = str(self.backup_path)
        if self.property is not None:
            result["property"] = self.property
            result["changes"] = result["property_changes"][self.property]
        return result


@dataclass(frozen=True, slots=True)
class _PlannedAxis:
    property_name: str
    axis: str
    source_start: int
    before_raw: bytes
    after_raw: bytes
    before: float
    after: float


@dataclass(frozen=True, slots=True)
class _PlannedComponentValue:
    property_name: str
    member: str
    source_start: int
    source_length: int
    before_raw: bytes
    after_raw: bytes
    before: Any
    after: Any


class PMHScene:
    """A loaded PMH file with narrowly scoped, validated mutation APIs."""

    def __init__(self, source: Path, original_bytes: bytes, scene: Scene) -> None:
        self.source = source
        self._original_bytes = original_bytes
        self.scene = scene

    @classmethod
    def load(cls, path: str | Path) -> PMHScene:
        source = Path(path)
        original = source.read_bytes()
        scene = PMHReader().read_bytes(original, source=source)
        return cls(source, original, scene)

    def get_transform(
        self, identifier: ObjectIdentifier | str
    ) -> Component:
        """Return the unique existing ModTransform selected by identifier."""
        return self._get_transform_target(identifier)[1]

    def get_object(self, identifier: ObjectIdentifier | str) -> GameObject:
        """Resolve an object with the same rules used by all writer operations."""
        return self._resolve_object(identifier)

    def get_component(
        self,
        identifier: ObjectIdentifier | str,
        component_identifier: str,
    ) -> Component:
        """Resolve one existing component by GUID or unambiguous type name."""
        obj = self._resolve_object(identifier)
        return self._resolve_component(obj, component_identifier)

    def get_component_property(
        self,
        identifier: ObjectIdentifier | str,
        component_identifier: str,
        property_name: str,
    ) -> Field:
        """Return one existing property without exposing mutable raw storage."""
        return self.get_component(identifier, component_identifier).get_field(
            property_name
        )

    def get_component_properties(
        self,
        identifier: ObjectIdentifier | str,
        component_identifier: str,
    ) -> tuple[Field, ...]:
        """Return all properties on one uniquely selected existing component."""
        return tuple(self.get_component(identifier, component_identifier).fields)

    def set_component_property(
        self,
        identifier: ObjectIdentifier | str,
        component_identifier: str,
        property_name: str,
        value: Any,
        *,
        dry_run: bool = False,
        backup: bool = True,
    ) -> ChangeReport:
        """Patch one registered fixed-length property without reserializing PMH."""
        obj = self._resolve_object(identifier)
        component = self._resolve_component(obj, component_identifier)
        component_schema = get_component_schema(component.type_name)
        if component_schema is None:
            raise UnknownComponentSchemaError(
                f"component type {component.type_name!r} has no writable Component schema"
            )
        property_schema = component_schema.get(property_name)
        if property_schema is None:
            raise ComponentPropertyNotFoundError(
                f"property {property_name!r} is not registered for {component.type_name!r}"
            )
        if not property_schema.writable:
            detail = f": {property_schema.note}" if property_schema.note else ""
            raise ComponentPropertyReadOnlyError(
                f"property {component.type_name}.{property_name} is read-only{detail}"
            )
        codec = property_schema.codec
        if codec is None or codec.byte_length is None:
            raise ComponentWriteError(
                f"property {component.type_name}.{property_name} has no fixed-length writable codec"
            )
        try:
            field_value = component.get_field(property_name)
        except PMHLookupError as exc:
            raise ComponentPropertyNotFoundError(
                f"property {property_name!r} does not exist on component {component.guid!r}"
            ) from exc
        span = field_value.source_span
        if (
            span is None
            or span.length != codec.byte_length
            or len(field_value.raw) != codec.byte_length
        ):
            raise ComponentWriteError(
                f"{component.type_name}.{property_name} does not have the required "
                f"{codec.byte_length}-byte source span"
            )
        try:
            codec_patch = codec.patch(field_value.raw, value)
        except SchemaValueError as exc:
            raise ComponentWriteError(
                f"invalid value for {component.type_name}.{property_name}: {exc}"
            ) from exc

        patched = bytearray(self._original_bytes)
        planned = self._plan_component_patch(
            patched, property_name, span.start, codec, codec_patch
        )
        patched_bytes = bytes(patched)
        if dry_run:
            reparsed = PMHReader().read_bytes(patched_bytes, source=self.source)
            validation = self._validate_component(
                reparsed,
                patched_bytes,
                obj,
                component,
                field_value,
                codec_patch.raw,
                planned,
            )
            backup_path = None
        else:
            self._assert_source_unchanged()
            validation, backup_path = self._safe_replace_component(
                patched_bytes,
                obj,
                component,
                field_value,
                codec_patch.raw,
                planned,
                backup=backup,
            )
            self._original_bytes = patched_bytes
            self.scene = PMHReader().read_bytes(patched_bytes, source=self.source)

        property_changes = {
            property_name: {
                item.member: AxisChange(
                    before=item.before,
                    after=item.after,
                    source_start=item.source_start,
                    source_length=item.source_length,
                )
                for item in planned
            }
        }
        return ChangeReport(
            object_name=obj.name,
            guid=obj.guid,
            hierarchy_path=self._object_path(obj),
            component=component.type_name,
            component_guid=component.guid,
            property_changes=property_changes,
            source_file=self.source,
            dry_run=dry_run,
            backup_path=backup_path,
            validation=validation,
            bytes_changed=len(validation.changed_offsets),
        )

    def _get_transform_target(
        self, identifier: ObjectIdentifier | str
    ) -> tuple[GameObject, Component]:
        obj = self._resolve_object(identifier)
        matches = [c for c in obj.components if c.type_name == "ModTransform"]
        if len(matches) != 1:
            detail = "missing" if not matches else f"ambiguous ({len(matches)} matches)"
            raise TransformNotFoundError(
                f"ModTransform on object {obj.name!r} is {detail}"
            )
        return obj, matches[0]

    def set_position(
        self,
        identifier: ObjectIdentifier | str,
        *,
        x: float | None = None,
        y: float | None = None,
        z: float | None = None,
        dry_run: bool = False,
        backup: bool = True,
    ) -> ChangeReport:
        return self._set_properties(
            identifier,
            {"position": {"x": x, "y": y, "z": z}},
            dry_run=dry_run,
            backup=backup,
        )

    def set_rotation(
        self,
        identifier: ObjectIdentifier | str,
        *,
        x: float | None = None,
        y: float | None = None,
        z: float | None = None,
        dry_run: bool = False,
        backup: bool = True,
    ) -> ChangeReport:
        return self._set_properties(
            identifier,
            {"rotation": {"x": x, "y": y, "z": z}},
            dry_run=dry_run,
            backup=backup,
        )

    def set_scale(
        self,
        identifier: ObjectIdentifier | str,
        *,
        x: float | None = None,
        y: float | None = None,
        z: float | None = None,
        dry_run: bool = False,
        backup: bool = True,
    ) -> ChangeReport:
        return self._set_properties(
            identifier,
            {"scale": {"x": x, "y": y, "z": z}},
            dry_run=dry_run,
            backup=backup,
        )

    def set_transform(
        self,
        identifier: ObjectIdentifier | str,
        *,
        position: Mapping[str, float | None] | None = None,
        rotation: Mapping[str, float | None] | None = None,
        scale: Mapping[str, float | None] | None = None,
        dry_run: bool = False,
        backup: bool = True,
    ) -> ChangeReport:
        updates = {
            name: values
            for name, values in (
                ("position", position),
                ("rotation", rotation),
                ("scale", scale),
            )
            if values is not None
        }
        return self._set_properties(
            identifier, updates, dry_run=dry_run, backup=backup
        )

    def _set_properties(
        self,
        identifier: ObjectIdentifier | str,
        updates: Mapping[str, Mapping[str, float | None]],
        *,
        dry_run: bool,
        backup: bool,
    ) -> ChangeReport:
        obj, transform = self._get_transform_target(identifier)
        planned: list[_PlannedAxis] = []
        patched = bytearray(self._original_bytes)

        for property_name, axis_values in updates.items():
            if property_name not in _TRANSFORM_PROPERTIES:
                raise TransformWriteError(
                    f"writing transform property {property_name!r} is not allowed"
                )
            field_value = transform.get_field(property_name)
            planned.extend(
                self._plan_field_patch(patched, property_name, field_value, axis_values)
            )
        if not planned:
            raise TransformWriteError("at least one transform axis must be supplied")

        patched_bytes = bytes(patched)
        if dry_run:
            reparsed = PMHReader().read_bytes(patched_bytes, source=self.source)
            validation = self._validate(
                reparsed, patched_bytes, obj, transform, planned
            )
            backup_path = None
        else:
            self._assert_source_unchanged()
            validation, backup_path = self._safe_replace(
                patched_bytes, obj, transform, planned, backup=backup
            )
            self._original_bytes = patched_bytes
            self.scene = PMHReader().read_bytes(patched_bytes, source=self.source)

        property_changes: dict[str, dict[str, AxisChange]] = {}
        for item in planned:
            property_changes.setdefault(item.property_name, {})[item.axis] = AxisChange(
                before=item.before,
                after=item.after,
                source_start=item.source_start,
            )
        return ChangeReport(
            object_name=obj.name,
            guid=obj.guid,
            hierarchy_path=self._object_path(obj),
            component="ModTransform",
            component_guid=transform.guid,
            property_changes=property_changes,
            source_file=self.source,
            dry_run=dry_run,
            backup_path=backup_path,
            validation=validation,
            bytes_changed=len(validation.changed_offsets),
        )

    @staticmethod
    def _plan_field_patch(
        patched: bytearray,
        property_name: str,
        field_value: Field,
        axis_values: Mapping[str, float | None],
    ) -> list[_PlannedAxis]:
        unknown_axes = set(axis_values) - set(_AXES)
        if unknown_axes:
            raise TransformWriteError(f"invalid transform axes: {sorted(unknown_axes)!r}")
        span = field_value.source_span
        if span is None or span.length != 12 or len(field_value.raw) != 12:
            raise TransformWriteError(
                f"{property_name} does not have a safe 12-byte source span"
            )
        if not isinstance(field_value.value, Vector3):
            raise TransformWriteError(f"{property_name} is not a decoded Vector3")

        result = []
        for index, axis in enumerate(_AXES):
            requested = axis_values.get(axis)
            if requested is None:
                continue
            start = span.start + index * 4
            before_raw = bytes(patched[start : start + 4])
            try:
                after_raw = struct.pack("<f", requested)
            except (OverflowError, struct.error, TypeError) as exc:
                raise TransformWriteError(
                    f"invalid float32 value for {property_name}.{axis}: {requested!r}"
                ) from exc
            patched[start : start + 4] = after_raw
            result.append(
                _PlannedAxis(
                    property_name=property_name,
                    axis=axis,
                    source_start=start,
                    before_raw=before_raw,
                    after_raw=after_raw,
                    before=struct.unpack("<f", before_raw)[0],
                    after=struct.unpack("<f", after_raw)[0],
                )
            )
        return result

    @staticmethod
    def _plan_component_patch(
        patched: bytearray,
        property_name: str,
        source_start: int,
        codec: Any,
        codec_patch: CodecPatch,
    ) -> list[_PlannedComponentValue]:
        members = getattr(codec, "members", ("value",))
        result = []
        for member, (before, after) in codec_patch.changes.items():
            if member == "value":
                relative_start = 0
                length = codec.byte_length
            else:
                relative_start = members.index(member) * 4
                length = 4
            if length is None:
                raise ComponentWriteError("variable-length component patch is not allowed")
            start = source_start + relative_start
            before_raw = bytes(patched[start : start + length])
            after_raw = codec_patch.raw[relative_start : relative_start + length]
            patched[start : start + length] = after_raw
            result.append(
                _PlannedComponentValue(
                    property_name=property_name,
                    member=member,
                    source_start=start,
                    source_length=length,
                    before_raw=before_raw,
                    after_raw=after_raw,
                    before=before,
                    after=after,
                )
            )
        return result

    def _safe_replace(
        self,
        patched: bytes,
        original_object: GameObject,
        original_transform: Component,
        planned: list[_PlannedAxis],
        *,
        backup: bool,
    ) -> tuple[ValidationResult, Path | None]:
        return self._replace_with_validation(
            patched,
            lambda reparsed, data: self._validate(
                reparsed, data, original_object, original_transform, planned
            ),
            backup=backup,
        )

    def _safe_replace_component(
        self,
        patched: bytes,
        original_object: GameObject,
        original_component: Component,
        original_field: Field,
        expected_field_raw: bytes,
        planned: list[_PlannedComponentValue],
        *,
        backup: bool,
    ) -> tuple[ValidationResult, Path | None]:
        return self._replace_with_validation(
            patched,
            lambda reparsed, data: self._validate_component(
                reparsed,
                data,
                original_object,
                original_component,
                original_field,
                expected_field_raw,
                planned,
            ),
            backup=backup,
        )

    def _replace_with_validation(
        self,
        patched: bytes,
        validator: Callable[[Scene, bytes], ValidationResult],
        *,
        backup: bool,
    ) -> tuple[ValidationResult, Path | None]:
        """Shared temp-parse-validate-backup-atomic-replace safety pipeline."""
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.source.name}.", suffix=".tmp", dir=self.source.parent
        )
        temp_path = Path(temp_name)
        backup_path: Path | None = None
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(patched)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temp_path, stat.S_IMODE(self.source.stat().st_mode))

            reparsed = PMHReader().read_path(temp_path)
            validation = validator(reparsed, temp_path.read_bytes())
            self._assert_source_unchanged()
            if backup:
                backup_path = self._create_backup(self._original_bytes)
            os.replace(temp_path, self.source)
            return validation, backup_path
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    def _validate(
        self,
        reparsed: Scene,
        patched: bytes,
        original_object: GameObject,
        original_transform: Component,
        planned: list[_PlannedAxis],
    ) -> ValidationResult:
        allowed_offsets = {
            offset
            for item in planned
            for offset in range(item.source_start, item.source_start + 4)
        }
        changed_offsets = tuple(
            index
            for index, (before, after) in enumerate(
                zip(self._original_bytes, patched, strict=False)
            )
            if before != after
        )
        checks: dict[str, bool] = {
            "same_byte_length": len(self._original_bytes) == len(patched),
            "magic_unchanged": reparsed.magic == self.scene.magic,
            "version_unchanged": reparsed.version == self.scene.version,
            "root_count_unchanged": reparsed.root_count == self.scene.root_count,
            "object_count_unchanged": reparsed.object_count == self.scene.object_count,
            "component_count_unchanged": (
                reparsed.component_count == self.scene.component_count
            ),
            "fully_consumed": reparsed.fully_consumed,
            "diff_only_in_requested_axes": set(changed_offsets) <= allowed_offsets,
        }

        target_matches = [obj for obj in reparsed.walk() if obj.guid == original_object.guid]
        checks["target_object_guid_unchanged"] = len(target_matches) == 1
        target_transform: Component | None = None
        if len(target_matches) == 1:
            component_matches = [
                component
                for component in target_matches[0].components
                if component.type_name == "ModTransform"
                and component.guid == original_transform.guid
            ]
            checks["target_component_guid_unchanged"] = len(component_matches) == 1
            if len(component_matches) == 1:
                target_transform = component_matches[0]
        else:
            checks["target_component_guid_unchanged"] = False

        planned_by_property: dict[str, dict[str, _PlannedAxis]] = {}
        for item in planned:
            planned_by_property.setdefault(item.property_name, {})[item.axis] = item
        for property_name, changed_axes in planned_by_property.items():
            original_field = original_transform.get_field(property_name)
            if target_transform is None:
                checks[f"{property_name}_new_values"] = False
                checks[f"{property_name}_untouched_axes_raw"] = False
                continue
            new_field = target_transform.get_field(property_name)
            new_values_ok = True
            untouched_ok = True
            for index, axis in enumerate(_AXES):
                axis_raw = new_field.raw[index * 4 : index * 4 + 4]
                if axis in changed_axes:
                    new_values_ok &= axis_raw == changed_axes[axis].after_raw
                else:
                    untouched_ok &= axis_raw == original_field.raw[index * 4 : index * 4 + 4]
            checks[f"{property_name}_new_values"] = new_values_ok
            checks[f"{property_name}_untouched_axes_raw"] = untouched_ok

        passed = all(checks.values())
        result = ValidationResult(passed, checks, changed_offsets)
        if not passed:
            failed = ", ".join(name for name, ok in checks.items() if not ok)
            raise WriterValidationError(f"patched PMH validation failed: {failed}")
        return result

    def _validate_component(
        self,
        reparsed: Scene,
        patched: bytes,
        original_object: GameObject,
        original_component: Component,
        original_field: Field,
        expected_field_raw: bytes,
        planned: list[_PlannedComponentValue],
    ) -> ValidationResult:
        allowed_offsets = {
            offset
            for item in planned
            for offset in range(item.source_start, item.source_start + item.source_length)
        }
        changed_offsets = tuple(
            index
            for index, (before, after) in enumerate(
                zip(self._original_bytes, patched, strict=False)
            )
            if before != after
        )
        checks: dict[str, bool] = {
            "same_byte_length": len(self._original_bytes) == len(patched),
            "magic_unchanged": reparsed.magic == self.scene.magic,
            "version_unchanged": reparsed.version == self.scene.version,
            "root_count_unchanged": reparsed.root_count == self.scene.root_count,
            "object_count_unchanged": reparsed.object_count == self.scene.object_count,
            "component_count_unchanged": (
                reparsed.component_count == self.scene.component_count
            ),
            "fully_consumed": reparsed.fully_consumed,
            "diff_only_in_requested_value": set(changed_offsets) <= allowed_offsets,
        }

        target_objects = [
            obj for obj in reparsed.walk() if obj.guid == original_object.guid
        ]
        checks["target_object_guid_unchanged"] = len(target_objects) == 1
        target_component: Component | None = None
        if len(target_objects) == 1:
            components = [
                component
                for component in target_objects[0].components
                if component.guid == original_component.guid
                and component.type_name == original_component.type_name
            ]
            checks["target_component_guid_unchanged"] = len(components) == 1
            if len(components) == 1:
                target_component = components[0]
        else:
            checks["target_component_guid_unchanged"] = False

        target_field: Field | None = None
        if target_component is not None:
            matches = [
                field
                for field in target_component.fields
                if field.name == original_field.name
            ]
            checks["target_property_exists"] = len(matches) == 1
            if len(matches) == 1:
                target_field = matches[0]
        else:
            checks["target_property_exists"] = False
        checks["target_property_value_matches"] = (
            target_field is not None and target_field.raw == expected_field_raw
        )
        checks["target_property_span_unchanged"] = (
            target_field is not None
            and target_field.source_span == original_field.source_span
        )

        original_span = original_field.source_span
        property_allowed = (
            {
                offset - original_span.start
                for offset in allowed_offsets
                if original_span is not None
            }
            if original_span is not None
            else set()
        )
        checks["untouched_property_bytes_unchanged"] = (
            target_field is not None
            and len(target_field.raw) == len(original_field.raw)
            and all(
                before == after or index in property_allowed
                for index, (before, after) in enumerate(
                    zip(original_field.raw, target_field.raw, strict=True)
                )
            )
        )
        checks["other_component_properties_unchanged"] = (
            target_component is not None
            and [
                (field.name, field.raw)
                for field in target_component.fields
                if field.name != original_field.name
            ]
            == [
                (field.name, field.raw)
                for field in original_component.fields
                if field.name != original_field.name
            ]
        )
        if original_component.type_name == "ModTrigger":
            for action_name in (
                "OnHitActions",
                "OnEnterActions",
                "OnExitActions",
                "OnStayActions",
            ):
                original_actions = [
                    field.raw
                    for field in original_component.fields
                    if field.name == action_name
                ]
                reparsed_actions = (
                    [
                        field.raw
                        for field in target_component.fields
                        if field.name == action_name
                    ]
                    if target_component is not None
                    else []
                )
                checks[f"{action_name}_unchanged"] = (
                    len(original_actions) == 1
                    and reparsed_actions == original_actions
                )

        passed = all(checks.values())
        result = ValidationResult(passed, checks, changed_offsets)
        if not passed:
            failed = ", ".join(name for name, ok in checks.items() if not ok)
            raise WriterValidationError(f"patched PMH validation failed: {failed}")
        return result

    def _resolve_object(self, identifier: ObjectIdentifier | str) -> GameObject:
        if isinstance(identifier, str):
            identifier = ObjectIdentifier.parse(identifier)
        if not isinstance(identifier, ObjectIdentifier):
            raise TypeError("identifier must be a string or ObjectIdentifier")

        if identifier.guid is not None:
            matches = [
                obj
                for obj in self.scene.walk()
                if obj.guid.casefold() == identifier.guid.casefold()
            ]
            label = f"GUID {identifier.guid!r}"
        elif identifier.hierarchy_path is not None:
            wanted = tuple(
                part for part in identifier.hierarchy_path.strip("/").split("/") if part
            )
            matches = [obj for obj in self.scene.walk() if self._path_parts(obj) == wanted]
            label = f"hierarchy path {identifier.hierarchy_path!r}"
        else:
            matches = self.scene.find_game_objects(identifier.unique_name or "")
            label = f"name {identifier.unique_name!r}"

        if not matches:
            raise ObjectNotFoundError(f"no GameObject matches {label}")
        if len(matches) > 1:
            raise AmbiguousObjectError(
                f"{len(matches)} GameObjects match {label}; use GUID or hierarchy path"
            )
        return matches[0]

    @staticmethod
    def _resolve_component(obj: GameObject, component_identifier: str) -> Component:
        guid_matches = [
            component
            for component in obj.components
            if component.guid.casefold() == component_identifier.casefold()
        ]
        if guid_matches:
            if len(guid_matches) != 1:
                raise AmbiguousComponentError(
                    f"component GUID {component_identifier!r} is ambiguous on {obj.name!r}"
                )
            return guid_matches[0]

        type_matches = [
            component
            for component in obj.components
            if component.type_name == component_identifier
        ]
        if not type_matches:
            raise ComponentNotFoundError(
                f"no component matches GUID or type {component_identifier!r} on {obj.name!r}"
            )
        if len(type_matches) > 1:
            raise AmbiguousComponentError(
                f"{len(type_matches)} components have type {component_identifier!r} on "
                f"{obj.name!r}; use component GUID"
            )
        return type_matches[0]

    @staticmethod
    def _path_parts(obj: GameObject) -> tuple[str, ...]:
        parts = []
        current: GameObject | None = obj
        while current is not None:
            parts.append(current.name)
            current = current.parent
        return tuple(reversed(parts))

    @classmethod
    def _object_path(cls, obj: GameObject) -> str:
        return obj.hierarchy_path

    def _assert_source_unchanged(self) -> None:
        if self.source.read_bytes() != self._original_bytes:
            raise ConcurrentModificationError(
                f"source changed after load and will not be replaced: {self.source}"
            )

    def _create_backup(self, original: bytes) -> Path:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        for counter in range(1000):
            suffix = f".{counter}" if counter else ""
            candidate = self.source.with_name(
                f"{self.source.name}.bak.{stamp}{suffix}"
            )
            try:
                fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            except FileExistsError:
                continue
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(original)
                    stream.flush()
                    os.fsync(stream.fileno())
            except Exception:
                candidate.unlink(missing_ok=True)
                raise
            return candidate
        raise TransformWriteError("could not allocate a unique backup filename")

    @property
    def source_sha256(self) -> str:
        return hashlib.sha256(self._original_bytes).hexdigest()
