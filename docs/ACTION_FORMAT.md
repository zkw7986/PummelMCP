# Action payload format (Inspector v0.4 / Graph Writer v0.5)

This document describes the read-only framing implemented by
`pummelmcp.pmh.actions`. Evidence comes from all 40 Action properties on the
10 `ModTrigger` components in the approved Joker 21 fixture and the matching
live Mod scene. Parsing is lossless in core: the original payload, JSON
subtrees, every framed segment and its span, and any unparsed ranges are kept.
MCP responses deliberately omit raw bytes.

## Outer framing

The following byte layout consumes every observed payload exactly:

```text
u16 little-endian outer prefix
7-bit little-continuation root JSON byte length
root ActionList JSON (strict UTF-8)
u16 little-endian reference segment count
repeat count times:
    u16 little-endian type tag
    7-bit little-continuation reference JSON byte length
    managed-reference JSON (strict UTF-8)
```

| Finding | Status | Evidence |
|---|---|---|
| Outer prefix occupies two bytes | **CONFIRMED** | All 40 properties frame and consume to EOF with this width |
| Observed outer prefix value is zero | **CONFIRMED** | Every approved and live sample begins `00 00` |
| Meaning of the outer prefix | **UNKNOWN** | No differing value or authoritative definition was observed |
| Lengths use 7-bit groups, low group first, high bit as continuation | **CONFIRMED** | One-, two-, and three-byte lengths agree with following UTF-8 byte spans and exact EOF; e.g. `90 de 01` = 28,432 |
| Root document count is exactly one | **CONFIRMED for observed corpus** | Each property has one length-framed root JSON document |
| Two bytes after the root are the tail reference segment count | **CONFIRMED** | Value equals both the number of following framed documents and `references.RefIds` count in every sample |
| Tail type tag equals the corresponding `data.m_type` | **CONFIRMED for observed corpus** | Equality holds for every observed reference segment |
| Tail documents are backing serialized managed-reference copies | **INFERRED** | Their JSON equals `RefIds[i].data` and tags match, but no authoritative binary-format definition was used |
| General semantics/range of the type tag | **UNKNOWN** | Only observed values can be reported; it is not treated as an enum |

An empty Action list is 115 bytes: prefix `00 00`, one-byte root length `6e`
(110), root JSON, and reference count `00 00`. A SpawnPrefab sample is 47,145
bytes: root length `90 de 01` (28,432), one reference segment with type tag
576, a reference JSON length of 18,701, and exact EOF.

All spans are half-open and relative to the Action property payload. The
owning PMH `Field.source_span` separately records where that payload resides in
the scene file. `parsed_ranges` merges adjacent framed spans;
`unparsed_ranges` precisely identifies trailing data not covered by known
framing. `fully_consumed` is true only when that latter list is empty.

## ActionList root and resolution

Observed roots contain:

```json
{
  "m_type": 0,
  "m_actions": [{"rid": 1000}],
  "references": {
    "version": 2,
    "RefIds": [
      {
        "rid": 1000,
        "type": {
          "class": "SpawnPrefabAction",
          "ns": "ModSystem.Logic",
          "asm": "Assembly-CSharp"
        },
        "data": {"m_type": 576}
      }
    ]
  }
}
```

| Finding | Status | Evidence |
|---|---|---|
| `m_actions` array order is execution-significant | **CONFIRMED** | User-supplied official editor description and observed ordered arrays; parser never sorts |
| `m_actions[].rid` resolves through `references.RefIds[].rid` | **CONFIRMED** | Explicit equality resolves every observed entry, including a two-Action sample |
| Action index equals `RefIds` index | **Not required** | Resolution is by `rid`; v0.5 move changes only `m_actions` order and validation proves RefIds/tails remain stable |
| `references.version` is 2 | **CONFIRMED for observed corpus** | Every observed root reports 2 |
| `rid=0` or null semantics | **UNKNOWN** | No representative real sample was observed |
| One `rid` may be used by multiple action entries | **UNKNOWN** | Parser permits it and preserves order, but corpus does not demonstrate it |

