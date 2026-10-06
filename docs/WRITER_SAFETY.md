# PMH Writer v0.1 / Component Writer v0.2 safety model

Writer v0.1 is deliberately limited to existing `ModTransform.position`,
`rotation`, and `scale` payloads. It cannot create or remove objects or
components, change hierarchy, edit actions/triggers/prefabs, or write arbitrary
properties.

Component Writer v0.2 extends the same mechanism to fixed-length properties
explicitly marked writable in the Component Schema Registry. It does not infer
write safety from payload length. Unknown, unregistered, and variable-length
properties remain read-only.

## Minimal source-span patching

Full reserialization would require assumptions about every inferred or unknown
part of PMH. Even a semantically equivalent rewrite could alter unknown bytes,
ordering, float bit patterns, or editor-specific details. The writer therefore
uses the Reader's dynamically captured property payload spans and patches only
requested four-byte float32 axes.

For ordinary Components, scalar patches cover exactly one registered payload;
partial vector/color patches cover only requested four-byte members. All
values are validated and encoded by explicit little-endian codecs. No caller
can supply raw bytes or offsets.

For a partial update, untouched axes are copied byte-for-byte. Values are
encoded explicitly with Python `struct` little-endian float32 (`<f`). Callers
never provide an offset.

## Validation before replacement

Every planned patch, including dry runs, is reparsed and checked before it can
replace the source. Validation checks:

- unchanged magic, version, hierarchy/object/component counts;
- complete parsing to EOF;
- unchanged target object and component GUIDs;
- requested values have the intended float32 bytes;
- untouched transform axes retain their original raw bytes; and
- every file difference is inside a requested four-byte axis span.

Component validation additionally checks the target Component GUID and type,
target property existence and exact requested payload, untouched property
members, and that all unrelated file bytes are identical.

For `ModTrigger`, validation also compares every non-target Trigger property
and separately checks `OnHitActions`, `OnEnterActions`, `OnExitActions`, and
`OnStayActions` byte-for-byte. Action payloads are never decoded for writing or
included in a patch plan.

A failed check raises an error and leaves the source untouched.

## Temporary file and atomic replacement

For a real save, patched bytes are written and flushed to a uniquely named
temporary file in the source directory. The Reader reparses that temporary
file. Only after successful validation and backup creation does `os.replace`
atomically replace the source on the same filesystem. Temporary files are
removed on failure.

The writer also checks that the source bytes have not changed since loading,
both before writing and immediately before backup/replacement.

## Backups

Real saves create a backup by default. Backup names contain a timestamp with
microseconds and are created with exclusive-create semantics, so an existing
backup is never overwritten. The backup contains the exact pre-patch source
bytes. Dry runs never create backups.

## Fixture protection

Writer tests copy `tests/fixtures/MainScene.scene` into pytest's temporary
directory and mutate only the copy. A fixed expected SHA-256 and an automatic
test-session before/after digest check protect the real fixture. The sole test
that addresses the fixture through `PMHScene` uses `dry_run=True`.
