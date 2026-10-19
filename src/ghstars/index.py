"""SQLite 索引（Markdown 的派生缓存，可随时重建）。

**为什么不用向量库**：星标收藏的检索瓶颈是精确词命中（项目名、中文场景标签），
FTS5 + 字段加权就够，零依赖、零模型下载、毫秒级。需要语义召回时应在知识库自己
的 RAG 层做混合，而不是把 240MB 模型塞进每个人的安装包。

**为什么排序不用 bm25**：实测 bm25 对高频词（如 ``agent``）会返回 ``-0.0``，
所有命中并列，排序完全失效；再叠加 LIKE 兜底就变成"兜底结果盖过真实命中"。
因此这里用**可解释的字段加权打分**（项目名 > 中文标签 > topics > 中文描述 > 英文描述），
FTS 只负责快速圈候选。
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .store import body_text, iter_repo_files, parse_frontmatter

ARCHIVED_TYPE = "github-star-archived"

# 检索词切分：ASCII 词块 或 连续中文。中文整段当一个词（"视频制作"），
# 标签/描述里的短词（"简历"）靠 LIKE 兜底，不依赖分词器。
_TERM_RE = re.compile(r"[0-9A-Za-z_./+-]+|[\u3400-\u4dbf\u4e00-\u9fff]+")
_SHORT_TERM_LEN = 3  # trigram 无法索引 <3 字符

# 字段权重：命中"项目名/标签"比命中长描述更该排前面。
FIELD_WEIGHTS = {
    "repo": 6.0,
    "title": 4.0,
    "tags": 4.0,
    "topics": 2.0,
    "chinese": 2.0,
    "description": 1.5,
}
_EXACT_BONUS = {"repo": 2.0, "title": 1.5, "tags": 1.25, "topics": 1.2}


@contextmanager
def _connect(db_path: Path):
    """连接上下文：正常退出提交，无论如何都关闭。

    ``with sqlite3.connect(...)`` 只管事务不管关闭 —— Windows 上句柄不释放会
    导致索引文件删不掉/被占用（测试与重建时都会踩）。
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _create_schema(conn: sqlite3.Connection) -> str:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS repos (
            repo        TEXT PRIMARY KEY,
            path        TEXT NOT NULL,
            title       TEXT,
            url         TEXT,
            description TEXT,
            chinese     TEXT,
            tags        TEXT,
            topics      TEXT,
            language    TEXT,
            stars       INTEGER,
            category    TEXT,
            starred_at  TEXT,
            type        TEXT
        );
        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )
    tokenizer = "trigram"
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS repos_fts USING fts5("
            "repo, title, tags, topics, body, tokenize='trigram')"
        )
    except sqlite3.OperationalError:
        tokenizer = "unicode61"
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS repos_fts USING fts5("
            "repo, title, tags, topics, body, tokenize='unicode61')"
        )
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('tokenizer', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (tokenizer,),
    )
    return tokenizer


def tokenizer(db_path: Path) -> str:
    try:
        with _connect(db_path) as conn:
            row = conn.execute("SELECT value FROM meta WHERE key='tokenizer'").fetchone()
            return row["value"] if row else ""
    except sqlite3.Error:
        return ""


