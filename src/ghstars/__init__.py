"""ghstars —— 把 GitHub 星标变成本地可检索的知识库。

设计要点：
- **Markdown 是唯一权威数据源**，SQLite 索引只是可重建缓存；
- **零第三方依赖**即可完成拉取/分类/写入/检索（FTS5 是 Python 内置）；
- 不读取任何宿主 Agent 的内部配置，任何人装完就能跑。
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
