# Action target system evidence

Stage 8 scanned 13 parseable Workshop Mod `.scene` files through the PMH and
Action readers. For `SpawnEffectAction`, `PlaySoundAction`, and `KillAction`,
the observed serialized target representation is:

```json
{"m_targetFlags": 1, "m_targets": []}
```

The corpus contains raw `m_targetFlags` values `1`, `2`, `3`, `4`, `5`, and `6`.
The Stage 8 registry classes use `1` and/or `2`. Their integer encoding is
**CONFIRMED**. `m_targets` is a JSON list and its exact source span is
**CONFIRMED**; the selector-list instances observed on these three classes are
empty.

The meaning of every bit/value and the schema of a non-empty target selector
are **UNKNOWN**. Names such as self, triggering player, all players, or random
player are deliberately not assigned. MCP reports `raw_value` plus
`semantic: null`. No target field is writable and there is no raw-flags tool.

Target offsets observed on `SpawnPrefabAction` are ordinary preserved Vector3
JSON fields. Their relationship to selection semantics is not proven, so they
also remain read-only.

## SpawnPrefab advanced Transform targets

Stage 10A.7 records a distinct, non-empty `SpawnPrefabAction.m_targets` form
created by `Spawn Prefab -> Targets -> Advanced -> Transforms to Spawn Prefabs
At`. Its items are concrete Scene `ModTransform` references containing an
`m_assetGUID.serializedGuid` plus `m_FileID = -71472` and `m_PathID = 0`.

The Action reference parser classifies this field as `TransformReferenceList`
and resolves ownership using the structured Component index. It is not treated
as a target-selector list, asset GUID, prefab GUID, Action rid, RefId, or target
flag. The field remains read-only.

In Oracle 05, duplicating the source preserves the duplicate Action's self
Transform reference to the original source Transform. In Oracle 06, the
duplicate preserves the reference to the same external Transform. These results
prove Component-reference behavior only; independently serialized GameObject
target semantics remain `NOT_OBSERVED`.

For future editor before/after evidence, the read-only command
`pummelmcp research diff-action BEFORE AFTER OBJECT COMPONENT EVENT --rid RID`
reports exact logical field differences without changing either scene.
