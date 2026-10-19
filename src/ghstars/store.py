"""Markdown 存储层：这是**唯一权威数据源**（索引只是可重建的缓存）。

目录约定（与 Obsidian / 任何 Markdown 笔记库兼容）::

    <store>/
      _index.md            # 自动生成的总览，不参与索引
      <category>/
        owner-repo.md      # 每个项目一个文件，frontmatter 带检索字段
      .sync_state.json     # 断点续传状态

文件内容格式固定，方便 git diff、手工编辑、被其他工具读取。
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

INDEX_FILENAME = "_index.md"
STATE_FILENAME = ".sync_state.json"

_ARCHIVED_TYPE = "github-star-archived"
_ACTIVE_TYPE = "github-star"
# 精确匹配整个 type 值。历史上用 str.replace 做子串替换，
# 会把已归档的 `github-star-archived` 再叠成 `-archived-archived`（无限叠加），
# 而检索层是按 type 值精确查表的，污染值会拿到错误权重。
_TYPE_LINE_RE = re.compile(
    r"^type:[ \t]*(" + _ACTIVE_TYPE + r"(?:-archived)*)[ \t]*$", re.MULTILINE
)
_REPO_LINE_RE = re.compile(r"^repo:[ \t]*(.+?)[ \t]*$", re.MULTILINE)


# ---------------------------------------------------------------- frontmatter


def _parse_scalar(raw: str):
    value = raw.strip()
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [x.strip().strip("\"'") for x in inner.split(",") if x.strip()]
    return value.strip("\"'")


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """解析扁平 YAML frontmatter（零 yaml 依赖）。返回 ``(meta, body)``。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    meta: dict = {}
    body_start = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            body_start = i + 1
            break
        match = re.match(r"^([A-Za-z_][\w-]*):[ \t]*(.*)$", line)
        if match:
            meta[match.group(1)] = _parse_scalar(match.group(2))
    body = "\n".join(lines[body_start:]) if body_start is not None else ""
    return meta, body


def body_text(body: str) -> str:
    """正文去掉标题行与链接行，只留可检索文本。"""
    keep = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("# ") or stripped.startswith("🔗"):
            continue
        keep.append(stripped)
    return "\n".join(keep)


# ---------------------------------------------------------------- 渲染与写入


def repo_filename(full_name: str) -> str:
    return full_name.replace("/", "-") + ".md"


def render_markdown(
    project: dict,
    category: str,
    tags: list[str] | None = None,
    chinese_desc: str = "",
    today: str | None = None,
) -> tuple[str, str]:
    """生成 ``(filename, content)``，字段与历史文件保持一致。"""
    full_name = project["full_name"]
    owner, _, name = full_name.partition("/")
    now = today or date.today().isoformat()
    topics = project.get("topics") or []
    starred_at = (project.get("starred_at") or "")[:10] or now
    desc = project.get("description") or full_name
    desc_block = f"\n{chinese_desc}" if chinese_desc else ""

    content = f"""---
title: {name}
type: {_ACTIVE_TYPE}
platform: github
source: {project.get('html_url', '')}
repo: {full_name}
author: {owner}
stars: {project.get('stargazers_count', 0)}
language: {project.get('language', '')}
topics: [{', '.join(topics)}]
starred_at: {starred_at}
category: {category}
tags: [{', '.join(tags or [])}]
---

# {name}

{desc}{desc_block}

🔗 [GitHub]({project.get('html_url', '')})
"""
    return repo_filename(full_name), content


def is_archived(path: Path) -> bool:
    """判断文件是否已标记归档（只看 frontmatter 头部，避免整文件读）。"""
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:800]
    except Exception:
        return False
    match = _TYPE_LINE_RE.search(head)
    return bool(match and match.group(1) == _ARCHIVED_TYPE)


def iter_repo_files(store_dir: Path):
    for md in sorted(store_dir.rglob("*.md")):
        if md.name == INDEX_FILENAME:
            continue
        yield md


def scan_existing(store_dir: Path) -> dict[str, Path]:
    """扫描已有项目：``repo(小写) -> 文件路径``。

    GitHub 仓库名大小写不敏感，键必须统一小写；否则 ``Owner/Repo`` 与
    ``Owner/repo`` 会被当成两个项目各入库一份。
    """
    existing: dict[str, Path] = {}
    if not store_dir.exists():
        return existing
    for md in iter_repo_files(store_dir):
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        match = _REPO_LINE_RE.search(text)
        if match:
            existing[match.group(1).strip().lower()] = md
    return existing


def write_project(
    store_dir: Path,
    project: dict,
    category: str,
    tags: list[str] | None = None,
    chinese_desc: str = "",
) -> Path:
    filename, content = render_markdown(project, category, tags, chinese_desc)
    target_dir = store_dir / category
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / filename
    path.write_text(content, encoding="utf-8")
    return path


def archive_removed(existing: dict[str, Path], removed: set[str]) -> dict:
    """把「已取消 star」的项目标记为归档。**幂等**：已归档的跳过不写盘。"""
    result = {"archived": 0, "normalized": 0, "skipped": 0, "failed": 0, "changed": []}
    for repo in sorted(removed):
        path = existing.get(repo)
        if path is None:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            result["failed"] += 1
            continue
        match = _TYPE_LINE_RE.search(text)
        if not match:
            result["skipped"] += 1
            continue
        old_type = match.group(1)
        if old_type == _ARCHIVED_TYPE:
            result["skipped"] += 1
            continue
        new_text = text[: match.start(1)] + _ARCHIVED_TYPE + text[match.end(1):]
        path.write_text(new_text, encoding="utf-8")
        if old_type == _ACTIVE_TYPE:
            result["archived"] += 1
        else:
            result["normalized"] += 1
        result["changed"].append((repo, old_type, _ARCHIVED_TYPE))
    return result


# ---------------------------------------------------------------- 索引与状态


def write_index(store_dir: Path, stamp: str | None = None) -> Path:
    """生成 ``_index.md``（分类统计）。"""
    from datetime import datetime

    when = stamp or datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = ["# GitHub Stars 索引", "", f"> 更新时间：{when}", "", "## 分类概览", ""]
    total = 0
    for cat_dir in sorted(store_dir.iterdir()):
        if not cat_dir.is_dir():
            continue
        files = [p for p in cat_dir.glob("*.md")]
        active = 0
        for f in files:
            try:
                head = f.read_text(encoding="utf-8", errors="replace")[:600]
            except Exception:
                continue
            if _ARCHIVED_TYPE not in head:
                active += 1
        if files:
            total += active
            lines.append(f"- **{cat_dir.name}/** ({active} 个项目)")
    lines.extend(["", f"合计：{total} 个项目", ""])
    path = store_dir / INDEX_FILENAME
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def load_sync_state(store_dir: Path) -> dict:
    state_file = store_dir / STATE_FILENAME
    if state_file.exists():
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("processed", [])
                return data
        except Exception:
            pass
    return {"processed": []}


def save_sync_state(store_dir: Path, state: dict) -> None:
    """写入前去重，防止重复追加。"""
    if "processed" in state:
        seen: set[str] = set()
        deduped = []
        for repo in state["processed"]:
            if repo not in seen:
                seen.add(repo)
                deduped.append(repo)
        state["processed"] = deduped
    (store_dir / STATE_FILENAME).write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
