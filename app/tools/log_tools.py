"""
 * @Module: app/tools/log_tools
 * @Description: 供 Agent / ReAct 调用的日志语义检索工具（kNN + message_embedding）
 * @Interface: search_logs_tool, es_search_tool
"""

from __future__ import annotations

import logging
from typing import Any

from app.schema.log_schema import LOG_INDEX_NAME
from app.tools.config import (
    KNN_NUM_CANDIDATES_MIN,
    KNN_NUM_CANDIDATES_PER_TOP_K,
    SEARCH_LOGS_DEFAULT_TOP_K,
)
from app.tools.es_client import es_node
from app.utils.embedding import embedder

logger = logging.getLogger(__name__)


def _search_response_as_dict(resp: Any) -> dict[str, Any]:
    body = getattr(resp, "body", None)
    if isinstance(body, dict):
        return body
    if isinstance(resp, dict):
        return resp
    return {}


def search_logs_tool(query_text: str, top_k: int = SEARCH_LOGS_DEFAULT_TOP_K) -> str:
    """
    语义搜索日志工具：将自然语言查询向量化，对 `message_embedding` 做 kNN 检索。

    :param query_text: 用户想查询的描述（如「支付服务为什么报错」）
    :param top_k: 返回最相近的文档条数
    :return: 供 LLM 阅读的纯文本摘要；无结果或失败时返回说明字符串
    """
    # @Step: 1 - 查询向量化（与写入侧同一 embedder，保证空间一致）
    # @Security: 不向日志打印完整 query 原文（可能含敏感业务描述）；仅 DEBUG 可酌情开启
    cleaned = (query_text or "").strip()
    if not cleaned:
        logger.warning("[WARN][LogTools]: 空查询，跳过检索")
        return "未找到相关日志。（查询为空）"

    try:
        query_vector = embedder.get_embedding(cleaned)
    except Exception as exc:
        logger.error("[ERROR][LogTools]: 查询向量化失败: %s", exc)
        return f"检索失败：无法生成查询向量（{exc!s}）"

    num_candidates = max(KNN_NUM_CANDIDATES_MIN, top_k * KNN_NUM_CANDIDATES_PER_TOP_K)

    try:
        # @Step: 2 - ES 8.x：使用 knn 参数，而非整包 body（与 elasticsearch-py 对齐）
        # @Agent_Logic: 仅返回必要 _source 字段，降低 Token 占用与泄露面
        response = es_node.client.search(
            index=LOG_INDEX_NAME,
            knn={
                "field": "message_embedding",
                "query_vector": query_vector,
                "k": top_k,
                "num_candidates": num_candidates,
            },
            source_includes=["@timestamp", "log_level", "service_name", "message"],
        )
    except Exception as exc:
        logger.error("[ERROR][LogTools]: ES 检索失败: %s", exc)
        return f"检索失败：Elasticsearch 请求异常（{exc!s}）"

    payload = _search_response_as_dict(response)
    hits = payload.get("hits", {}).get("hits", [])
    if not hits:
        logger.info("[INFO][LogTools]: kNN 无命中（top_k=%s）", top_k)
        return "未找到相关日志。"

    lines: list[str] = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        src = hit.get("_source") or {}
        ts = src.get("@timestamp", "")
        svc = src.get("service_name", "")
        level = src.get("log_level", "")
        msg = src.get("message", "")
        lines.append(f"[{ts}] {svc} | {level}: {msg}")

    logger.info("[INFO][LogTools]: kNN 命中 %s 条", len(lines))
    return "\n".join(lines)


# @Agent_Logic: 任务单中的工具名别名，便于 Prompt 固定为 es_search_tool
es_search_tool = search_logs_tool
