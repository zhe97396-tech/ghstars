"""SQLite 索引（Markdown 的派生缓存，可随时重建）。

**为什么不用向量库**：星标收藏的检索瓶颈是精确词命中（项目名、中文场景标签），
字段加权 + IDF 就够，零依赖、零模型下载、毫秒级。需要语义召回时应在知识库自己
的 RAG 层做混合，而不是把 240MB 模型塞进每个人的安装包。

**为什么排序不用 bm25**：实测 bm25 对高频词（如 ``agent``）会返回 ``-0.0``，
所有命中并列，排序完全失效；再叠加 LIKE 兜底就变成"兜底结果盖过真实命中"。
因此这里用**可解释的字段加权 + IDF + 证据分层**打分（项目名 > 中文标签 > topics
> 中文描述 > 英文描述），idf 让稀有词压过高频词。

**为什么中文要切 bigram（2026-10-30 修复）**：旧版把整段中文当一个检索词
（``"视频制作"``），只有文档里**连续出现这几个字**才命中——实测「简历模板」
「语音转文字」「知识库检索」「股票量化」「读书笔记」5 条查询直接 0 命中。
改为 2 字滑窗切分后，同样的查询全部有结果，且不引入分词器（保持零依赖）。

**为什么要证据分层**：不同来源的分数不可比——「整串命中标签」和「只命中一个
bigram」混在一起排序，会让高频短词凑出的低质结果盖过真实命中。分层规则：
整串命中 > 全部检索词命中 > 部分命中，层内再比idf加权分；覆盖率不足一半的
直接丢弃（宁可说"没找到"，也不硬凑噪声）。
"""

from __future__ import annotations

import math
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .store import body_text, iter_repo_files, parse_frontmatter

ARCHIVED_TYPE = "github-star-archived"

# 检索词切分：ASCII 词块 或 连续中文。中文按 2 字滑窗切 bigram（见模块 docstring），
# ASCII 词块原样；整串另存用于"整串命中"层级判定。
_TERM_RE = re.compile(r"[0-9A-Za-z_./+-]+|[\u3400-\u4dbf\u4e00-\u9fff]+")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_QUERY_STRIP_RE = re.compile(r"[^0-9a-z\u3400-\u4dbf\u4e00-\u9fff]+")
_SHORT_TERM_LEN = 3  # trigram 无法索引 <3 字符
_MIN_COVERAGE_RATIO = 0.5  # 检索词覆盖率低于此值 → 视为不相关，不返回

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


def _raw_chunks(query: str) -> list[str]:
    """按原样切块（中文不拆），用于整串命中判定。"""
    return [t.lower() for t in _TERM_RE.findall(query or "") if t.strip()]


def _terms(query: str) -> list[str]:
    """检索词：中文按 2 字滑窗切 bigram，ASCII 词块原样（去重保序）。"""
    out: list[str] = []
    for chunk in _raw_chunks(query):
        if _CJK_RE.fullmatch(chunk) and len(chunk) > 2:
            out.extend(chunk[i:i + 2] for i in range(len(chunk) - 1))
        else:
            out.append(chunk)
    return list(dict.fromkeys(out))


def _phrase(query: str) -> str:
    """整串归一化：只留字母数字与汉字（去空格/标点），用于最高证据层判定。"""
    return _QUERY_STRIP_RE.sub("", (query or "").lower())


