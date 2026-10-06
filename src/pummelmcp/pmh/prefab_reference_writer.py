"""Validated-template writer for one existing SpawnPrefabAction prefab item."""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import asdict, dataclass, field as dataclass_field
from pathlib import Path
from typing import Any, Mapping

from .action_references import (
    MAX_ASSET_METADATA_BYTES,
    AssetRecord,
    build_asset_catalog,
    find_mod_root,
    inspect_action_entry_references,
)
from .action_writer import (
    ActionFieldWriter,
    ActionValidationResult,
    GraphFingerprint,
    _TokenReplacement,
    _apply_replacements,
    _graph_fingerprint,
    _reframe_reference_json,
    _valid_sha256,
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
    AmbiguousPrefabReferenceTemplateError,
    NoValidatedPrefabReferenceTemplateError,
    PrefabReferenceAssetError,
    PrefabReferenceCurrentGuidMismatchError,
    PrefabReferenceIndexError,
)
from .json_spans import JsonSpanError, parse_json_spans
from .models import Component, Field, GameObject, Scene, SourceSpan
from .reader import PMHReader
from .writer import PMHScene


@dataclass(frozen=True, slots=True)
class PrefabReferenceTemplateSource:
    source_file: Path
    object_guid: str
    hierarchy_path: str
    component_guid: str
    event_property: str
    action_index: int
    action_rid: int
    prefab_index: int
    source_span: SourceSpan

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["source_file"] = str(self.source_file)
        result["source_span"] = _span_dict(self.source_span)
        return result


@dataclass(frozen=True, slots=True)
class PrefabReferenceTemplate:
    guid: str
    relative_path: str | None
    metadata_relative_path: str | None
    raw_bytes: bytes
    raw_value: Any
    normalized_value: Mapping[str, Any]
    unknown_fields: Mapping[str, Any]
    reference_sha256: str
    normalized_sha256: str
    asset_sha256: str | None
    metadata_sha256: str | None
    evidence_status: str
    sources: tuple[PrefabReferenceTemplateSource, ...]

    @property
    def validated(self) -> bool:
        return self.relative_path is not None and self.metadata_relative_path is not None


@dataclass(frozen=True, slots=True)
class PrefabReferenceTemplateGroup:
    guid: str
    occurrence_count: int
    variants: tuple[PrefabReferenceTemplate, ...]

    @property
    def ambiguous(self) -> bool:
        return len(self.variants) != 1


@dataclass(frozen=True, slots=True)
class PrefabReferenceTemplateCatalog:
    groups: Mapping[str, PrefabReferenceTemplateGroup]
    total_occurrences: int
    mod_root: Path
    allowed_root: Path
    asset_catalog: Mapping[str, tuple[AssetRecord, ...]]
    warnings: tuple[str, ...] = ()

    def get(self, guid: str) -> PrefabReferenceTemplateGroup | None:
        return self.groups.get(guid)


@dataclass(frozen=True, slots=True)
class PrefabListFingerprint:
    ordered_guids: tuple[str | None, ...]
    item_sha256: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "ordered_guids": list(self.ordered_guids),
            "item_sha256": list(self.item_sha256),
        }


@dataclass(frozen=True, slots=True)
class PrefabReferenceChangeReport:
    object_name: str
    object_guid: str
    hierarchy_path: str
    component_guid: str
    event_property: str
    action_index: int
    action_rid: int
    namespace: str
    class_name: str
    prefab_index: int
    before: Mapping[str, Any]
    after: Mapping[str, Any]
    template_source: PrefabReferenceTemplateSource
    source_file: Path
    dry_run: bool
    no_op: bool
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
    prefab_list_fingerprint_before: PrefabListFingerprint
    prefab_list_fingerprint_after: PrefabListFingerprint
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
        result["template_source"] = self.template_source.to_dict()
        result["prefab_list_fingerprint_before"] = self.prefab_list_fingerprint_before.to_dict()
        result["prefab_list_fingerprint_after"] = self.prefab_list_fingerprint_after.to_dict()
        result["graph_fingerprint_before"] = self.graph_fingerprint_before.to_dict()
        result["graph_fingerprint_after"] = self.graph_fingerprint_after.to_dict()
        return result


@dataclass(slots=True)
class _VariantBuilder:
    guid: str
    relative_path: str | None
    metadata_relative_path: str | None
    raw_bytes: bytes
    raw_value: Any
    normalized_value: Mapping[str, Any]
    unknown_fields: Mapping[str, Any]
    asset_sha256: str | None
    metadata_sha256: str | None
    sources: list[PrefabReferenceTemplateSource] = dataclass_field(default_factory=list)


