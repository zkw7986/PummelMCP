# Runtime assertions

Assertions return `PASS`, `FAIL`, `NOT_OBSERVABLE`, `NOT_APPLICABLE`, or
`UNKNOWN`. Static reference/Scene validity and runtime observations are kept
separate. `NoRuntimeErrors` can pass only with an intact incremental log window.
Trigger firing, Action execution, and prefab spawning remain `NOT_OBSERVABLE`
until the runtime emits direct evidence; absence of an error is not proof.

The semantic Scene fingerprint covers ordered hierarchy, object and Component
identity/order/ownership, names, active/layer/tag state, known decoded values,
and exact hashes for Action graphs and unknown payloads. The only normalized
field is `ModTransform.position` on `/Player Spawnpoints/PlayerSpawn_*`, bounded
by the documented 1.2e-6 Editor float32 normalization. Other fields remain exact
semantic values or exact raw hashes; normalization is never applied Scene-wide.
