# PummelMCP

[中文](#中文) | [English](#english)

## 中文

初版安装及客户端配置请先阅读[安装指南](docs/INSTALLATION.md)。本仓库发布核心工具和模板，不包含个人小游戏成果或完整游戏资源。

### 项目简介与当前能力

PummelMCP 是用于读取和制作 Pummel Party Mod Editor PMH 场景的研究工具，提供命令行、Python 核心服务及 MCP 接口。

完整文件制作 v0.3 为实验功能：`generate_card_minigame` 可在一次调用中制作新的 Joker 风格竞技场、卡牌/外观 Prefab、随机池、拾取得分/淘汰、玩家事件及回合设置。其他受支持机制通过 `get_authoring_capabilities`、`get_authoring_donors`、`plan_authored_minigame`、`build_authored_minigame` 检查、规划和制作。`audit_minigame_archive` 只读检查来源 ZIP，不解压或执行内容。来源目录只读；新 Mod 创建在用户明确授权的 `WorkshopMods` 内，初次创建仍须完成方案审核。生成状态保持 `AUTHORED_UNVERIFIED` / `NOT_RUN`，不代表游戏内验证通过。

参见[制作能力边界](docs/JOKER_AUTHORING_BOUNDARY_ZH.md)和 [Authoring v0.3 合约](docs/AUTHORING_V0_3.md)。完整文件编译器独立创建新文件，下述旧版原位 Writer 的限制仍有效。

当前阶段包含 PMH v1 Reader、修改现有 `ModTransform` 的 Writer v0.1、基于 schema 的 Component Writer v0.2、修改已确认固定长度非 Action 属性的 Trigger Writer v0.3、Action Inspector v0.4a、现有 Action 字段 Writer v0.4b、只读 Action Reference Inspector v0.4c、基于已验证模板的 Prefab Reference Writer v0.4d、Stage 8 Action 生态证据、Action Graph Writer v0.5，以及只读对象引用图检查和安全叶节点复制 v0.6b。

检查工具读取 Trigger Action 列表、解析托管引用、分类已观察的引用字段并识别 Action 类。Writer 可修改四类普通字段、替换一个现有 Prefab 槽位，并在严格引用图不变量下，为 11 个 schema 注册类增删或移动来源支持的顶层 Action。Stage 10B 的广泛 GameObject 复制未通过官方 Ctrl+D 对照验证门槛，因此不可用；下述两种精确验证过的叶节点形状是单独支持的有限路径。

### 开发与格式文档

```text
python -m pip install -e .[test]
python -m pytest
```

真实回归样例位于 `tests/fixtures/MainScene.scene`。测试只读取该文件，并验证整个测试会话前后 SHA-256 不变。

| 文档 | 内容 |
|---|---|
| [PMH_FORMAT](docs/PMH_FORMAT.md) | 各格式字段的证据级别 |
| [WRITER_SAFETY](docs/WRITER_SAFETY.md) | Writer 安全不变量 |
| [COMPONENT_SCHEMAS](docs/COMPONENT_SCHEMAS.md) | 普通组件字段允许列表 |
| [TRIGGER_FORMAT](docs/TRIGGER_FORMAT.md) | Trigger 布局与 Action 边界 |
| [ACTION_FORMAT](docs/ACTION_FORMAT.md)、[ACTION_CATALOG](docs/ACTION_CATALOG.md) | Action 编码结构及已观察类目录 |
| [ACTION_REFERENCES](docs/ACTION_REFERENCES.md) | 引用形状、解析证据及未知语义 |
| [PREFAB_REFERENCE_WRITER](docs/PREFAB_REFERENCE_WRITER.md) | 现有 Prefab 项替换验证 |
| [ACTION_WRITER_SAFETY](docs/ACTION_WRITER_SAFETY.md) | 定向字段修改验证 |
| [AUDIO_EFFECT_REFERENCES](docs/AUDIO_EFFECT_REFERENCES.md)、[ACTION_TARGETS](docs/ACTION_TARGETS.md) | 音频、特效及目标证据 |
| [ASSEMBLY_TRUTH](docs/ASSEMBLY_TRUTH.md) | 游戏程序集中的 Action 定义、枚举数值及目标选择语义 |
| [BUILTIN_MOD_CENSUS](docs/BUILTIN_MOD_CENSUS.md) | 官方场景、Action 模板和资产只读普查 |
| [BUILTIN_ASSET_TOOLS](docs/BUILTIN_ASSET_TOOLS.md) | 内置资产只读目录 |
| [ACTION_GRAPH_WRITER](docs/ACTION_GRAPH_WRITER.md) | Action 图修改规则 |
| [OBJECT_REFERENCE_GRAPH](docs/OBJECT_REFERENCE_GRAPH.md)、[GAMEOBJECT_DUPLICATION](docs/GAMEOBJECT_DUPLICATION.md) | 对象图证据和暂停的广泛复制研究 |

### 快速开始

安装项目后使用注册的 `pummelmcp` 命令：

```text
python -m pip install -e .

pummelmcp summary MainScene.scene
pummelmcp objects MainScene.scene --query PlayerSpawn
pummelmcp object MainScene.scene PlayerSpawn_0
pummelmcp transform MainScene.scene PlayerSpawn_0
pummelmcp set-position MainScene.scene PlayerSpawn_0 --x 8
pummelmcp set-position MainScene.scene PlayerSpawn_0 --x -3 --dry-run
pummelmcp validate MainScene.scene
pummelmcp research diff-action BEFORE.scene AFTER.scene OBJECT COMPONENT OnHitActions --rid 1000 --json
```

主要命令支持 `--json`。对象标识可使用 GUID、绝对层级路径（如 `/Player Spawnpoints/PlayerSpawn_0`）或唯一对象名；有歧义的名字会被拒绝。

CLI 提供受控的只读 Action 差异比较；Action 检查和安全修改通过 Python 核心/服务与 MCP 提供。对照验证约束下的叶节点复制，仅支持标准 Transform 叶节点，或四个 Action 图均为空的精确 `ModTransform + ModBoxCollider + ModTrigger` 结构。Blender 导入管线还可通过已验证的 Mod 本地 OBJ、贴图和材质创建有限的 `ModProp` 对象。参见 [Blender 导入](docs/BLENDER_SCENE_IMPORT.md)。任意 GameObject/Component 创建、任意 Prefab 组件编辑和任意属性/Action JSON 写入不受支持。

### MCP 服务与制作管线

小游戏配置工具可读取、规划并应用对现有 `ModSettings.json` 和 `MinigameDefinitionData.json` 的允许列表修改。`plan_minigame_v2` / `build_minigame_v2` 在来源 Mod 的暂存副本中组合类型化 Trigger 规则、场景构建和游戏设置。参见[一句话制作路线图](docs/ONE_SENTENCE_MINIGAME_ROADMAP.md)和 [v0.2 请求格式](docs/MINIGAME_SPEC_V0_2.md)。这些旧版副本构建路径不能用于替换已经交付的成品，成品迭代须遵循后面的原位编辑规则。

`plan_prefab_pack` / `build_prefab_pack` 提供有限的 Prefab 资产路径：把现有 PMH Prefab 和元数据复制到新 Mod，重映射副本间依赖，可选添加拾取得分 Action，并重定向现有 `ModSpawner`。参见 [Prefab Pack v0.1](docs/PREFAB_PACK_V0_1.md)。

Phase 2 提供内置小游戏资产的只读目录。Stage 16 提供内置 Asset Browser 目录及 `spawn_builtin_prop`，可从注册资产创建真正的 `ModProp`，无需复制场景中的现有对象。Stage 15 保留严格的完整小游戏原型规划器，Stage 14 保留手动启动的运行证据管线，并兼容已有检查、Writer、Template Factory 和 Gameplay Composer。

Phase 4 提供四类原型：Prefab 生成挑战、得分区域、死亡惩罚和 Trigger 反馈；配方使用固定的 Phase 3 已验证模板 Action 序列。独立 v0.2 路径可在从现有模板复制的 Trigger 上使用有限 Action 选项及标量字段。参见[小游戏原型](docs/MINIGAME_ARCHETYPES.md)和[玩法配方](docs/GAMEPLAY_RECIPES.md)。

主要工具（制作授权和审核工具见后文）：

- `get_scene_summary`
- `get_minigame_config`
- `plan_minigame_config_update`
- `apply_minigame_config_update`
- `plan_minigame_v2`
- `build_minigame_v2`
- `plan_prefab_pack`
- `build_prefab_pack`
- `get_builtin_asset_catalog_summary`
- `search_builtin_assets`
- `get_builtin_asset`
- `list_editor_assets`
- `get_editor_asset`
- `spawn_builtin_prop`
- `plan_blender_scene_import`
- `build_blender_scene_import`
- `list_scene_objects`
- `get_object`
- `get_components`
- `get_transform`
- `set_transform`
- `validate_scene`
- `get_component_property`
- `inspect_action_list`
- `inspect_action_references`
- `replace_prefab_reference`
- `set_action_field`
- `set_component_property`
- `add_action`
- `delete_action`
- `move_action`
- `inspect_reference_graph`
- `inspect_object_references`
- `analyze_duplication_safety`
- `plan_gameobject_duplication`
- `duplicate_gameobject`
- `inspect_runtime_capabilities`
- `plan_playtest_session`
- `start_playtest_session`
- `collect_playtest_evidence`
- `evaluate_playtest`
- `list_minigame_archetypes`
- `plan_minigame`
- `build_minigame`

服务仅使用 stdio。启动前，把路径替换为自己电脑上的实际目录：

```powershell
$env:PUMMELMCP_ALLOWED_ROOT = "C:\Users\<user>\AppData\LocalLow\Rebuilt Games\Pummel Party\WorkshopMods"
$env:PUMMELMCP_BUILTIN_ASSET_ROOT = "D:\SteamLibrary\steamapps\common\Pummel Party\PummelParty_Data\StreamingAssets"
$env:PUMMELMCP_EDITOR_ASSET_ROOT = "D:\SteamLibrary\steamapps\common\Pummel Party\PummelParty_Data\StreamingAssets"
$env:PUMMELMCP_IMPORT_ROOT = "D:\PummelImports"
pummelmcp-mcp
```

允许访问根目录是必填项。场景工具请求的路径会解析为实际路径，必须是根目录内已有的普通 `.scene` 文件；读写均受此限制，不会默认授权整个磁盘或用户目录。

`PUMMELMCP_BUILTIN_ASSET_ROOT` 为可选项，只供三个只读内置资产工具使用，指向游戏 `StreamingAssets`。扫描 `InbuiltMods` 和 `WorkshopTemplates/Minigames`，排除 Board；返回根内相对路径，不修改或导入资产。

`PUMMELMCP_EDITOR_ASSET_ROOT` 为可选项，供 `list_editor_assets`、`get_editor_asset`、`spawn_builtin_prop` 使用，指向包含 `aa` Addressables 构建的 `StreamingAssets`。未设置时使用 `PUMMELMCP_BUILTIN_ASSET_ROOT`。工具解析 Asset Browser 使用的 `ModAssetList`，调用方只用名字或目录内相对路径标识内置资产，不提交 GUID、原始字节或序列化引用。参见[编辑器资产工具](docs/EDITOR_ASSET_TOOLS.md)。

Blender 导入需要 `PUMMELMCP_IMPORT_ROOT`，其中包含导出的 `pummel_scene.json`、OBJ、MTL 和贴图。`plan_blender_scene_import` / `build_blender_scene_import` 两阶段流程只复制已验证的来源文件到目标 Mod，创建元数据和材质引用，并按清单变换放置对象。参见 [Blender 导入](docs/BLENDER_SCENE_IMPORT.md)。

stdio 是机器间协议；直接运行 `pummelmcp-mcp` 通常不会输出面向人的内容，而是等待 MCP 客户端。日志与配置错误写入 stderr，避免破坏协议消息。

### 安全写入与引用边界

所有修改工具共用核心安全管线：保留部分成员更新、动态来源跨度、最小字节修改、验证、备份、同目录临时文件和原子替换。不提供原始偏移/字节、强制写入或跳过验证选项。`ModText.Text` 保持只读。通用组件工具也不写 `ModProp.customMaterials`，只有有限的 Blender 导入构建器在创建新 `ModProp` 时写入该列表。

`ModProp.prop` 不在 schema 字段写入允许列表中。可通过同场景模板路径替换（`set_component_property` 的 `replace_prop_reference_from_template` 映射）；也可由 `spawn_builtin_prop` 在服务端根据解析的 Asset Browser 注册表解析引用，并原子写入完整新对象。

Trigger Writer v0.3 复用两个通用组件工具，可读取/修改 `ModTrigger.Radius`，或用严格布尔值启用 `ModTrigger.OneUsePerPlayer`：

```text
get_component_property(..., component_identifier="ModTrigger", property_name="Radius")
set_component_property(..., component_identifier="ModTrigger", property_name="Radius", value=2.5)
set_component_property(..., component_identifier="ModTrigger", property_name="OneUsePerPlayer", value=true)
```

`get_component_property` 对 `OnHitActions`、`OnEnterActions`、`OnExitActions`、`OnStayActions` 仅返回长度和哈希。可单独请求结构化、分页、只读视图：

```text
inspect_action_list(
    ...,
    component_identifier="ModTrigger",
    event_property="OnHitActions",
    offset=0,
    limit=20
)
```

返回值保留 Action 顺序，通过 `RefIds` 解析 `rid`，报告命名空间/类和有限字段，包含 payload SHA-256、完整解析状态及字段写入元数据，不改变已有字段值结构。核心保留无损原始数据，但 MCP 不输出原始字节或完整原始 JSON。

选择 `rid` 后，可检查引用类字段，而不暴露原始引用数据：

```text
inspect_action_references(
    ...,
    component_identifier="ModTrigger",
    event_property="OnHitActions",
    action_rid=1000,
    offset=0,
    limit=10
)
```

结果区分 Prefab 列表、音频对象、特效枚举、目标列表/标志及未知引用形状，并提供精确字段/项/叶跨度、只读指纹和仅按精确 GUID 的资产解析。资产路径相对当前 Mod 根目录，目标标志和特效枚举与程序集证据交叉核对。

`replace_prefab_reference` 使用完整、已观察的模板替换一个现有 `m_prefabs[index]`；模板按精确 GUID 或目录内精确 `.pfab` 相对路径选择，支持 payload 哈希和当前项 GUID 校验。不能改变列表长度/顺序、创建未观察引用、修改资产，或写音频/特效/目标/Action 图。跨场景来源模板路径要求允许的 `template_scene_path` 和精确 `template_reference_sha256`；模板场景须在当前 Mod 中，写入前再次校验所选 Prefab 和元数据哈希。

对现有 `ModSystem.Logic.SpawnPrefabAction`，可安全修改已存在的 `m_spawnAtPosition`、`m_parentToTarget`、部分 `m_position.x/y/z` 和部分 `m_rotation.x/y/z`。检查并保存返回的 payload 哈希后：

```text
set_action_field(
    ...,
    event_property="OnHitActions",
    action_rid=1000,
    expected_class="SpawnPrefabAction",
    field_name="m_spawnAtPosition",
    value=true,
    expected_payload_sha256="..."
)

set_action_field(
    ...,
    event_property="OnHitActions",
    action_rid=1000,
    expected_class="SpawnPrefabAction",
    field_name="m_position",
    value={"x": 5.0},
    expected_payload_sha256="..."
)
```

`set_action_field` 不能创建、删除、复制、替换或重排 Action；不能改类型、`rid`、`RefIds`、命名空间、类、程序集、类型标记、`m_prefabs`、目标/引用字段、音频/特效引用或未知字段。Phase 3 可复制其他类的完整已验证模板，但其单独字段仍只读。通用 Prefab 资产修改、通用 Prefab 列表编辑和任意 GameObject/Component 增删不在支持范围。

独立 `duplicate_gameobject` 仅接受有父节点的叶对象，形状必须是标准单个 `ModTransform`，或按顺序排列的 `ModTransform`、`ModBoxCollider`、`ModTrigger` 且四个 Action 图为空；未知引用与其他结构均拒绝。

v0.5 与 Phase 3 可从精确现有模板复制 `SpawnPrefabAction`、`KillAction`、`ChangeScoreAction`、`SpawnEffectAction`、`PositionAction`、`RotationAction`、`SetPlacementAction`、`PlaySoundAction`、`ShowMessageAction` 或 `SetPlayerVisualAction`。新 `rid` 由服务端选择，调用方不能指定：

```text
add_action(
    ...,
    event_property="OnHitActions",
    action_class="SpawnPrefabAction",
    insert_index=1,
    expected_payload_sha256="..."
)

move_action(..., event_property="OnHitActions", action_rid=1001, new_index=0,
            expected_payload_sha256="...")
delete_action(..., event_property="OnHitActions", action_rid=1001,
              expected_payload_sha256="...")
```

仅当现有 Action 和 RefId 的 rid 序列精确符合属性局部 `1000+n` 模式时允许创建。模板必须包含完整、有来源证据的托管引用 JSON，并通过已注册类/类型/字段/值规则；`SpawnPrefabAction` 还要求已有 Prefab 槽位。空 ActionList 可使用 `inspect_action_list` 返回的无歧义 `managed_reference_sha256` 作为 `template_sha256`。删除检查依赖；移动只改变执行顺序，保留 RefIds 和托管引用字节。

不支持任意 Action JSON 创建、未知或仅限 Board 的 Action、调用方指定 rid、原始 RefId 修改、嵌套图修改、通用 Prefab 文件 Writer、任意 GameObject/Component 创建、子树/根节点/含动作 Trigger 复制、任意对象删除、Computer Use 或自动试玩。

复制前先调用 `plan_gameobject_duplication`；安全方案只读报告预览 UUID、组件来源跨度/策略、追加位置和预期修改。`duplicate_gameobject` 对当前字节重新执行全部校验，可检查 `expected_scene_hash`，写入并解析同目录临时场景，验证身份、层级、来源保留和引用策略，创建备份后原子替换。

### 内置小游戏初始化模板

完整模板保存在 src/pummelmcp/templates/，随 GitHub 克隆和 Python 包分发，不依赖原作者的游戏路径。catalog.json 包含文件完整性校验。

- minimal-third-person：第三人称基础
- minimal-top-down：俯视角基础
- simple-arena：简单竞技场
- third-person-obstacle-course：第三人称障碍赛，含道具预制件
- third-person-shooter：第三人称射击，含枪械预制件

制作流程：

1. 用户提出制作游戏/小游戏后，Agent 必须先询问现有 `WorkshopMods` 绝对路径，并明确询问是否允许在其中创建新模组文件夹及文件。两项都收到之前，不调用任何用于小游戏检查、计划、初始化或制作的 MCP 工具。
2. 收到用户直接提供的路径和明确写入授权后，调用 `authorize_workshop_directory`。路径必须存在、是 `WorkshopMods` 文件夹且可写；授权只在当前 MCP 会话有效。
3. 目录授权后只做能力检查和只读规划。逐项对照用户规则与真实组件/Action，将每项标记为支持、等价方案或不支持，并说明依据和边界，禁止编造脚本 API。
4. 仅在首次制作一个游戏时，调用 `submit_gameplay_review` 提交完整方案：游戏流程、全部规则参数、场景与资产、能力限制/替代方案、Playtest 检查。将返回的原样方案展示给用户，等待用户明确审核通过，随后用对应 `review_sha256` 和用户原话调用 `approve_gameplay_review`。目录写入授权、最初制作请求、Agent 自行判断或用户沉默不能替代首次方案审核。首次创建前方案改变时仍需重新审核。
5. 首次审核通过后才可初始化/制作游戏。初次创建用唯一新目录，创建成功后该目录自动成为当前游戏绑定目录。已有游戏的后续规则修改、功能添加和场景调整直接在原文件进行，不需要重复玩家 review；能力核对、文件验证和目录授权仍然适用。可选提交迭代方案会返回 `ITERATION_READY`，不会暂停写入。
6. 成品必须在绑定的原目录持续迭代：先读取当前保存文件，保留用户修改和发布身份，使用现有写入工具更新原文件。禁止再生成一版新游戏然后覆盖/替换原成品。事务性临时文件和备份允许用于对原目录做经过验证的局部修改。只有用户明确要求另一个独立游戏时，才可提交 `user_requested_separate_game=true` 的新目录方案；该独立游戏的首次创建仍需完整方案审核。不能用该字段绕过用户要求。

`get_game_workflow_status` 可读取当前授权目录、首次审核方案、绑定成品目录和审核策略。目录授权与绑定状态按 MCP 会话保存；重启后需重新记录已有目录授权，继续编辑已初始化模组时会自动绑定该原目录，不再要求重复首次审核。已有模组须包含 MainScene.scene、Meta.json 和 ModSettings.json；空目录或不完整目录不视为已有成品。工具记录 Agent 提交的用户授权声明，不能独立验证人类身份或证明文字确实来自用户；Agent 不能自行批准首次创建。更新工具源码后，重新启动 MCP 服务即可加载新的描述和规则。

制作规划与实际制作过程中，Agent 应主动使用可用的联网搜索工具寻找符合玩法、画面风格和性能需求的素材，不等待用户逐项提供。按需寻找音效、背景音乐/BGM、OBJ 模型、贴图与材质；先检查已有 Mod 和游戏内置资产，避免重复导入。优先采用有明确复用及再分发许可的素材，例如 CC0 或按要求署名的 CC-BY，并遵循用户约束；付费购买须有用户明确授权。素材方案及成品 CREDITS/资产清单应记录来源链接、作者、许可、署名要求和修改情况。导入前核对真实格式、模型尺度与复杂度、贴图/材质依赖和音频兼容性；必要时使用可用工具转换，并通过受支持的导入流程写入授权目录。无法联网、许可不明或无法转换时，应说明限制，使用合适的内置资源或明确标识的占位素材，禁止虚构下载结果、来源和许可。此规则要求宿主 Agent 使用其搜索/下载/转换能力，不表示 MCP 服务本身新增了联网下载功能。

内置模板应保留为制作起点，成品放在另一个目录。静态检查和初始化不代表游戏内玩法已验证。模板来自用户提供的游戏目录，项目代码 LICENSE 不构成第三方游戏素材的授权声明。

---

## English

Start with the [installation and client configuration guide](docs/INSTALLATION.md). This release includes core tools and templates, not personal game deliveries or the full game assets.

### New: complete-file authoring v0.3 (experimental)

`generate_card_minigame` now builds a new Joker-style arena, card/visual Prefabs,
random pools, pickup scoring/elimination, player events and round settings in one
call. For other supported mechanics use `get_authoring_capabilities`,
`get_authoring_donors`, `plan_authored_minigame` and `build_authored_minigame`.
`audit_minigame_archive` reads the source ZIP without extracting or executing it.
The server now exposes **67 tools**. Donor directories are read-only and outputs
are created as new Mods directly inside the user's explicitly authorized
`WorkshopMods` folder. Builds remain `AUTHORED_UNVERIFIED` / `NOT_RUN`.

See [Authoring capabilities and delivery boundary](docs/JOKER_AUTHORING_BOUNDARY_ZH.md) and
[Authoring v0.3 contract](docs/AUTHORING_V0_3.md). The authoring compiler creates
new files independently; the legacy in-place Writer restrictions below remain.

Research tooling for reading Pummel Party Mod Editor PMH scene files.

The current milestone includes the PMH v1 Reader, Writer v0.1 for existing
`ModTransform` values, schema-driven Component Writer v0.2, Trigger Writer
v0.3 for confirmed fixed-length non-Action properties, and Action Inspector
v0.4a, Existing Action Field Writer v0.4b, read-only Action Reference
Inspector v0.4c, validated-template Prefab Reference Writer v0.4d, Stage 8
Action ecosystem evidence, Action Graph Writer v0.5, and the read-only Object
Reference Graph Inspector and safe leaf duplication v0.6b. The inspectors read Trigger Action Lists, resolve managed
references, classify observed reference fields, and detect Action classes.
The writers can patch four ordinary fields, replace one existing prefab slot,
and add/delete/move source-backed top-level Actions for eleven schema-registered
classes under narrow graph invariants.
Stage 10B GameObject duplication is unavailable because the official Ctrl+D
Oracle Hard Gate has not passed.

### Development

```text
python -m pip install -e .[test]
python -m pytest
```

The real-world regression fixture belongs at
`tests/fixtures/MainScene.scene`. Tests only read this file and verify that its
SHA-256 digest is unchanged for the full test session.

See [`docs/PMH_FORMAT.md`](docs/PMH_FORMAT.md) for the evidence level of each
documented field.

Writer safety invariants are documented in
[`docs/WRITER_SAFETY.md`](docs/WRITER_SAFETY.md).
The ordinary Component allowlist is documented in
[`docs/COMPONENT_SCHEMAS.md`](docs/COMPONENT_SCHEMAS.md).
The Trigger layout and permanent Action boundary are documented in
[`docs/TRIGGER_FORMAT.md`](docs/TRIGGER_FORMAT.md).
The read-only Action framing and observed class catalog are documented in
[`docs/ACTION_FORMAT.md`](docs/ACTION_FORMAT.md) and
[`docs/ACTION_CATALOG.md`](docs/ACTION_CATALOG.md).
Reference shapes, resolution evidence, and unknown semantics are documented in
[`docs/ACTION_REFERENCES.md`](docs/ACTION_REFERENCES.md).
The existing-item prefab replacement proof is documented in
[`docs/PREFAB_REFERENCE_WRITER.md`](docs/PREFAB_REFERENCE_WRITER.md).
The targeted mutation proof is documented in
[`docs/ACTION_WRITER_SAFETY.md`](docs/ACTION_WRITER_SAFETY.md).
Audio/effect and target evidence is documented in
[`docs/AUDIO_EFFECT_REFERENCES.md`](docs/AUDIO_EFFECT_REFERENCES.md) and
[`docs/ACTION_TARGETS.md`](docs/ACTION_TARGETS.md). The game assembly's
authoritative Action class definitions, enum numerics, and target-selection
semantics are documented in
[`docs/ASSEMBLY_TRUTH.md`](docs/ASSEMBLY_TRUTH.md). The read-only census of
shipped minigame scenes, Action templates, and assets is documented in
[`docs/BUILTIN_MOD_CENSUS.md`](docs/BUILTIN_MOD_CENSUS.md). The configured
read-only built-in asset catalog is documented in
[`docs/BUILTIN_ASSET_TOOLS.md`](docs/BUILTIN_ASSET_TOOLS.md). Action graph mutation is documented in
[`docs/ACTION_GRAPH_WRITER.md`](docs/ACTION_GRAPH_WRITER.md).
Object graph evidence and the stopped duplication research are documented in
[`docs/OBJECT_REFERENCE_GRAPH.md`](docs/OBJECT_REFERENCE_GRAPH.md) and
[`docs/GAMEOBJECT_DUPLICATION.md`](docs/GAMEOBJECT_DUPLICATION.md).

### Quick start

Install the project, then use the registered `pummelmcp` command:

```text
python -m pip install -e .

pummelmcp summary MainScene.scene
pummelmcp objects MainScene.scene --query PlayerSpawn
pummelmcp object MainScene.scene PlayerSpawn_0
pummelmcp transform MainScene.scene PlayerSpawn_0
pummelmcp set-position MainScene.scene PlayerSpawn_0 --x 8
pummelmcp set-position MainScene.scene PlayerSpawn_0 --x -3 --dry-run
pummelmcp validate MainScene.scene
pummelmcp research diff-action BEFORE.scene AFTER.scene OBJECT COMPONENT OnHitActions --rid 1000 --json
```

Every major command accepts `--json`. Object identifiers may be a GUID, an
absolute hierarchy path such as `/Player Spawnpoints/PlayerSpawn_0`, or a
unique object name. Ambiguous names are rejected.

The CLI adds only the read-only controlled Action diff command. Action
inspection and safe mutation are available through the Python service/core and
MCP server. Oracle-constrained leaf duplication supports standard
Transform-only leaves and the exact
`ModTransform + ModBoxCollider + ModTrigger` shape when all four Action graphs are
empty. The Blender scene-import pipeline can also create bounded `ModProp`
objects backed by validated Mod-local OBJ, texture, and material assets. See
[`docs/BLENDER_SCENE_IMPORT.md`](docs/BLENDER_SCENE_IMPORT.md). Arbitrary
GameObject/Component creation, arbitrary Prefab component editing, and
arbitrary property/Action JSON writes remain unsupported.

### MCP Server

PummelMCP exposes 67 MCP tools. The minigame configuration tools read, plan, and
apply allowlisted changes to existing `ModSettings.json` and
`MinigameDefinitionData.json` files. `plan_minigame_v2` and
`build_minigame_v2` combine typed trigger rules, Scene construction, and game
settings in a staged copy of the source Mod. See
[`docs/ONE_SENTENCE_MINIGAME_ROADMAP.md`](docs/ONE_SENTENCE_MINIGAME_ROADMAP.md).
The v0.2 request format is documented in
[`docs/MINIGAME_SPEC_V0_2.md`](docs/MINIGAME_SPEC_V0_2.md).
`plan_prefab_pack` and `build_prefab_pack` add a narrow Prefab asset path: clone
existing PMH Prefabs and metadata into a new Mod, remap dependencies between
the clones, optionally add an Item pickup score Action, and retarget an existing
`ModSpawner`. See
[`docs/PREFAB_PACK_V0_1.md`](docs/PREFAB_PACK_V0_1.md).
Phase 2 adds a read-only built-in minigame
asset catalog, and Stage 16 adds the built-in Asset Browser catalog plus
`spawn_builtin_prop`, which creates a real `ModProp` object from a registered
built-in asset without copying an existing in-scene object. Stage 15 retains its
strict whole-minigame prototype planner while Stage 14 retains its manual-start
runtime evidence pipeline while keeping the earlier inspection, Writer,
Template Factory, and Gameplay Composer tools compatible:

Phase 4 provides four Minigame archetypes: the original prefab-spawn challenge
plus score-pad, death-penalty, and trigger-feedback prototypes. Their recipes
use fixed Phase 3 validated-template Action sequences. The separate v0.2 path
supports a bounded set of Action choices and scalar fields on a trigger cloned
from an existing template. See
[`docs/MINIGAME_ARCHETYPES.md`](docs/MINIGAME_ARCHETYPES.md) and
[`docs/GAMEPLAY_RECIPES.md`](docs/GAMEPLAY_RECIPES.md).

- `get_scene_summary`
- `get_minigame_config`
- `plan_minigame_config_update`
- `apply_minigame_config_update`
- `plan_minigame_v2`
- `build_minigame_v2`
- `plan_prefab_pack`
- `build_prefab_pack`
- `get_builtin_asset_catalog_summary`
- `search_builtin_assets`
- `get_builtin_asset`
- `list_editor_assets`
- `get_editor_asset`
- `spawn_builtin_prop`
- `plan_blender_scene_import`
- `build_blender_scene_import`
- `list_scene_objects`
- `get_object`
- `get_components`
- `get_transform`
- `set_transform`
- `validate_scene`
- `get_component_property`
- `inspect_action_list`
- `inspect_action_references`
- `replace_prefab_reference`
- `set_action_field`
- `set_component_property`
- `add_action`
- `delete_action`
- `move_action`
- `inspect_reference_graph`
- `inspect_object_references`
- `analyze_duplication_safety`
- `plan_gameobject_duplication`
- `duplicate_gameobject`
- `inspect_runtime_capabilities`
- `plan_playtest_session`
- `start_playtest_session`
- `collect_playtest_evidence`
- `evaluate_playtest`
- `list_minigame_archetypes`
- `plan_minigame`
- `build_minigame`

The server uses stdio only. Before starting it, configure the narrowest folder
that contains the `.scene` files an MCP host should be allowed to access:

```powershell
$env:PUMMELMCP_ALLOWED_ROOT = "C:\Users\<user>\AppData\LocalLow\Rebuilt Games\Pummel Party\WorkshopMods"
$env:PUMMELMCP_BUILTIN_ASSET_ROOT = "D:\SteamLibrary\steamapps\common\Pummel Party\PummelParty_Data\StreamingAssets"
$env:PUMMELMCP_EDITOR_ASSET_ROOT = "D:\SteamLibrary\steamapps\common\Pummel Party\PummelParty_Data\StreamingAssets"
$env:PUMMELMCP_IMPORT_ROOT = "D:\PummelImports"
pummelmcp-mcp
```

The allowed root is required. Every requested path is resolved and must be an
existing regular `.scene` file whose final path remains inside that root. This
policy applies to read and write tools alike; the server never defaults to an
entire drive or user profile.

`PUMMELMCP_BUILTIN_ASSET_ROOT` is optional and used only by the three read-only
built-in asset tools. It must point to `StreamingAssets`; those tools scan
`InbuiltMods` and `WorkshopTemplates/Minigames` while excluding Boards. They
return contained relative paths and cannot modify or import assets.

`PUMMELMCP_EDITOR_ASSET_ROOT` is optional and used by `list_editor_assets`,
`get_editor_asset`, and `spawn_builtin_prop`. It must point to a
`StreamingAssets` copy containing the `aa` Addressables build, and it defaults
to `PUMMELMCP_BUILTIN_ASSET_ROOT` when unset. Those tools decode the
`ModAssetList` registry the Asset Browser itself lists, so callers reference
built-in assets by name or contained relative path only — never by GUID, raw
bytes, or a serialized reference. See
[`docs/EDITOR_ASSET_TOOLS.md`](docs/EDITOR_ASSET_TOOLS.md).

`PUMMELMCP_IMPORT_ROOT` is required for Blender scene imports and must contain
the exported `pummel_scene.json`, OBJ, MTL, and texture files. The two-phase
`plan_blender_scene_import` / `build_blender_scene_import` workflow copies only
validated sources from this root into the target Mod, creates their metadata
and material references, and places each object using the manifest transform.
See [`docs/BLENDER_SCENE_IMPORT.md`](docs/BLENDER_SCENE_IMPORT.md).

Because stdio is a machine-to-machine protocol, directly running
`pummelmcp-mcp` normally produces no human-readable stdout and keeps waiting
for an MCP host. Logs and configuration errors go to stderr so they cannot
corrupt protocol messages.

All mutation tools delegate to the shared service/core safety pipeline. They
retain partial-member updates, dynamic source spans, minimal byte patching,
validation, backup creation, same-directory temporary files, and atomic
replacement. No raw offset/bytes, force-write, or skip-validation option is
exposed. `ModText.Text` remains read-only. The generic Component mutation tool
also keeps `ModProp.customMaterials` read-only; only the bounded Blender import
builder writes that list while creating a new `ModProp`.

`ModProp.prop` remains outside the schema-driven write allowlist. It can still be
replaced through the existing same-scene template path
(`set_component_property` with the `replace_prop_reference_from_template`
mapping), and `spawn_builtin_prop` adds a second route that resolves the
reference server-side from the decoded Asset Browser registry and writes the
whole new object atomically.

Trigger Writer v0.3 reuses the two generic Component tools. For example, an
Agent can read or modify `ModTrigger.Radius`, or enable
`ModTrigger.OneUsePerPlayer` with a strict boolean:

```text
get_component_property(..., component_identifier="ModTrigger", property_name="Radius")
set_component_property(..., component_identifier="ModTrigger", property_name="Radius", value=2.5)
set_component_property(..., component_identifier="ModTrigger", property_name="OneUsePerPlayer", value=true)
```

`get_component_property` continues to return only a length/hash summary for
`OnHitActions`, `OnEnterActions`, `OnExitActions`, and `OnStayActions`. An
Agent can request the structured, paginated read-only view separately:

```text
inspect_action_list(
    ...,
    component_identifier="ModTrigger",
    event_property="OnHitActions",
    offset=0,
    limit=20
)
```

The response preserves Action order, resolves `rid` through `RefIds`, reports
namespace/class and bounded fields, and includes payload SHA-256 plus full
consumption status. It also reports field-level write metadata without changing
the existing field value shape. Core retains lossless raw data, but MCP never
emits raw bytes or full raw JSON.

After selecting an Action `rid`, an Agent can inspect its reference-like fields
without exposing raw reference data:

```text
inspect_action_references(
    ...,
    component_identifier="ModTrigger",
    event_property="OnHitActions",
    action_rid=1000,
    offset=0,
    limit=10
)
```

The response distinguishes prefab lists, audio objects, effect enums, target
lists/flags, and unknown reference shapes. It includes exact field/item/leaf
spans, read-only fingerprints, and exact-GUID-only asset resolution. Resolved
asset paths are relative to the contained Mod root. Target flags and effect
enum values are cross-referenced to assembly-truth evidence.

`replace_prefab_reference` replaces one existing `m_prefabs[index]` using a
complete, already-observed template selected by exact GUID or exact contained
`.pfab` relative path. It supports payload-hash and current-index GUID guards.
It cannot resize or reorder the list, construct an unseen reference, modify an
asset, or write audio, effect, target, or Action graph data.

For a source-backed cross-Scene workflow, it also accepts an allowlisted
`template_scene_path` paired with the exact `template_reference_sha256`. The
template Scene must be inside the current Mod, and the selected Prefab plus
metadata hashes are revalidated before writing the target Scene.

PummelMCP can safely change these already-present fields of an existing
`ModSystem.Logic.SpawnPrefabAction`:

- `m_spawnAtPosition`
- `m_parentToTarget`
- partial `m_position.x/y/z`
- partial `m_rotation.x/y/z`

For example, after inspecting and retaining the returned payload hash:

```text
set_action_field(
    ...,
    event_property="OnHitActions",
    action_rid=1000,
    expected_class="SpawnPrefabAction",
    field_name="m_spawnAtPosition",
    value=true,
    expected_payload_sha256="..."
)

set_action_field(
    ...,
    event_property="OnHitActions",
    action_rid=1000,
    expected_class="SpawnPrefabAction",
    field_name="m_position",
    value={"x": 5.0},
    expected_payload_sha256="..."
)
```

`set_action_field` cannot create, delete, duplicate, replace, or reorder Actions; change Action
types, `rid`, `RefIds`, namespace, class, assembly, or type tags; or modify
`m_prefabs`, target/reference fields, audio/effect references, or unknown
fields. Phase 3 can clone complete validated templates for additional classes,
but their individual fields remain read-only. General Prefab asset mutation,
generic prefab-list editing, and
arbitrary GameObject/Component creation and deletion remain out of scope. The
separate `duplicate_gameobject` tool accepts only a parented leaf in one of two
exact Oracle-approved shapes: a standard `ModTransform` alone, or ordered
`ModTransform`, `ModBoxCollider`, `ModTrigger` with four empty Action graphs. Unknown
references and all other shapes are rejected.

After inspection, v0.5 plus Phase 3 can clone one exact, already-present
template for `SpawnPrefabAction`, `KillAction`, `ChangeScoreAction`,
`SpawnEffectAction`, `PositionAction`, `RotationAction`, `SetPlacementAction`,
`PlaySoundAction`, `ShowMessageAction`, or `SetPlayerVisualAction`. The server chooses the new rid; the
Agent cannot:

```text
add_action(
    ...,
    event_property="OnHitActions",
    action_class="SpawnPrefabAction",
    insert_index=1,
    expected_payload_sha256="..."
)

move_action(..., event_property="OnHitActions", action_rid=1001, new_index=0,
            expected_payload_sha256="...")
delete_action(..., event_property="OnHitActions", action_rid=1001,
              expected_payload_sha256="...")
```

Creation is allowed only when the existing Action and RefId rid sequences
exactly match the confirmed property-local `1000+n` pattern. Templates contain
complete source-backed managed-reference JSON and must pass the registered
class/type/field/value rules; `SpawnPrefabAction` additionally requires existing
prefab slots. Empty existing ActionLists are supported when an unambiguous
`managed_reference_sha256` from `inspect_action_list` is supplied as
`template_sha256`. Delete checks dependencies; move changes only execution
order and preserves RefIds and managed-reference bytes.

No arbitrary JSON Action creation, unknown or Board-only Action creation,
caller-selected rid, raw RefId mutation, nested graph mutation, general Prefab file Writer, arbitrary
GameObject/Component creation, subtree/root/populated-Trigger duplication, deletion,
Computer Use, or automatic playtest exists.

For Codex MCP workflows, call `plan_gameobject_duplication` first. A safe plan
reports generated preview UUIDs, per-Component source spans and policies, append
position, and estimated mutations without writing. `duplicate_gameobject` repeats all safety
checks against current bytes, optionally verifies `expected_scene_hash`, writes and
parses a same-directory temporary Scene, validates identity/hierarchy/source
preservation/reference policy, creates a backup, and uses atomic replacement.

### Bundled minigame starter templates

Complete templates live in `src/pummelmcp/templates/` and are distributed with GitHub clones and Python packages, independently of the original author's game paths. `catalog.json` includes file integrity checks.

- `minimal-third-person`: basic third-person game.
- `minimal-top-down`: basic top-down game.
- `simple-arena`: simple arena.
- `third-person-obstacle-course`: third-person obstacle course with item Prefabs.
- `third-person-shooter`: third-person shooter with weapon Prefabs.

Production workflow:

1. After a user requests a game/minigame, first ask for the absolute path to their existing `WorkshopMods` folder and explicit permission to create a new Mod folder and files there. Do not call minigame inspection, planning, initialization or production MCP tools until both are provided.
2. Record the directly supplied path and explicit write authorization with `authorize_workshop_directory`. The folder must exist, be named `WorkshopMods`, and be writable. Authorization applies only to the current MCP session.
3. After authorization, assess capabilities and plan read-only. Match every requested rule against actual components/Actions, mark it supported, equivalent or unsupported, and explain evidence and limitations. Never invent script APIs.
4. For the first creation of a game only, submit the complete flow, rules/parameters, scene/assets, limitations/alternatives and playtest checks through `submit_gameplay_review`. Show the exact returned plan to the user and wait for explicit approval; then call `approve_gameplay_review` with the matching `review_sha256` and the user's actual reply. Directory permission, the initial request, Agent inference and silence do not replace first-creation review. A changed plan must be reviewed again before first creation.
5. Initialize/build only after first-creation approval. Create a unique new folder and bind it as the current game directory. Later requested rule changes, features and scene edits apply directly to the original files without another gameplay review. Capability checks, validation and directory authorization still apply. An optional iteration proposal returns `ITERATION_READY` and does not suspend writes.
6. Continue editing the bound original directory: read current saved files, preserve user edits and publishing identity, and use existing writers. Never regenerate a new game version to replace the delivered Mod. Transactional temporary files and backups may support validated bounded edits. Set `user_requested_separate_game=true` only when the user explicitly asks for a separate game; its first creation still requires a complete review. Do not use this flag to bypass the user's intent.

`get_game_workflow_status` reports the authorized folder, first-creation proposal, bound game directory and review policy. State is session-local. After restarting, record directory authorization again; resuming an initialized Mod automatically binds its original folder without repeating first-creation review. An existing Mod must contain `MainScene.scene`, `Meta.json` and `ModSettings.json`; empty or incomplete directories do not count as delivered games. Tools record the Agent's statement of user authorization; they cannot independently authenticate the human or prove text originated from the user. The Agent cannot approve first creation itself. Restart the MCP server after source changes to load new rules and descriptions.

### Proactive asset sourcing

During planning and production, the Agent must actively use available online search tools to find assets matching gameplay, visual style and performance needs, without waiting for the user to supply each asset. Seek sound effects, background music/BGM, OBJ models, textures and materials as needed. Inspect existing Mod and built-in assets first to avoid duplicate imports. Prefer assets with clear reuse and redistribution permission, such as CC0 or appropriately attributed CC-BY, and respect user constraints; purchases require explicit user authorization. Record source URLs, creators, licenses, attribution requirements and modifications in the asset plan and the delivered Mod's CREDITS/asset manifest. Check actual formats, model scale/complexity, texture/material dependencies and audio compatibility before import. Convert with available tools when needed and import through supported workflows into authorized directories. If online access, licensing or conversion is unavailable, explain the limitation and use suitable built-in assets or clearly identified placeholders. Never invent downloads, sources or licenses. This rule uses the host Agent's search/download/conversion capabilities; it does not add online downloading to the MCP server itself.

Keep bundled templates as starting points and place delivered Mods in a separate directory. Static checks and initialization do not prove runtime playability. Templates originate from the user-provided game directory; the project's code LICENSE does not grant rights to third-party game assets.
