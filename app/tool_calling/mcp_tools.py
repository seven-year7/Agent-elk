"""
 * @Module: app/tool_calling/mcp_tools
 * @Description: MCP tools 注册（仅描述 + 执行器）：供模型自由选择 tool，由执行器调用 MCP Server
 * @Interface: build_mcp_tool_registry
"""

from __future__ import annotations

import json
from typing import Any

from app.mcp_client.client import McpClient
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
                "description": """# Role: Elasticsearch 专家级 DSL 翻译官

# Context:
你正在为工业级 ELK 日志系统生成查询 DSL。
当前索引 Mapping 核心字段如下：
- 时间字段: `@timestamp` (date)
- 过滤字段 (Keyword 类型，使用 term 查询):
    `service_name`, `log_level`, `env`, `region`, `trace_id`, `requestId`, `error_code`, `error_type`
- 数值字段 (使用 range 或 term): `status_code` (integer)
- 文本字段 (使用 match 查询):
    `message` (ik_smart 分词), `raw_message`
- 全文字段 (使用 term 精确匹配): `message.keyword`

# Rules:
0. 动态映射优先: 先基于当前 `indexName` 的实时 mapping 判断字段是否存在与字段类型，再生成 DSL。
1. 时间驱动: 必须包含 `@timestamp` 的 `range` 过滤。当前时间由系统提供。
2. 精确优先: 涉及 `trace_id`、`requestId`、`service_name` 时，必须使用 `term` 查询。
3. 模糊搜索: 涉及日志内容搜索时，使用 `match` 查询 `message` 字段。
4. 性能优化: 默认返回 `size: 10`，并按 `@timestamp` 降序排列。
5. 严禁幻觉: 严禁使用 Mapping 以外的字段（如不存在的 `msg` 或 `serviceID`）。
6. 类型一致性: 
   - `term/terms` 仅用于 keyword/numeric/date/boolean（或 text 的 `.keyword` 子字段）
   - `match/match_phrase` 仅用于 text
   - `range` 仅用于 date/numeric
   - `aggs.terms.field` 仅用于可聚合字段

# Few-Shot Examples:

User: 查一下最近10分钟 pay-service 报错 trace_id 为 T123 的日志
Output:
{
  "query": {
    "bool": {
      "must": [
        { "term": { "service_name": "pay-service" } },
        { "term": { "trace_id": "T123" } },
        { "range": { "@timestamp": { "gte": "now-10m" } } }
      ]
    }
  },
  "sort": [ { "@timestamp": { "order": "desc" } } ]
}

User: 查找昨天 env 为 prod 且状态码为 500 的错误日志，关键词是 "timeout"
Output:
{
  "query": {
    "bool": {
      "must": [
        { "term": { "env": "prod" } },
        { "term": { "status_code": 500 } },
        { "match": { "message": "timeout" } },
        { "range": { "@timestamp": { "gte": "now-1d/d", "lt": "now/d" } } }
      ]
    }
  }
}

`dsl` 参数必须是 JSON 字符串。""",
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
                    },
                    "required": [],
                },
            },
        },
    )
    def _query_knowledge_base_hybrid(args: dict[str, Any]) -> dict[str, Any]:
        return mcp.call_tool("queryKnowledgeBaseHybrid", args)

    return registry

