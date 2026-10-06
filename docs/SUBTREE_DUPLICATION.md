# Stage 11B safe subtree duplication

Oracle 09 proves that Ctrl+D appends the duplicate root to its outer parent's children and inserts the complete duplicate subtree as one continuous preorder block. Descendant order and relative parents are preserved. Every duplicate GameObject receives a fresh canonical UUID v4 shared with its owning `ModTransform`; the source subtree is unchanged.

The approved class is `SAFE_REFERENCE_FREE_TRANSFORM_ONLY_SUBTREE_DUPLICATION`. Every node must have only the standard `ModTransform`, and the whole subtree must contain no Scene, Component, Transform, asset, or unknown references. The narrow scope avoids assuming any internal-reference remapping policy.

`plan_subtree_duplication` returns an explicit `SubtreeClonePlan` with ordered nodes, identity and parent mappings, hierarchy reconstruction, destination, and reference inventories. `duplicate_subtree` inserts cloned hierarchy, index, and payload blocks using parsed spans and patches only allocated identity slots and the outer-parent child count. With Oracle UUID allocation, output is byte-for-byte identical to Oracle 09. Root subtrees, unsupported Components, references, malformed identities, allocation failures, and stale scenes are rejected.

Stage 11C may register this exact subtree class as a same-scene template. Creation remains under the source outer parent and may change only the duplicate root's local Transform; subtree reparent remains unsupported.
