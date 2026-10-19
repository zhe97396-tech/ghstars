"""MCP server（stdio）：把 ghstars 包成类型化工具，供 MCP 宿主调用。

薄壳设计：所有逻辑都在 :mod:`ghstars.index` / :mod:`ghstars.sync`，
这里只做参数收敛与结果序列化 —— 命令行和 MCP 永远不会给出不一致的答案。

兼容两代 SDK：
- ``mcp`` 2.x：``mcp.server.mcpserver.MCPServer``
- ``mcp`` 1.x：``mcp.server.fastmcp.FastMCP``

启动：``ghstars mcp``，宿主配置示例见 README。
"""

from __future__ import annotations

import json
from typing import Any

from . import config, github, index as index_mod, llm as llm_mod, sync as sync_mod


def _cfg() -> dict:
    return config.load_config()


def _dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False)


def stars_search(
    query: str,
    limit: int = 10,
    category: str | None = None,
    include_archived: bool = False,
) -> str:
    """检索本地已收藏的 GitHub 星标项目。

    这是查「我 star 过什么」的首选工具：支持中文场景词（如「视频制作」「简历」）、
    项目名、技术标签。返回项目名、分类、中文标签、描述、star 数、本地文件路径。

    Args:
        query: 检索词，中英文均可。
        limit: 返回条数上限。
        category: 只在该分类内检索（如 ai-agents）。
        include_archived: 是否包含已取消 star 的历史项目。
    """
    cfg = _cfg()
    db = config.index_path(cfg)
    try:
        rows = index_mod.search(
            db, query, limit=limit, category=category, include_archived=include_archived
        )
    except FileNotFoundError as exc:
        return _dumps({"error": str(exc), "hint": "运行 `ghstars sync` 或 `ghstars index` 建立索引"})
    return _dumps({"query": query, "count": len(rows), "results": rows})


def stars_get(repo: str) -> str:
    """按仓库名查看单个已收藏项目的完整信息（owner/name，大小写不敏感）。

    Args:
        repo: 仓库全名，如 ``owner/name``；也接受部分匹配。
    """
    cfg = _cfg()
    try:
        row = index_mod.get_repo(config.index_path(cfg), repo)
    except FileNotFoundError as exc:
        return _dumps({"error": str(exc)})
    if row is None:
        return _dumps({"error": f"未收录：{repo}"})
    return _dumps(row)


def stars_stats() -> str:
    """查看星标库概况：总数、已归档数、各分类计数、索引位置。"""
    return _dumps(index_mod.stats(config.index_path(_cfg())))


def stars_sync(dry_run: bool = False, use_llm: bool = True, limit: int | None = None) -> str:
    """把 GitHub 上最新 star 的项目同步到本地库（写文件，有副作用）。

    流程：拉取 API → 与本地去重 → 分类 → 生成中文标签/描述 → 写 Markdown
    → 归档已取消 star 的项目 → 重建索引。项目多时会比较慢，按需使用。

    Args:
        dry_run: 只报告新增/取消的数量，不写任何文件。
        use_llm: 是否调用 LLM 生成中文标签与描述（关掉更快更省钱）。
        limit: 本次最多处理多少个新项目。
    """
    cfg = _cfg()
    try:
        report = sync_mod.sync(
            cfg,
            use_llm=use_llm,
            dry_run=dry_run,
            limit=limit,
            quiet=True,
        )
    except (ValueError, github.GitHubError) as exc:
        return _dumps({"error": str(exc), "hint": getattr(exc, "hint", "")})
    return _dumps(report)


def stars_reindex() -> str:
    """重建检索索引（手工编辑过 Markdown 文件，或删过文件后使用）。"""
    cfg = _cfg()
    info = index_mod.build(config.store_dir(cfg), config.index_path(cfg))
    return _dumps(info)


TOOLS = (stars_search, stars_get, stars_stats, stars_sync, stars_reindex)


def build():
    """构造 MCP server 实例（兼容 mcp 1.x / 2.x）。"""
    try:  # mcp >= 2.0
        from mcp.server.mcpserver import MCPServer as Server  # type: ignore

        server = Server(
            "ghstars",
            instructions=(
                "本地 GitHub 星标知识库。查「我 star 过什么」用 stars_search；"
                "看单个项目用 stars_get；同步最新 star 用 stars_sync（有副作用）。"
            ),
        )
    except ImportError:  # mcp 1.x
        from mcp.server.fastmcp import FastMCP as Server  # type: ignore

        server = Server("ghstars")

    for func in TOOLS:
        server.tool()(func)
    return server


def run() -> int:
    try:
        server = build()
    except ImportError:
        print(
            "✗ 未安装 MCP SDK。安装：pip install 'ghstars[mcp]'（或 pip install mcp）",
            file=__import__("sys").stderr,
        )
        return 2
    server.run()
    return 0
