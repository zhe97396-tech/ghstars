# Changelog

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本遵循 [SemVer](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Fixed
- **中文检索召回**：旧版把整段中文当一个检索词（`"视频制作"`），只有文档里连续出现这几个字才命中——实测「简历模板」「语音转文字」「知识库检索」「股票量化」「读书笔记」5 条常见查询直接 0 命中。现改为 **2 字滑窗 bigram** 切分，词序不同也能召回，仍不引入分词器（保持零依赖）。
- **排序**：字段加权分乘 **idf**（稀有词压过高频词），不再出现"高频词 + star 数"主导排序；`"AI 视频生成"` 旧版 top3 是网站克隆/项目合集，现在 top3 是真正的 AI 视频生成项目。
- **证据分层**：整串命中（phrase）> 全部检索词命中（full）> 部分命中（partial），不跨层比分数；覆盖率不足一半的结果直接丢弃——宁可返回"没找到"，也不硬凑噪声。
- 搜索结果新增 `match` 字段（`phrase` / `full` / `partial`）；CLI 对全 partial 的结果打印「只找到弱相关」提示。

### Changed
- 检索改用 SQL LIKE 圈候选（语料规模下更快且能覆盖 <3 字符词），`repos_fts` 索引仍保留供将来扩展。
- 单元测试 15 → 19（新增召回、分层、空结果语义用例）。

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