def _score(row: sqlite3.Row, terms: list[str], idf: dict[str, float],
           phrase: str = "") -> tuple[int, float, int, float]:
    """打分：返回 (证据层级, 分数, 命中词数, 加权覆盖率)。

    证据层级（不跨层比分数）::

        3  整串命中 repo/title/tags（用户输入的原样短语出现在字段里）
        2  全部检索词都命中
        1  部分命中（调用方按加权覆盖率过滤）

    层内分数 = Σ 字段权重 × 位置折算 × 该词的 idf；精确等于整个字段再加 bonus。
    加权覆盖率 = 命中词的 idf 之和 / 全部词 idf 之和 —— 命中一个高频短词
    （如「笔记」）不该算作强证据，命中稀有词才算。
    """
    texts = {f: (row[f] or "").lower() for f in FIELD_WEIGHTS}

    score = 0.0
    covered = 0
    matched_idf = 0.0
    for term in terms:
        hit = False
        for field, weight in FIELD_WEIGHTS.items():
            value = texts[field]
            if not value or term not in value:
                continue
            hit = True
            factor = 1.0 if field in ("repo", "title", "tags", "topics") else 0.6
            bonus = _EXACT_BONUS.get(field, 0.0) if term == value else 0.0
            score += (weight * factor + bonus) * idf.get(term, 1.0)
        if hit:
            covered += 1
            matched_idf += idf.get(term, 1.0)

    total_idf = sum(idf.get(t, 1.0) for t in terms) or 1.0
    weighted_cov = matched_idf / total_idf

    if phrase and any(phrase in texts[f] for f in ("repo", "title", "tags")):
        tier = 3
    elif covered == len(terms):
        tier = 2
    else:
        tier = 1
    return tier, score, covered, weighted_cov


def _row_to_dict(row: sqlite3.Row, relevance: float | None = None,
                 match: str | None = None) -> dict:
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
    if match is not None:
        # phrase=输入原样短语命中 / full=全部检索词命中 / partial=仅部分命中
        data["match"] = match
    return data


def search(
    db_path: Path,
    query: str,
    *,
    limit: int = 10,
    category: str | None = None,
    include_archived: bool = False,
) -> list[dict]:
    """检索：中文 bigram 召回 + IDF 加权 + 证据分层排序。

    分层（不跨层比分数）：整串命中 > 全部检索词命中 > 部分命中；层内按 idf 加权
    字段分排序，仍并列时按 star 数。覆盖率不足一半的结果直接丢弃——宁可返回
    「没找到」，也不硬凑噪声（例：库里有「小红书笔记」但没有「读书笔记」时，
    不应把前者当答案）。
    """
    terms = _terms(query)
    if not terms:
        return []
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"索引不存在：{db_path}（先运行 `ghstars index`）")

    phrase = _phrase(query)

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

    like_cols = " OR ".join(f"r.{f} LIKE ?" for f in FIELD_WEIGHTS)
    like_args = ("%{}%",) * len(FIELD_WEIGHTS)

    candidates: dict[str, sqlite3.Row] = {}
    idf: dict[str, float] = {}
    with _connect(db_path) as conn:
        total_row = conn.execute(
            f"SELECT COUNT(*) FROM repos r WHERE 1=1 {filter_sql}", params
        ).fetchone()
        total = int(total_row[0]) if total_row else 0

        # idf：语料层面统计每个词的文档频率，让稀有词压过高频词
        for term in terms:
            df_row = conn.execute(
                f"SELECT COUNT(*) FROM repos r WHERE ({like_cols}) {filter_sql}",
                tuple(a.format(term) for a in like_args) + tuple(params),
            ).fetchone()
            df = int(df_row[0]) if df_row else 0
            idf[term] = math.log((total + 1) / (df + 0.5)) if total else 1.0

        for term in terms:
            rows = conn.execute(
                f"SELECT r.* FROM repos r WHERE ({like_cols}) {filter_sql} "
                f"LIMIT {prefilter_cap}",
                tuple(a.format(term) for a in like_args) + tuple(params),
            ).fetchall()
            for row in rows:
                candidates.setdefault(row["repo"], row)

    min_cover = max(1, math.ceil(len(terms) * _MIN_COVERAGE_RATIO))
    scored: list[tuple[int, float, sqlite3.Row]] = []
    for row in candidates.values():
        tier, score, covered, _weighted_cov = _score(row, terms, idf, phrase)
        if score <= 0 or covered < min_cover:
            continue
        scored.append((tier, score, row))

    scored.sort(key=lambda item: (-item[0], -item[1], -(item[2]["stars"] or 0)))
    labels = {3: "phrase", 2: "full", 1: "partial"}
    return [
        _row_to_dict(row, score, labels.get(tier, "partial"))
        for tier, score, row in scored[:limit]
    ]


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
