# Existing Action Field Writer v0.4b safety

Writer v0.4b changes selected ordinary fields of an existing
`ModSystem.Logic.SpawnPrefabAction`. It is not an Action graph writer.

## Allowlist

The Action Schema Registry admits only:

- `m_spawnAtPosition`: strict JSON boolean;
- `m_parentToTarget`: strict JSON boolean;
- `m_position`: non-empty partial `x/y/z` finite JSON numbers;
- `m_rotation`: non-empty partial `x/y/z` finite JSON numbers.

`m_prefabs`, targets, references, unknown fields, and absent fields are
read-only through this API. Other observed classes are registered for
inspection but all their fields remain read-only. Strings are never coerced to numbers or
booleans. NaN and infinities are rejected.

## Source-span patch

The JSON token parser walks the grammar rather than searching text. It handles
objects, arrays, strings and escapes, numbers, booleans, null, whitespace, and
nested values. Each logical node retains a half-open byte span relative to its
managed-reference JSON segment.

Only the requested boolean token or requested Vector3 member tokens are
replaced. Unrequested Vector3 members keep their original bytes. The JSON
object is never passed through `json.dumps`, so whitespace, key order,
escaping, unrelated number spelling, and unrelated fields are preserved.

## Variable-length framing

After a token replacement, the writer updates only:

1. the target reference segment's 7-bit JSON byte-length prefix;
2. the target reference JSON bytes;
3. the enclosing PMH property's adjacent little-endian `u32` payload length.

The target type tag is before the changed internal range and stays unchanged.
All bytes before the PMH property length field and all suffix content after the
old property payload are compared byte-for-byte, even when their eventual
file offsets shift.

## Validation and graph fingerprint

Before replacement, a temporary full PMH image must reparse successfully. The
validator checks magic, version, root/object/component counts, EOF consumption,
target object/component/property identity, and all non-target Component fields.
It then reparses the Action payload and requires:

- full consumption with no unparsed ranges;
- root JSON and therefore `m_actions` bytes unchanged;
- ordered Action rids unchanged;
- ordered RefIds membership unchanged;
- namespace, class, assembly, and type tags unchanged;
- graph fingerprint unchanged;
- all non-target managed-reference segments unchanged;
- the target segment equals the exact source-span reconstruction;
- only requested tokens changed and the new logical value is present;
- the other three Trigger Action event payloads unchanged.

Failure leaves the source untouched. Dry-run performs the same in-memory PMH
and Action validation but creates neither a backup nor a replacement.

## Concurrency, backup, and replacement

Callers may return `sha256` from `inspect_action_list` as
`expected_payload_sha256`. A mismatch raises `ActionPayloadChangedError`
before mutation. The shared Writer pipeline also verifies the whole source has
not changed since loading, writes and fsyncs a same-directory temporary file,
reparses it, creates the normal timestamped backup, and performs atomic
replacement.

There is no raw JSON, raw bytes, raw offset, force-write, or skip-validation
interface. v0.5 graph operations are a separate validated-template API and do
not weaken these field-writer invariants. See
[`ACTION_GRAPH_WRITER.md`](ACTION_GRAPH_WRITER.md).
