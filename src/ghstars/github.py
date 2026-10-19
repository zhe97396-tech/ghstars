"""GitHub API 访问：拉 star 列表、读 README。纯标准库。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request

DEFAULT_API_BASE = "https://api.github.com"
README_CANDIDATES = [
    "README.md", "readme.md", "README.rst", "readme.rst",
    "README.txt", "readme.txt", "README",
]
USER_AGENT = "ghstars/0.1 (+https://github.com/zhe97396-tech/ghstars)"


class GitHubError(RuntimeError):
    """带状态码与治疗建议的 GitHub API 错误。"""

    def __init__(self, message: str, status: int = 0, hint: str = ""):
        super().__init__(message)
        self.status = status
        self.hint = hint


def get_token(cfg: dict, explicit: str | None = None) -> str:
    """token 来源：显式参数 > 配置指定的环境变量 > GITHUB_TOKEN > ``gh auth token``。"""
    if explicit:
        return explicit.strip()
    env_name = str((cfg.get("github") or {}).get("token_env") or "GITHUB_TOKEN")
    for name in (env_name, "GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    gh = shutil.which("gh")
    if gh:
        try:
            result = subprocess.run(
                [gh, "auth", "token"], capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
    return ""


def _api_base(cfg: dict) -> str:
    return str((cfg.get("github") or {}).get("api_base") or DEFAULT_API_BASE).rstrip("/")


def api_get(url: str, token: str = "", timeout: int = 30):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"token {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        remaining = exc.headers.get("X-RateLimit-Remaining") if exc.headers else None
        reset = exc.headers.get("X-RateLimit-Reset") if exc.headers else None
        if exc.code == 401:
            raise GitHubError("GitHub token 无效或已过期", 401,
                              "运行 `gh auth login` 或重新导出 GITHUB_TOKEN") from exc
        if exc.code == 403 and remaining == "0":
            raise GitHubError(
                f"GitHub API 速率限制已用尽（重置时间戳 {reset}）", 403,
                "配置 token 可把限额从 60/小时提到 5000/小时") from exc
        raise GitHubError(f"GitHub API HTTP {exc.code}: {exc.reason}", exc.code) from exc
    except urllib.error.URLError as exc:
        raise GitHubError(f"无法连接 GitHub API: {exc.reason}") from exc


def fetch_stars(cfg: dict, username: str, token: str = "", per_page: int = 100) -> list[dict]:
    """拉取全部 starred 项目（自动翻页）。"""
    base = _api_base(cfg)
    stars: list[dict] = []
    page = 1
    while True:
        url = f"{base}/users/{username}/starred?per_page={per_page}&page={page}"
        data = api_get(url, token)
        if not data:
            break
        for item in data:
            stars.append(
                {
                    "full_name": item["full_name"],
                    "description": item.get("description") or "",
                    "stargazers_count": item.get("stargazers_count") or 0,
                    "language": item.get("language") or "",
                    "topics": item.get("topics") or [],
                    "html_url": item.get("html_url") or "",
                    "starred_at": item.get("starred_at") or "",
                }
            )
        if len(data) < per_page:
            break
        page += 1
    return stars


def fetch_readme(cfg: dict, full_name: str, token: str = "", max_lines: int = 200) -> str:
    """取 README 前 N 行；任何失败都返回空串（调用方降级）。"""
    base = _api_base(cfg)
    headers = {
        "Accept": "application/vnd.github.raw",
        "User-Agent": USER_AGENT,
    }
    if token:
        headers["Authorization"] = f"token {token}"
    for name in README_CANDIDATES:
        url = f"{base}/repos/{full_name}/contents/{name}"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                content = resp.read().decode("utf-8", errors="replace")
            return "\n".join(content.split("\n")[:max_lines])
        except Exception:
            continue
    return ""