Duplicate `RefIds` values, dangling action ids, unreferenced references, tail
count mismatches, tail JSON mismatches, and tag mismatches are reported without
normalizing the source. Unknown namespaces, classes, and fields remain valid
inspectable data. No class is imported or instantiated.

## Nested lists and references

No real nested ActionList was observed in the supplied Joker 21 scenes. This
does not establish that nested lists do not exist. The model recursively
represents an object containing both `m_actions` and `references`, and
synthetic tests prove nested discovery and the configurable Action-list depth
limit. Managed-reference-shaped `rid` links are analyzed as data, with cycles
reported rather than followed indefinitely.

Arbitrary nested JSON objects and arrays occur inside real reference data.
Their application semantics are **UNKNOWN** unless listed in the catalog.
Core keeps the complete JSON subtree; MCP applies previews and depth/item
limits only when presenting it.

## Safety limits

The parser performs no evaluation, dynamic import, deserialization to runtime
game classes, or mutation. It enforces:

- payload length at most 16 MiB;
- at most five bytes per 7-bit length prefix;
- strict bounds checks before every read;
- strict UTF-8 and JSON parsing;
- JSON depth at most 64 and at most 100,000 nodes;
- at most 10,000 managed references;
- Action-list recursion depth from 0 through 8 (default 8);
- explicit duplicate, dangling, unreferenced, and cycle reporting.

Whether other PMH versions, `.pfab` files, editor releases, assemblies, or
unseen Action classes use identical framing is **UNKNOWN**.

## Targeted field writing in v0.4b

Field Writer v0.4b does not serialize a JSON object. The strict JSON span
parser records byte spans for object, array, string, number, boolean, and null
tokens, including nested and escaped content. A write replaces only the
registered value token spans inside one separately framed managed-reference
JSON segment.

If token length changes, the writer replaces that segment's 7-bit length
prefix and the enclosing PMH property's little-endian `u32` payload length.
The Action root JSON, root length prefix, outer prefix, reference count, target
type tag, other reference segments, bytes before the PMH property record, and
content after it remain byte-for-byte identical. Tests cover `false → true`,
`true → false`, `0.0 → 10.0`, and the 127/128 plus 16,383/16,384 7-bit length
boundaries.

The separately framed reference JSON is treated as the writable instance.
The duplicate `RefIds[i].data` subtree in root JSON remains unchanged as the
lossless graph snapshot. This authority choice is **INFERRED** from the
separate framing and the explicit root-immutability requirement; an editor
round-trip remains a later manual validation step. No live Mod file is written
automatically.

Only four already-present fields of an existing
`ModSystem.Logic.SpawnPrefabAction` are writable. See
[`ACTION_WRITER_SAFETY.md`](ACTION_WRITER_SAFETY.md). Action graph mutation and
full managed-reference replacement remain unsupported through the field writer.

## Read-only reference inspection in v0.4c

Reference Inspector v0.4c reuses the exact JSON token spans retained by this
parser. It classifies only evidence-backed fields, keeps unknown reference-like
subtrees losslessly, and adds no write path. MCP paginates reference items and
omits raw bytes. See [`ACTION_REFERENCES.md`](ACTION_REFERENCES.md) for the
observed prefab, audio, effect, and target structures and their evidence levels.

## Top-level graph mutation in v0.5

The cross-Mod Stage 8 corpus confirms empty roots and local contiguous rid
sequences from 1000 through as high as 1020. v0.5 patches only the
`m_actions` and `references.RefIds` array spans, and adds/removes separately
framed tail segments with their count. Reorder patches only `m_actions`.
`GraphFingerprintV2` validates the exact permitted membership/order delta.
See [`ACTION_GRAPH_WRITER.md`](ACTION_GRAPH_WRITER.md).
