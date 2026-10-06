# Assembly truth

Stage 16 evidence extracted from the game's own `Assembly-CSharp.dll`
(Pummel Party build dated 2025-12-23) with a local decompiler. Source lives in
a gitignored `research/decompiled/` tree; only factual interface information is
recorded here.

Evidence level **ASSEMBLY_TRUTH** means the game's own C# definition is the
authority for a name, field, or value. It is stronger than scene observation
because every serialized name and enum numeric is checked against the code that
reads it. It is still weaker than Oracle verification: a field definition does
not prove the runtime behavior of a byte patch, and every new writable class
still requires one official-Editor pass before Writer approval.

## Action class mapping

`ModAction.GetActionClassType(ModActionType)` is the authoritative
type-to-class switch. `m_type` values are stride-32 with one gap (768→1024):

| `m_type` | Class | | `m_type` | Class |
|---:|---|---|---:|---|
| 0 | `ModAction` (None) | | 672 | `GiveKeysAction` |
| 32 | `WaitAction` | | 704 | `DropKeysAction` |
| 64 | `KillAction` | | 736 | `GiveTrophyAction` |
| 96 | `ChangeHealthAction` | | 768 | `GiveItemAction` |
| 128 | `ChangeScoreAction` | | 800 | `SpawnKeysAction` |
| 160 | `SpawnEffectAction` | | 832 | `ChangeVelocityAction` |
| 256 | `ScaleAction` | | 864 | `MoveSpaceAction` |
| 288 | `SetColorAction` | | 1024 | `ShowMessageAction` |
| 320 | `SetActiveAction` | | 1056 | `GiveMinigameItemAction` |
| 352 | `PlayTweenAnimationAction` | | 1088 | `StunPlayerAction` |
| 384 | `PositionAction` | | 1120 | `DropMinigameItemAction` |
| 416 | `RotationAction` | | 1184 | `MatchTransformAction` |
| 448 | `SetGravityAction` | | 1248 | `SetPlayerVisualAction` |
| 480 | `SetPlacementAction` | | 1280 | `SetConnectionActiveAction` |
| 512 | `ModifyPlayerAction` | | 1312 | `SetBoardSpaceActiveAction` |
| 544 | `PlaySoundAction` | | 1344 | `BoardPopupAction` |
| 576 | `SpawnPrefabAction` | | 1376 | `RemoveTrophyAction` |

35 concrete Action classes. The wiki catalog (Minigame/Board/Utility/Movement/
Visuals groups) maps onto these exactly; the assembly adds none beyond the wiki
list.

## Target selection semantics (previously unknown, now resolved)

Every gameplay Action derives from `TargetAction`, which serializes:

| Field | Type | Meaning |
|---|---|---|
| `m_targetFlags` | `ModActionTargets` flags | which participants the Action runs on |
| `m_targets` | `ModAssetReference<Component>[]` | explicit target list, used only by the `Advanced` flag |

`ModActionTargets`: `Source = 1`, `Receiver = 2`, `Advanced = 4`. Flags
combine (observed value 3 = Source+Receiver). Execution in
`TargetAction.Execute`:

- `Source` → the event's source component; if it is a `ModItem` held by a
  player, the holder player is substituted.
- `Receiver` → the event's receiver component (same ModItem substitution).
  This is the serializer default.
- `Advanced` → each non-null entry of `m_targets` receives the effect.

This explains the observed corpus: `m_targets` is usually empty because the
default `Receiver` flag ignores it. Writer implication: scalar
`m_targetFlags` patching is a plain enum-token edit; `m_targets` mutation
requires reference writes and stays out of scope for now.

## Per-class serialized fields

Inherited first: `ModAction` contributes `m_type` (enum token) and
`m_version` (short). `TargetAction` contributes `m_targetFlags` and
`m_targets` to everything below except where noted. `(base only)` = the class
adds no serialized field of its own.

