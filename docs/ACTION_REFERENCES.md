# Action Reference Inspector v0.6b

This document records the read-only reference evidence from all 40 Action
properties in the approved Joker 21 fixture and a read-only scan of the live
Joker 21 Mod. The inspector never writes a scene, Action payload, `.pfab`,
`.pmeta`, or other asset. Core retains the complete raw JSON subtree and raw
bytes; MCP returns bounded normalized summaries, exact spans, and hashes but
never raw bytes.

## Reference registry

| Action class | Field | Kind | Observed shape | Resolution | Evidence | Writable |
|---|---|---|---|---|---|---:|
| `SpawnPrefabAction` | `m_prefabs` | `PrefabReferenceList` | `list<object>` | exact `.pmeta` GUID | **CONFIRMED** | existing-item template replacement only |
| `SpawnPrefabAction` | `m_targets` | `TransformReferenceList` | list of Scene Transform references | exact Component GUID and owner | **CONFIRMED** | no |
| `SpawnPrefabAction` | `m_targetFlags` | `TargetFlags` | number (`2`) | semantic unresolved | **CONFIRMED raw / UNKNOWN meaning** | no |
| `SpawnEffectAction` | `m_effectType` | `EffectIndexReference` | number (`3`) | semantic unresolved | **CONFIRMED raw / UNKNOWN meaning** | no |
| `SpawnEffectAction` | `m_targets` | `TargetSelectorList` | empty list | semantic unresolved | **CONFIRMED shape / UNKNOWN meaning** | no |
| `SpawnEffectAction` | `m_targetFlags` | `TargetFlags` | number (`2`) | semantic unresolved | **CONFIRMED raw / UNKNOWN meaning** | no |
| `PlaySoundAction` | `m_clip` | `AudioReference` | object | no exact local GUID match | **INFERRED kind** | no |
| `PlaySoundAction` | `m_targets` | `TargetSelectorList` | empty list | semantic unresolved | **CONFIRMED shape / UNKNOWN meaning** | no |
| `PlaySoundAction` | `m_targetFlags` | `TargetFlags` | number (`2`) | semantic unresolved | **CONFIRMED raw / UNKNOWN meaning** | no |
| `KillAction` | `m_targets` | `TargetSelectorList` | empty list | semantic unresolved | **CONFIRMED shape / UNKNOWN meaning** | no |
| `KillAction` | `m_targetFlags` | `TargetFlags` | number (`1`) | semantic unresolved | **CONFIRMED raw / UNKNOWN meaning** | no |

Structurally reference-like fields outside this registry remain valid and are
reported as `UnknownReference`. They are not rejected and are never writable.

## `SpawnPrefabAction.m_prefabs`

Every observed list item has this logical shape:

```json
{
  "m_assetGUID": {"serializedGuid": "..."},
  "m_asset": {
    "enabled": true,
    "name": "...",
    "guid": {"serializedGuid": "..."},
    "tags": ["prefabs", "tiny"],
    "assetTypeString": "",
    "atlasIndex": 1,
    "atlasPtrIndex": 0,
    "assetLocation": {"serializedGuid": "..."},
    "assetFolder": "/Prefabs/",
    "isEnabled": true
  }
}
```

The two GUID locations agree in every observed item. No null item was observed,
so null support is **UNKNOWN**. Seven Actions contain 23 items and one contains
22; order is preserved and never sorted. Across 183 occurrences there are 23
unique prefab GUIDs. Repeated occurrences of the same GUID have one identical
logical encoding. The 22-item Action omits one entry; the inspector does not
fill it in or infer why.

All 23 unique GUIDs resolve by exact equality with `guid.serializedGuid` in a
contained `Assets/Prefabs/*.pfab.pmeta` file. The resolved asset is the sibling
path with the `.pmeta` suffix removed, for example:

```text
40cc8920-5e37-4500-9d8d-67caf6b86cc4
→ Assets/Prefabs/Result_Card_01.pfab.pmeta
→ Assets/Prefabs/Result_Card_01.pfab
```

This resolution is **CONFIRMED**. Filename similarity, embedded asset name,
folder strings, and substring matching are never sufficient. Duplicate or
missing metadata GUID matches remain unresolved.

