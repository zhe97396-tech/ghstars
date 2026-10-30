# ghstars

**把你的 GitHub 星标变成本地可检索的知识库。**

> [English README](README.md) · [使用指南（HTML）](docs/guide.zh.html) · [更新日志](CHANGELOG.md)

每个 star 的项目落成一个 Markdown 文件（Obsidian 友好），再建一个零依赖的 SQLite/FTS5 索引 ——
于是你能（你用的任何 AI Agent 也能）在毫秒级回答：**「我收藏过哪个能做 X 的项目？」**
中文、英文都行，不需要向量数据库，也不用下 240MB 的嵌入模型。

```bash
ghstars sync                 # 拉取 star → 写 Markdown → 建索引
ghstars search "视频制作"     # 中文场景标签能命中
ghstars search "rag pipeline" # 英文项目名也能命中
```

## 为什么不是又一个 star 列表

| | `gh` / GitHub 网页 | 书签管理工具 | **ghstars** |
|---|---|---|---|
| 按「它干什么」找项目（不是靠名字） | ✗ | 部分 | ✓ 中文场景标签 + 字段加权 |
| 离线、可 grep、可 git diff | ✗ | ✗ | ✓ 纯 Markdown |
| AI Agent 能用（CLI / MCP / skill） | ✗ | ✗ | ✓ 三个入口，一份内核 |
| 安装成本 | npm/pip | SaaS | **纯标准库**（拉取与检索） |

中文标签这件事对中文用户是刚需：GitHub 描述是英文，搜「视频剪辑」永远命不中
*"video editing toolkit"*。`ghstars` 让 LLM（可选）生成 3–5 个中文场景标签 + 2–3 句中文描述，
和英文原文并排存 —— **标签与描述互补，缺一都会有漏召回**。

## 安装

```bash
# 不安装直接试
uvx --from git+https://github.com/zhe97396-tech/ghstars ghstars --help

# 装成全局命令
uv tool install git+https://github.com/zhe97396-tech/ghstars

# 需要 MCP 时（可选）
uv tool install "ghstars[mcp] @ git+https://github.com/zhe97396-tech/ghstars.git"
# pip 等价写法
pip install "ghstars[mcp] @ git+https://github.com/zhe97396-tech/ghstars.git"
```

> 尚未发布到 PyPI，所以安装必须带 git 源。发布后 `pip install ghstars` 即可。

要求 Python ≥ 3.11（用到 `tomllib`），除此之外**零第三方依赖**。

## 快速开始

```bash
ghstars init                  # 生成配置模板
# Windows: %APPDATA%/ghstars/config.toml
# macOS/Linux: ~/.config/ghstars/config.toml
$EDITOR ~/.config/ghstars/config.toml

ghstars doctor                # 自检：token / 目录 / 索引 / LLM
ghstars sync                  # 首次全量同步
ghstars search "小红书" --json
```

token 查找顺序：`--token` → 配置里 `github.token_env` 指定的环境变量（默认 `GITHUB_TOKEN`）
→ `GITHUB_TOKEN` / `GH_TOKEN` → `gh auth token`
**如果已经 `gh auth login` 过，什么都不用配。**

## 配置

```toml
[store]
dir = "D:/Notes/github-stars"   # Markdown 落地目录（必填或用 --dir）
index = ""                      # 索引路径，空 = 配置目录/index.db

[github]
username = "your-github-username"
token_env = "GITHUB_TOKEN"
api_base = "https://api.github.com"

[llm]                            # 可选，不配就离线跑
base = "https://api.openai.com/v1"
model = "gpt-4o-mini"
api_key_env = "OPENAI_API_KEY"   # 推荐：key 放环境变量，不写进文件
timeout = 60

[categories]                     # 你自己的分类；键名 = 目录名
ai-agents = ["ai-agents", "agent", "agentic-ai"]
mcp-tools = ["mcp"]
rag = ["rag"]
tools-utilities = ["cli", "developer-tools", "tools"]
```

