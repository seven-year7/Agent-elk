"""
 * @Module: test/test_llm_connectivity
 * @Description: 独立模型连通性/限流诊断脚本：对比小模型(MEMORY_SUMMARY_MODEL)与大模型(LLM_MODEL)调用结果
 * @Interface: main
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI


_OPENROUTER_DEFAULT_BASE = "https://openrouter.ai/api/v1"
_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _ensure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _env_str(name: str) -> str | None:
    raw = os.getenv(name)
    if not isinstance(raw, str):
        return None
    cleaned = raw.strip()
    return cleaned or None


def _mask_secret(value: str | None) -> str:
    if not value:
        return "<empty>"
    if len(value) <= 8:
        return "<set>"
    return value[:4] + "…" + value[-4:]


def _extract_headers_from_exc(exc: BaseException) -> dict[str, str]:
    # openai>=1.x: APIStatusError 通常带 response.headers
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None)
    if isinstance(headers, dict):
        return {str(k): str(v) for k, v in headers.items()}
    return {}


def _pick_headers(headers: dict[str, str]) -> dict[str, str]:
    wanted_prefixes = ("x-ratelimit-", "retry-after")
    picked: dict[str, str] = {}
    for k, v in headers.items():
        lk = k.lower()
        if lk.startswith(wanted_prefixes):
            picked[k] = v
    return picked


def _build_client() -> tuple[OpenAI, dict[str, Any]]:
    """
    @Agent_Logic: 与 app/core/agent_brain.py、app/memory/manager.py 一致：优先 OpenRouter，否则回退 OpenAI。
    @Security: 不输出明文 key。
    """
    router_key = _env_str("OPENROUTER_API_KEY")
    openai_key = _env_str("OPENAI_API_KEY")

    referer = _env_str("OPENROUTER_HTTP_REFERER")
    app_title = _env_str("OPENROUTER_APP_NAME")
    default_headers: dict[str, str] = {}
    if referer:
        default_headers["HTTP-Referer"] = referer
    if app_title:
        default_headers["X-Title"] = app_title

    if router_key:
        base_url = _env_str("OPENROUTER_BASE_URL") or _OPENROUTER_DEFAULT_BASE
        kwargs: dict[str, Any] = {"api_key": router_key, "base_url": base_url}
        if default_headers:
            kwargs["default_headers"] = default_headers
        return OpenAI(**kwargs), {
            "provider": "openrouter",
            "base_url": base_url,
            "key_masked": _mask_secret(router_key),
        }

    if not openai_key:
        raise SystemExit("缺少 OPENROUTER_API_KEY / OPENAI_API_KEY：无法测试模型连通性。")

    kwargs = {"api_key": openai_key}
    base_url = _env_str("OPENAI_BASE_URL")
    if base_url:
        kwargs["base_url"] = base_url
    if default_headers:
        kwargs["default_headers"] = default_headers
    return OpenAI(**kwargs), {
        "provider": "openai_compatible",
        "base_url": base_url or "<default>",
        "key_masked": _mask_secret(openai_key),
    }


@dataclass(frozen=True)
class ModelTestCase:
    name: str
    model: str
    prompt: str


def _json_print(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True))


def _call_once(client: OpenAI, case: ModelTestCase) -> dict[str, Any]:
    started_ms = int(time.time() * 1000)
    try:
        resp = client.chat.completions.create(
            model=case.model,
            messages=[
                {"role": "system", "content": "You are a connectivity test. Reply with a single short sentence."},
                {"role": "user", "content": case.prompt},
            ],
            temperature=0.0,
            max_tokens=32,
        )
        content = (resp.choices[0].message.content or "").strip()
        return {
            "name": case.name,
            "model": case.model,
            "ok": True,
            "elapsed_ms": int(time.time() * 1000) - started_ms,
            "content_preview": content[:200],
        }
    except Exception as exc:
        headers = _extract_headers_from_exc(exc)
        picked_headers = _pick_headers(headers)
        err = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
        # 尝试提取 OpenAI 兼容错误对象结构（best-effort）
        try:
            body = getattr(exc, "body", None)
            if isinstance(body, dict):
                err["body"] = body
        except Exception:
            pass

        return {
            "name": case.name,
            "model": case.model,
            "ok": False,
            "elapsed_ms": int(time.time() * 1000) - started_ms,
            "error": err,
            "rate_limit_headers": picked_headers,
        }


def main() -> int:
    _ensure_utf8_stdio()

    # @Step: 0 - 加载仓库根目录 .env（与现有模块一致）
    load_dotenv(_PROJECT_ROOT / ".env")

    small_model = _env_str("MEMORY_SUMMARY_MODEL") or "xiaomi/mimo-v2-flash"
    large_model = _env_str("LLM_MODEL") or _env_str("DEFAULT_LLM_MODEL") or "openai/gpt-4o-mini"

    client, client_meta = _build_client()

    print("=== LLM Connectivity Test ===")
    _json_print(
        {
            "client": client_meta,
            "models": {"small": small_model, "large": large_model},
            "tips": [
                "若 small 返回 429/Rate limit，而 large 正常：基本可判定为 free 小模型上游限流。",
                "若出现 402/Spend limit exceeded：通常是该 API Key 设置了花费上限或额度不足。",
            ],
        }
    )
    print()

    cases = [
        ModelTestCase(name="small", model=small_model, prompt="ping (small model)"),
        ModelTestCase(name="large", model=large_model, prompt="ping (large model)"),
    ]

    results = [_call_once(client, c) for c in cases]
    _json_print({"results": results})

    ok_all = all(r.get("ok") is True for r in results)
    return 0 if ok_all else 2


if __name__ == "__main__":
    raise SystemExit(main())

