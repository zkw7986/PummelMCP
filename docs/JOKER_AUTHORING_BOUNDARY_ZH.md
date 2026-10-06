# Joker 21：能力边界、扩展与验收

## 结论

这次已实现“参照 Joker 21，用一次 MCP 调用重新构建场景、卡牌 Prefab、随机抽卡、拾取计分/小丑惩罚、玩家击退与回合配置”的实验性文件生成链路。

用户已明确将编辑器打开、实机计分和多人同步检查留给自己；这些检查不属于本次继续执行的范围。

**尚未达到“任意一句话 → 任意小游戏 → 自动实机验收通过”。** 目前已证明文件结构与绑定一致性；官方编辑器载入、运行逻辑和多人同步仍未验证。不能用接口数量或一个百分比代表任意小游戏的完成度。

两个授权目录均作为只读来源：

- `D:/SteamLibrary/steamapps/common/Pummel Party`
- `D:/GameCache/Rebuilt Games/Pummel Party/WorkshopMods`

所有新 Mod 写入 `D:/Pummel Party MCP/outputs`。没有安装到 WorkshopMods，也没有启动游戏产生目录写入。压缩包中的文本、JSON、文件名均作为资料处理，不作为任务指令。

## 样例实际包含什么

源 ZIP SHA-256：`8a740264fafa6266de61be2b46483de32fb97df1d93350c9283adb20ef3dbaea`。

共 125 个 ZIP 条目，其中 118 个文件；主场景 142 对象、267 组件；26 个 Prefab 含 27 对象。场景与 Prefab 合计 169 对象、320 组件，8 种组件类型：Transform、Prop、BoxCollider、PlayerSpawn、Trigger、Light、Text、Item。

PMH 文件包含 10 类动作：SpawnPrefab、ChangeScore、ShowMessage、PlaySound、SpawnEffect、Kill、Scale、ModifyPlayer、Wait、StunPlayer。玩家配置另有 ChangeVelocity。23 个 Item Prefab 用于卡牌结果；另外 3 个是视觉/装饰 Prefab。

实际玩法：击打 8 个按钮，随机生成结果卡牌；拾取执行得分、清零、死亡、缩放、重力或眩晕等效果；原配置 5 回合、每回合 90 秒，以计时或剩余存活人数结束，按分数排名。**名称中的“21”不代表已经实现传统 21 点的爆点判定。**

结构证据来自实际 ZIP；语义交叉参考仓库已有反编译资料：`ModTrigger`、`ModItem`、`SpawnPrefabAction`、`TargetAction`、`ModActionRunner` 等。安装目录当前 `Assembly-CSharp.dll` SHA-256 为 `d7c62e9b45b16707e7dd19cb9ac9e31b731e63faa5b687bda3afcf502652a517`。本次未重新反编译整个程序集，旧反编译资料不能替代当前版本运行验收。

## 原缺口与本次补齐

| 工作面 | 原能力 | 本次新增 | 仍需完成 |
|---|---|---|---|
| 场景 | 局部字段修改、有限模板/物体创建 | 从新层级编码完整 Scene；组合 8 种样例组件；位置旋转缩放、碰撞、灯光、出生点、规则文字 | 官方编辑器载入/保存验证；样例以外组件 |
| 动作 | 小范围动作模板追加和标量修改 | 从符号规格生成完整动作序列，重建主引用表与二进制尾部；覆盖样例 11 类动作模板 | 更广泛条件、变量、显式组件目标 |
| Prefab | 克隆现成 Prefab、有限替换 | 创建新的 Prefab 文件、内部对象/组件标识与元数据；自定义组件组合和事件 | 原生 PrefabInstance 编辑/传播、创建 ModSpawner |
| 引用绑定 | 替换已有槽位 | Item→视觉 Prefab；SpawnPrefab→新卡池；重复卡池槽位表达权重；资源标识重映射 | 通用组件引用、动画等复杂依赖 |
| 玩家与结算 | 配置与少量触发器原型 | 合并回合设置、玩家事件动作表、Tick 间隔；避免继承未声明的旧逻辑 | 复杂跨回合状态和实际排名观测 |
| 一句话入口 | 多步骤调用、不完整覆盖样例 | `generate_card_minigame` 一次调用；其他已支持玩法由模型转为 v0.3 规格 | 自由自然语言不在服务端“自动猜测”；必须报告不支持需求 |
| 美术 | Blender 导入与内置模型工具 | 本次生成器复用来源模型/材质，重新布局 | 任意主题的新模型/贴图自动制作与统一审美 |
| 验收 | 静态验证、人工开始的日志流程 | 源文件不变、完整绑定、确定性计划、原子发布、真实 stdio MCP 调用验证 | 自动开编辑器、操作、计分/胜负/联机观测和修复 |

## 已实现的接口