环境变量覆盖：`GHSTARS_STORE`、`GHSTARS_INDEX`、`GHSTARS_GITHUB_USERNAME`、
`GHSTARS_LLM_BASE`、`GHSTARS_LLM_KEY`、`GHSTARS_LLM_MODEL`、`GHSTARS_CONFIG`。

## 命令速查

| 命令 | 作用 |
|---|---|
| `sync` | 拉取 → 去重 → 分类 → 中文标签 → 写 Markdown → 归档取消 star → 重建索引 |
| `search "<词>"` | 名称/标签/topics/中文描述/英文描述 加权检索 |
| `get <owner/name>` | 单个项目详情（`--full` 打印完整 Markdown） |
| `list` | 按 star 数列出（`--category` 过滤） |
| `stats` | 分类计数、归档数、索引位置 |
| `export` | 导出 JSON / CSV |
| `index` | 重建 SQLite 索引 |
| `doctor` | 自检 Python/FTS5/token/目录/索引/LLM |
| `mcp` | 以 stdio MCP server 运行 |

所有命令都支持 `--json`。**进度日志走 stderr，结构化结果走 stdout** —— 可以安全地
`| jq` 或直接丢给 Agent 解析。

`sync` 常用参数：`--dry-run`（只报差异，不写盘不调 LLM）、`--limit N`、`--no-llm`、`--full`。

## 三种用法

### 1. CLI（任何能跑命令的 Agent 都能用）

```bash
ghstars search "剪视频" --json | jq -r '.results[].repo'
```

### 2. MCP（Claude Code / Codex / Hermes / Cursor…）

`ghstars mcp` 暴露 3 个**只读**工具：`stars_search`、`stars_get`、`stars_stats`。
写入（同步 / 重建索引）只走 CLI：`ghstars sync` / `ghstars index` —— 写路径只留一个入口，
护栏（exit 3 = LLM 全失败）也只有一处；MCP 没有退出码语义，容易把「标签全空」当成功。

```jsonc
// Claude Code ~/.claude.json、Cursor、其他通用客户端
{
  "mcpServers": {
    "ghstars": { "command": "ghstars", "args": ["mcp"] }
  }
}
```

```yaml
# Hermes: ~/.hermes/config.yaml
mcp_servers:
  ghstars:
    command: "ghstars"
    args: ["mcp"]
    timeout: 120
```

> 部分宿主会给 MCP 子进程**过滤环境变量**（Hermes 只传 PATH/HOME 等）。
> 这里不受影响：token 走 `gh auth token`，密钥从配置文件或 `env:` 显式传入。

### 3. Skill（给不会自己翻文档的 Agent 看）

把 `skills/ghstars/` 拷进宿主的 skill 目录（`~/.claude/skills/`、`~/.codex/skills/` 等）：

```bash
python scripts/sync_skill.py            # 自动探测已存在的宿主目录
python scripts/sync_skill.py --list     # 只看装到哪
```

skill 只教「什么时候调、怎么读结果」，行为全在 CLI 里 —— 三种入口永远不会给出不同答案。

## 工作方式

```
GitHub API（stars + README）
  → 按 repo 名去重，大小写不敏感
  → 分类：topics 映射 → 描述关键词 → LLM 从"已有目录"里选 → other
  → LLM：3–5 个中文场景标签 + 2–3 句中文描述
  → 每个项目一个 Markdown（frontmatter 承载全部检索字段）
  → SQLite 索引（派生缓存，可随时重建）
```

**Markdown 是唯一权威数据源。** 索引只是缓存：删掉它、`ghstars index` 重建，什么都不丢。
文件内容稳定，`git diff` 只显示真实变更，Obsidian 图谱也干净。

### 几个非显然的设计决定

