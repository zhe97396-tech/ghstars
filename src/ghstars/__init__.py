"""ghstars —— 把 GitHub 星标变成本地可检索的知识库。

设计要点：
- **Markdown 是唯一权威数据源**，SQLite 索引只是可重建缓存；
- **零第三方依赖**即可完成拉取/分类/写入/检索（FTS5 是 Python 内置）；
- 不读取任何宿主 Agent 的内部配置，任何人装完就能跑。
"""

from __future__ import annotations

__all__ = ["__version__"]

# 版本号唯一来源 = pyproject.toml（安装后从包元数据读，避免两处手改漂移）
try:
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _pkg_version

    try:
        __version__ = _pkg_version("ghstars")
    except PackageNotFoundError:  # 直接跑源码（未安装）时
        __version__ = "0.1.0"
except Exception:  # pragma: no cover - 极端环境兜底
    __version__ = "0.1.0"
