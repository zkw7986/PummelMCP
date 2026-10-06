# Prefab Reference Writer v0.4d safety

Prefab Reference Writer v0.4d has one narrowly defined operation: replace one
existing `ModSystem.Logic.SpawnPrefabAction.m_prefabs[index]` item with the
complete serialized bytes of another prefab reference already observed in the
same trusted Scene corpus. It does not edit prefab files or expose a generic
reference/JSON writer.

## Validated template strategy

A prefab reference contains both `m_assetGUID` and a complete `m_asset`
metadata object, including fields whose serializer semantics are not fully
known. Consequently, changing only the GUID could combine one prefab's GUID
with another prefab's metadata. v0.4d never does this and never constructs an
unseen reference from `.pmeta` alone.

`PrefabReferenceTemplateCatalog` scans existing `SpawnPrefabAction` instances
and groups complete `m_prefabs` item bytes by `m_assetGUID`. Each variant keeps:

- exact raw JSON subtree and SHA-256;
- normalized structure and unknown fields;
- all source object/component/event/Action/item provenance;
- exact source span;
- resolved prefab and metadata paths relative to the current Mod root;
- prefab and `.pmeta` hashes;
- evidence status.

Exact raw bytes define a variant. Multiple observations with identical bytes
become one variant with multiple provenance records. If one GUID has multiple
serialized variants, mutation is rejected as ambiguous instead of selecting
one silently.

The approved Joker 21 corpus contains 183 reference occurrences grouped under
23 GUIDs. Each GUID has exactly one serialized representation: raw JSON,
normalized shape, unknown fields, and `m_asset` metadata agree across its
occurrences. This result is **CONFIRMED for the observed corpus**, not a general
serializer guarantee.

## Target selection and asset validation

The target is selected by exactly one of:

- exact `target_prefab_guid`; or
- exact `target_prefab_relative_path`, such as
  `Assets/Prefabs/Result_Joker.pfab`.

A relative path is resolved inside the current Mod only. It must name an
existing `.pfab`; its adjacent `.pfab.pmeta` is read, and the exact
`guid.serializedGuid` is used to locate the observed template. Absolute paths,
parent traversal, symlink escapes, paths outside `Assets`, and cross-Mod paths
are rejected. Filename substrings and fuzzy names are never used.

Before a template is admitted, its `m_assetGUID` and embedded `m_asset.guid`
must agree, the GUID must exactly match `.pmeta`, the paired `.pfab` must exist,
and both files must remain within the Mod and active MCP allowed root. Asset and
metadata hashes are checked again immediately before and during validation.

An existing `.pfab` with no observed complete template is rejected. Whether an
unobserved prefab reference can be safely constructed using `.pmeta` alone is
**UNKNOWN**, so v0.4d does not attempt it.

## Exact replacement and invariants

The writer uses the source span of `m_prefabs[index]` and replaces only that
complete JSON subtree. It does not parse, modify, and reserialize the Action
object or the full list. Variable item length is supported by updating only:

1. the selected item subtree;
2. the selected managed-reference JSON 7-bit length prefix;
3. the enclosing PMH property `u32` payload length.

Validation proves:

- list length and item index sequence are unchanged;
- every non-target item is byte-for-byte identical;
- order and duplicates are preserved;
- bytes before and after the target item are identical;
- `m_spawnAtPosition`, `m_parentToTarget`, `m_position`, and `m_rotation`
  bytes are unchanged;
- root ActionList JSON, `m_actions`, Action order, `rid`, `RefIds`, namespace,
  class, assembly, type tags, and all non-target managed references are
  unchanged;
- PMH and Action payloads reparse and consume fully;
- the new target GUID resolves to the expected contained `.pfab`;
- scene content before and after the variable-length property remains intact.

A same-GUID request is a validated no-op: no temporary replacement, backup, or
atomic write occurs. Formal changes retain the existing backup, same-directory
temporary file, validation, concurrent-source check, and atomic replacement
pipeline. `expected_payload_sha256` protects the inspected Action payload and
`expected_current_prefab_guid` protects the selected list index.

## Evidence and exclusions

**CONFIRMED:** the 23 observed GUIDs exactly match their `.pmeta`; observed
templates have one representation per GUID; real-template forward/reverse
variable-length replacements and 7-bit boundary changes pass structural,
graph, list, scalar-field, prefix/suffix, and asset-integrity tests.

**UNKNOWN:** serializer requirements for adding list entries or constructing
references for prefabs never observed in a trusted Action corpus.

Therefore v0.4d implements no list append/insert/remove/reorder, unseen
reference construction, GUID-only patch, Audio/Effect/Target Writer, Action
graph mutation, asset import/copy, Prefab Writer, or GameObject/Component
creation or deletion.
