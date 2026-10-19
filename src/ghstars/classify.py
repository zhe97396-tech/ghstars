"""分类与中文标签/描述生成。

分类三层兜底（顺序不能变，这是经过全库复核的结论：

1. **topics 精确匹配**配置里的映射表 —— 作者自己打的标签，最可信；
2. **描述关键词**推断 —— topics 缺失时兜底；
3. **LLM 从已有分类中选**（附带 README 摘要）—— 规则兜不住时用理解力；
4. ``other`` —— 都不行。

两个历史结论已内建：
- GitHub topics 里的**兼容性标签**（裸 ``claude-code`` / ``anthropic``）只表示"支持/兼容"，
  不代表项目形态，绝不能拿来判断分类；
- 描述关键词里**不要放 "ai"**，否则任何带 ai 的描述都被吞进 ai-agents。
"""

from __future__ import annotations

import re

from . import llm as llm_mod

# 描述关键词（第二优先级）。注意：不含 "ai"。
DESC_KEYWORDS: dict[str, list[str]] = {
    "ai-agents": ["agent", "agentic", "autonomous"],
    "claude-code-skills": ["claude", "codex", "skill", "cursor"],
    "mcp-tools": ["mcp", "model context protocol"],
    "llm-tools": ["llm", "prompt", "gpt", "language model"],
    "browser-automation": ["browser", "scraping", "crawl", "selenium", "playwright"],
    "devops": ["docker", "kubernetes", "ci/cd", "deploy", "infra"],
    "tools-utilities": ["cli", "tool", "utility", "framework"],
    "image-gen": ["image", "diffusion", "generation", "art"],
}

COMPAT_TOPICS_WARNING = (
    "⚠️ topics 里的兼容性标签（claude-code/anthropic/claude 等）不代表项目形态，"
    "要看项目本质（是什么工具/什么形态）。"
)


def classify(
    project: dict,
    categories: dict[str, list[str]],
    *,
    existing_dirs: list[str] | None = None,
    settings: dict | None = None,
    readme: str = "",
) -> str:
    """返回分类名。LLM 不可用时自动停在关键词层。"""
    topics = [str(t).lower() for t in project.get("topics") or []]
    desc = (project.get("description") or "").lower()

    # 第一优先：topics 精确匹配
    for category, keywords in categories.items():
        for kw in keywords:
            if kw.lower() in topics:
                return category

    # 第二优先：描述关键词
    for category, keywords in DESC_KEYWORDS.items():
        if category not in categories:
            continue
        for kw in keywords:
            if kw in desc:
                return category

    # 第三优先：LLM 从"已有目录"中选，不新建分类
    candidates = existing_dirs or list(categories.keys())
    if settings and llm_mod.available(settings) and candidates:
        prompt = f"""根据以下 GitHub 项目信息，从已有分类中选择最匹配的一个分类。
已有分类：{('、'.join(candidates))}
{COMPAT_TOPICS_WARNING}
只输出分类名，不要解释。如果都不匹配，输出 other。

项目名：{project.get('full_name', '')}
描述：{project.get('description', '')}
Topics：{', '.join(project.get('topics') or []) or '无'}
README 摘要：
{(readme or '（无 README）')[:2000]}"""
        result = llm_mod.call_llm(prompt, settings, max_tokens=50)
        picked = result.strip().lower().split()[0] if result.strip() else ""
        if picked in candidates:
            return picked

    return "other"


def generate_tags(project: dict, settings: dict | None, readme: str = "") -> tuple[list[str], str]:
    """生成中文场景标签 + 中文描述。返回 ``(tags, chinese_desc)``。"""
    if not settings or not llm_mod.available(settings):
        return [], ""

    topics_str = "、".join(project.get("topics") or []) or "无"
    prompt = f"""根据以下 GitHub 项目信息，生成：
1. 2-3 句中文描述（第 1 句概括核心功能，第 2-3 句说明适用场景和用户群体，总共 40-80 字）
2. 3-5 个中文场景标签（用于中文检索）

严格按以下格式输出，不要加其他内容：
描述：xxx。适用于xxx等场景。
标签：xxx, xxx, xxx

项目名：{project.get('full_name', '')}
描述：{project.get('description', '')}
Topics：{topics_str}
README 摘要：
{(readme or '（无 README）')[:2000]}"""

    content = llm_mod.call_llm(prompt, settings, max_tokens=300)
    if not content:
        return [], ""

    tags: list[str] = []
    chinese_desc = ""
    desc_match = re.search(r"描述[：:]\s*(.+)", content)
    if desc_match:
        chinese_desc = desc_match.group(1).strip()
    tags_match = re.search(r"标签[：:]\s*(.+)", content)
    if tags_match:
        tags = [t.strip() for t in re.split(r"[,，、\s]+", tags_match.group(1)) if t.strip()][:5]
    return tags, chinese_desc
