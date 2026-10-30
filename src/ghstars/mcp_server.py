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

from . import config, index as index_mod


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


# 只读：写入（sync / reindex）只走 CLI，MCP 不暴露有副作用的工具。
# 理由：写路径只留一个入口，护栏（exit 3 = LLM 全失败）就只有一处需要维护；
# MCP 没有退出码语义，agent 容易把「标签全空」当成同步成功。
TOOLS = (stars_search, stars_get, stars_stats)


def build():
    """构造 MCP server 实例（兼容 mcp 1.x / 2.x）。"""
    try:  # mcp >= 2.0
        from mcp.server.mcpserver import MCPServer as Server  # type: ignore

        server = Server(
            "ghstars",
            instructions=(
                "本地 GitHub 星标知识库（只读）。查「我 star 过什么」用 stars_search；"
                "看单个项目用 stars_get；看库概况用 stars_stats。"
                "同步/重建索引不在 MCP 里，请让用户跑 CLI：ghstars sync / ghstars index。"
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
