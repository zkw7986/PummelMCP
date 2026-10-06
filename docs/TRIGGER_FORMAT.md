# ModTrigger format, Writer v0.3, Inspector v0.4a, and Action Writer v0.4b

This note records evidence from the approved Joker 21 `MainScene.scene` and
the user-provided live Mod copy. Both contain 10 `ModTrigger` components with
the same fixed-field layout. The Writer still discovers every offset from the
current Reader parse; none of the observed offsets are part of the schema.

## Fixed properties

| Property | Payload | Interpretation | Writable | Status |
|---|---:|---|---:|---|
| `TriggerShape` | 4 | little-endian int32 enum | yes | CONFIRMED encoding; enum names UNKNOWN |
| `Size` | 12 | 3 little-endian float32 | yes | CONFIRMED |
| `Center` | 12 | 3 little-endian float32 | yes | CONFIRMED |
| `Radius` | 4 | little-endian float32 | yes | CONFIRMED |
| `Height` | 4 | little-endian float32 | yes | CONFIRMED |
| `TriggerOnHit` | 1 | strict boolean 0/1 | yes | CONFIRMED |
| `TriggerOnEnter` | 1 | strict boolean 0/1 | yes | CONFIRMED |
| `TriggerOnExit` | 1 | strict boolean 0/1 | yes | CONFIRMED |
| `TriggerOnStay` | 1 | strict boolean 0/1 | yes | CONFIRMED |
| `StayTriggerInterval` | 4 | little-endian float32 | yes | CONFIRMED |
| `DisableAfterTriggered` | 1 | strict boolean 0/1 | yes | CONFIRMED |
| `OneUsePerPlayer` | 1 | strict boolean 0/1 | yes | CONFIRMED |
| `guid` | variable | length-prefixed UTF-8 identity | no | CONFIRMED framing |

No sign/range constraints are imposed on the three floats because no reliable
business-rule evidence was found. The codec rejects NaN and infinities.
`TriggerShape` accepts a raw signed integer and returns
`{ "raw_value": n, "name": null }`; no shape names are claimed.

## Action boundary

| Property | Joker 21 observed lengths | Schema | Writable | Status |
|---|---|---|---:|---|
| `OnHitActions` | 115, 1,961, 45,175, 47,145 | `ManagedReferencePayload` | no | UNKNOWN complex semantics |
| `OnEnterActions` | 115, 687 | `ManagedReferencePayload` | no | UNKNOWN complex semantics |
| `OnExitActions` | 115 | `ManagedReferencePayload` | no | UNKNOWN complex semantics |
| `OnStayActions` | 115 | `ManagedReferencePayload` | no | UNKNOWN complex semantics |

Writer v0.3 does not parse, interpret, or write these payloads. The separate
Inspector v0.4a now frames them losslessly for read-only use, resolves `rid`
through `RefIds`, preserves Action order, and reports structured class/field
summaries. `get_component_property` remains a bounded length/hash summary;
`inspect_action_list` provides the paginated structure. Action Field Writer
v0.4b may replace only four registered JSON value tokens in an existing
`SpawnPrefabAction`; no Action class, `rid`, `RefIds`, header, ordering,
prefab/reference value, or other payload content is writable. See
[`ACTION_FORMAT.md`](ACTION_FORMAT.md) and
[`ACTION_WRITER_SAFETY.md`](ACTION_WRITER_SAFETY.md).

## Safety proof

Every mutation follows the existing Component Writer path:

1. resolve object and unique Component from the current parse;
2. require an exact registry property and fixed-length codec;
3. patch only the scalar payload or supplied Vector3 members;
4. write a same-directory temporary file and fully reparse it;
5. validate magic, version, counts, target GUIDs, property value and span;
6. verify all other Component properties and all four Action payloads are
   byte-for-byte unchanged;
7. create the default backup and atomically replace the source.

Dry runs perform in-memory patching and the same validation without replacing
the source or creating a backup. Trigger Writer v0.3 does not add an MCP tool;
it reuses `get_component_property` and `set_component_property`.

## Real fixture-copy diff evidence

The following spans came from one test run against a temporary copy. They are
diagnostic evidence only and are never embedded in the implementation.

| Mutation | Dynamic property span | Planned member span | Actual differing offsets |
|---|---|---|---|
| `TriggerShape: 1 -> 2` | `[37090, 37094)` | `[37090, 37094)` | `37090` |
| `Radius: 0.5 -> 2.5` | `[37149, 37153)` | `[37149, 37153)` | `37151, 37152` |
| `Size.x: 1 -> 10` | `[37103, 37115)` | `[37103, 37107)` | `37105, 37106` |
| `OneUsePerPlayer: false -> true` | `[84881, 84882)` | `[84881, 84882)` | `84881` |

All four Action payload SHA-256 values were identical before and after every
mutation. The approved fixture SHA-256 remained
`a0af6631df7e99d4f98d964122eca56ab66cb8beaad53d7217bc54efff89cde8`.
