"""命令行入口：``ghstars <command>``。

约定：**进度日志一律走 stderr，结构化结果走 stdout**，这样 Agent 直接用
``ghstars search --json`` 拿到干净 JSON，而人类看到的是可读输出。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
from pathlib import Path

from . import __version__, config, github, index as index_mod, llm as llm_mod, store, sync as sync_mod


# ------------------------------------------------------------------ 输出辅助


def _emit(data, as_json: bool, render=None) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif render:
        render(data)


def _print_results(data: dict) -> None:
    results = data.get("results") or []
    print(f"「{data['query']}」命中 {len(results)} 条")
    if results and all(r.get("match") == "partial" for r in results):
        print("⚠️ 只找到弱相关（未覆盖全部关键词）——可能库里没有完全对应的项目")
    for i, item in enumerate(results, start=1):
        flag = " [已归档]" if item.get("archived") else ""
        weak = "  ⚠️弱相关" if item.get("match") == "partial" else ""
        print(f"\n{i}. {item['repo']}{flag}   ★{item['stars']}   [{item['category']}]   rel={item.get('relevance')}{weak}")
        if item["description"]:
            print(f"   {item['description'][:160]}")
        if item["tags"]:
            print(f"   tags: {', '.join(item['tags'])}")
        print(f"   {item['path']}")


def _print_stats(data: dict) -> None:
    if not data.get("exists"):
        print(f"索引不存在：{data['db']}\n先运行 `ghstars index`（或 `ghstars sync`）")
        return
    print(f"索引：{data['db']}")
    print(f"数据：{data.get('store') or '（未知）'}")
    print(f"合计 {data['total']} 个（其中已归档 {data['archived']}）  分词器 {data.get('tokenizer')}")
    for cat, count in data["by_category"].items():
        print(f"  {cat:24s} {count}")


# ------------------------------------------------------------------ 子命令


def cmd_init(args) -> int:
    path = Path(args.path).expanduser() if args.path else config.config_path()
    existed = path.exists()
    config.init_config(path, overwrite=args.force)
    print(f"{'已覆盖' if existed and args.force else '配置就绪'}：{path}")
    if not existed:
        print("下一步：填入 [store] dir 与 [github] username，然后 `ghstars doctor`")
    return 0


def cmd_doctor(args) -> int:
    cfg = config.load_config()
    problems: list[str] = []
    notes: list[str] = []

    py = sys.version.split()[0]
    if sys.version_info < (3, 11):
        problems.append(f"Python {py} 过低，需要 >= 3.11（tomllib）")
    notes.append(f"Python {py}")

    try:
        sqlite_version = sqlite3.sqlite_version
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(x, tokenize='trigram')")
        conn.close()
        notes.append(f"SQLite {sqlite_version}：FTS5 + trigram 可用")
    except Exception as exc:
        problems.append(f"SQLite FTS5/trigram 不可用（{exc}）——检索功能不可用")

    cfg_path = config.config_path()
    if cfg_path.exists():
        notes.append(f"配置：{cfg_path}")
    else:
        problems.append(f"配置文件不存在：{cfg_path}（运行 `ghstars init`）")

    username = config.github_username(cfg)
    if username:
        notes.append(f"GitHub 用户：{username}")
    else:
        problems.append("未配置 GitHub 用户名（GHSTARS_GITHUB_USERNAME / [github] username）")

    token = github.get_token(cfg)
    if token:
        src = "环境变量" if (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")) else "gh CLI"
        notes.append(f"GitHub token：已获取（来源 {src}，长度 {len(token)}）")
    else:
        notes.append("GitHub token：未配置（匿名 60 次/小时，可能限流）")

    settings = config.llm_settings(cfg)
    if llm_mod.available(settings):
        notes.append(
            f"LLM：{settings['model']} @ {settings['base']}（key 长度 {len(settings['key'])}）"
        )
        if args.check_llm:
            text = llm_mod.call_llm("只回复两个字：可用", settings, max_tokens=16, timeout=30)
            if text:
                notes.append(f"LLM 连通性：OK（返回「{text[:20]}」）")
            else:
                problems.append(f"LLM 调用失败：{llm_mod.last_error()}")
    else:
        notes.append("LLM：未配置（分类退化为 topics/关键词，无中文描述）")

    target = config.store_dir(cfg)
    if target.exists():
        count = sum(1 for _ in store.iter_repo_files(target))
        notes.append(f"入库目录：{target}（{count} 个 Markdown）")
    else:
        notes.append(f"入库目录：{target}（不存在，首次 sync 会创建）")

    db = config.index_path(cfg)
    info = index_mod.stats(db)
    if info["exists"]:
        stale = info.get("store") and Path(info["store"]) != target
        notes.append(
            f"索引：{db}（{info['total']} 条 / 分词器 {info.get('tokenizer')}）"
            + ("  ⚠ 索引指向的目录与当前配置不一致，需重建" if stale else "")
        )
        if stale:
            problems.append("索引与入库目录不一致：运行 `ghstars index --rebuild`")
    else:
        notes.append(f"索引：{db}（不存在，运行 `ghstars index`）")

    print(f"ghstars {__version__}")
    for line in notes:
        print(f"  ✓ {line}")
    for line in problems:
        print(f"  ✗ {line}")
    print("\n结论：" + ("可直接使用" if not problems else f"{len(problems)} 个问题待修"))
    return 0 if not problems else 1


def cmd_sync(args) -> int:
    cfg = config.load_config()
    try:
        report = sync_mod.sync(
            cfg,
            store_override=args.dir,
            username=args.username,
            token=args.token,
            use_llm=not args.no_llm,
            full=args.full,
            dry_run=args.dry_run,
            rebuild_index=not args.no_index,
            index_override=args.index,
            limit=args.limit,
            quiet=args.json,
        )
    except (ValueError, github.GitHubError) as exc:
        hint = getattr(exc, "hint", "")
        print(f"✗ {exc}" + (f"\n  提示：{hint}" if hint else ""), file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"\n新增 {report['added']} / 跳过 {report['skipped']} / 归档 {report['archived']}")
        if report["pending"] and report["dry_run"]:
            print("待处理：" + ", ".join(report["pending"][:20]))
        llm_info = report.get("llm") or {}
        if llm_info.get("failed") and not llm_info.get("failed_all"):
            print(f"⚠ LLM 部分失败：{llm_info['note']}", file=sys.stderr)
    llm_info = report.get("llm") or {}
    if llm_info.get("failed_all"):
        # 全挂 = 数据已经写进去了但质量坏了（中文标签/描述全空），
        # 退出码 3 让脚本/Agent 无法把它当成功吞掉（回归：2026-10-30 模型名漂移，10/10 标签静默为空）。
        return 3
    return 0


def cmd_index(args) -> int:
    cfg = config.load_config()
    target = config.store_dir(cfg, args.dir)
    db = config.index_path(cfg, args.index)
    info = index_mod.build(target, db)
    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        print(f"已索引 {info['indexed']}/{info['files']} 个文件 → {info['db']}（{info['tokenizer']}）")
    return 0


def cmd_search(args) -> int:
    cfg = config.load_config()
    db = config.index_path(cfg, args.index)
    try:
        rows = index_mod.search(
            db,
            args.query,
            limit=args.limit,
            category=args.category,
            include_archived=args.include_archived,
        )
    except FileNotFoundError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    _emit(
        {"query": args.query, "count": len(rows), "results": rows},
        args.json,
        _print_results,
    )
    return 0


def cmd_get(args) -> int:
    cfg = config.load_config()
    db = config.index_path(cfg, args.index)
    try:
        row = index_mod.get_repo(db, args.repo)
    except FileNotFoundError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    if row is None:
        print(f"✗ 未收录：{args.repo}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(row, ensure_ascii=False, indent=2))
    else:
        for key in ("repo", "title", "category", "stars", "tags", "url", "path", "description"):
            print(f"{key:12s} {row.get(key)}")
        if args.full and row.get("path"):
            print("\n" + Path(row["path"]).read_text(encoding="utf-8", errors="replace"))
    return 0


def cmd_list(args) -> int:
    cfg = config.load_config()
    db = config.index_path(cfg, args.index)
    try:
        rows = index_mod.list_repos(
            db,
            category=args.category,
            include_archived=args.include_archived,
            limit=args.limit,
        )
    except FileNotFoundError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"count": len(rows), "results": rows}, ensure_ascii=False, indent=2))
    else:
        for row in rows:
            print(f"{row['repo']:52s} ★{row['stars']:<7} {row['category']}")
    return 0


def cmd_stats(args) -> int:
    cfg = config.load_config()
    data = index_mod.stats(config.index_path(cfg, args.index))
    _emit(data, args.json, _print_stats)
    return 0


def cmd_export(args) -> int:
    cfg = config.load_config()
    db = config.index_path(cfg, args.index)
    rows = index_mod.list_repos(db, include_archived=args.include_archived, limit=args.limit)
    if args.format == "json":
        payload = json.dumps(rows, ensure_ascii=False, indent=2)
    else:
        import io

        buffer = io.StringIO()
        fields = ["repo", "title", "category", "stars", "tags", "description", "url", "path"]
        writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            item = dict(row)
            item["tags"] = " ".join(item.get("tags") or [])
            writer.writerow(item)
        payload = buffer.getvalue()
    if args.output:
        Path(args.output).expanduser().write_text(payload, encoding="utf-8")
        print(f"已导出 {len(rows)} 条 → {args.output}", file=sys.stderr)
    else:
        print(payload)
    return 0


def cmd_mcp(args) -> int:
    from . import mcp_server

    return mcp_server.run()


# ------------------------------------------------------------------ 参数解析


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ghstars",
        description="把 GitHub 星标同步成本地可检索的知识库（Markdown + FTS5，中文友好）",
    )
    parser.add_argument("--version", action="version", version=f"ghstars {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="生成配置文件模板")
    p.add_argument("--path", help="配置文件路径（默认平台配置目录）")
    p.add_argument("--force", action="store_true", help="覆盖已有配置")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("doctor", help="环境自检（配置/token/索引/LLM）")
    p.add_argument("--check-llm", action="store_true", help="真发一次请求验证 LLM 连通性")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("sync", help="拉取 GitHub stars 并写入本地")
    p.add_argument("--dir", help="覆盖入库目录")
    p.add_argument("--username", help="覆盖 GitHub 用户名")
    p.add_argument("--token", help="覆盖 GitHub token（默认读环境变量或 gh CLI）")
    p.add_argument("--no-llm", action="store_true", help="不调用 LLM（省 token，无中文标签）")
    p.add_argument("--full", action="store_true", help="忽略断点续传")
    p.add_argument("--dry-run", action="store_true", help="只报告差异，不写盘")
    p.add_argument("--no-index", action="store_true", help="同步后不重建索引")
    p.add_argument("--index", help="覆盖索引路径")
    p.add_argument("--limit", type=int, help="本次最多处理 N 个新项目")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("index", help="重建检索索引")
    p.add_argument("--dir", help="覆盖入库目录")
    p.add_argument("--index", help="覆盖索引路径")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("search", help="检索本地的星标项目")
    p.add_argument("query")
    p.add_argument("-n", "--limit", type=int, default=10)
    p.add_argument("--category", help="限定分类")
    p.add_argument("--include-archived", action="store_true", help="包含已取消 star 的项目")
    p.add_argument("--index", help="覆盖索引路径")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("get", help="查看单个项目")
    p.add_argument("repo")
    p.add_argument("--full", action="store_true", help="打印完整 Markdown")
    p.add_argument("--index", help="覆盖索引路径")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_get)

    p = sub.add_parser("list", help="列出项目（按 star 数排序）")
    p.add_argument("--category")
    p.add_argument("--include-archived", action="store_true")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--index", help="覆盖索引路径")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("stats", help="索引统计")
    p.add_argument("--index", help="覆盖索引路径")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("export", help="导出为 JSON / CSV")
    p.add_argument("--format", choices=["json", "csv"], default="json")
    p.add_argument("--output", help="输出文件（默认打印到 stdout）")
    p.add_argument("--include-archived", action="store_true")
    p.add_argument("--limit", type=int, default=5000)
    p.add_argument("--index", help="覆盖索引路径")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("mcp", help="以 MCP server（stdio）方式运行")
    p.set_defaults(func=cmd_mcp)

    return parser


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130


def _ensure_utf8_console() -> None:
    """Windows 控制台默认 GBK（cp936）时，中文输出会抛 UnicodeEncodeError。

    不改全局 locale，只把标准流切到 UTF-8 并对无法编码的字符降级，
    保证 `ghstars search` 在 cmd.exe / PowerShell 里也不会崩。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
