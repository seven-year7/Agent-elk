"""
 * @Module: app/tool_calling/mcp_tools
 * @Description: MCP tools 注册（仅描述 + 执行器）：供模型自由选择 tool，由执行器调用 MCP Server
 * @Interface: build_mcp_tool_registry
"""

from __future__ import annotations

import json
from typing import Any

from app.mcp_client.client import McpClient
from app.prompts.tool_calling_prompts import EXECUTE_DSL_TOOL_DESCRIPTION
from app.tool_calling.registry import ToolRegistry, tool


def build_mcp_tool_registry(*, mcp: McpClient) -> ToolRegistry:
    registry = ToolRegistry()

    @tool(
        registry=registry,
        name="queryByTimeRange",
        schema={
            "type": "function",
            "function": {
                "name": "queryByTimeRange",
                "description": "按时间范围查询日志。支持绝对时间 startTime+endTime，或相对时间 lastMinutes/lastHours（二选一）。绝对时间格式 yyyy-MM-dd HH:mm:ss。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "indexName": {"type": "string", "default": "database_v"},
                        "startTime": {"type": "string"},
                        "endTime": {"type": "string"},
                        "lastMinutes": {"type": "integer", "minimum": 1},
                        "lastHours": {"type": "integer", "minimum": 1},
                        "timeZone": {"type": "string", "default": "Asia/Shanghai"},
                        "level": {"type": "string"},
                        "keyword": {"type": "string", "description": "支持 AND/OR"},
                        "logip": {"type": "string"},
                        "size": {"type": "integer", "default": 100, "maximum": 1000},
                        "fields": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
        },
    )
    def _query_by_time_range(args: dict[str, Any]) -> dict[str, Any]:
        return mcp.call_tool("queryByTimeRange", args)

    @tool(
        registry=registry,
        name="queryByRequestId",
        schema={
            "type": "function",
            "function": {
                "name": "queryByRequestId",
                "description": "按 requestId + serviceName 查询日志（直连 ES，不依赖 Hera）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "requestId": {"type": "string"},
                        "serviceName": {"type": "string", "description": "ES 字段名 servicename"},
                        "indexName": {"type": "string", "default": "database_v"},
                        "level": {"type": "string"},
                        "size": {"type": "integer", "default": 100, "maximum": 1000},
                    },
                    "required": ["requestId", "serviceName"],
                },
            },
        },
    )
    def _query_by_request_id(args: dict[str, Any]) -> dict[str, Any]:
        return mcp.call_tool("queryByRequestId", args)

    @tool(
        registry=registry,
        name="executeDsl",
        schema={
            "type": "function",
            "function": {
                "name": "executeDsl",
                "description": EXECUTE_DSL_TOOL_DESCRIPTION,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "indexName": {"type": "string", "default": "database_v"},
                        "dsl": {"type": "string"},
                    },
                    "required": ["dsl"],
                },
            },
        },
    )
    def _execute_dsl(args: dict[str, Any]) -> dict[str, Any]:
        # 兼容：若上游给了 dict，则在客户端侧转成字符串以对齐文档约束
        dsl_val = args.get("dsl")
        if isinstance(dsl_val, dict):
            args = dict(args)
            args["dsl"] = json.dumps(dsl_val, ensure_ascii=False)
        return mcp.call_tool("executeDsl", args)

    @tool(
        registry=registry,
        name="queryKnowledgeBaseHybrid",
        schema={
            "type": "function",
            "function": {
                "name": "queryKnowledgeBaseHybrid",
                "description": (
                    "知识库混合检索（BM25+向量）。用于在日志分析后补充 SOP 与历史案例证据。"
                    "推荐使用 mcp_summary_only 模式：传入 errorTitle/symptom/suspectedRootCause/logExcerpt/candidateResolution，"
                    "服务端会构造摘要查询文本并执行 hybrid 召回。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "errorTitle": {"type": "string"},
                        "symptom": {"type": "string"},
                        "suspectedRootCause": {"type": "string"},
                        "logExcerpt": {"type": "string"},
                        "candidateResolution": {"type": "string"},
                        "serviceName": {"type": "string"},
                        "errorCode": {"type": "string"},
                        "indexName": {"type": "string"},
                        "topK": {"type": "integer", "default": 5, "minimum": 1, "maximum": 20},
                        "candidateK": {"type": "integer", "default": 30, "minimum": 5, "maximum": 200},
                        "bm25MinScore": {"type": "number", "default": 0.1},
                        "vectorMinScore": {"type": "number", "default": 0.7},
                        "mmrLambda": {"type": "number", "default": 0.7},
                    },
                    "required": [],
                },
            },
        },
    )
    def _query_knowledge_base_hybrid(args: dict[str, Any]) -> dict[str, Any]:
        return mcp.call_tool("queryKnowledgeBaseHybrid", args)

    return registry

