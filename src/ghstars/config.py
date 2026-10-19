"""配置加载：环境变量 > 配置文件 > 内置默认。

配置文件位置（优先级从高到低）：
  1. 环境变量 ``GHSTARS_CONFIG`` 指向的路径
  2. Windows: ``%APPDATA%/ghstars/config.toml``
     其他平台: ``$XDG_CONFIG_HOME/ghstars/config.toml``（默认 ``~/.config``）

设计原则：本包**不读取任何宿主 Agent 的内部配置**（Hermes / Claude Code / Codex
的 config 一律不碰），保证换宿主、换机器、给别人用都能跑。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

try:  # Python >= 3.11
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 仅 3.11 以下
    tomllib = None  # type: ignore[assignment]

APP_NAME = "ghstars"

# 通用默认分类（仅作开箱即用的起点，建议在 config.toml 里改成自己的）
DEFAULT_CATEGORIES: dict[str, list[str]] = {
    "ai-agents": ["ai-agents", "ai-agent", "agent", "agentic-ai"],
    "claude-code-skills": [
        "claude-skills", "agent-skills", "claude-skill", "claude-code-skill",
        "skills", "skill", "codex",
    ],
    "mcp-tools": ["mcp"],
    "rag": ["rag"],
    "llm-tools": ["llm", "prompt-engineering", "openai"],
    "browser-automation": ["browser-automation", "web", "scraping"],
    "devops": ["devops", "ci-cd", "docker", "kubernetes"],
    "tools-utilities": ["cli", "developer-tools", "tools", "utility"],
    "resume-career": ["resume", "career", "job"],
    "image-gen": ["image-generation", "ai-art", "stable-diffusion"],
}

CONFIG_TEMPLATE = """# ghstars 配置 —— 由 `ghstars init` 生成
# 所有项都可用环境变量覆盖，见 README「配置」一节。

[store]
# 星标项目 Markdown 落地目录（必须显式指定，或用 --dir / GHSTARS_STORE）
dir = ""
# 检索索引（SQLite，可删除重建）；留空 = 与配置文件同目录的 index.db
index = ""

[github]
username = ""
# 从哪个环境变量读 GitHub token（也可用 `gh auth token` 兜底）
token_env = "GITHUB_TOKEN"
api_base = "https://api.github.com"

[llm]
# 留空则关闭 LLM（退化为英文描述 + topics/关键词分类）
base = ""
model = ""
api_key = ""
# 从哪个环境变量读 key（推荐，避免明文写进配置）
api_key_env = ""
timeout = 60

[llm.headers]
# 需要额外请求头时写这里，例如
# x-opencode-session = "oneshot-fixed"

[categories]
# 分类名 = topics 关键词列表（第一优先级匹配）
# 删掉本段则使用内置默认分类
ai-agents = ["ai-agents", "ai-agent", "agent", "agentic-ai"]
mcp-tools = ["mcp"]
rag = ["rag"]
llm-tools = ["llm", "prompt-engineering", "openai"]
tools-utilities = ["cli", "developer-tools", "tools", "utility"]
"""


def config_path() -> Path:
    override = os.environ.get("GHSTARS_CONFIG", "").strip()
    if override:
        return Path(override).expanduser()
    return config_dir() / "config.toml"


def config_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_NAME


def load_config(path: Path | None = None) -> dict:
    """读取配置文件；缺失或解析失败返回空 dict（不抛异常）。"""
    p = path or config_path()
    if not p.exists() or tomllib is None:
        return {}
    try:
        with open(p, "rb") as f:
            return tomllib.load(f) or {}
    except Exception:
        return {}


def get(cfg: dict, dotted: str, default=None):
    """按 ``a.b.c`` 取值。"""
    cur = cfg
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur if cur != "" else default


def store_dir(cfg: dict, override: str | None = None) -> Path:
    """Markdown 落地目录：命令行 > 环境变量 > 配置文件 > ``./github-stars``。"""
    for raw in (override, os.environ.get("GHSTARS_STORE"), get(cfg, "store.dir")):
        if raw:
            return Path(str(raw)).expanduser().resolve()
    return (Path.cwd() / "github-stars").resolve()


def index_path(cfg: dict, override: str | None = None) -> Path:
    for raw in (override, os.environ.get("GHSTARS_INDEX"), get(cfg, "store.index")):
        if raw:
            return Path(str(raw)).expanduser().resolve()
    return config_dir() / "index.db"


def github_username(cfg: dict, override: str | None = None) -> str:
    for raw in (override, os.environ.get("GHSTARS_GITHUB_USERNAME"), get(cfg, "github.username")):
        if raw:
            return str(raw).strip().lstrip("@")
    return ""


def github_token_env(cfg: dict) -> str:
    return str(get(cfg, "github.token_env", "GITHUB_TOKEN") or "GITHUB_TOKEN")


def categories(cfg: dict) -> dict[str, list[str]]:
    custom = get(cfg, "categories")
    if isinstance(custom, dict) and custom:
        out: dict[str, list[str]] = {}
        for name, kws in custom.items():
            if isinstance(kws, str):
                kws = [kws]
            out[str(name)] = [str(k).lower() for k in (kws or [])]
        return out
    return {k: list(v) for k, v in DEFAULT_CATEGORIES.items()}


def llm_settings(cfg: dict) -> dict:
    """LLM 配置解析结果。api_key 为空表示 LLM 不可用。"""
    base = os.environ.get("GHSTARS_LLM_BASE") or get(cfg, "llm.base", "") or ""
    model = os.environ.get("GHSTARS_LLM_MODEL") or get(cfg, "llm.model", "") or ""
    key = os.environ.get("GHSTARS_LLM_KEY") or get(cfg, "llm.api_key", "") or ""
    if not key:
        env_name = os.environ.get("GHSTARS_LLM_KEY_ENV") or get(cfg, "llm.api_key_env", "") or ""
        if env_name:
            key = os.environ.get(env_name, "")
    headers = get(cfg, "llm.headers", {}) or {}
    return {
        "base": str(base).rstrip("/"),
        "key": str(key),
        "model": str(model),
        "timeout": int(get(cfg, "llm.timeout", 60) or 60),
        "headers": {str(k): str(v) for k, v in headers.items()} if isinstance(headers, dict) else {},
    }


def init_config(path: Path | None = None, overwrite: bool = False) -> Path:
    """写出配置模板。"""
    p = path or config_path()
    if p.exists() and not overwrite:
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    return p