| Class | Own serialized fields (name: type = default) |
|---|---|
| `KillAction` | (base only) |
| `SetPlacementAction` | (base only) |
| `BoardPlayerTargetAction` | (base only) |
| `WaitAction` | `m_seconds`: `ModComplexFloat` (Constant or min/max random pair) |
| `ChangeHealthAction` | `m_operation`: `ModActionOperation`; `m_value`: int |
| `ChangeScoreAction` | `m_operation`: `ModActionOperation`; `m_value`: int |
| `HealPlayerAction` | `m_value`: int |
| `GiveKeysAction` | `m_value`: int = 1 |
| `SpawnKeysAction` | `m_keys`: int = 1 |
| `DropKeysAction` | `m_keys`: int = 1; `m_spawnkeys`: bool = true |
| `RemoveTrophyAction` | `m_trophiesToRemove`: int = 1 |
| `GiveTrophyAction` | `m_playSound`: bool = true; `m_trophiesToGive`: int = 1 |
| `StunPlayerAction` | `m_stunDuration`: float = 1 |
| `ModifyPlayerAction` | `m_playerActionType`: `ModifyPlayerActionType`; `m_movementSpeed`: float = 1; `m_gravity`: Vector3 = (0,-9.81,0) |
| `ChangeVelocityAction` | `m_operation`: `ModActionOperation`; `m_space`: `ModActionSpace`; `m_localTarget`: `LocalSpaceTarget` = Receiver; `m_velocityDir`: `VelocityDirection`; `m_customDir`: Vector3 = zero; `m_speed`: float = 1 |
| `SetGravityAction` | `m_value`: Vector3 = (0,-9.81,0) |
| `SetActiveAction` | `m_newState`: `SetActiveType` = Disabled |
| `SetBoardSpaceActiveAction` | `m_newState`: `SetActiveType` = Disabled |
| `SetColorAction` | `m_color`: Color = white |
| `PositionAction` | `m_space`: `ModActionSpace`; `m_operation`: `ModActionOperation`; `m_position`: Vector3 = (1,1,1) |
| `RotationAction` | `m_space`; `m_operation`; `m_rotation`: Vector3 = (1,1,1) |
| `ScaleAction` | `m_operation`: `ModActionOperation`; `m_scale`: Vector3 = (1,1,1) |
| `ShowMessageAction` | `m_messageTarget`: `ShowMessageTarget` = Everyone; `m_message`: string = "Hello!"; `m_duration`: float = 1 |
| `SpawnEffectAction` | `m_effectType`: `SpawnEffectType` = Blood |
| `PlaySoundAction` | `m_clip`: `ModAssetReference<AudioClip>`; `m_volume`: float = 1 |
| `SpawnPrefabAction` | `m_prefabs`: `ModAssetReference<PummelPrefab>[]`; `m_spawnAtPosition`: bool; `m_position`: Vector3 = zero; `m_rotation`: Vector3 = zero; `m_parentToTarget`: bool; `m_targetPositionOffset`: Vector3 = zero; `m_targetRotationOffset`: Vector3 = zero |
| `MatchTransformAction` | `m_transformToMatch`: `ModAssetReference<Transform>`; `m_transformValuesToMatch`: `TransformValues` flags = all Position+Rotation bits |
| `PlayTweenAnimationAction` | `m_tweenType`: `ModTweenType`; `m_easeType`: `LeanTweenType` = linear; `m_time`: float = 1; `m_startValueType`: `ModTweenValue`; `m_startValue`: Vector3 = zero; `m_endValueType`: `ModTweenValue` = Offset; `m_endValue`: Vector3 = (1,1,1); `m_rotationAxis`: Vector3 = (0,1,0); `m_rotationDegrees`: float |
| `SetConnectionActiveAction` | `m_connectionMappings`: `List<ConnectionMapping>` |
| `MoveSpaceAction` | `m_spaces`: `ModAssetReference<BoardNode>[]` |
| `BoardPopupAction` | `m_titleText`: string; `m_bodyText`: string; `m_speakerImageReference`: `ModAssetReference<Texture2D>`; `m_showCloseButton`: bool; `m_closeButtonText`: string = "Close"; `m_choiceCount`: int; `m_choices`: `List<InteractionPopupChoice>`; `m_isTimed`: bool; `m_timedDuration`: float = 10 |
| `SetPlayerVisualAction` | `m_prefab`: `ModAssetReference<PummelPrefab>`; `m_recolorPrefabUsingPlayerColor`: bool = true; `m_verticalLookTargetChildIndex`: int = -1; `m_movementRotationTargetChildIndex`: int = -1 |
| `GiveItemAction` | `m_item`: `Items`; `m_items`: `ModGiveItemMask` (15 weapon bits) — class marked `[Obsolete]`, prefer `GiveMinigameItemAction` |
| `GiveMinigameItemAction` | `m_item`: `ModAssetReference<PummelPrefab>` |
| `DropMinigameItemAction` | `m_itemSlot`: `ModItemSlot` = RightHand; `m_dropAllItems`: bool |
| `DamagePlayerAction` | `m_damageInstance`: `DamageInstance` (nested value object, not TargetAction-based per observed data) |

