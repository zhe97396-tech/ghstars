# ghstars

[![tests](https://github.com/zhe97396-tech/ghstars/actions/workflows/tests.yml/badge.svg)](https://github.com/zhe97396-tech/ghstars/actions/workflows/tests.yml)
[![python](https://img.shields.io/badge/python-3.11%2B-blue)](pyproject.toml)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![version](https://img.shields.io/badge/version-0.1.0-informational)](CHANGELOG.md)

**Your GitHub stars, as a local searchable knowledge base.**

> [中文说明](README.zh-CN.md) · [使用指南 · HTML](docs/guide.zh.html) · [Changelog](CHANGELOG.md)

Turns your starred repos into plain Markdown files (Obsidian-friendly) plus a
zero-dependency SQLite/FTS5 index — so you (and any AI agent you use) can answer
*"what did I star that does X?"* in milliseconds, in **Chinese or English**, without
a vector database or a 240 MB embedding model.

```bash
ghstars sync                 # pull stars → markdown → index
ghstars search "视频制作"      # Chinese scene tags work
ghstars search "rag pipeline" # English project names work too
```

## Why another stars tool

| | `gh` / GitHub UI | bookmark managers | **ghstars** |
|---|---|---|---|
| Find a starred project by *what it does* (not its name) | ✗ | partial | ✓ Chinese scene tags + weighted fields |
| Works offline, grep-able, git-diff-able | ✗ | ✗ | ✓ plain Markdown |
| Usable by AI agents (CLI / MCP / skill) | ✗ | ✗ | ✓ three adapters, one core |
| Install cost | npm/pip | SaaS | **stdlib only** for pull/search |

The Chinese-tag part matters if you write in Chinese: GitHub descriptions are English,
so searching "视频剪辑" never hits a repo described as *"video editing toolkit"*.
`ghstars` asks an LLM (optional) for 3–5 Chinese scene tags + a 2–3 sentence Chinese
description, and stores them next to the English text — **tags and description
complement each other, neither alone is enough**.

## Install

```bash
# try without installing
uvx --from git+https://github.com/zhe97396-tech/ghstars ghstars --help

# or install as a tool
uv tool install git+https://github.com/zhe97396-tech/ghstars

# MCP server support (optional)
uv tool install "ghstars[mcp] @ git+https://github.com/zhe97396-tech/ghstars.git"
# pip 等价写法
pip install "ghstars[mcp] @ git+https://github.com/zhe97396-tech/ghstars.git"
```

Requires Python ≥ 3.11 (uses `tomllib`) and nothing else.

## Quickstart

```bash
ghstars init                 # write config template
$EDITOR "$APPDATA/ghstars/config.toml"   # Windows
# $EDITOR ~/.config/ghstars/config.toml  # macOS / Linux

ghstars doctor               # check token / store / index / LLM
ghstars sync                 # first run: pulls everything
ghstars search "小红书" --json
```

Token lookup order: `--token` → the env var named in `github.token_env`
(default `GITHUB_TOKEN`) → `GITHUB_TOKEN` / `GH_TOKEN` → `gh auth token`
(so if `gh auth login` is already done, **nothing to configure**).

## Config

`config.toml` (all keys have env-var equivalents; env wins):

```toml
[store]
dir = "D:/Notes/github-stars"   # where the Markdown files live
index = ""                      # SQLite index path; empty = config dir/index.db

[github]
username = "your-github-username"
token_env = "GITHUB_TOKEN"
api_base = "https://api.github.com"

[llm]                            # optional — omit to stay offline
base = "https://api.openai.com/v1"
model = "gpt-4o-mini"
api_key_env = "OPENAI_API_KEY"   # recommended over putting the key inline
timeout = 60

[categories]                     # your own buckets; keys = directory names
ai-agents = ["ai-agents", "agent", "agentic-ai"]
mcp-tools = ["mcp"]
rag = ["rag"]
tools-utilities = ["cli", "developer-tools", "tools"]
```

Env overrides: `GHSTARS_STORE`, `GHSTARS_INDEX`, `GHSTARS_GITHUB_USERNAME`,
`GHSTARS_LLM_BASE`, `GHSTARS_LLM_KEY`, `GHSTARS_LLM_MODEL`, `GHSTARS_CONFIG`.

## Commands

| Command | What it does |
|---|---|
| `sync` | Pull stars → dedupe → classify → Chinese tags → Markdown → archive unstarred → reindex |
| `search "<q>"` | Weighted search over name / tags / topics / Chinese desc / English desc. Chinese is split into 2-char bigrams (reordered queries still match); ranking = field weight × idf, tiered by evidence (`phrase` > `full` > `partial`)。结果带 `match` 字段，全是 `partial` 说明库里没有完全对口的项目 |
| `get <owner/name>` | Full record for one repo (`--full` prints the Markdown) |
| `list` | List by stars, filter `--category` |
| `stats` | Counts per category, archived count, index location |
| `export` | JSON or CSV |
| `index` | Rebuild the SQLite index |
| `doctor` | Self-check: Python/SQLite FTS5/token/store/index/LLM |
| `mcp` | Run as a stdio MCP server |

All commands accept `--json`. **Progress logs go to stderr, structured output to
stdout** — safe to pipe into `jq` or parse in an agent.

Useful `sync` flags: `--dry-run` (report only, no writes, no LLM), `--limit N`,
`--no-llm`, `--full` (ignore resume state).

**LLM failure is not silent.** If the LLM endpoint is configured but every call
fails (renamed model, dead key, network), `sync` still writes the Markdown but
prints a hard warning to stderr and **exits 3** — the run is *not* a success.
Partial failures exit 0 with a warning. The report carries the detail:

```jsonc
"llm": {
  "enabled": true, "calls": 10, "ok": 0, "failed": 10,
  "tag_failed": 10, "failed_all": true,
  "last_error": "model=... HTTP 400: Upstream request failed: Model is unavailable",
  "note": "LLM 全部失败（10 次调用无一次成功）..."
}
```

Exit codes: `0` ok (or nothing to do) · `1` doctor found problems · `2` config /
GitHub error · `3` LLM all-failed (files written without Chinese tags).

## Use it from AI agents

**1. MCP (Claude Code / Codex / Hermes / Cursor …)** — `ghstars mcp` exposes
**read-only** tools: `stars_search`, `stars_get`, `stars_stats`. Writing (sync /
index rebuild) is CLI-only on purpose: one write path, one place for the exit-3
guardrail, and MCP has no exit-code semantics for "every tag failed".
MCP servers are long-lived child processes: after upgrading ghstars, **restart the
host** for the new code to load.

```jsonc
// Claude Code: ~/.claude.json  →  "mcpServers"
// Cursor / generic clients: same shape
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

> MCP hosts often run servers with a **filtered environment** (Hermes passes only
> PATH/HOME and friends). That is fine here: the token comes from `gh auth token`
> or from whatever `[github] token_env` names — pass secrets explicitly via the
> host's `env:` block if you use an env-only token.

**2. Skill** — copy `skills/ghstars/` into your agent's skills dir
(`~/.claude/skills/`, `~/.codex/skills/`, Hermes profile `skills/`). The skill only
teaches *when* to call the CLI; the CLI owns all behavior.

**3. Plain shell** — anything that can run a command can use it:

```bash
ghstars search "剪视频" --json | jq -r '.results[].repo'
```

## How it works

```
GitHub API (stars + README)
  → dedupe by repo name, case-insensitive
  → classify: topics map → description keywords → LLM picking from *existing* dirs → other
  → LLM: 3–5 Chinese scene tags + 2–3 sentence Chinese description
  → Markdown file per repo  (frontmatter carries every searchable field)
  → SQLite index (derived cache, rebuildable)
```

**Markdown is the single source of truth.** The index is a cache: delete it, run
`ghstars index`, nothing is lost. Files are byte-stable, so `git diff` shows only
real changes and Obsidian shows a clean graph.

### Design notes (the non-obvious decisions)

- **Ranking is not bm25.** Measured on a 268-repo library: bm25 returns `-0.0` for
  high-frequency terms (`agent`), so every hit ties and ranking collapses; adding a
  LIKE fallback then lets fallback hits outrank real ones. ghstars scores fields
  explicitly — `repo 6 > title/tags 4 > topics/chinese 2 > english desc 1.5` — with an
  exact-match bonus. Predictable, explainable, and it keeps "tag hit > description hit".
- **Short CJK queries** (`简历`, `剪辑`) are below trigram's 3-char minimum, so they
  bypass FTS and go through LIKE. No Chinese tokenizer dependency (no jieba).
- **Classification never invents new categories.** The LLM picks from directories that
  already exist, which stops taxonomy drift.
- **Compatibility topics are ignored.** Bare `claude-code` / `anthropic` topics mean
  "compatible with", not "is a Claude skill" — using them for classification was the
  single biggest source of misfiled projects.
- **Archiving is idempotent.** `type: github-star-archived` is matched as a whole value
  and rewritten exactly once; substring replacement produced
  `-archived-archived-...` and broke type-based weighting.

## Privacy

- **No telemetry, no cloud.** The only network calls are to the GitHub API (to read *your*
  star list) and, if you configure one, your own LLM endpoint.
- **No data in this repo.** Your stars, index, sync state and config live only where you point
  `store.dir`; `.gitignore` excludes `index.db`, `config.toml`, `.sync_state.json` and `.local/`.
- **Docs contain no personal data.** Examples use placeholders (`acme/video-kit`, `<your notes dir>`).
- **Secrets stay out of files when you choose so.** Prefer `llm.api_key_env` over inlining a key;
  GitHub tokens are read from env or `gh auth token`.

## Platforms

| Platform | Status |
|---|---|
| Windows | ✅ tested (console forced to UTF-8; cmd/PowerShell safe) |
| macOS | ✅ by design — stdlib only, config in `~/.config` (honors `XDG_CONFIG_HOME`) |
| Linux | ✅ by design — same; `gh auth token` needs `gh` on PATH |

The only platform branch is the config directory (`%APPDATA%` vs `$XDG_CONFIG_HOME`).

## Maintenance

| Change | Where | How it takes effect |
|---|---|---|
| Search / sync / classification behaviour | `src/ghstars/` | `uv tool install --reinstall git+…` (or `-e .` while developing) |
| MCP tools | `TOOLS` in `mcp_server.py` | same, then restart the host |
| Skill wording | `skills/ghstars/SKILL.md` | `python scripts/sync_skill.py` |
| Version | `version` in `pyproject.toml` only | `__version__` follows automatically; then `git tag` |
| Index schema | `index.py` | users run `ghstars index` (the index is a cache — no migrations) |

**Compatibility contract**: the Markdown frontmatter fields (`repo`, `type`, `category`,
`tags`, `topics`, `starred_at`) are public API. Renaming one is a major version and needs a
migration script. The index can change freely — it is rebuildable.

## Known limits

- **Renames aren't tracked.** A repo transferred/renamed gets a new `owner/name`, so it
  is treated as a new star and produces a second file. (Archive the old one by hand.)
- **Deleting a Markdown file leaves the index stale** until `ghstars index`.
- **LLM calls are reasoning, not translation.** Machine translation can't produce useful
  scene tags. Without an LLM you still get English descriptions + keyword classification.

## Development

```bash
git clone https://github.com/zhe97396-tech/ghstars
cd ghstars
uv venv .venv && uv pip install -e ".[mcp]" --python .venv/Scripts/python.exe
.venv/Scripts/python.exe -m unittest discover -s tests -v
```

> Windows/Hermes users: run with `PYTHONPATH=""` when a host injects its own
> site-packages, otherwise the wrong `mcp`/`pydantic` gets imported.

MIT licensed.
