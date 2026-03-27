"""
 * @Module: app/tools/log_tools
 * @Description: 供 Agent / ReAct 调用的日志检索工具（通过 MCP Server，避免直连 ES）
 * @Interface: search_logs_tool, es_search_tool
"""

from __future__ import annotations

import logging
from typing import Any

from app.schema.log_schema import LOG_INDEX_NAME
from app.tools.config import SEARCH_LOGS_DEFAULT_TOP_K
from app.mcp_client.client import get_mcp_client

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
    日志检索工具（经 MCP Server）：
    - 取消直连 ES 与向量检索
    - 使用 MCP `executeDsl` 在日志索引上做 query_string 检索（BM25/倒排）

    :param query_text: 用户想查询的描述（如「支付服务为什么报错」）
    :param top_k: 返回最相近的文档条数
    :return: 供 LLM 阅读的纯文本摘要；无结果或失败时返回说明字符串
    """
    # @Step: 1 - 构造检索 DSL（受 MCP Server 白名单约束）
    # @Security: 不向日志打印完整 query 原文（可能含敏感业务描述）
    cleaned = (query_text or "").strip()
    if not cleaned:
        logger.warning("[WARN][LogTools]: 空查询，跳过检索")
        return "未找到相关日志。（查询为空）"

    try:
        dsl: dict[str, Any] = {
            "size": int(top_k),
            "sort": [{"@timestamp": {"order": "desc"}}],
            "query": {"bool": {"must": [{"query_string": {"query": cleaned}}]}},
            "_source": ["@timestamp", "service_name", "log_level", "trace_id", "requestId", "message"],
        }
        client = get_mcp_client()
        payload = client.call_tool("executeDsl", {"indexName": LOG_INDEX_NAME, "dsl": dsl, "size": int(top_k)})
    except Exception as exc:
        logger.error("[ERROR][LogTools]: MCP 检索失败: %s", exc)
        return f"检索失败：MCP 请求异常（{exc!s}）"

    hits = payload.get("hits", [])
    if not hits:
        logger.info("[INFO][LogTools]: 无命中（top_k=%s）", top_k)
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

    logger.info("[INFO][LogTools]: 命中 %s 条", len(lines))
    return "\n".join(lines)


# @Agent_Logic: 任务单中的工具名别名，便于 Prompt 固定为 es_search_tool
es_search_tool = search_logs_tool
