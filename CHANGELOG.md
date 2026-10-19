# Changelog

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本遵循 [SemVer](https://semver.org/lang/zh-CN/)。

## [0.1.0] — 2026-10-20

首个可用版本。

### Added
- `sync`：GitHub stars → 去重 → 分类 → 中文标签/描述 → Markdown → 归档 → 重建索引
- `search` / `get` / `list` / `stats` / `export` / `index` / `doctor` / `mcp` 子命令
- SQLite FTS5（trigram）索引，零第三方依赖；Markdown 为唯一权威源，索引可重建
- 字段加权排序（repo > title/tags > topics/chinese > 英文描述 + 精确命中加成）
- 2 字中文查询走 LIKE 兜底（无需 jieba）
- LLM 可选：OpenAI 兼容接口；内置浏览器 UA、opencode 会话头、deepseek-v4 thinking 关闭
- MCP server（stdio）：`stars_search` / `stars_get` / `stars_stats` / `stars_sync` / `stars_reindex`
- 可移植 skill：`skills/ghstars/SKILL.md` + `scripts/sync_skill.py` 同步到各宿主
- 幂等归档（整值匹配，历史叠加态归一）、repo 键小写去重、待归档/已归档分开计数
- Windows 控制台 UTF-8 兜底；`--json` 输出与 stderr 日志分离
- 15 个单元测试；GitHub Actions（3.11/3.13 × ubuntu/windows）

### Known limitations
- 仓库改名/换 owner 会被判为新 star（未做 repo_id 稳定键追踪）
- 删除 Markdown 文件后索引需重建
- 未发布 PyPI，安装使用 git 源
