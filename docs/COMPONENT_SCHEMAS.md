# Component Schema Registry v0.3

The registry is the sole authority for ordinary Component decoding and
writing. A writable entry identifies its Component type, exact property name,
codec, encoded byte length, little-endian rule, and write eligibility. Payload
length alone never grants write access.

`CONFIRMED` here means the property/type pairing is part of the current confirmed
contract and its encoded length agrees with every matching Joker 21 sample.
Enum names have not been confirmed: enum values are accepted as signed int32
and returned as `{ "raw_value": n, "name": null }`.

## Codecs

| Codec | Payload | API value |
|---|---:|---|
| `Bool1` | 1 byte | boolean; only encoded 0/1 is valid |
| `Int32LE` | 4 bytes | signed integer, explicit little-endian |
| `Float32LE` | 4 bytes | finite number, explicit little-endian float32 |
| `Vector2Float32` | 8 bytes | `{x, y}` float32 |
| `Vector3Float32` | 12 bytes | `{x, y, z}` float32 |
| `Color4Float32` | 16 bytes | `{r, g, b, a}` float32 |
| `Utf8String` | variable | one-byte UTF-8 byte length plus UTF-8 bytes |

Vector and color writes may supply a non-empty subset. Untouched members keep
their original four bytes.

## Registry

| Component | Property | Schema | Writable | Status / note |
|---|---|---|---:|---|
| `ModPlayerSpawn` | `SharedSpawn` | `Bool1` | yes | CONFIRMED |
| | `SpawnUsageType` | `Int32LE` enum | yes | CONFIRMED; names unknown |
| | `AllowedPlayers` | `Int32LE` | yes | CONFIRMED |
| | `SpawnShape` | `Int32LE` enum | yes | CONFIRMED; names unknown |
| | `PlayerCountMask` | `Int32LE` | yes | CONFIRMED |
| | `SpawnDistribution` | `Int32LE` enum | yes | CONFIRMED; names unknown |
| | `LineLength` | `Float32LE` | yes | CONFIRMED |
| | `Radius` | `Float32LE` | yes | CONFIRMED |
| | `Advanced` | `Bool1` | yes | CONFIRMED |
| | `guid` | `Utf8String` | no | identity metadata |
| `ModBoxCollider` | `center` | `Vector3Float32` | yes | CONFIRMED |
| | `size` | `Vector3Float32` | yes | CONFIRMED |
| | `guid` | `Utf8String` | no | identity metadata |
| `ModProp` | `prop` | unknown asset reference | no | UNKNOWN semantics |
| | `tintColor` | `Color4Float32` | yes | CONFIRMED |
| | `collisionType` | `Int32LE` enum | yes | CONFIRMED; names unknown |
| | `shadowCastingMode` | `Int32LE` enum | yes | CONFIRMED; names unknown |
| | `customMaterials` | unknown/reference structure | no | UNKNOWN; observed 4/42 bytes |
| | `guid` | `Utf8String` | no | identity metadata |
| `ModLight` | `type` | `Int32LE` enum | yes | CONFIRMED; names unknown |
| | `color` | `Color4Float32` | yes | CONFIRMED |
| | `range` | `Float32LE` | yes | CONFIRMED |
| | `intensity` | `Float32LE` | yes | CONFIRMED |
| | `spotAngle` | `Float32LE` | yes | CONFIRMED |
| | `shadows` | `Int32LE` enum | yes | CONFIRMED; names unknown |
| | `guid` | `Utf8String` | no | identity metadata |
| `ModText` | `Text` | `Utf8String` | no | CONFIRMED encoding; variable-length rewrite not proven safe |
| | `Font` | `Int32LE` | no | excluded from v0.2 write allowlist |
| | `Color` | `Color4Float32` | yes | CONFIRMED |
| | `FontSize` | `Float32LE` | yes | CONFIRMED |
| | `HorizontalAlignment` | `Int32LE` enum | yes | CONFIRMED; names unknown |
| | `Size` | `Vector2Float32` | yes | CONFIRMED |
| | `FontStyles` | `Int32LE` enum | yes | CONFIRMED; names unknown |
| | `Outline` | `Bool1` | yes | CONFIRMED |
| | `OutlineThickness` | `Float32LE` | yes | CONFIRMED |
| | `OutlineColor` | `Color4Float32` | yes | CONFIRMED |
| | `Shadow` | `Bool1` | yes | CONFIRMED |
| | `ShadowColor` | `Color4Float32` | yes | CONFIRMED |
| | `ShadowOffsetX` | `Float32LE` | yes | CONFIRMED |
| | `ShadowOffsetY` | `Float32LE` | yes | CONFIRMED |
| | `ShadowSoftness` | `Float32LE` | yes | CONFIRMED |
| | `guid` | `Utf8String` | no | identity metadata |
| `ModTrigger` | `TriggerShape` | `Int32LE` enum | yes | CONFIRMED raw encoding; enum names UNKNOWN |
| | `Size` | `Vector3Float32` | yes | CONFIRMED |
| | `Center` | `Vector3Float32` | yes | CONFIRMED |
| | `Radius` | `Float32LE` | yes | CONFIRMED |
| | `Height` | `Float32LE` | yes | CONFIRMED |
| | `TriggerOnHit` | `Bool1` | yes | CONFIRMED |
| | `TriggerOnEnter` | `Bool1` | yes | CONFIRMED |
| | `TriggerOnExit` | `Bool1` | yes | CONFIRMED |
| | `TriggerOnStay` | `Bool1` | yes | CONFIRMED |
| | `OnHitActions` | `ManagedReferencePayload` | no | CONFIRMED v0.4a read-only framing and inspection |
| | `OnEnterActions` | `ManagedReferencePayload` | no | CONFIRMED v0.4a read-only framing and inspection |
| | `OnExitActions` | `ManagedReferencePayload` | no | CONFIRMED v0.4a read-only framing and inspection |
| | `OnStayActions` | `ManagedReferencePayload` | no | CONFIRMED v0.4a read-only framing and inspection |
| | `StayTriggerInterval` | `Float32LE` | yes | CONFIRMED |
| | `DisableAfterTriggered` | `Bool1` | yes | CONFIRMED |
| | `OneUsePerPlayer` | `Bool1` | yes | CONFIRMED |
| | `guid` | `Utf8String` | no | identity metadata |

`ModTrigger.OnHitActions`, `OnEnterActions`, `OnExitActions`, and
`OnStayActions` are managed-reference payloads. Inspector v0.4a can frame
them, resolve Action entries, and return bounded structured views. They remain
read-only through the generic Component Writer. The separate Action Field
Writer v0.4b admits only four registered fields inside an existing
`SpawnPrefabAction`; it does not make the Action property itself generically
writable. Unknown/unregistered properties can never be written through the
generic API. See [`ACTION_FORMAT.md`](ACTION_FORMAT.md).

## Scope boundary

Trigger Writer v0.3 can mutate only registered fixed-size `ModTrigger`
properties. It cannot mutate Actions; the separate v0.4b path is limited to
four existing SpawnPrefab field tokens. Neither Writer can mutate Action
graphs, prefabs, hierarchy, objects, or component membership. There is no
raw-byte API. `ModTransform`
continues to use the existing dedicated v0.1 API and is not admitted to the
ordinary Component registry.
