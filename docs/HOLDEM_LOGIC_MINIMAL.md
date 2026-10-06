# Pummel Hold'em 最小逻辑原型

安装目标：`D:\GameCache\Rebuilt Games\Pummel Party\WorkshopMods\test`。
此版本只做一手固定牌、一次选择窗口；不是完整德州扑克。

## 已制作

- 4 个固定出生座位，隔墙隔开；暂时禁用跳跃、拳击。
- 绿色牌桌、桌腿、牌面、灯光、座位和三色选择区，使用游戏内置几何体与文字。
- 红色 FOLD 弃牌；蓝色 CALL 跟注；金色 ALL-IN 立即扣 5 分。
- 第一次选择后关闭该座位所有按钮；不选择默认 CALL。
- 开局约 2 秒开放选择，10 秒锁定并翻牌，14 秒转牌，18 秒河牌，20 秒结算，35 秒结束。
- 私牌提示发送给碰撞触发者所属客户端；公共牌按阶段出现。

公共牌：QS TS 4D JS 2H。S 黑桃、H 红桃、D 方块、C 梅花。

| 座位 | 私牌 | 牌型 | CALL / 超时 | ALL-IN 净分 | FOLD |
|---|---|---|---:|---:|---:|
| P1 | AS KS | 同花 | 30 | 40 | 0 |
| P2 | QH QC | 三条 | 20 | 23 | 0 |
| P3 | 8D 7D | 高牌 | 5 | -5 | 0 |
| P4 | AH JC | 一对 J | 10 | 3 | 0 |

ALL-IN 净分包含先扣的 5 分；结算增加分别为 45、28、0、8。
P4 根据实际牌面是一对 J，并非顺子。分数来自预设规则，不运行扑克牌比较算法。

## 边界与验证

座位归属依赖固定出生点及物理隔离，尚未实现玩家 ID 条件过滤。
私牌消息的隐私以各自客户端为前提，同屏玩家共用界面不保证互相不可见。
没有动态洗牌、十套牌局、三手循环、多轮下注、筹码池或边池。
本次直接创建场景对象，无需新建 Prefab；已有 Prefab 编译功能保留。

文件检查覆盖 PMH 序列化、Transform 引用、计时顺序、旧模板玩家事件清除，
并对 256 种选择组合做静态模型检查（包括重复触发和超时）。
静态模型假设碰撞事件按引擎规则到达，不验证物理可达性、网络竞态或真实界面。
按用户要求没有启动编辑器、运行游戏或验证多人同步。

原模板的 Assets、发布元数据和预览保持原样，预览可能仍显示旧场景。
完整安装前备份及安装文件哈希见 `outputs/backups` 和 `outputs/holdem-install-report.json`。

## MCP 扩展

原有 authoring 工具新增受限的 ModLogic 单计时器、SetActiveAction 本地对象绑定。
不接受任意脚本、未知动作或旧 instanceID 引用；编译时绑定新 Transform GUID。
规范见 `AUTHORING_V0_3.md`，可复用规格为 `outputs/Holdem_Logic_Minimal/authoring-spec.json`。
证据取自用户模板、Joker 21、LuckyChamberTournament 和官方 Advanced Board，均只读。
