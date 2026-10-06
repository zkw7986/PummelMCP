# Gameplay Action templates

The Stage 13D official-Editor Oracle added one
`ModSystem.Logic.SpawnPrefabAction` at RID 1000 to
`/Root/ParentB/ButtonLeaf` → `ModTrigger.OnEnterActions`. The complete managed
reference is 1278 bytes with SHA-256
`c31f2bbc79fb8e9b35399738ae397f59e155990c3c363e0d91d1cbdf87922398`.
The PrefabReference list SHA-256 is
`9b737a6d257b08d684e805a99a137fe25ec9e0d290c47bcbd1c68c1ff8be62d4`;
its single item names `Prefab_0`.

The Oracle preserved hierarchy, both `/father` empty Button templates, the
target Transform and BoxCollider, and every Trigger field except
`OnEnterActions`. Eight existing PlayerSpawn positions were re-quantized by
the official Editor by at most approximately 1.2e-6 during save. This is
bounded float32 normalization; identities, hierarchy, Components, and all
other fields remained unchanged.

The Composer may copy this exact validated Action template into a newly
created empty Button in the same Scene. It never accepts an Action RID, raw
managed-reference payload, or raw Prefab GUID from GameplaySpec.
