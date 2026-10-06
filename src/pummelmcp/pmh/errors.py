"""PMH-specific exceptions."""


class PMHError(Exception):
    """Base class for PMH errors."""


class PMHFormatError(PMHError):
    """The byte stream does not satisfy the currently understood layout."""


class PMHDecodeError(PMHError):
    """A confirmed field cannot be decoded as its confirmed type."""


class PMHLookupError(PMHError, LookupError):
    """A structural lookup is missing or ambiguous."""


class PMHWriterError(PMHError):
    """Base class for safe writer failures."""


class ObjectNotFoundError(PMHWriterError, LookupError):
    """No GameObject matches the supplied identifier."""


class AmbiguousObjectError(PMHWriterError, LookupError):
    """More than one GameObject matches the supplied identifier."""


class TransformNotFoundError(PMHWriterError, LookupError):
    """The selected GameObject has no unique ModTransform component."""


class TransformWriteError(PMHWriterError, ValueError):
    """A requested transform update cannot be safely represented."""


class ComponentNotFoundError(PMHWriterError, LookupError):
    """No component matches the supplied GUID or type."""


class AmbiguousComponentError(PMHWriterError, LookupError):
    """A component type identifies more than one component on an object."""


class ComponentPropertyNotFoundError(PMHWriterError, LookupError):
    """A requested property is absent from the schema or component."""


class ComponentPropertyReadOnlyError(PMHWriterError, ValueError):
    """A requested property is registered but not writable."""


class ComponentWriteError(PMHWriterError, ValueError):
    """A component property update cannot be safely represented."""


class UnknownComponentSchemaError(ComponentWriteError):
    """A Component type has no schema-driven write allowlist."""


class WriterValidationError(PMHWriterError):
    """Patched bytes failed the writer's pre-replacement validation."""


class ConcurrentModificationError(PMHWriterError):
    """The source file changed after it was loaded."""


class GameObjectDuplicationError(PMHWriterError):
    """A GameObject cannot be duplicated under the narrow Stage 10B contract."""


class UnsafeDuplicationError(GameObjectDuplicationError):
    """The selected object is outside the approved duplication surface."""


class GuidAllocationError(GameObjectDuplicationError):
    """A unique canonical UUID v4 could not be allocated safely."""


class ActionWriteError(PMHWriterError, ValueError):
    """An existing Action field cannot be safely changed."""


class ActionNotFoundError(ActionWriteError, LookupError):
    """No unique existing Action matches the supplied identity."""


class ActionRidNotFoundError(ActionNotFoundError):
    """No Action entry has the requested rid."""


class AmbiguousActionError(ActionNotFoundError):
    """More than one Action entry has the requested rid."""


class DuplicateRidError(ActionWriteError):
    """The Action graph contains duplicate managed-reference ids."""


class ActionClassMismatchError(ActionWriteError):
    """The resolved Action class differs from the caller's expectation."""


class UnknownActionSchemaError(ActionWriteError):
    """The Action class has no field-write schema."""


class UnknownActionFieldError(ActionWriteError, LookupError):
    """The requested Action field is absent or unregistered."""


class ReadOnlyActionFieldError(ActionWriteError):
    """The requested Action field is registered read-only."""


class InvalidActionFieldValueError(ActionWriteError):
    """The requested Action field value violates its strict schema."""


class ActionPayloadChangedError(ActionWriteError):
    """The Action payload changed since it was inspected."""


class ActionFramingError(ActionWriteError):
    """The Action payload cannot be safely framed for mutation."""


class ActionValidationError(ActionWriteError):
    """A patched Action failed pre-replacement validation."""


class ActionGraphWriteError(ActionWriteError):
    """An Action graph add/delete/move operation is outside the safe v0.5 contract."""


class ActionTemplateError(ActionGraphWriteError, LookupError):
    """No unique validated Action template matches the request."""


class ActionDependencyError(ActionGraphWriteError):
    """An Action cannot be deleted because dependency safety is not established."""


class PrefabReferenceWriteError(ActionWriteError):
    """An existing SpawnPrefabAction prefab item cannot be safely replaced."""


class PrefabReferenceIndexError(PrefabReferenceWriteError, IndexError):
    """The selected existing m_prefabs item index is invalid."""


class NoValidatedPrefabReferenceTemplateError(PrefabReferenceWriteError, LookupError):
    """No observed complete reference template exists for the target prefab."""


class AmbiguousPrefabReferenceTemplateError(PrefabReferenceWriteError):
    """One prefab GUID has multiple observed serialized template variants."""


class PrefabReferenceCurrentGuidMismatchError(PrefabReferenceWriteError):
    """The selected list item no longer has the expected prefab GUID."""


class PrefabReferenceAssetError(PrefabReferenceWriteError):
    """A target prefab or its metadata is missing, unresolved, or outside containment."""


class EditorAssetError(PMHError, ValueError):
    """Base class for the built-in Asset Browser catalog surface."""


class EditorAssetCatalogError(EditorAssetError):
    """The configured Asset Browser library is missing or unusable."""


class DamagedEditorAssetCatalogError(EditorAssetCatalogError):
    """The registry bundle cannot be decoded into the expected structure."""


class EditorAssetLookupError(EditorAssetError, LookupError):
    """Base class for a rejected Asset Browser asset request."""


class EditorAssetNotFoundError(EditorAssetLookupError):
    """No registered built-in asset matches the supplied identifier."""


class AmbiguousEditorAssetError(EditorAssetLookupError):
    """More than one registered built-in asset matches the supplied identifier."""


class EditorAssetNotInternalError(EditorAssetLookupError):
    """The requested asset is registered outside the built-in (Internal) library."""


class BuiltinPropSpawnError(PMHWriterError):
    """A registered built-in prop cannot be spawned under the spawn contract."""


class UnsafeBuiltinPropSpawnError(BuiltinPropSpawnError):
    """The destination scene or parent is outside the approved spawn surface."""


class BuiltinPropValidationError(BuiltinPropSpawnError):
    """Patched bytes failed the built-in prop spawn validation."""