def build_prefab_reference_template_catalog(
    scene_path: str | Path, allowed_root: str | Path
) -> PrefabReferenceTemplateCatalog:
    loaded = PMHScene.load(scene_path)
    mod_root = find_mod_root(loaded.source, allowed_root)
    if mod_root is None:
        raise PrefabReferenceAssetError(
            "no contained Mod root with an Assets directory was found"
        )
    return _build_template_catalog(
        loaded.scene,
        mod_root,
        Path(allowed_root),
        source_file=loaded.source,
    )


def _build_template_catalog(
    scene: Scene, mod_root: Path, allowed_root: Path, *, source_file: Path
) -> PrefabReferenceTemplateCatalog:
    root = mod_root.resolve(strict=True)
    allowed = allowed_root.resolve(strict=True)
    asset_catalog, scan_warnings = build_asset_catalog(root, allowed)
    variants: dict[tuple[str, bytes], _VariantBuilder] = {}
    occurrence_count = 0
    for obj in scene.walk():
        for component in obj.components:
            if component.type_name != "ModTrigger":
                continue
            for event_property in ACTION_EVENT_PROPERTIES:
                try:
                    action_field = component.get_field(event_property)
                except Exception:
                    continue
                parsed = parse_action_payload(
                    action_field.raw,
                    source_property=event_property,
                    source_span=action_field.source_span,
                )
                for action in parsed.actions:
                    if (
                        action.type_info is None
                        or action.type_info.namespace != "ModSystem.Logic"
                        or action.type_info.class_name != "SpawnPrefabAction"
                        or action.rid is None
                        or action.resolved_reference is None
                    ):
                        continue
                    inspection = inspect_action_entry_references(
                        action, asset_catalog=asset_catalog
                    )
                    prefab_fields = [
                        item
                        for item in inspection.references
                        if item.source_field == "m_prefabs"
                    ]
                    if len(prefab_fields) != 1:
                        continue
                    for item in prefab_fields[0].items:
                        guid = item.normalized_value.get("guid")
                        if not isinstance(guid, str):
                            continue
                        occurrence_count += 1
                        resolution = item.resolution
                        representation_valid = (
                            isinstance(item.raw_value, Mapping)
                            and isinstance(item.raw_value.get("m_assetGUID"), Mapping)
                            and isinstance(item.raw_value.get("m_asset"), Mapping)
                            and item.normalized_value.get("asset_guid") == guid
                            and resolution is not None
                            and resolution.resolved
                        )
                        metadata_hash = None
                        if representation_valid and resolution.metadata_relative_path:
                            metadata_path = (root / resolution.metadata_relative_path).resolve(strict=True)
                            metadata_hash = _hash_file(metadata_path)
                        key = (guid, item.raw_bytes)
                        builder = variants.get(key)
                        if builder is None:
                            builder = _VariantBuilder(
                                guid=guid,
                                relative_path=(resolution.relative_path if representation_valid else None),
                                metadata_relative_path=(
                                    resolution.metadata_relative_path if representation_valid else None
                                ),
                                raw_bytes=item.raw_bytes,
                                raw_value=item.raw_value,
                                normalized_value=item.normalized_value,
                                unknown_fields=item.unknown_fields,
                                asset_sha256=resolution.sha256 if representation_valid else None,
                                metadata_sha256=metadata_hash,
                            )
                            variants[key] = builder
                        builder.sources.append(
                            PrefabReferenceTemplateSource(
                                source_file=source_file,
                                object_guid=obj.guid,
                                hierarchy_path=obj.hierarchy_path,
                                component_guid=component.guid,
                                event_property=event_property,
                                action_index=action.index,
                                action_rid=action.rid,
                                prefab_index=item.index,
                                source_span=item.source_span,
                            )
                        )

    grouped: dict[str, list[PrefabReferenceTemplate]] = {}
    for builder in variants.values():
        template = PrefabReferenceTemplate(
            guid=builder.guid,
            relative_path=builder.relative_path,
            metadata_relative_path=builder.metadata_relative_path,
            raw_bytes=builder.raw_bytes,
            raw_value=builder.raw_value,
            normalized_value=builder.normalized_value,
            unknown_fields=builder.unknown_fields,
            reference_sha256=_sha256(builder.raw_bytes),
            normalized_sha256=_normalized_sha256(builder.raw_value),
            asset_sha256=builder.asset_sha256,
            metadata_sha256=builder.metadata_sha256,
            evidence_status="CONFIRMED",
            sources=tuple(builder.sources),
        )
        grouped.setdefault(builder.guid, []).append(template)
    groups = {
        guid: PrefabReferenceTemplateGroup(
            guid=guid,
            occurrence_count=sum(len(item.sources) for item in items),
            variants=tuple(sorted(items, key=lambda item: item.reference_sha256)),
        )
        for guid, items in grouped.items()
    }
    return PrefabReferenceTemplateCatalog(
        groups=groups,
        total_occurrences=occurrence_count,
        mod_root=root,
        allowed_root=allowed,
        asset_catalog=asset_catalog,
        warnings=scan_warnings,
    )


