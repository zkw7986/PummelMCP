# Asset-backed templates

Stage 12E introduces an Editor-verification candidate for parented leaf objects
with exact component order `ModTransform`, `ModProp`. The Prop field order must
match the audited schema, `prop` must use the exact compact UUID envelope, and
`customMaterials` must be empty. The candidate safety class is
`SAFE_PROP_LEAF_DUPLICATION_EMPTY_MATERIALS_CANDIDATE` until official Editor
verification passes. Template placement is restricted to the source parent.

Asset replacement is a separate candidate operation carried by the existing
`set_component_property` MCP tool. For `ModProp.prop`, callers supply a same
Scene `template_object`, `expected_scene_hash`, and
`template_reference_sha256`. PummelMCP copies the complete 38-byte typed
reference; caller-provided GUID patches are rejected. Validation proves exact
EOF parsing, Scene validity, source-template preservation, fixed byte length,
and that all changed bytes lie inside the target field.

Built-in Prop GUIDs commonly have no Mod-local `.pmeta`. Therefore these
features are evidence-based candidates rather than approved safety classes.
Non-empty material overrides, MeshCollider, cross-Scene references, Prop
reparenting, asset import, and asset-file mutation remain unsupported.
