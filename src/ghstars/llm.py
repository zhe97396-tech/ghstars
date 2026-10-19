"""LLM 调用（OpenAI 兼容 /chat/completions），零 SDK 依赖。

只认自己的配置（见 :mod:`ghstars.config`），不读取任何宿主 Agent 的内部配置。
内置三个真实网关坑的规避（都是实际踩过的，不是防御性编程）：

1. **默认 UA 被拒**：urllib 的 ``Python-urllib/3.x`` 会被部分网关 403，统一用浏览器 UA。
2. **opencode.ai 强制会话头**：缺 ``x-opencode-session`` 直接 400 ``MissingSessionID``。
3. **deepseek-v4 系列 thinking 耗尽 token**：长文本下 reasoning 吃掉 completion
   budget，``content`` 返回空（``finish_reason=length``），需显式关闭 thinking。
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
import uuid

BROWSER_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) EdgiOS/121.0.2277.107 "
    "Version/17.0 Mobile/15E148 Safari/604.1"
)

OPENCODE_SESSION_HEADER = "x-opencode-session"

_last_error = ""


def available(settings: dict) -> bool:
    """配置齐全（base + key + model）才算可用。"""
    return bool(settings.get("base") and settings.get("key") and settings.get("model"))


def last_error() -> str:
    return _last_error


def resolve_model(model: str, base: str) -> str:
    """把别名映射成厂商真实模型名（网关直传，官方 API 需映射）。"""
    m = (model or "").strip().lower()
    b = (base or "").lower()
    if "opencode.ai" in b or "zen" in b:
        return model
    if "deepseek.com" in b or "deepseek" in b:
        if m in ("deepseek-v4-flash", "deepseek-v4", "deepseek-chat"):
            return "deepseek-chat"
        if m in ("deepseek-v4-pro", "deepseek-v4-pro[1m]", "deepseek-reasoner", "deepseek-r1"):
            return "deepseek-reasoner"
    return model


def candidate_models(model: str, base: str) -> list[str]:
    """主模型 + 同厂备选（仅当别名被解析成厂商真名时才追加）。"""
    resolved = resolve_model(model, base)
    out = [resolved or "deepseek-chat"]
    if resolved and resolved != (model or ""):
        for fallback in ("deepseek-chat", "deepseek-reasoner"):
            if fallback not in out:
                out.append(fallback)
    return out


def _headers(settings: dict) -> dict:
    base = settings.get("base", "")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {settings.get('key', '')}",
        "User-Agent": BROWSER_UA,
    }
    if "opencode.ai" in base.lower():
        headers[OPENCODE_SESSION_HEADER] = f"oneshot-{uuid.uuid4().hex[:16]}"
    headers.update(settings.get("headers") or {})
    return headers


def call_llm(
    prompt: str,
    settings: dict,
    *,
    max_tokens: int = 300,
    temperature: float = 0.1,
    timeout: int | None = None,
) -> str:
    """调用 LLM，失败返回空字符串（错误信息通过 :func:`last_error` 取）。"""
    global _last_error
    if not available(settings):
        _last_error = "LLM 未配置（base/key/model 不全）"
        return ""

    url = f"{settings['base']}/chat/completions"
    timeout = timeout or int(settings.get("timeout") or 60)
    _last_error = ""

    for model in candidate_models(settings.get("model", ""), settings.get("base", "")):
        body: dict = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if "deepseek-v4" in model:
            body["thinking"] = {"type": "disabled"}
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers=_headers(settings),
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            content = (payload["choices"][0]["message"].get("content") or "").strip()
            if content:
                return content
            _last_error = f"model={model} 返回空内容"
        except urllib.error.HTTPError as exc:  # 打印响应体，MissingSessionID 才看得见
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            _last_error = f"model={model} HTTP {exc.code}: {detail or exc.reason}"
        except Exception as exc:
            _last_error = f"model={model}: {exc}"

    print(f"  ⚠ LLM 调用失败（{_last_error}）", file=sys.stderr)
    return ""