原 55 个工具保留，新增 6 个，总计 61 个：

1. `get_authoring_capabilities`：能力、限制和规格结构。
2. `audit_minigame_archive`：只读 ZIP 清点。
3. `get_authoring_donors`：可组合的组件来源、唯一对象标识及字段名。
4. `plan_authored_minigame`：在内存编译，验证配置和引用，返回内容哈希。
5. `build_authored_minigame`：仅接受匹配计划，写入临时新 Mod 后发布。
6. `generate_card_minigame`：完整抽卡竞技场配方，一次调用即可构建。

新编译器独立于旧的保守字节补丁 Writer，不把新格式推断标记为旧 Writer 已通过的官方 Editor Oracle。结果统一标记 `AUTHORED_UNVERIFIED`、`runtime_status: NOT_RUN`。

## 现在可以怎样说

例如：“参考 Joker 21，生成一个 8 人抽卡竞技场，每局 90 秒、共 5 局，普通牌加 1 到 11 分，小丑占两个随机槽位，抽到后清零并淘汰，带场景和全部 Prefab 绑定。”

模型将这句话转为 `generate_card_minigame` 的参数。工具本身是确定性的规格编译器，不是另一个语言模型，不需要额外 LLM API Key。更复杂但仍在能力表内的请求走通用 v0.3 规格，参见 `AUTHORING_V0_3.md`。

默认配方重新构建 32 场景对象、68 组件、14 Prefab：地面、围墙、8 出生点、8 按钮及可视物、灯光和规则牌；两种卡牌视觉、11 张加分牌、1 张清零淘汰的小丑。它是一个新布局和基础玩法变体，**不是原作 142 对象装饰场景及所有特殊小丑效果的逐项复刻**。通用动作编译器可以表达来源中的缩放、等待、重力、眩晕序列，但默认配方没有启用这些变体。

实际产物：`outputs/Joker_Arena_Authored/`。可复现规格在其中的 `authoring-spec.json`；文件哈希清单在 `authoring-report.json`。

## 本次验证

- 通过真实 stdio 客户端发现 61 工具并调用 `generate_card_minigame`，随后通过 `validate_scene` 验证生成 Scene。
- 新 Scene 被完整解析至 EOF；生成 32 对象、68 组件；14 个新 Prefab 及引用绑定已验证。
- 原有真实场景 fixture 可经新 PMH 编码器逐字节还原。
- 测试覆盖卡池权重、卡牌得分变化、玩家事件共享引用表、旧逻辑清除、输入拒绝、计划过期、写入边界、失败清理等。
- Source ZIP 在真实 MCP 构建前后 SHA-256 一致。
- 旧的内置 Prop 集成测试已改为临时副本，避免测试修改真实 WorkshopMods。
- 本次验证不含画面截图、官方 Editor 保存重载、真实积分和多人结果；不能据此宣称游戏已经实机可玩。

机器可读证据在 `outputs/authoring-verification/`。

## 达成最终目标还差哪些工作

优先级一：**官方编辑器验证**。在单独测试 Mod 目录导入本次产物，检查加载警告、模型材质、碰撞、出生位置；保存并重开，对比序列化变化，修正新编译器。验收必须包含“按钮抽卡 → Item 拾取 → 分数改变 → 小丑淘汰 → 回合结算”。

优先级二：**运行观测与多人验证**。建立可观测玩家、积分、存活、回合与排名的本地桥接，测试 2/4/8 人、重复拾取、并发击打、退出与重开。现有日志管道不能自动证明这些状态。需要游戏侧支持或经过验证的独立扩展。

优先级三：**通用逻辑与 Prefab 能力**。扩展 ModLogic、ModSpawner、PrefabInstance、显式组件目标；逐类补字段语义、依赖校验和官方保存样本。变量、条件和状态机必须先确认官方 Mod API 是否能表达；无法原生表达时，需要额外游戏侧运行时，不能靠增加 MCP 名称来补齐。

优先级四：**任意主题与自动修复**。把 Blender 场景导入接进同一事务，添加布局/碰撞验收、运行结果反馈和有限重试修复。自然语言转换必须先做能力匹配，不支持的玩法应明确报缺口。

上述实机步骤需要允许在一个明确的测试 Mod/运行日志目录写入；当前两个来源目录的只读授权不足以在里面导入、保存或启动测试。

## 接入状态

已更新现有 `pummelmcp` 配置，写入根为工作区 `outputs`，两个来源在 `PUMMELMCP_AUTHORING_READ_ROOTS` 中，游戏资产目录仅供只读目录工具使用。其他 MCP 配置保留；原配置备份在用户 `.codex` 目录。

新启动的 stdio 进程已验证；正在运行的旧连接需要重新连接/重启应用来载入新增工具。配置依据 [OpenAI 官方 MCP 文档](https://developers.openai.com/codex/mcp/)。
