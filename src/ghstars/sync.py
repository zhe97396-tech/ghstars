"""同步编排：GitHub → 去重 → 分类/标签 → 写 Markdown → 归档 → 重建索引。

CLI 与 MCP 共用本模块，保证两条入口的行为完全一致（不存在"命令行一次、工具里一次"的漂移）。
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import classify as classify_mod
from . import github, index as index_mod, llm as llm_mod, store
from .config import categories as cfg_categories
from .config import github_username, index_path, llm_settings, store_dir


def _log(message: str = "", *, quiet: bool = False) -> None:
    if not quiet:
        print(message, file=sys.stderr, flush=True)


def _warn(message: str) -> None:
    """必须让用户/调用方看见的告警：不受 quiet 抑制（stderr，不污染 --json 的 stdout）。"""
    print(message, file=sys.stderr, flush=True)


def sync(
    cfg: dict,
    *,
    store_override: str | None = None,
    username: str | None = None,
    token: str | None = None,
    use_llm: bool = True,
    full: bool = False,
    dry_run: bool = False,
    rebuild_index: bool = True,
    index_override: str | None = None,
    limit: int | None = None,
    quiet: bool = False,
) -> dict:
    """执行一次同步。``dry_run=True`` 时不写任何文件、不调用 LLM。"""
    target = store_dir(cfg, store_override)
    user = github_username(cfg, username)
    if not user:
        raise ValueError(
            "未配置 GitHub 用户名：设置 GHSTARS_GITHUB_USERNAME 或在 config.toml 的 [github] username"
        )

    token_value = github.get_token(cfg, token)
    if not token_value:
        _log("⚠ 未找到 GitHub token，将用匿名请求（60 次/小时，容易限流）")

    categories = cfg_categories(cfg)
    settings = llm_settings(cfg) if use_llm else {}
    llm_on = bool(use_llm and llm_mod.available(settings))
    llm_mod.reset_stats()

    _log("=" * 52)
    _log(f"ghstars sync  user={user}")
    _log(f"  入库目录：{target}")
    _log(f"  LLM：{'开启（中文标签/描述）' if llm_on else '关闭（英文描述 + 关键词分类）'}")
    if dry_run:
        _log("  模式：DRY-RUN（只报告，不写盘）")
    _log("=" * 52)

    _log("\n① 拉取 GitHub star 列表…")
    stars = github.fetch_stars(cfg, user, token_value)
    _log(f"   远端共 {len(stars)} 个项目")

    _log("\n② 与本地已有文件对比…")
    existing = store.scan_existing(target)
    remote = {s["full_name"].lower() for s in stars}
    local = set(existing)
    new_repos = remote - local
    removed_repos = local - remote
    # 区分"本次需要归档"和"历史上已归档"：后者只是不再出现在远端，
    # 每次都报成"取消 star"会让报告数字虚高（老脚本就踩过这个坑）。
    removed_pending = sorted(r for r in removed_repos if not store.is_archived(existing[r]))
    removed_already = len(removed_repos) - len(removed_pending)
    _log(
        f"   新增 {len(new_repos)} / 已有 {len(remote & local)} / 待归档 {len(removed_pending)}"
        + (f" / 已归档 {removed_already}" if removed_already else "")
    )

    pending = [s for s in stars if s["full_name"].lower() in new_repos]
    if not full and not dry_run:
        processed = {p.lower() for p in store.load_sync_state(target).get("processed", [])}
        resume_filtered = [p for p in pending if p["full_name"].lower() not in processed]
        if len(resume_filtered) != len(pending):
            _log(f"   断点续传：跳过历史已处理 {len(pending) - len(resume_filtered)} 个")
        pending = resume_filtered
    if limit is not None:
        pending = pending[:limit]

    report: dict = {
        "store": str(target),
        "username": user,
        "dry_run": dry_run,
        "remote_total": len(stars),
        "new": len(new_repos),
        "skipped": len(remote & local),
        "removed": len(removed_pending),
        "already_archived": removed_already,
        "added": 0,
        "archived": 0,
        "normalized": 0,
        "failures": [],
        "added_repos": [],
        "archived_repos": [],
        "pending": [p["full_name"] for p in pending],
    }

    if dry_run:
        _log("\n③ DRY-RUN：跳过写盘与 LLM")
        report["index"] = {"skipped": True}
        return report

    target.mkdir(parents=True, exist_ok=True)
    state = store.load_sync_state(target)
    tag_failed = 0

    if pending:
        _log(f"\n③ 处理 {len(pending)} 个新项目…")
        for i, project in enumerate(pending, start=1):
            full_name = project["full_name"]
            _log(f"\n   [{i}/{len(pending)}] {full_name}")
            readme = ""
            try:
                readme = github.fetch_readme(cfg, full_name, token_value)
                _log(f"      README {len(readme.splitlines())} 行" if readme else "      无 README，降级处理")
            except Exception as exc:
                _log(f"      README 获取失败（{exc}），降级处理")

            existing_dirs = [d.name for d in target.iterdir() if d.is_dir()] if target.exists() else []
            category = classify_mod.classify(
                project,
                categories,
                existing_dirs=existing_dirs or None,
                settings=settings if llm_on else None,
                readme=readme,
            )
            tags: list[str] = []
            chinese_desc = ""
            if llm_on:
                tags, chinese_desc = classify_mod.generate_tags(project, settings, readme)
                _log(f"      标签：{', '.join(tags)}" if tags else "      ⚠ 标签生成失败，跳过")
                if not tags:
                    tag_failed += 1
                if chinese_desc:
                    _log(f"      中文描述：{chinese_desc}")
            try:
                path = store.write_project(target, project, category, tags, chinese_desc)
                _log(f"      ✓ {category}/{path.name}")
            except Exception as exc:
                _log(f"      ✗ 写盘失败：{exc}")
                report["failures"].append({"repo": full_name, "error": str(exc)})
                continue

            report["added"] += 1
            report["added_repos"].append(full_name)
            if full_name not in state.setdefault("processed", []):
                state["processed"].append(full_name)
            store.save_sync_state(target, state)

    if removed_pending:
        _log(f"\n④ 归档 {len(removed_pending)} 个已取消 star 的项目…")
        archive_result = store.archive_removed(existing, set(removed_pending))
        report["archived"] = archive_result["archived"]
        report["normalized"] = archive_result["normalized"]
        report["archived_repos"] = [c[0] for c in archive_result["changed"]]
        if archive_result["normalized"]:
            _log(f"   归一（修复历史后缀叠加）：{archive_result['normalized']}")

    if rebuild_index:
        _log("\n⑤ 重建检索索引…")
        db = index_path(cfg, index_override)
        info = index_mod.build(target, db)
        report["index"] = info
        _log(f"   已索引 {info['indexed']}/{info['files']} 个文件（{info['tokenizer']}）")

    store.write_index(target)

    llm_stats = llm_mod.stats() if llm_on else {"calls": 0, "ok": 0, "failed": 0, "last_error": ""}
    failed_all = bool(llm_on and llm_stats["calls"] and not llm_stats["ok"])
    report["llm"] = {
        "enabled": llm_on,
        "calls": llm_stats["calls"],
        "ok": llm_stats["ok"],
        "failed": llm_stats["failed"],
        "tag_failed": tag_failed,
        "failed_all": failed_all,
        "last_error": llm_stats["last_error"],
        "note": "",
    }
    if llm_on and llm_stats["failed"]:
        if failed_all:
            report["llm"]["note"] = (
                f"LLM 全部失败（{llm_stats['failed']} 次调用无一次成功），"
                f"{tag_failed} 个项目已按英文描述入库、中文标签为空"
            )
            _warn(
                f"\n⚠ LLM 全部失败：{llm_stats['failed']} 次调用无一次成功 —— {llm_stats['last_error']}"
            )
            _warn(f"  受影响：{tag_failed}/{report['added']} 个新项目无中文标签/描述（已按英文描述入库）")
            _warn("  修好后补标签：备份并删除这些 md → 删 .sync_state.json 对应条目 → 重跑 sync")
        else:
            report["llm"]["note"] = (
                f"LLM 部分失败（{llm_stats['failed']}/{llm_stats['calls']}），{tag_failed} 个项目无中文标签"
            )
            _warn(
                f"\n⚠ LLM 部分失败：{llm_stats['failed']}/{llm_stats['calls']} 次调用失败 —— {llm_stats['last_error']}"
            )
            _warn(f"  受影响：{tag_failed} 个项目无中文标签/描述")

    _log("\n完成：" + f"新增 {report['added']} / 跳过 {report['skipped']} / 归档 {report['archived']}")
    return report