class PrefabReferenceWriter:
    """Replace one existing m_prefabs item using an observed complete template."""

    def __init__(self, path: str | Path, allowed_root: str | Path) -> None:
        self.loaded = PMHScene.load(path)
        self.source = self.loaded.source
        self.original_bytes = self.loaded._original_bytes
        mod_root = find_mod_root(self.source, allowed_root)
        if mod_root is None:
            raise PrefabReferenceAssetError(
                "no contained Mod root with an Assets directory was found"
            )
        self.catalog = _build_template_catalog(
            self.loaded.scene,
            mod_root,
            Path(allowed_root),
            source_file=self.source,
        )

    def replace_prefab_reference(
        self,
        object_identifier: str,
        component_identifier: str,
        event_property: str,
        action_rid: int,
        prefab_index: int,
        *,
        target_prefab_guid: str | None = None,
        target_prefab_relative_path: str | None = None,
        expected_current_prefab_guid: str | None = None,
        template_reference_sha256: str | None = None,
        template_scene_path: str | Path | None = None,
        expected_payload_sha256: str | None = None,
        dry_run: bool = False,
        backup: bool = True,
    ) -> PrefabReferenceChangeReport:
        if event_property not in ACTION_EVENT_PROPERTIES:
            allowed = ", ".join(sorted(ACTION_EVENT_PROPERTIES))
            raise ActionFramingError(f"event_property must be one of: {allowed}")
        if isinstance(action_rid, bool) or not isinstance(action_rid, int):
            raise ActionRidNotFoundError("action_rid must be an integer")
        if isinstance(prefab_index, bool) or not isinstance(prefab_index, int):
            raise PrefabReferenceIndexError("prefab_index must be an integer")
        if (target_prefab_guid is None) == (target_prefab_relative_path is None):
            raise PrefabReferenceAssetError(
                "provide exactly one of target_prefab_guid or target_prefab_relative_path"
            )

        obj = self.loaded.get_object(object_identifier)
        component = self.loaded.get_component(object_identifier, component_identifier)
        if component.type_name != "ModTrigger":
            raise ActionFramingError(
                f"component must be ModTrigger, got {component.type_name!r}"
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

        parsed = parse_action_payload(
            field.raw, source_property=event_property, source_span=field.source_span
        )
        ActionFieldWriter._require_safe_graph(parsed)
        action = ActionFieldWriter._resolve_action(parsed, action_rid)
        type_info = action.type_info
        if type_info is None or action.resolved_reference is None:
            raise ActionRidNotFoundError(f"Action rid {action_rid} is unresolved")
        if (
            type_info.namespace != "ModSystem.Logic"
            or type_info.class_name != "SpawnPrefabAction"
        ):
            raise ActionClassMismatchError(
                "replace_prefab_reference requires ModSystem.Logic.SpawnPrefabAction"
            )
        reference = action.resolved_reference
        if (
            reference.raw_segment is None
            or reference.source_span is None
            or reference.segment_index is None
        ):
            raise ActionFramingError("target Action has no separately framed reference JSON")

        inspection = inspect_action_entry_references(
            action, asset_catalog=self.catalog.asset_catalog
        )
        prefab_fields = [
            item for item in inspection.references if item.source_field == "m_prefabs"
        ]
        if len(prefab_fields) != 1:
            raise ActionFramingError("m_prefabs is not uniquely available")
        prefab_field = prefab_fields[0]
        if not 0 <= prefab_index < len(prefab_field.items):
            raise PrefabReferenceIndexError(
                f"prefab_index {prefab_index} is outside existing list length {len(prefab_field.items)}"
            )
        current = prefab_field.items[prefab_index]
        current_guid = current.normalized_value.get("guid")
        if not isinstance(current_guid, str):
            raise PrefabReferenceAssetError("current prefab item has no string GUID")
        if (
            expected_current_prefab_guid is not None
            and current_guid != expected_current_prefab_guid
        ):
            raise PrefabReferenceCurrentGuidMismatchError(
                "current prefab GUID differs from expected_current_prefab_guid; inspect again"
            )

        template_catalog = self._template_catalog(
            template_scene_path, template_reference_sha256
        )
        template = self._resolve_target_template(
            target_prefab_guid,
            target_prefab_relative_path,
            catalog=template_catalog,
            template_reference_sha256=template_reference_sha256,
        )
        before_fingerprint = _prefab_list_fingerprint(prefab_field.items)
        graph_before = _graph_fingerprint(parsed, event_property)
        if current_guid == template.guid:
            self.loaded._assert_source_unchanged()
            validation = ActionValidationResult(
                True,
                {
                    "no_op": True,
                    "pmh_fully_consumed": self.loaded.scene.fully_consumed,
                    "action_payload_fully_consumed": parsed.fully_consumed,
                    "target_template_validated": template.validated,
                    "list_length_unchanged": True,
                    "graph_fingerprint_unchanged": True,
                    "assets_unchanged": self._template_assets_unchanged(template),
                },
            )
            return self._report(
                obj, component, field, action, prefab_index, current, template,
                dry_run=dry_run, no_op=True, backup_path=None, validation=validation,
                payload_before=payload_hash, payload_after=payload_hash,
                reference_before=_sha256(reference.raw_segment),
                reference_after=_sha256(reference.raw_segment),
                root_before=_sha256(parsed.root_segment.raw),
                root_after=_sha256(parsed.root_segment.raw),
                old_length=len(field.raw), new_length=len(field.raw),
                before_fingerprint=before_fingerprint,
                after_fingerprint=before_fingerprint,
                graph_before=graph_before, graph_after=graph_before,
            )

        replacement = _TokenReplacement(
            member=f"m_prefabs[{prefab_index}]",
            span=current.source_span,
            before_raw=current.raw_bytes,
            after_raw=template.raw_bytes,
            before=current.raw_value,
            after=template.raw_value,
        )
        patched_reference = _apply_replacements(reference.raw_segment, [replacement])
        parse_json_spans(patched_reference)
        new_payload = _reframe_reference_json(
            parsed, reference.segment_index, patched_reference
        )
        record_start = field.source_span.start - 4
        if (
            record_start < 0
            or self.original_bytes[record_start : field.source_span.start]
            != struct.pack("<I", len(field.raw))
        ):
            raise ActionFramingError(
                "PMH Action property length field is not adjacent or valid"
            )
        old_record_end = field.source_span.end
        patched_bytes = (
            self.original_bytes[:record_start]
            + struct.pack("<I", len(new_payload))
            + new_payload
            + self.original_bytes[old_record_end:]
        )
        context = _PrefabValidationContext(
            obj=obj,
            component=component,
            field=field,
            parsed=parsed,
            action=action,
            prefab_index=prefab_index,
            current=current,
            template=template,
            prefab_field=prefab_field,
            patched_reference=patched_reference,
            new_payload=new_payload,
            record_start=record_start,
            old_record_end=old_record_end,
        )
        if dry_run:
            reparsed = PMHReader().read_bytes(patched_bytes, source=self.source)
            validation, parsed_after, after_fingerprint = self._validate(
                reparsed, patched_bytes, context
            )
            backup_path = None
        else:
            self.loaded._assert_source_unchanged()
            holder: list[tuple[ActionList, PrefabListFingerprint]] = []

            def validate_temp(reparsed: Scene, data: bytes) -> ActionValidationResult:
                result, parsed_result, fingerprint = self._validate(
                    reparsed, data, context
                )
                holder.append((parsed_result, fingerprint))
                return result

            validation, backup_path = self.loaded._replace_with_validation(
                patched_bytes, validate_temp, backup=backup
            )
            parsed_after, after_fingerprint = holder[0]

        after_action = ActionFieldWriter._resolve_action(parsed_after, action_rid)
        after_reference = after_action.resolved_reference
        assert after_reference is not None and after_reference.raw_segment is not None
        return self._report(
            obj, component, field, action, prefab_index, current, template,
            dry_run=dry_run, no_op=False, backup_path=backup_path, validation=validation,
            payload_before=payload_hash, payload_after=_sha256(new_payload),
            reference_before=_sha256(reference.raw_segment),
            reference_after=_sha256(after_reference.raw_segment),
            root_before=_sha256(parsed.root_segment.raw),
            root_after=_sha256(parsed_after.root_segment.raw),
            old_length=len(field.raw), new_length=len(new_payload),
            before_fingerprint=before_fingerprint,
            after_fingerprint=after_fingerprint,
            graph_before=graph_before,
            graph_after=_graph_fingerprint(parsed_after, event_property),
        )

    def _resolve_target_template(
        self,
        guid: str | None,
        relative_path: str | None,
        *,
        catalog: PrefabReferenceTemplateCatalog,
        template_reference_sha256: str | None,
    ) -> PrefabReferenceTemplate:
        requested_path: str | None = None
        if relative_path is not None:
            if not isinstance(relative_path, str) or not relative_path:
                raise PrefabReferenceAssetError(
                    "target_prefab_relative_path must be a non-empty relative path"
                )
            relative = Path(relative_path)
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or relative.suffix.casefold() != ".pfab"
            ):
                raise PrefabReferenceAssetError(
                    "target_prefab_relative_path must name a relative .pfab file"
                )
            try:
                candidate = (catalog.mod_root / relative).resolve(strict=True)
            except OSError as exc:
                raise PrefabReferenceAssetError(
                    "target prefab path does not exist or is unreadable"
                ) from exc
            _require_contained(candidate, catalog.mod_root, catalog.allowed_root)
            assets = (catalog.mod_root / "Assets").resolve(strict=True)
            try:
                candidate.relative_to(assets)
            except ValueError as exc:
                raise PrefabReferenceAssetError(
                    "target prefab must be inside the current Mod Assets directory"
                ) from exc
            if not candidate.is_file():
                raise PrefabReferenceAssetError("target prefab is not a regular file")
            try:
                metadata_path = Path(str(candidate) + ".pmeta").resolve(strict=True)
            except OSError as exc:
                raise PrefabReferenceAssetError(
                    "target prefab metadata does not exist or is unreadable"
                ) from exc
            _require_contained(
                metadata_path, catalog.mod_root, catalog.allowed_root
            )
            guid = _read_metadata_guid(metadata_path)
            requested_path = candidate.relative_to(catalog.mod_root).as_posix()
        if not isinstance(guid, str) or not guid:
            raise PrefabReferenceAssetError("target_prefab_guid must be a non-empty string")
        group = catalog.get(guid)
        if group is None:
            raise NoValidatedPrefabReferenceTemplateError(
                "Prefab exists or GUID was supplied, but no validated serialized "
                "PrefabReference template has been observed; v0.4d does not construct "
                "references from scratch"
            )
        variants = group.variants
        if template_reference_sha256 is not None:
            if not _valid_sha256(template_reference_sha256):
                raise PrefabReferenceAssetError(
                    "template_reference_sha256 must be 64 hexadecimal characters"
                )
            expected = template_reference_sha256.casefold()
            variants = tuple(
                item for item in variants if item.reference_sha256 == expected
            )
            if not variants:
                raise NoValidatedPrefabReferenceTemplateError(
                    "no validated PrefabReference template matches "
                    "template_reference_sha256"
                )
        if len(variants) != 1:
            raise AmbiguousPrefabReferenceTemplateError(
                f"Prefab GUID {guid!r} has {len(group.variants)} serialized template variants"
            )
        template = variants[0]
        if not template.validated:
            raise PrefabReferenceAssetError(
                "observed template does not resolve by exact metadata GUID to a contained .pfab"
            )
        if requested_path is not None and template.relative_path != requested_path:
            raise PrefabReferenceAssetError(
                "validated template resolves to a different prefab path"
            )
        if not self._template_assets_unchanged(template):
            raise PrefabReferenceAssetError(
                "target prefab or metadata changed after template catalog construction"
            )
        return template

    def _template_catalog(
        self,
        template_scene_path: str | Path | None,
        template_reference_sha256: str | None,
    ) -> PrefabReferenceTemplateCatalog:
        if template_scene_path is None:
            return self.catalog
        if template_reference_sha256 is None:
            raise PrefabReferenceAssetError(
                "external template_scene_path requires exact template_reference_sha256"
            )
        try:
            template_source = Path(template_scene_path).resolve(strict=True)
        except OSError as exc:
            raise PrefabReferenceAssetError(
                "template Scene does not exist or is unreadable"
            ) from exc
        _require_contained(
            template_source, self.catalog.mod_root, self.catalog.allowed_root
        )
        if not template_source.is_file():
            raise PrefabReferenceAssetError("template Scene is not a regular file")
        template_loaded = PMHScene.load(template_source)
        return _build_template_catalog(
            template_loaded.scene,
            self.catalog.mod_root,
            self.catalog.allowed_root,
            source_file=template_source,
        )

    def _template_assets_unchanged(self, template: PrefabReferenceTemplate) -> bool:
        if not template.relative_path or not template.metadata_relative_path:
            return False
        try:
            asset = (self.catalog.mod_root / template.relative_path).resolve(strict=True)
            metadata = (
                self.catalog.mod_root / template.metadata_relative_path
            ).resolve(strict=True)
            _require_contained(asset, self.catalog.mod_root, self.catalog.allowed_root)
            _require_contained(metadata, self.catalog.mod_root, self.catalog.allowed_root)
            return (
                asset.is_file()
                and metadata.is_file()
                and _hash_file(asset) == template.asset_sha256
                and _hash_file(metadata) == template.metadata_sha256
                and _read_metadata_guid(metadata) == template.guid
            )
        except (OSError, PrefabReferenceAssetError):
            return False

    def _validate(
        self, reparsed: Scene, patched: bytes, context: _PrefabValidationContext
    ) -> tuple[ActionValidationResult, ActionList, PrefabListFingerprint]:
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
            "property_length_updated": patched[
                context.record_start : context.record_start + 4
            ]
            == struct.pack("<I", len(context.new_payload)),
            "target_assets_unchanged": self._template_assets_unchanged(context.template),
        }
        objects = [item for item in reparsed.walk() if item.guid == context.obj.guid]
        checks["target_object_guid_unchanged"] = len(objects) == 1
        new_component: Component | None = None
        if len(objects) == 1:
            components = [
                item
                for item in objects[0].components
                if item.guid == context.component.guid and item.type_name == "ModTrigger"
            ]
            checks["target_component_guid_unchanged"] = len(components) == 1
            if len(components) == 1:
                new_component = components[0]
        else:
            checks["target_component_guid_unchanged"] = False
        new_field: Field | None = None
        if new_component is not None:
            matches = [
                item for item in new_component.fields if item.name == context.field.name
            ]
            if len(matches) == 1:
                new_field = matches[0]
        checks["target_property_exists"] = new_field is not None
        checks["target_property_payload_matches"] = (
            new_field is not None and new_field.raw == context.new_payload
        )
        checks["other_component_properties_unchanged"] = (
            new_component is not None
            and [
                (item.name, item.raw)
                for item in new_component.fields
                if item.name != context.field.name
            ]
            == [
                (item.name, item.raw)
                for item in context.component.fields
                if item.name != context.field.name
            ]
        )
        checks["other_event_payloads_unchanged"] = (
            new_component is not None
            and all(
                new_component.get_field(name).raw
                == context.component.get_field(name).raw
                for name in ACTION_EVENT_PROPERTIES
                if name != context.field.name
            )
        )
        try:
            parsed_after = parse_action_payload(
                new_field.raw if new_field is not None else b"",
                source_property=context.field.name,
                source_span=new_field.source_span if new_field else None,
            )
        except Exception as exc:
            raise ActionValidationError(
                f"patched Action payload cannot be reparsed: {exc}"
            ) from exc
        checks["action_payload_fully_consumed"] = parsed_after.fully_consumed
        checks["action_unparsed_ranges_empty"] = not parsed_after.unparsed_ranges
        checks["root_json_bytes_unchanged"] = (
            parsed_after.root_segment.raw == context.parsed.root_segment.raw
        )
        checks["m_actions_bytes_unchanged"] = checks["root_json_bytes_unchanged"]
        graph_before = _graph_fingerprint(context.parsed, context.field.name)
        graph_after = _graph_fingerprint(parsed_after, context.field.name)
        checks["action_order_unchanged"] = graph_before.action_rids == graph_after.action_rids
        checks["rids_unchanged"] = graph_before.action_rids == graph_after.action_rids
        checks["refids_unchanged"] = tuple(x[0] for x in graph_before.references) == tuple(
            x[0] for x in graph_after.references
        )
        checks["action_identity_unchanged"] = graph_before == graph_after
        checks["graph_fingerprint_unchanged"] = graph_before == graph_after
        target_segment_index = context.action.resolved_reference.segment_index
        checks["non_target_reference_hashes_unchanged"] = (
            len(context.parsed.references) == len(parsed_after.references)
            and all(
                before.raw_segment == after.raw_segment
                for index, (before, after) in enumerate(
                    zip(context.parsed.references, parsed_after.references, strict=True)
                )
                if index != target_segment_index
            )
        )
        after_action = ActionFieldWriter._resolve_action(
            parsed_after, context.action.rid
        )
        after_reference = after_action.resolved_reference
        checks["target_reference_exact_patch"] = (
            after_reference is not None
            and after_reference.raw_segment == context.patched_reference
        )
        if after_reference is None or after_reference.raw_segment is None:
            raise ActionValidationError("patched target reference is unresolved")
        after_length_segments = [
            item
            for item in parsed_after.segments
            if item.kind == "reference_json_length"
            and item.index == after_reference.segment_index
        ]
        checks["reference_7bit_length_updated"] = (
            len(after_length_segments) == 1
            and after_reference.source_span is not None
            and after_length_segments[0].raw
            == encode_action_varuint7(len(after_reference.raw_segment))
            and after_length_segments[0].source_span.end
            == after_reference.source_span.start
        )
        after_inspection = inspect_action_entry_references(
            after_action, asset_catalog=self.catalog.asset_catalog
        )
        after_fields = [
            item
            for item in after_inspection.references
            if item.source_field == "m_prefabs"
        ]
        if len(after_fields) != 1:
            raise ActionValidationError("patched m_prefabs is not uniquely inspectable")
        after_field = after_fields[0]
        after_fingerprint = _prefab_list_fingerprint(after_field.items)
        before_items = context.prefab_field.items
        after_items = after_field.items
        checks["list_length_unchanged"] = len(before_items) == len(after_items)
        checks["target_index_guid_matches"] = (
            len(after_items) > context.prefab_index
            and after_items[context.prefab_index].normalized_value.get("guid")
            == context.template.guid
        )
        checks["target_item_matches_template_bytes"] = (
            len(after_items) > context.prefab_index
            and after_items[context.prefab_index].raw_bytes == context.template.raw_bytes
        )
        checks["other_prefab_items_unchanged"] = (
            len(before_items) == len(after_items)
            and all(
                before.raw_bytes == after.raw_bytes
                for index, (before, after) in enumerate(
                    zip(before_items, after_items, strict=True)
                )
                if index != context.prefab_index
            )
        )
        expected_guids = list(
            item.normalized_value.get("guid") for item in before_items
        )
        expected_guids[context.prefab_index] = context.template.guid
        checks["list_order_and_duplicates_preserved"] = (
            list(after_fingerprint.ordered_guids) == expected_guids
        )
        expected_hashes = list(
            _sha256(item.raw_bytes) for item in before_items
        )
        expected_hashes[context.prefab_index] = context.template.reference_sha256
        checks["prefab_list_only_target_index_changes"] = (
            list(after_fingerprint.item_sha256) == expected_hashes
        )
        old_start = context.current.source_span.start
        old_end = context.current.source_span.end
        new_item = after_items[context.prefab_index]
        checks["target_item_prefix_unchanged"] = (
            context.action.resolved_reference.raw_segment[:old_start]
            == after_reference.raw_segment[: new_item.source_span.start]
        )
        checks["target_item_suffix_unchanged"] = (
            context.action.resolved_reference.raw_segment[old_end:]
            == after_reference.raw_segment[new_item.source_span.end :]
        )
        before_scalars = _scalar_field_bytes(context.action)
        after_scalars = _scalar_field_bytes(after_action)
        checks["scalar_action_fields_unchanged"] = before_scalars == after_scalars
        after_resolution = after_items[context.prefab_index].resolution
        checks["target_guid_resolves_expected_pfab"] = (
            after_resolution is not None
            and after_resolution.resolved
            and after_resolution.guid == context.template.guid
            and after_resolution.relative_path == context.template.relative_path
        )
        passed = all(checks.values())
        result = ActionValidationResult(passed, checks)
        if not passed:
            failed = ", ".join(name for name, ok in checks.items() if not ok)
            raise ActionValidationError(
                f"patched prefab reference validation failed: {failed}"
            )
        return result, parsed_after, after_fingerprint

    def _report(
        self,
        obj: GameObject,
        component: Component,
        field: Field,
        action: ActionEntry,
        prefab_index: int,
        current: Any,
        template: PrefabReferenceTemplate,
        *,
        dry_run: bool,
        no_op: bool,
        backup_path: Path | None,
        validation: ActionValidationResult,
        payload_before: str,
        payload_after: str,
        reference_before: str,
        reference_after: str,
        root_before: str,
        root_after: str,
        old_length: int,
        new_length: int,
        before_fingerprint: PrefabListFingerprint,
        after_fingerprint: PrefabListFingerprint,
        graph_before: GraphFingerprint,
        graph_after: GraphFingerprint,
    ) -> PrefabReferenceChangeReport:
        current_resolution = current.resolution
        return PrefabReferenceChangeReport(
            object_name=obj.name,
            object_guid=obj.guid,
            hierarchy_path=obj.hierarchy_path,
            component_guid=component.guid,
            event_property=field.name,
            action_index=action.index,
            action_rid=action.rid,
            namespace=action.type_info.namespace or "",
            class_name=action.type_info.class_name or "",
            prefab_index=prefab_index,
            before={
                "guid": current.normalized_value.get("guid"),
                "relative_path": (
                    current_resolution.relative_path if current_resolution else None
                ),
                "reference_sha256": _sha256(current.raw_bytes),
            },
            after={
                "guid": template.guid,
                "relative_path": template.relative_path,
                "reference_sha256": template.reference_sha256,
            },
            template_source=template.sources[0],
            source_file=self.source,
            dry_run=dry_run,
            no_op=no_op,
            backup_path=backup_path,
            validation=validation,
            payload_sha256_before=payload_before,
            payload_sha256_after=payload_after,
            managed_reference_sha256_before=reference_before,
            managed_reference_sha256_after=reference_after,
            root_json_sha256_before=root_before,
            root_json_sha256_after=root_after,
            old_property_length=old_length,
            new_property_length=new_length,
            bytes_added_or_removed=new_length - old_length,
            prefab_list_fingerprint_before=before_fingerprint,
            prefab_list_fingerprint_after=after_fingerprint,
            graph_fingerprint_before=graph_before,
            graph_fingerprint_after=graph_after,
        )