def build(store_dir: Path, db_path: Path) -> dict:
    """全量重建索引。星标量级（千级）下比增量简单可靠得多。"""
    store_dir = Path(store_dir)
    db_path = Path(db_path)
    tokenizer_name = ""
    files = indexed = skipped = 0
    with _connect(db_path) as conn:
        tokenizer_name = _create_schema(conn)
        conn.execute("DELETE FROM repos")
        conn.execute("DELETE FROM repos_fts")
        for md in iter_repo_files(store_dir):
            files += 1
            try:
                text = md.read_text(encoding="utf-8", errors="replace")
            except Exception:
                skipped += 1
                continue
            meta, body = parse_frontmatter(text)
            repo = (meta.get("repo") or "").strip()
            if not repo:
                skipped += 1
                continue
            tags = meta.get("tags") or []
            topics = meta.get("topics") or []
            body_clean = body_text(body)
            body_lines = [ln for ln in body_clean.split("\n") if ln.strip()]
            description = body_lines[0] if body_lines else ""
            chinese = "\n".join(body_lines[1:]) if len(body_lines) > 1 else ""
            cur = conn.execute(
                "INSERT OR REPLACE INTO repos(repo, path, title, url, description, chinese,"
                " tags, topics, language, stars, category, starred_at, type)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    repo,
                    str(md),
                    meta.get("title") or repo.split("/")[-1],
                    meta.get("source") or "",
                    description,
                    chinese,
                    " ".join(tags) if isinstance(tags, list) else str(tags),
                    " ".join(topics) if isinstance(topics, list) else str(topics),
                    meta.get("language") or "",
                    int(meta.get("stars") or 0),
                    meta.get("category") or "",
                    meta.get("starred_at") or "",
                    meta.get("type") or "",
                ),
            )
            conn.execute(
                "INSERT INTO repos_fts(rowid, repo, title, tags, topics, body)"
                " VALUES(?,?,?,?,?,?)",
                (
                    cur.lastrowid,
                    repo,
                    meta.get("title") or "",
                    " ".join(tags) if isinstance(tags, list) else str(tags),
                    " ".join(topics) if isinstance(topics, list) else str(topics),
                    body_clean,
                ),
            )
            indexed += 1
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('store_dir', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(store_dir.resolve()),),
        )
        conn.commit()
    return {
        "store": str(store_dir.resolve()),
        "db": str(db_path.resolve()),
        "files": files,
        "indexed": indexed,
        "skipped": skipped,
        "tokenizer": tokenizer_name,
    }


def _terms(query: str) -> list[str]:
    return [t.lower() for t in _TERM_RE.findall(query or "") if t.strip()]


def _score(row: sqlite3.Row, terms: list[str]) -> float:
    """确定性打分：字段权重 ×（精确命中加成 / 子串折算）。

    精确命中（标签就是这个词）远强于长描述里顺带出现，这是星标检索的
    核心判据；bm25 不做这种区分，所以排序不用它。
    """
    fields = {
        "repo": (row["repo"] or "").lower(),
        "title": (row["title"] or "").lower(),
        "tags": (row["tags"] or "").lower(),
        "topics": (row["topics"] or "").lower(),
        "chinese": (row["chinese"] or "").lower(),
        "description": (row["description"] or "").lower(),
    }
    total = 0.0
    for term in terms:
        for field, weight in FIELD_WEIGHTS.items():
            value = fields[field]
            if not value:
                continue
            if term == value:
                total += weight + _EXACT_BONUS.get(field, 0.0)
            elif term in value:
                total += weight * (0.8 if field in ("repo", "title", "tags", "topics") else 0.5)
    return total


def _row_to_dict(row: sqlite3.Row, relevance: float | None = None) -> dict:
    data = {
        "repo": row["repo"],
        "title": row["title"],
        "category": row["category"],
        "tags": (row["tags"] or "").split(),
        "description": row["description"] or "",
        "path": row["path"],
        "url": row["url"] or "",
        "stars": row["stars"] or 0,
        "archived": (row["type"] or "") == ARCHIVED_TYPE,
    }
    if relevance is not None:
        data["relevance"] = round(relevance, 3)
    return data