## Effect, audio, and target findings

Across 27 read-only corpus instances, `SpawnEffectAction.m_effectType` takes
integer values `0`, `1`, `2`, `3`, and `4`. Its numeric representation is
**CONFIRMED**, but whether it is an enum, registry id, asset index, or another
discriminator—and every value meaning—are **UNKNOWN**.

Sixteen read-only corpus instances share this `PlaySoundAction` shape:

```json
{
  "m_clip": {
    "m_assetGUID": {
      "serializedGuid": "10a663ec-e461-4711-8db8-4aadbc86aeb5"
    },
    "m_asset": {"m_FileID": 107498, "m_PathID": 0}
  },
  "m_volume": 1.0
}
```

The raw object and numeric ids are **CONFIRMED**. `AudioReference` is an
**INFERRED** semantic classification. Its GUID has no exact `.pmeta` match in
the Mod asset corpus, so it is valid but unresolved. The inspector does not
guess from audio filenames. Cross-Mod repetition supports a common/built-in
possibility but does not prove it. The GUID target and FileID/PathID meanings
are **UNKNOWN**. `m_volume` is present but remains read-only.

For `SpawnEffectAction`, `PlaySoundAction`, and `KillAction`, every observed
`m_targets` is an empty target-selector list. The wider corpus includes raw
`m_targetFlags` values `1`, `2`, `3`, `4`, `5`, and `6`. Their raw shapes and values are **CONFIRMED**, but meanings
such as self, all players, triggering player, explicit object, or any bit names
are **UNKNOWN**. The API therefore returns `semantic: null` and never invents
labels.

Stage 10A.7 supplies the first non-empty `SpawnPrefabAction.m_targets` evidence.
The Editor path is `Spawn Prefab -> Targets -> Advanced -> Transforms to Spawn
Prefabs At`. Each item has this shape:

```json
{
  "m_assetGUID": {"serializedGuid": "<ModTransform GUID>"},
  "m_asset": {"m_FileID": -71472, "m_PathID": 0}
}
```

This field is therefore a `TransformReferenceList`, not the semantic selector
list used by the other Action classes. The parser records the GUID, file/path
ids, item and leaf spans, resolves the GUID through the structured Component
index, and reports the owning GameObject. It does not infer a GameObject
reference from the shared GameObject/ModTransform GUID text.

## Lossless spans and fingerprints

Each registered field has a half-open span relative to its separately framed
managed-reference JSON. List items and every nested leaf—including GUID
strings, file/path ids, effect indices, and flags—have their own exact spans.
The service also translates these to Action-payload and scene-file offsets.

Core preserves the raw subtree, exact raw bytes, unknown members, list order,
and duplicates. A reference fingerprint includes namespace, class, field,
source span, SHA-256 of the exact field bytes, and SHA-256 of canonicalized
logical JSON. MCP omits raw bytes and paginates items with `offset` and `limit`.

## Containment and non-goals

Asset discovery starts only from the nearest Mod root with a contained
`Assets` directory. Scene, Mod root, metadata, and resolved asset paths must
all remain under the active MCP allowed root after path resolution. MCP emits
only paths relative to the Mod root. Metadata count/size and asset hashing have
explicit limits; malformed, missing, ambiguous, or escaped candidates do not
become resolved assets.

v0.4d adds only complete existing-item replacement through a validated observed
PrefabReference template. It does not add/remove/reorder list entries or patch
a GUID independently of metadata. Audio/effect/target mutation, Action graph
mutation, and Prefab Writer remain absent. `set_action_field` remains limited
to the same four ordinary v0.4b fields. See
[`PREFAB_REFERENCE_WRITER.md`](PREFAB_REFERENCE_WRITER.md). Stage 8 details
are split into [`ACTION_TARGETS.md`](ACTION_TARGETS.md) and
[`AUDIO_EFFECT_REFERENCES.md`](AUDIO_EFFECT_REFERENCES.md). v0.5 graph
operations clone or remove complete managed-reference templates; they do not
make any of these individual unknown reference fields writable.
