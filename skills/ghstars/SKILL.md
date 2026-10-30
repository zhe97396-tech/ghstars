---
name: ghstars
description: Use when the user asks what GitHub projects they starred, wants to find a tool they bookmarked, or asks to sync/pull their GitHub stars. Wraps the `ghstars` CLI.
---

# ghstars — 我的 GitHub 星标库

本 skill 只是**路由层**：所有行为由 `ghstars` CLI 决定，不要自己实现搜索或同步逻辑。

## 何时使用

- "我 star 过什么能剪视频的项目" / "我之前收藏的那个简历工具叫什么"
- "找一下我收藏里做 RAG 的" / "有没有能自动发小红书的"
- "同步一下我的 star" / "拉一下最新星标"

## 调用方式（一律加 `--json`）

```bash
ghstars search "<关键词>" --json        # 中英文均可，返回 repo/分类/中文标签/描述/路径/相关度
ghstars search "<关键词>" -n 20 --category ai-agents --json
ghstars get "<owner/name>" --json       # 单个项目详情；--full 打印完整 Markdown
ghstars list --category <分类> --json   # 按 star 数排序列出
ghstars stats --json                    # 总数 / 已归档 / 分类分布
ghstars sync --json                     # 同步（有副作用）
```

## 铁律

1. **只转述工具返回的内容**，不要凭印象补项目名、star 数或链接。
2. **`sync` 有副作用**（写 Markdown、调用 LLM、重建索引）——用户没明确要求同步时不要跑；
   想先看差异就用 `ghstars sync --dry-run --json`。
3. 报错 `索引不存在` → 提示先 `ghstars sync`（首次）或 `ghstars index`（重建）。
4. 结果为 0 条时直说"库里没有"，不要降级成网络搜索假装是收藏内容；需要找新项目时
   明确区分"你的收藏里没有"和"GitHub 上有"。
5. 用户问"这个项目怎么样"时，`ghstars` 只有描述/标签/star 数 —— 别把它当成 README 全文。
   需要细节就 `ghstars get <repo> --full` 或去看仓库本身。

6. **`sync` 退出码 3 = LLM 全部失败**（模型名/密钥/网关挂了）：文件已写但中文标签全空，
   **不要当成功汇报**。检查 `report.llm.last_error`，修好配置后重跑（先备份并删除受影响 md、
   删 `.sync_state.json` 对应条目），否则这些项目在中文检索里等于不存在。部分失败是 exit 0 +
   `llm.failed > 0`，同样要如实告知用户"有几条没标签"。MCP 的 `stars_sync` 不会抛异常，
   必须读返回里的 `llm.failed_all` 才看得出。

## 输出解读

- `relevance`：字段加权分（标签精确命中 ≈4.95，长描述顺带出现 ≈1），**只用于排序，不是质量评分**。
- `llm`：`{enabled, calls, ok, failed, tag_failed, failed_all, last_error, note}` —— 只有 `sync` 返回。
- `archived: true`：用户已取消 star 的历史项目（默认不返回，除非 `--include-archived`）。
- `path`：本地 Markdown 绝对路径，可直接读取原文。
