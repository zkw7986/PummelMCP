# PMH v1 scene format (research notes)

This document records only the layout exercised by the Joker 21
`MainScene.scene`. It is not a complete specification. Status labels mean:

- **CONFIRMED** — observed in the supplied scene and/or validated by loading an
  externally modified file in Pummel Party Mod Editor.
- **INFERRED** — consistently parsed by the supplied inspector, but not yet
  independently validated for all values or files.
- **UNKNOWN** — semantics are not understood; bytes must be retained verbatim.

Reader evidence is shared by Writer v0.1/v0.2 and the local MCP interface.

## File envelope

| Order | Field | Encoding | Status | Evidence |
|---:|---|---|---|---|
| 1 | magic length | `u8` | INFERRED | Supplied inspector |
| 2 | magic | ASCII bytes | **CONFIRMED** | Value is `PMH` |
| 3 | version | little-endian `u32` | **CONFIRMED** | Value is `1` |
| 4 | root count | little-endian `u16` | **CONFIRMED** | Joker 21 value is `41` |
| 5 | root hierarchy | recursive GameObject records | **CONFIRMED** | 142 objects recovered |
| 6 | object/component index table | one record per flattened object | INFERRED | Full parse reaches EOF |
| 7 | component property table | one payload record per indexed component | INFERRED | Full parse reaches EOF |

The hierarchy is flattened in preorder (object, then its children recursively).
Both later tables are associated with objects in that order. This ordering is
**INFERRED** from the supplied inspector and the successful complete parse.

## GameObject hierarchy record

| Order | Field | Encoding | Status |
|---:|---|---|---|
| 1 | name | `str8` | **CONFIRMED** field; INFERRED encoding |
| 2 | active | `u8`, interpreted as boolean | **CONFIRMED** field; INFERRED encoding |
| 3 | layer | little-endian signed `i32` | **CONFIRMED** field; INFERRED encoding |
| 4 | tag | `str8` | **CONFIRMED** field; INFERRED encoding |
| 5 | child count | little-endian `u16` | **CONFIRMED** hierarchy; INFERRED encoding |
| 6 | children | recursive GameObject records | **CONFIRMED** |

`str8` is an **INFERRED** one-byte byte length followed by that many UTF-8
bytes. The Joker 21 scene contains 41 roots and 142 total GameObjects.

## Object/component index table

For every flattened GameObject:

| Order | Field | Encoding | Status |
|---:|---|---|---|
| 1 | object GUID | `str8` | INFERRED |
| 2 | component count | little-endian `u32` | INFERRED |
| 3 | component type | `str8` | INFERRED |
| 4 | component GUID | `str8` | INFERRED |
| 5 | enabled | `u8`, interpreted as boolean | INFERRED |

Fields 3–5 repeat `component count` times. Joker 21 contains 267 components.

## Component property table

For every indexed component, in object/index order:

| Order | Field | Encoding | Status |
|---:|---|---|---|
| 1 | property count | little-endian `u16` | INFERRED |
| 2 | property name | `str8` | INFERRED |
| 3 | payload byte length | little-endian `u32` | INFERRED |
| 4 | payload | opaque bytes of the stated length | **CONFIRMED** framing; semantics vary |

Fields 2–4 repeat `property count` times. Every property is represented by a
model object containing its exact payload bytes. A decoder may additionally
expose a typed value, but it never discards or replaces the raw payload.

## Field decoding

| Component/property | Payload | Interpretation | Status |
|---|---|---|---|
| `ModTransform.position` | 12 bytes | 3 little-endian `float32` values | **CONFIRMED** |
| `ModTransform.rotation` | 12 bytes | 3 little-endian `float32` values | **CONFIRMED** |
| `ModTransform.scale` | 12 bytes | 3 little-endian `float32` values | **CONFIRMED** |
| Selected known scalar names | 4 bytes | little-endian `float32` | INFERRED |
| Other 4-byte properties | 4 bytes | little-endian signed `i32` | INFERRED |
| 1-byte properties | 1 byte | boolean | INFERRED |
| `Size` | 8 bytes | 2 little-endian `float32` values | INFERRED |
| Other 12/16-byte payloads | 3/4 little-endian `float32` values | vector-like | INFERRED |
| length-prefixed or printable UTF-8 | variable | string | INFERRED |
| all other payloads | variable | raw bytes only | **UNKNOWN** |

The `PlayerSpawn_0` transform is located structurally: traverse the hierarchy
by GameObject name, then select its `ModTransform` component, then select the
`position`, `rotation`, and `scale` fields. No absolute file offset is used.

## Joker 21 regression facts

| Fact | Expected | Status |
|---|---:|---|
| Magic | `PMH` | **CONFIRMED** |
| Version | `1` | **CONFIRMED** |
| Root count | `41` | **CONFIRMED** |
| GameObject count | `142` | **CONFIRMED** |
| Component count | `267` | **CONFIRMED** |
| Parser consumption | exactly EOF | **CONFIRMED** |
| External edit | `PlayerSpawn_0.position.x = 5.0` loads and moves object | **CONFIRMED** |

