# Stage 11A hierarchy writer

Oracle 08 (`08_leaf_reparent_before.scene` → `08_leaf_reparent_after.scene`) proves that the Editor removes a middle leaf from its old parent and appends it to the destination parent's child list. Existing destination children keep their order; the old following sibling shifts left; object index and Component payload order follow the new preorder.

The approved class is `SAFE_TRANSFORM_ONLY_LEAF_REPARENT`: a parented, childless, reference-free object with the exact standard `ModTransform` shape. Identity and Component ownership are preserved. Local position is byte-identical in the Oracle. Rotation and scale remain semantically equal but the Editor normalizes three least-significant float bytes; the Writer preserves the source float32 payload exactly under `PRESERVE_LOCAL_TRANSFORM`.

`plan_gameobject_reparent` returns a `HierarchyMovePlan`. `reparent_gameobject` applies dynamic-span moves for hierarchy, object index, and payload records; validates exact EOF, counts, identities, ownership, destination order, preorder, and payload; then uses the existing temporary-file, backup, stale-state, and atomic-replace pipeline. Roots, child-bearing sources, unsupported shapes, references, cycles, ambiguous identifiers, and stale hashes fail closed.

Stage 11C reuses this Writer only for `SAFE_TRANSFORM_ONLY_LEAF_REPARENT` inside an isolated template transaction. Known-component ButtonLeaf templates remain same-parent-only.