Cross-check against `ACTION_CATALOG.md` observed scenes: `SpawnPrefabAction`
(7 fields), `SpawnEffectAction` (`m_effectType`), `PlaySoundAction`
(`m_clip`, `m_volume`), `KillAction` (base only) — all four match exactly.
The observed `KillAction` entry carried only `m_type`/`m_version`/target
fields, consistent with the base-only definition above.

## Enum reference

| Enum | Values |
|---|---|
| `ModActionTargets` | Source=1, Receiver=2, Advanced=4 (flags) |
| `ModActionOperation` | Set, Add, Subtract, Multiply, Divide |
| `ModActionSpace` | World, Local |
| `SpawnEffectType` | Wood, Blood, Explosion, Confetti, Sparkles |
| `SetActiveType` | Enabled, Disabled |
| `ShowMessageTarget` | Everyone=1, Target=2 |
| `ModifyPlayerActionType` | MovementSpeedMultiplier, Gravity |
| `ModTweenType` | WorldPosition, LocalRotation, LocalScale, LocalPosition, RotateAroundLocalAxis, RotateAroundWorldAxis, WorldRotation |
| `ModTweenValue` | Current, Specific, Offset |
| `LocalSpaceTarget` | Source, Receiver, Target |
| `ComplexValueType` | Constant, RandomBetweenTwoValues |
| `ModItemSlot` | DefaultForItemType, LeftHand, RightHand, Head, Body, … |
| `TransformValues` (private, MatchTransform) | PositionX=1…ScaleZ=0x100 (flags) |
| `VelocityDirection` (private, ChangeVelocity) | Up, Down, Left, Right, Forward, Backward, TowardsSource, … |
| `ModGiveItemMask` | BoxingGlove=1 … Challenge=0x4000 (15 weapon flags) |
| `HitType` | CollisionEnter=1, CollisionStay=2, CollisionExit=4 |

`ModComplexFloat` is `ModComplexValue<float>`: `valueType` picks Constant
(uses `min`) or RandomBetweenTwoValues (uses `min`/`max`). `WaitAction`
intervals and `TimerTrigger` intervals are therefore either fixed floats or
seeded random ranges — the scene field is an object with two float leaves,
not a bare number. Any Writer for these must preserve the nested shape.

## Trigger classes and mapping

`ModLogicTrigger.GetTriggerClassType(ModTriggerType)`:

| `m_type` | Class | Fires |
|---:|---|---|
| 0 | `ModLogicTrigger` | None |
| 32 | `StartTrigger` | scene/mod start |
| 40 | `OnEnableTrigger` | object enabled |
| 48 | `UpdateTrigger` | every frame |
| 64 | `HitTrigger` | physics hit (adds `m_hitType`: CollisionEnter/Stay/Exit) |
| 80 | `WeaponHitTrigger` | weapon hit (adds `m_hitType`: WeaponHitEventType) |
| 96 | `TimerTrigger` | repeating timer (adds `m_interval`: `ModComplexFloat`) |
| 112 | `BoardSpaceTrigger` | board space landing |
| 128 | `RoundStartTrigger` | round start |
| 144 | `RoundEndTrigger` | round end |

