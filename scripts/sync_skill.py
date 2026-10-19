#!/usr/bin/env python3
"""把仓库里的 skill 同步到各宿主目录（维护用，不是运行时依赖）。

skill 的唯一来源是 ``skills/ghstars/SKILL.md``；改完跑一次本脚本即可，
避免"三个宿主三份拷贝各自漂移"。

用法：
    python scripts/sync_skill.py            # 自动探测已存在的宿主目录
    python scripts/sync_skill.py --list     # 只看会装到哪
    python scripts/sync_skill.py --target ~/.claude/skills
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE = REPO_ROOT / "skills" / "ghstars"

# 各宿主的 skill 目录约定（存在才装，不创建无关目录）
CANDIDATES = [
    ("Claude Code", Path.home() / ".claude" / "skills"),
    ("Codex", Path.home() / ".codex" / "skills"),
    ("Hermes", Path.home() / "AppData" / "Local" / "hermes" / "skills"),
    ("Hermes (XDG)", Path.home() / ".hermes" / "skills"),
]


def targets(explicit: list[str] | None) -> list[tuple[str, Path]]:
    if explicit:
        return [(str(Path(p).expanduser()), Path(p).expanduser()) for p in explicit]
    found = []
    for name, path in CANDIDATES:
        if path.parent.exists():
            found.append((name, path))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description="把 ghstars skill 同步到各 Agent 宿主")
    parser.add_argument("--target", action="append", help="额外/指定 skill 目录（可多次）")
    parser.add_argument("--list", action="store_true", help="只列出目标，不写入")
    args = parser.parse_args()

    if not (SOURCE / "SKILL.md").exists():
        print(f"✗ 找不到 skill 源：{SOURCE}/SKILL.md", file=sys.stderr)
        return 2

    found = targets(args.target)
    if not found:
        print("没有检测到任何宿主 skill 目录；用 --target 指定", file=sys.stderr)
        return 1

    for name, base in found:
        dest = base / "ghstars"
        if args.list:
            print(f"{name:16s} → {dest}")
            continue
        base.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(SOURCE, dest)
        print(f"✓ {name:16s} → {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