def search(
    db_path: Path,
    query: str,
    *,
    limit: int = 10,
    category: str | None = None,
    include_archived: bool = False,
) -> list[dict]:
    """混合检索：FTS5 圈候选 + 短词 LIKE 兜底 → 字段加权排序。"""
    terms = _terms(query)
    if not terms:
        return []
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"索引不存在：{db_path}（先运行 `ghstars index`）")

    filters = []
    params: list = []
    if category:
        filters.append("AND r.category = ?")
        params.append(category)
    if not include_archived:
        filters.append("AND IFNULL(r.type,'') != ?")
        params.append(ARCHIVED_TYPE)
    filter_sql = " ".join(filters)
    prefilter_cap = max(limit * 40, 200)

    candidates: dict[str, sqlite3.Row] = {}
    with _connect(db_path) as conn:
        fts_terms = [t for t in terms if len(t) >= _SHORT_TERM_LEN]
        if fts_terms:
            match_expr = " OR ".join(f'"{t}"' for t in fts_terms)
            try:
                rows = conn.execute(
                    f"""
                    SELECT r.* FROM repos_fts
                    JOIN repos r ON r.rowid = repos_fts.rowid
                    WHERE repos_fts MATCH ? {filter_sql}
                    LIMIT {prefilter_cap}
                    """,
                    (match_expr, *params),
                ).fetchall()
                for row in rows:
                    candidates[row["repo"]] = row
            except sqlite3.OperationalError:
                pass

        # 短词（<3 字符，如「简历」）trigram 索引不到；FTS 圈不到候选时整句回退 LIKE
        like_terms = [t for t in terms if len(t) < _SHORT_TERM_LEN]
        if not candidates:
            like_terms = terms
        for term in like_terms:
            like = f"%{term}%"
            rows = conn.execute(
                f"""
                SELECT r.* FROM repos r
                WHERE (r.repo LIKE ? OR r.title LIKE ? OR r.tags LIKE ?
                       OR r.description LIKE ? OR r.chinese LIKE ?) {filter_sql}
                LIMIT {prefilter_cap}
                """,
                (like, like, like, like, like, *params),
            ).fetchall()
            for row in rows:
                candidates.setdefault(row["repo"], row)

    scored = []
    for row in candidates.values():
        score = _score(row, terms)
        if score > 0:
            scored.append((score, row))
    scored.sort(key=lambda item: (-item[0], -(item[1]["stars"] or 0)))
    return [_row_to_dict(row, score) for score, row in scored[:limit]]


def get_repo(db_path: Path, repo: str) -> dict | None:
    if not Path(db_path).exists():
        raise FileNotFoundError(f"索引不存在：{db_path}（先运行 `ghstars index`）")
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM repos WHERE lower(repo) = lower(?)", (repo,)
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT * FROM repos WHERE repo LIKE ? LIMIT 1", (f"%{repo}%",)
            ).fetchone()
    return _row_to_dict(row) if row else None


def list_repos(
    db_path: Path,
    *,
    category: str | None = None,
    include_archived: bool = False,
    limit: int = 500,
) -> list[dict]:
    if not Path(db_path).exists():
        raise FileNotFoundError(f"索引不存在：{db_path}（先运行 `ghstars index`）")
    sql = "SELECT * FROM repos WHERE 1=1"
    params: list = []
    if category:
        sql += " AND category = ?"
        params.append(category)
    if not include_archived:
        sql += " AND IFNULL(type,'') != ?"
        params.append(ARCHIVED_TYPE)
    sql += " ORDER BY stars DESC LIMIT ?"
    params.append(limit)
    with _connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def stats(db_path: Path) -> dict:
    db_path = Path(db_path)
    if not db_path.exists():
        return {"db": str(db_path), "exists": False, "total": 0, "by_category": {}}
    with _connect(db_path) as conn:
        total = conn.execute("SELECT COUNT(*) c FROM repos").fetchone()["c"]
        archived = conn.execute(
            "SELECT COUNT(*) c FROM repos WHERE type = ?", (ARCHIVED_TYPE,)
        ).fetchone()["c"]
        by_category = {
            row["category"] or "other": row["c"]
            for row in conn.execute(
                "SELECT category, COUNT(*) c FROM repos WHERE IFNULL(type,'') != ?"
                " GROUP BY category ORDER BY c DESC",
                (ARCHIVED_TYPE,),
            ).fetchall()
        }
        store = conn.execute("SELECT value FROM meta WHERE key='store_dir'").fetchone()
        tokenizer_name = conn.execute(
            "SELECT value FROM meta WHERE key='tokenizer'"
        ).fetchone()
    return {
        "db": str(db_path.resolve()),
        "exists": True,
        "store": store["value"] if store else "",
        "tokenizer": tokenizer_name["value"] if tokenizer_name else "",
        "total": total,
        "archived": archived,
        "by_category": by_category,
    }