## Event surface census (who can hold an ActionList)

| Component | Serialized trigger storage | Notes |
|---|---|---|
| `ModTrigger` | `m_onHitActions`, `m_onEnterActions`, `m_onExitActions`, `m_onStayActions` (4 × `ModLogicTrigger`) | plus `m_triggerShape`, size/center/radius/height, per-event enable booleans (`m_triggerOnEnter` default true), `m_stayTriggerInterval` = 0.5, `m_disableAfterTriggered`, `m_oneUsePerPlayer` |
| `ModDestructible` | `m_onHitActions` (1 × `HitTrigger`) | second event carrier; hit-typing via `HitType` |
| `ModLogic` | `m_triggers`: `ModLogicTrigger[]` | unbounded trigger array; the generalized logic component |
| `ModBoardSpace` | holds `ModLogicTrigger` (board-space event path) | board space event source |

The four fixed properties of `ModTrigger` match the inspector's existing
event-property allowlist exactly. `ModLogic`'s array is a different, unbounded
shape and remains outside the current Writer scope.

## Execution semantics

- Triggers relay through `GameManager.Board.ExecuteTriggerAndRelayEvent` —
  action lists run per-machine with a shared `ModEvent.Seed`, and all
  randomness (`ModComplexFloat` ranges) draws from that seed, keeping
  multiplayer deterministic.
- `ModActionRunMode`: All, ServerRelay, ServerNoRelay, Origin — per-class
  network execution mode, decided by `ActionRunMode`, not serialized per
  instance.
- `ModActionRunner` supports coroutine actions; `WaitAction` implements
  `GetExecutionTime` so ordered delays are first-class. Timer triggers and
  waits compose into time-based gameplay without any scripting.

## MinigameDefinition

The runtime `MinigameDefinition : ScriptableObject` matches
`MinigameDefinitionData.json` field-for-field (`MinigameName`,
`Description`, tokens, `ScreenshotTextureGuid`, `MinPlayers`/`MaxPlayers`,
`InputHelp`, plus workshop fields `isWorkshop`/`publishedFileId`). JSON side
is already understood; no hidden fields block a future
`create_mod_from_template` tool.

## Writer implications (Phase 3 input, not approvals)

Candidates whose fields are pure scalar/enum tokens in exactly the observed
PMH shapes — cheapest to bring through the template pipeline after one Oracle
pass each:

1. `WaitAction` (one nested ModComplexFloat), `SetActiveAction` (one enum),
   `KillAction` (no fields), `StunPlayerAction`, `ChangeScoreAction`,
   `ChangeHealthAction`, `HealPlayerAction`, `SetGravityAction` (Vector3),
   `SetColorAction` (Color), `SpawnEffectAction` (enum),
   `ShowMessageAction` (enum + string + float).
2. Vector/operation family (`PositionAction`, `RotationAction`,
   `ScaleAction`, `ChangeVelocityAction`, `ModifyPlayerAction`) once the
   nested enum-token shapes are confirmed in scenes.
3. `m_targetFlags` scalar patching on any TargetAction.

Reference-bearing fields (`m_targets`, `m_prefabs`, `m_clip`,
`m_transformToMatch`, popup images, tween targets) stay behind the existing
reference-writer gates. `GiveItemAction` is obsolete in the game code; avoid
building on it.

## Boundaries

ASSEMBLY_TRUTH is evidence about definitions, not a license to write. Runtime
behavior of patched bytes is still only proven by the official-Editor Oracle
gate per class. Private nested enums (`TransformValues`,
`VelocityDirection`) are serialized numerics without wiki documentation;
their scene representation must be confirmed before any Writer uses them.