- **排序不用 bm25。** 268 个项目的实测：bm25 对高频词（`agent`）返回 `-0.0`，所有命中并列，
  排序直接失效；再叠一层 LIKE 兜底就变成「兜底结果盖过真实命中」。这里改用显式字段加权
  （`repo 6 > title/tags 4 > topics/chinese 2 > 英文描述 1.5` + 精确命中加成），可解释、可预测。
- **2 字中文查询**（`简历`、`剪辑`）低于 trigram 的 3 字符下限，走 LIKE 兜底，**不需要 jieba**。
- **分类不发明新目录。** LLM 只能从已存在的分类里选，避免分类体系无限膨胀。
- **兼容性标签不算形态。** 裸 `claude-code` / `anthropic` 表示「兼容」，不代表「是个 Claude 技能」——
  这是历史误分类的最大来源。
- **归档幂等。** `type: github-star-archived` 整值匹配、只写一次；子串替换会造出
  `-archived-archived-...` 并破坏按 type 的权重表。

## 隐私

- **无遥测、无云端。** 唯一的网络请求是 GitHub API（读你自己的 star 列表）；配了 LLM 才会再请求你自己的模型端点。
- **仓库里不含你的数据。** 星标、索引、同步状态、配置只存在于你配置的 `store.dir`；`.gitignore` 已排除
  `index.db`、`config.toml`、`.sync_state.json`、`.local/`。
- **文档不含个人数据。** 示例一律用占位符（`acme/video-kit`、`<你的笔记目录>`）。
- **密钥可以不落文件。** 推荐用 `llm.api_key_env` 指向环境变量；GitHub token 走环境变量或 `gh auth token`。

## 平台支持

| 平台 | 状态 | 说明 |
|---|---|---|
| Windows | ✅ 已实测 | 控制台自动切 UTF-8，cmd/PowerShell 中文不会崩 |
| macOS | ✅ 设计支持 | 纯标准库；配置走 `~/.config`（可用 `XDG_CONFIG_HOME`） |
| Linux | ✅ 设计支持 | 同上；`gh auth token` 需要 `gh` 在 PATH |

唯一的平台分支是配置目录（`%APPDATA%` vs `$XDG_CONFIG_HOME`），没有其他平台相关代码。

## 维护（改代码的人看）

| 你想改什么 | 改哪里 | 怎么生效 |
|---|---|---|
| 检索/同步/分类行为 | `src/ghstars/` | `uv tool install --reinstall git+https://…`（或开发时 `uv tool install -e .`） |
| MCP 工具 | `src/ghstars/mcp_server.py` 的 `TOOLS` | 同上；宿主重启后重新发现 |
| Skill 话术 | `skills/ghstars/SKILL.md` | `python scripts/sync_skill.py` |
| 版本号 | 只改 `pyproject.toml` 的 `version` | `__init__.__version__` 自动跟随；再打 tag |
| 索引结构 | `src/ghstars/index.py` | 用户侧 `ghstars index` 重建即可（索引是缓存，不做迁移） |

**兼容性红线**：Markdown frontmatter 字段（`repo`/`type`/`category`/`tags`/`topics`/`starred_at`）
是公开契约 —— 改名等于 major 版本，且要提供迁移脚本。

## 已知限制

- **改名不追踪**：仓库转移/改名后 `owner/name` 变了，会被当成新 star 再写一份（旧的需手工作废）。
- **删文件后索引会脏**：直到 `ghstars index` 重建。
- **LLM 是理解不是翻译**：机器翻译产不出有用的场景标签。不配 LLM 就只有英文描述 + 关键词分类。

## 开发

```bash
git clone https://github.com/zhe97396-tech/ghstars
cd ghstars
uv venv .venv && uv pip install -e ".[mcp]" --python .venv/Scripts/python.exe
.venv/Scripts/python.exe -m unittest discover -s tests -v     # 15 个单测
```

> Windows/Hermes 用户：宿主若注入 `PYTHONPATH`，请用 `PYTHONPATH=""` 运行，
> 否则会加载到宿主的 `mcp`/`pydantic` 而不是本项目 venv 的。

MIT License。
