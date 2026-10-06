# Action Graph Writer v0.5 / Phase 3 safety

v0.5 adds exactly three top-level ActionList operations: `add_action`,
`delete_action`, and `move_action`. It does not accept Action JSON or a caller
chosen rid.

## RID evidence and constrained allocator

A structured read-only scan parsed 13 real Workshop Mod scenes and 201 Action
instances. Every valid non-empty Action property uses the local sequence
`1000, 1001, ...`; the longest observed list ends at 1020. Separate events and
components independently reuse 1000. This confirms the observed serializer
pattern is property-local, starts at 1000, and is contiguous/monotonic.

Gaps, reserved values outside that sequence, and arbitrary pre-existing rid
layouts were not observed. The allocator therefore operates only when both
the `m_actions` rid membership and ordered `RefIds` exactly equal
`range(1000, 1000+n)`. Action execution order may differ after a move. It returns
`1000+n`; any gap, reorder, non-1000 start, mismatch, duplicate, dangling, or
unreferenced rid refuses creation. It is not a general `max(rid)+1` rule.

## Phase 3 creation schemas

Phase 3 keeps the v0.5 graph mutation mechanism class-independent and moves
class admission into `action_schemas.py`. Eleven classes have explicit creation
schemas:

| Class | `m_type` | Creation boundary |
|---|---:|---|
| `KillAction` | 64 | exact base/target template |
| `ChangeScoreAction` | 128 | operation enum + signed int template |
| `SpawnEffectAction` | 160 | assembly-confirmed effect enum template |
| `PositionAction` | 384 | space/operation template + writable destination Vector3 |
| `RotationAction` | 416 | space/operation enums + exact Vector3 |
| `SetPlacementAction` | 480 | exact base/target template |
| `PlaySoundAction` | 544 | exact audio-reference shape + bounded volume |
| `SpawnPrefabAction` | 576 | non-empty prefab references + existing v0.5 rules |
| `ShowMessageAction` | 1024 | target enum + bounded string/duration template |
| `SetPlayerVisualAction` | 1248 | version-1 source template with validated visual Prefab reference |

The eight classes other than `SpawnPrefabAction` are the complete set observed
in the Phase 1 minigame-only corpus. Board-only `WaitAction` and
`SetActiveAction` are deliberately not registered.

Every creation template must use namespace `ModSystem.Logic`, assembly
`Assembly-CSharp`, the registered type tag, the supported class version (the
observed `SetPlayerVisualAction` uses version 1; the other registered classes
use version 0), and the exact field
set for its class. Strict class validators reject boolean-as-number values,
unknown enum numerics, partial/non-finite vectors, extra or missing fields,
unbounded messages/durations, malformed asset-reference envelopes, and
non-empty `m_targets`. Target flags are limited to the minigame-observed
`Source=1` and `Receiver=2` modes.

This registry enables exact template cloning. Only explicitly allowlisted
fields are writable through `set_action_field`; `PositionAction.m_position` is
the writable destination vector, while its space and operation remain from the
selected source template. `SetPlayerVisualAction` keeps its Prefab reference
from that template.

## Validated templates

`ActionTemplateCatalog` gathers source-backed templates from already parsed
top-level Actions. A template records namespace, class, assembly, type tag,
complete managed-reference JSON, complete root RefId item, source Action/event,
source rid, and SHA-256. The catalog admits only fully consumed, dependency-safe
version-2 graphs with real `RefIds` and framed managed references. Legacy v1
payloads are readable but are skipped as creation sources. Unknown or
unregistered Action creation is rejected.

If the target list already contains that class, selection is restricted to its
local variants. A single scene-wide variant can seed another event; ambiguous
variants require the exact `template_sha256` exposed by
`inspect_action_list`. An optional template Scene must be allowlisted and
hash-selected. The caller never supplies raw JSON or a rid.

The official minigame acceptance scan found 55 safe v2 instances across all
eight Phase 1 classes, and every one passed its class schema. The remaining 19
observed Actions use legacy v1 framing and remain read-only template evidence.

## Targeted mutations

Add clones an exact Action entry, RefId item, and managed-reference segment,
patching only the two cloned top-level rid tokens. It inserts the Action at the
requested execution index and appends the new sequential RefId plus its framed
reference segment, updates the root 7-bit length, reference `u16` count, and PMH property
`u32` length, then reparses everything.

Delete removes the selected Action entry, matching RefId, and matching framed
segment. It refuses deletion if a known incoming rid link or an unresolved
reference-like dependency is present. Empty lists serialize back to the
confirmed `[]` root form.

Move rewrites only the `m_actions` array order. RefIds order, rid values,
managed-reference segment order, and every managed-reference byte remain
unchanged. Resolution remains by rid, not array position.

Only the two relevant root arrays are source-span patched. Unrelated root keys
are not serialized. Tail segments are copied byte-for-byte except for the one
added/removed segment.

## Graph validation and transaction

`ActionGraph` models ordered action rids, managed references, type identity,
segment hashes, and known/unknown incoming dependencies. `GraphFingerprintV2`
describes the permitted delta for add, delete, or move. Validation requires
unique fully resolved rids, matching counts, correct tags, full payload and PMH
consumption, unchanged non-target component fields, exact source prefix/suffix,
and operation-specific graph changes.

For add, post-write validation additionally proves that the new reference's
namespace, class, assembly, type tag, complete managed-reference bytes, and
class schema still match the selected template. Existing reference bytes must
remain unchanged.

All three calls support `expected_payload_sha256` and `dry_run`. A real write
uses the existing source-change check, same-directory temporary file, full PMH
and Action reparse, semantic delta validation, timestamped backup, and atomic
replacement. Tests cover `0→1`, `1→0`, `1→2`, `2→1`, insertion, deletion,
movement, stale hashes, and root-length prefix transitions across 127/128 and
16383/16384. The tested add → move → delete round trip is byte-identical to
the original scene copy.

## Explicit non-goals

No arbitrary Action JSON, unknown/unregistered Action creation, new field-value
construction, non-empty advanced-target cloning, raw rid/RefId write,
nested graph mutation, prefab file writer, prefab-list resizing,
GameObject/Component creation, hierarchy mutation, asset import, GUI control,
or automatic playtest is implemented.
