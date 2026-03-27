"""
 * @Module: app/database/vector_service
 * @Description: 负责向量化（Embedding）与批量入库（写入 Elasticsearch）
 * @Interface: N/A
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")


def _env_str(name: str) -> str | None:
    raw = os.getenv(name)
    if not isinstance(raw, str):
        return None
    stripped = raw.strip()
    return stripped or None


def _build_embedding_client() -> OpenAI:
    """
    @Agent_Logic: 与项目现有约定一致：优先走 OpenRouter（OPENROUTER_API_KEY），否则回退 OPENAI_API_KEY。
    """
    router_key = _env_str("OPENROUTER_API_KEY")
    openai_key = _env_str("OPENAI_API_KEY")

    default_headers: dict[str, str] = {}
    referer = _env_str("OPENROUTER_HTTP_REFERER")
    app_title = _env_str("OPENROUTER_APP_NAME")
    if referer:
        default_headers["HTTP-Referer"] = referer
    if app_title:
        default_headers["X-Title"] = app_title

    if router_key:
        base_url = _env_str("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1"
        kwargs: dict = {"api_key": router_key, "base_url": base_url}
        if default_headers:
            kwargs["default_headers"] = default_headers
        logger.info("[INFO][VectorService]: Embedding 客户端使用 OpenRouter（base_url=%s）", base_url)
        return OpenAI(**kwargs)

    if not openai_key:
        raise ValueError("Embedding 需要 OPENROUTER_API_KEY 或 OPENAI_API_KEY")

    kwargs = {"api_key": openai_key}
    base_url = _env_str("OPENAI_BASE_URL")
    if base_url:
        kwargs["base_url"] = base_url
    if default_headers:
        kwargs["default_headers"] = default_headers

    logger.info("[INFO][VectorService]: Embedding 客户端使用 OPENAI_API_KEY（base_url_set=%s）", bool(base_url))
    return OpenAI(**kwargs)


_client = _build_embedding_client()


def _resolve_embedding_model() -> str:
    # @Agent_Logic: OpenRouter 上 Embeddings 模型 ID 通常为 vendor/model（与 app/utils/embedding.py 一致）
    # 允许用户通过 EMBEDDING_MODEL 覆盖。
    return _env_str("EMBEDDING_MODEL") or "openai/text-embedding-3-small"


def get_embedding(text: str) -> list[float] | None:
    """
    将文本转化为向量。建议拼接 Title 和 Content 以获得更完整语义。
    """
    cleaned = (text or "").replace("\n", " ").strip()
    if not cleaned:
        return None

    model = _resolve_embedding_model()
    try:
        response = _client.embeddings.create(
            input=[cleaned],
            model=model,
        )
        return list(response.data[0].embedding)
    except Exception as exc:
        logger.error("[ERROR][VectorService]: Embedding 失败（model=%s）：%s", model, exc)
        return None

