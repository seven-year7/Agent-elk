"""
 * @Module: app/utils
 * @Description: 嵌入向量、时间解析等通用工具
 * @Interface: EmbeddingGenerator, embedder（惰性加载，见 __getattr__）
"""

from __future__ import annotations

from typing import Any

__all__ = ["EmbeddingGenerator", "embedder"]


def __getattr__(name: str) -> Any:
    # @Agent_Logic: 避免 import app.utils 时立刻实例化 OpenAI 客户端（无 Key 会误伤其它子模块）
    if name == "EmbeddingGenerator":
        from app.utils.embedding import EmbeddingGenerator

        return EmbeddingGenerator
    if name == "embedder":
        from app.utils.embedding import embedder

        return embedder
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
