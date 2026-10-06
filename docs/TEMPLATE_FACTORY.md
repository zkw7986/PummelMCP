# Stage 11C approved template factory

Stage 11C registers existing, same-scene GameObjects as approved templates and creates one instance through a single transaction. It does not construct arbitrary PMH records or transplant objects between Scenes.

## Catalog

The human-readable deterministic catalog is `.pummelmcp-object-templates.json` in the configured Mod root. A `TemplateEntry` contains a stable explicit ID, display name, Mod-root-relative source Scene path, source GUID/path, `LEAF` or `SUBTREE` kind, inherited safety class, Component/subtree shape, reference inventory, allowed operations, destination policy, registered Scene hash, timestamp, and template fingerprint. Duplicate IDs are rejected.

The fingerprint covers the source root identity relation, complete relative hierarchy, names and ordinary object fields, Component order and enabled state, and every property name and payload hash. Creation resolves the source again and requires an exact fingerprint match; changes return `TEMPLATE_STALE` and never refresh registration implicitly.

## Capability matrix

| Safety class | Kind | Destination | Placement |
| --- | --- | --- | --- |
| `SAFE_TRANSFORM_ONLY_LEAF_DUPLICATION` | leaf | arbitrary parent in the same Scene | duplicate, approved leaf reparent, set destination-local Transform |
| `SAFE_EMPTY_TRIGGER_BOXCOLLIDER_LEAF_DUPLICATION` | leaf | source parent only | duplicate, set local Transform |
| `SAFE_REFERENCE_FREE_TRANSFORM_ONLY_SUBTREE_DUPLICATION` | subtree | source outer parent only | duplicate subtree, set duplicate root local Transform |
| `SAFE_PLAYERSPAWN_LEAF_DUPLICATION` | leaf | source parent only | duplicate, set local Transform |
| `SAFE_LIGHT_LEAF_DUPLICATION` | leaf | source parent only | duplicate, set local Transform |
| `SAFE_TEXT_LEAF_DUPLICATION` | leaf | source parent only | duplicate, set local Transform |

ButtonLeaf arbitrary-parent placement passed official Editor verification. Subtree
reparent is not approved. Names remain copied from the template because no rename
Writer exists.

## Transaction

`plan_create_from_template` verifies the Scene hash, catalog provenance, source fingerprint, current safety gate, destination policy, and requested local Transform overrides without mutation.

`create_from_template` copies the original Scene into an isolated temporary directory. Existing duplication, reparent, and Transform primitives operate only on that temporary Scene. Each primitive reparses and validates its output. Final validation confirms exact EOF, full Scene validity, template-source preservation, and created-root presence. Only then does the stale-state-aware backup and atomic replacement mechanism replace the real Scene once. Any intermediate exception discards the temporary workspace, leaving the real Scene byte-identical and without partial hierarchy/count mutations.

The result reports the template ID and fingerprint, source GUID, created root/node/Component GUIDs, destination, before/after hashes, one final backup path, and validation checks. Catalog provenance is not written into PMH data.

## Failure modes

The factory rejects missing or duplicate template IDs, stale Scene hashes, stale fingerprints, catalog path escape, cross-scene use, unsafe or changed source shapes, unsupported Components or references, arbitrary Button destinations, subtree reparent, invalid identities, and any intermediate or final validation failure. It does not add rename, delete, batch creation, Prefab template, or cross-Mod import capabilities.
Stage 12 candidate: exact empty-material `ModProp` leaves may be registered and
instantiated only under their source parent. The template fingerprint already
covers the complete Prop payload, typed-reference bytes, component order,
identity relation, and hierarchy. This candidate awaits official Editor
verification.