@dataclass(frozen=True, slots=True)
class _PrefabValidationContext:
    obj: GameObject
    component: Component
    field: Field
    parsed: ActionList
    action: ActionEntry
    prefab_index: int
    current: Any
    template: PrefabReferenceTemplate
    prefab_field: Any
    patched_reference: bytes
    new_payload: bytes
    record_start: int
    old_record_end: int


def _prefab_list_fingerprint(items: Any) -> PrefabListFingerprint:
    return PrefabListFingerprint(
        ordered_guids=tuple(item.normalized_value.get("guid") for item in items),
        item_sha256=tuple(_sha256(item.raw_bytes) for item in items),
    )


def _scalar_field_bytes(action: ActionEntry) -> dict[str, bytes]:
    reference = action.resolved_reference
    if reference is None or reference.raw_segment is None:
        return {}
    document = reference.json_document or parse_json_spans(reference.raw_segment)
    result: dict[str, bytes] = {}
    for name in ("m_spawnAtPosition", "m_parentToTarget", "m_position", "m_rotation"):
        try:
            node = document.root.member(name)
        except JsonSpanError:
            continue
        result[name] = reference.raw_segment[node.span.start : node.span.end]
    return result


def _read_metadata_guid(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_ASSET_METADATA_BYTES:
            raise PrefabReferenceAssetError("target prefab metadata exceeds safety limit")
        value = json.loads(path.read_text(encoding="utf-8"))
    except PrefabReferenceAssetError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PrefabReferenceAssetError("target prefab metadata is unreadable or malformed") from exc
    guid_object = value.get("guid") if isinstance(value, Mapping) else None
    guid = guid_object.get("serializedGuid") if isinstance(guid_object, Mapping) else None
    if not isinstance(guid, str) or not guid:
        raise PrefabReferenceAssetError("target prefab metadata has no string GUID")
    return guid


def _require_contained(path: Path, mod_root: Path, allowed_root: Path) -> None:
    try:
        path.relative_to(mod_root)
        path.relative_to(allowed_root)
    except ValueError as exc:
        raise PrefabReferenceAssetError(
            "target prefab path escapes the current Mod or allowed root"
        ) from exc


def _normalized_sha256(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return _sha256(raw)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _span_dict(span: SourceSpan) -> dict[str, int]:
    return {"start": span.start, "end": span.end, "length": span.length}