The checked-in regression fixture is the editor-validated modified sample.
Its `PlayerSpawn_0.position` payload is `05 00 a0 40 9e f1 d9 3e 42 ea 11 c1`,
which decodes to approximately `(5.000002384, 0.425671518, -9.119691849)`.
The fixture SHA-256 is
`a0af6631df7e99d4f98d964122eca56ab66cb8beaad53d7217bc54efff89cde8`.

## Unknowns and scope boundary

- **UNKNOWN:** semantics of any property not explicitly decoded above or in
  the Component Schema Registry.
- **UNKNOWN:** whether other PMH versions or asset kinds use the same layout.
- **UNKNOWN:** constraints beyond the observed integer widths, ordering, and
  lengths.
- **UNKNOWN:** checksums, cross-reference invariants, or editor validation rules
  not present in the observed scene.

The reader rejects magic other than `PMH` and versions other than `1`. It does
not attempt recovery or serialization. Mutation is performed only by the
separate allowlisted Writers against Reader-provided source spans.

## Writer v0.1 evidence

Writer v0.1 does not reserialize the file. During each read, every property
payload receives a half-open source span (`start`, `length`) derived from the
current parse cursor and the payload's encoded length. Callers cannot provide
or override this offset.

| Behavior | Status | Evidence |
|---|---|---|
| Property payload source span identifies the exact payload bytes | **CONFIRMED** | Span slices equal every retained raw payload in tests |
| `position`, `rotation`, and `scale` axes encode as `<f` | **CONFIRMED** | Real and synthetic reparse tests |
| A partial axis update can preserve the other two raw axis slices | **CONFIRMED** | Byte-level tests for position, rotation, and scale |
| `PlayerSpawn_0.position.x` can be patched to float32 `8.0` | **CONFIRMED** | Joker 21 fixture-copy regression |
| Differences can be restricted to the requested four-byte axis span | **CONFIRMED** | Whole-file byte diff regression |
| Patched Joker 21 file reparses to EOF with unchanged 41/142/267 counts | **CONFIRMED** | Writer round-trip regression |
| The same mutation strategy is valid for other PMH versions | **UNKNOWN** | Writer rejects versions other than 1 |
| Existing non-Transform properties can be safely changed | **UNKNOWN** | Explicitly outside Writer v0.1 scope |

Backup naming and atomic replacement are application safety mechanisms rather
than PMH format claims. See [`WRITER_SAFETY.md`](WRITER_SAFETY.md).

## Component Writer v0.2 evidence

Component Writer v0.2 adds explicit schemas for five existing ordinary
Component types. The Joker 21 fixture contains 8 `ModPlayerSpawn`, 15
`ModBoxCollider`, 85 `ModProp`, 6 `ModLight`, and 1 `ModText` components. Every
registered fixed-width field has the expected payload length across all of its
samples. See [`COMPONENT_SCHEMAS.md`](COMPONENT_SCHEMAS.md) for the exact
registry and confidence boundary.

Fixed-width writes reuse dynamically parsed payload spans. Scalar writes patch
only their 1- or 4-byte payload; vector/color partial writes patch only the
requested 4-byte members. Validation proves that every file difference is
inside those planned spans and that the target object GUID, component GUID,
property, structure, and requested raw value survive a full reparse.

`ModText.Text` is encoded as a one-byte UTF-8 byte length followed by UTF-8
bytes in the supplied sample. It is decoded but remains read-only in v0.2:
changing its length would require rewriting the enclosing property length and
moving every later source span, which is outside the fixed-length Writer safety
proof.

## Trigger Writer v0.3 evidence

Both the approved Joker 21 fixture and the supplied live Mod scene contain 10
`ModTrigger` components. Across all samples, `TriggerShape`, `Radius`, `Height`,
and `StayTriggerInterval` are consistently 4 bytes; `Size` and `Center` are 12
bytes; the six trigger flags are 1 byte. These fields are registered with the
existing little-endian codecs and are writable through Component Writer.

`TriggerShape` names remain **UNKNOWN** because no reliable enum mapping was
available; the API exposes its raw int32 without inventing Box/Sphere/Capsule
names. The four Action fields are variable managed-reference payloads. They
remain read-only; Inspector v0.4a now exposes a bounded structured view. Their
observed lengths range from 115 to 47,145 bytes. See
[`TRIGGER_FORMAT.md`](TRIGGER_FORMAT.md) for the full table and safety
boundary.

## Action Inspector v0.4a evidence

The four `ModTrigger` Action fields are no longer opaque to read APIs. A
separate lossless parser recognizes a two-byte prefix, 7-bit length-framed
ActionList root JSON, a two-byte reference count, and length-framed managed
reference JSON segments. It explicitly resolves `m_actions[].rid` through
`references.RefIds[].rid` and preserves the `m_actions` order.

All 40 Action properties in the approved fixture and matching live Mod scene
are consumed to exact EOF with this framing. Inspector v0.4a is read-only;
Field Writer v0.4b separately permits four registered existing
`SpawnPrefabAction` fields while preserving the graph. See
[`ACTION_FORMAT.md`](ACTION_FORMAT.md) for segment evidence, unknowns, and
parser limits, [`ACTION_CATALOG.md`](ACTION_CATALOG.md) for observed classes,
and [`ACTION_WRITER_SAFETY.md`](ACTION_WRITER_SAFETY.md) for mutation safety.
