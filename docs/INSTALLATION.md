# 安装与连接 / Installation and connection

## 中文

初版为实验性版本，需要 Python 3.10 或更新版本、支持 stdio MCP 的 AI 客户端，以及自己安装的 Pummel Party。仓库不包含完整游戏资源、作者的个人小游戏、素材库或本机 Python 环境。

### Windows 安装

在 PowerShell 中运行；也可从 GitHub 下载 ZIP，解压后进入项目目录再创建环境：

```powershell
git clone https://github.com/zkw7986/PummelMCP.git
cd PummelMCP
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install .
```

### 连接 MCP 客户端

在客户端的 MCP 配置中注册下列 stdio 服务。以下是常见 JSON 配置形式；各客户端配置入口及格式可能不同。把所有示例路径替换为自己电脑上的真实绝对路径。`WorkshopMods` 和素材导入目录须已存在。

```json
{
  "mcpServers": {
    "pummelmcp": {
      "command": "C:/Projects/PummelMCP/.venv/Scripts/python.exe",
      "args": ["-m", "pummelmcp.mcp_server"],
      "env": {
        "PUMMELMCP_ALLOWED_ROOT": "C:/Users/YOUR_NAME/AppData/LocalLow/Rebuilt Games/Pummel Party/WorkshopMods",
        "PUMMELMCP_BUILTIN_ASSET_ROOT": "C:/Program Files (x86)/Steam/steamapps/common/Pummel Party/PummelParty_Data/StreamingAssets",
        "PUMMELMCP_EDITOR_ASSET_ROOT": "C:/Program Files (x86)/Steam/steamapps/common/Pummel Party/PummelParty_Data/StreamingAssets",
        "PUMMELMCP_IMPORT_ROOT": "C:/PummelImports",
        "PUMMELMCP_AUTHORING_READ_ROOTS": "[\"C:/Users/YOUR_NAME/AppData/LocalLow/Rebuilt Games/Pummel Party/WorkshopMods\"]"
      }
    }
  }
}
```

- `PUMMELMCP_ALLOWED_ROOT`：允许场景工具访问的范围，通常是自己的 `WorkshopMods`。
- `PUMMELMCP_BUILTIN_ASSET_ROOT` / `PUMMELMCP_EDITOR_ASSET_ROOT`：自己安装的游戏资源目录。内置资产功能需要；无需复制到仓库。
- `PUMMELMCP_IMPORT_ROOT`：模型、贴图及 Blender 导入清单所在目录。
- `PUMMELMCP_AUTHORING_READ_ROOTS`：来源 Mod / ZIP 的只读目录，以 JSON 数组字符串表示。完整文件 v0.3 制作需要兼容来源，随包模板初始化不要求额外 ZIP。

目录授权环境变量不代替人类授权。开始制作时，AI 仍须记录用户提供的 `WorkshopMods` 路径、明确写入授权及首次游戏方案审核。初始化后在原 Mod 目录持续修改。

可以先让 AI 列出五个初始化模板、检查实际编辑器能力，再制作一个简单得分区域小游戏。初版不保证覆盖编辑器全部功能，也不会自动运行游戏；请在游戏内打开、试玩并验证。素材搜索依赖 AI 客户端提供的联网能力。

### 验证与排错

```powershell
.\.venv\Scripts\python.exe -m pummelmcp.cli --help
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m pytest
```

直接运行 MCP 服务会等待客户端，不打印交互菜单，这是正常行为。配置错误在 stderr。修改客户端配置后重新连接服务。

普通测试不需要个人素材库。真实游戏资源、官方编辑器对照样例和来源 ZIP 的集成测试属于可选检查，没有对应输入时跳过。可按需设置 `PUMMELMCP_EDITOR_ASSET_ROOT`、`PUMMELMCP_E2E_MOD_SCENE`、`PUMMELMCP_TEST_DONOR_ARCHIVE` 或 `PUMMELMCP_ORACLE_ROOT`。

## English

This initial release is experimental. It requires Python 3.10 or newer, an AI client supporting stdio MCP, and your own Pummel Party installation. The repository does not include the full game assets, the author's personal minigames, asset library or Python environment.

### Windows installation

Run in PowerShell. Alternatively, download the GitHub ZIP, extract it, and enter the project directory before creating the environment:

```powershell
git clone https://github.com/zkw7986/PummelMCP.git
cd PummelMCP
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install .
```

### MCP client connection

Register a stdio server in your client's MCP settings using the JSON example in the Chinese section above. Client configuration locations and formats vary. Replace every sample path with an actual absolute path on your machine. The `WorkshopMods` and import directories must already exist.

- `PUMMELMCP_ALLOWED_ROOT`: allowed scene access scope, normally your `WorkshopMods` directory.
- `PUMMELMCP_BUILTIN_ASSET_ROOT` / `PUMMELMCP_EDITOR_ASSET_ROOT`: assets from your own game installation, needed for built-in asset features. Do not copy these into the repository.
- `PUMMELMCP_IMPORT_ROOT`: models, textures and Blender import manifests.
- `PUMMELMCP_AUTHORING_READ_ROOTS`: read-only donor Mod / ZIP directories encoded as a JSON array string. Full-file v0.3 authoring needs compatible donors; bundled starter initialization does not require an additional ZIP.

Environment configuration does not replace human authorization. Before production, the Agent must record your WorkshopMods path, explicit write permission and the first game's reviewed plan. Subsequent edits continue in the original Mod folder.

Start by asking the Agent to list the five starter templates, inspect actual capabilities and create a simple score-pad minigame. The initial release does not cover every editor feature or automatically launch/playtest the game. Open and test the Mod in the game. Online asset sourcing relies on the AI client's network tools.

### Verification and troubleshooting

```powershell
.\.venv\Scripts\python.exe -m pummelmcp.cli --help
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m pytest
```

A directly launched MCP server waits for its client without an interactive menu. Configuration errors go to stderr. Reconnect the server after changing client settings.

Ordinary tests do not require the author's assets. Integration checks using real game resources, editor comparison fixtures or donor ZIPs are optional and skip when inputs are unavailable. Configure `PUMMELMCP_EDITOR_ASSET_ROOT`, `PUMMELMCP_E2E_MOD_SCENE`, `PUMMELMCP_TEST_DONOR_ARCHIVE` or `PUMMELMCP_ORACLE_ROOT` as needed.
