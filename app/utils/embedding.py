"""
 * @Module: app/utils/embedding
 * @Description: 文本嵌入（默认经 OpenRouter 调用 OpenAI 兼容 /v1/embeddings，可回退直连 OpenAI）
 * @Interface: EmbeddingGenerator, embedder
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

logger = logging.getLogger(__name__)

# @Step: 1 - 与 es_client 一致：从仓库根目录加载 .env
# @Security: 禁止在日志中输出 OPENROUTER_API_KEY、OPENAI_API_KEY
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")

_OPENROUTER_DEFAULT_BASE = "https://openrouter.ai/api/v1"


def _env_str(name: str) -> str | None:
    raw = os.getenv(name)
    if not isinstance(raw, str):
        return None
    stripped = raw.strip()
    return stripped or None


def _build_openai_client_for_embeddings() -> tuple[OpenAI, str]:
    # @Step: 2 - 优先 OpenRouter；否则回退 OPENAI_API_KEY（直连或自建网关）
    # @Agent_Logic: OpenRouter 模型 ID 通常为 vendor/model，如 openai/text-embedding-3-small
    router_key = _env_str("OPENROUTER_API_KEY")
    if router_key:
        base_url = _env_str("OPENROUTER_BASE_URL") or _OPENROUTER_DEFAULT_BASE
        model = _env_str("EMBEDDING_MODEL") or "openai/text-embedding-3-small"

        default_headers: dict[str, str] = {}
        referer = _env_str("OPENROUTER_HTTP_REFERER")
        app_title = _env_str("OPENROUTER_APP_NAME")
        if referer:
            default_headers["HTTP-Referer"] = referer
        if app_title:
            default_headers["X-Title"] = app_title

        client_kwargs: dict = {"api_key": router_key, "base_url": base_url}
        if default_headers:
            client_kwargs["default_headers"] = default_headers

        client = OpenAI(**client_kwargs)
        logger.info(
            "[INFO][Embedding]: OpenRouter 已配置（model=%s, base_url=%s, extra_headers=%s）",
            model,
            base_url,
            bool(default_headers),
        )
        return client, model

    openai_key = _env_str("OPENAI_API_KEY")
    if not openai_key:
        raise ValueError(
            "请配置 OPENROUTER_API_KEY（推荐，向量经 OpenRouter）"
            " 或 OPENAI_API_KEY（直连 OpenAI / 其它兼容网关）"
        )

    base_url = _env_str("OPENAI_BASE_URL")
    model = _env_str("EMBEDDING_MODEL") or "text-embedding-3-small"
    client_kwargs = {"api_key": openai_key}
    if base_url:
        client_kwargs["base_url"] = base_url

    client = OpenAI(**client_kwargs)
    logger.info(
        "[INFO][Embedding]: 使用 OPENAI_API_KEY（model=%s, base_url_set=%s）",
        model,
        bool(base_url),
    )
    return client, model


class EmbeddingGenerator:
    """将日志等短文本编码为稠密向量，供 `message_embedding` 字段写入。"""

    def __init__(self) -> None:
        self._client, self._model = _build_openai_client_for_embeddings()

    def get_embedding(self, text: str) -> list[float]:
        # @Agent_Logic: 换行清洗可减少部分模型对控制字符的敏感波动
        # @Security: 不向 DEBUG 日志输出完整用户/日志原文，避免敏感数据落盘
        cleaned = text.replace("\n", " ").strip()
        if not cleaned:
            raise ValueError("嵌入输入不能为空")

        response = self._client.embeddings.create(input=[cleaned], model=self._model)
        vector = response.data[0].embedding
        logger.debug("[DEBUG][Embedding]: 向量维度=%s", len(vector))
        return vector


# @Agent_Logic: 模块级单例，便于脚本与后续 Tool 直接引用
embedder = EmbeddingGenerator()
