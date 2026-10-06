# GameplaySpec v0.1

GameplaySpec is strict, versioned JSON. Its top-level fields are `version`,
`scene`, `destination_root`, `objects`, `layout`, and execution-only `metadata`.
Each object requires a unique `logical_id`, an explicit registered
`template_id`, and a recipe type. Optional transforms are interpreted in the
destination parent's local space and accept only `position`, `rotation`, and
`scale` with numeric `x`, `y`, and `z` members.

Scene and Component GUIDs, raw bytes, raw asset GUIDs, arbitrary properties,
unknown keys, and existing mutable Scene objects are forbidden. Limits are 64
created objects, 128 Actions, and 512 primitive operations.

`layout` is a list of deterministic layout blocks. `ROW` requires an ordered
logical-object list, origin, nonzero direction, and nonzero spacing. `GRID`
requires exact rows-by-columns capacity, origin, nonzero row and column
directions, and finite nonzero spacings. Layouts set position only; explicit
rotation and scale remain unchanged.
