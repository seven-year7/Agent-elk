"""
 * @Module: app/prompts/tool_calling_prompts
 * @Description: Tool-calling + Memory 场景提示词常量（编排约束 + DSL 降级 + executeDsl + 记忆压缩提取）
 * @Interface: RAG_EXECUTION_CONSTRAINT_PROMPT, FORCE_SIMPLE_DSL_PROMPT, EXECUTE_DSL_TOOL_DESCRIPTION, MEMORY_*_PROMPT_TMPL
"""

RAG_EXECUTION_CONSTRAINT_PROMPT = (
    "RAG 增强执行约束（必须遵守）：\n"
    "1) 先通过 queryByTimeRange/queryByRequestId/executeDsl 拿到日志事实（LogFacts）。\n"
    "2) 再调用 queryKnowledgeBaseHybrid，优先传入 errorTitle/symptom/suspectedRootCause/logExcerpt/"
    "candidateResolution（mcp_summary_only），可附带 serviceName/errorCode 过滤。\n"
    "3) 回答时必须分区：先写【LogFacts】，再写【KBEvidence】，最后写【DiagnosisAndActions】。\n"
    "4) 若 LogFacts 与 KBEvidence 不一致，以 LogFacts 为准，并在结论中说明不一致点。"
)

FORCE_SIMPLE_DSL_PROMPT = (
    "你在 executeDsl 上已连续出现 2 次 DSL 错误。"
    "从现在开始，禁止使用 aggs/sort/script/range/bool 嵌套。"
    "请仅使用最简单的关键词查询：query.match（必要时 match_phrase），"
    "并返回可直接执行的最小 DSL。"
)

EXECUTE_DSL_TOOL_DESCRIPTION = """# Role: Elasticsearch 专家级 DSL 翻译官

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

`dsl` 参数必须是 JSON 字符串。"""

MEMORY_SUMMARY_PROMPT_TMPL = """请简要总结以下运维对话，保留关键服务名、TraceID 和错误结论：

{dialog_content}
"""

MEMORY_KEYPOINT_JSON_PROMPT_TMPL = """你是运维会话压缩助手。请从输入中提取关键内容，返回严格 JSON，不要输出其它文本。
JSON schema:
{{
  "core_conclusion": "string",
  "service_scope": ["string"],
  "error_codes": ["string"],
  "trace_ids": ["string"],
  "action_items": ["string"]
}}
要求：
1) 没有信息时填空字符串或空数组；
2) action_items 最多 4 条，简洁可执行；
3) 保留原始标识符，不要编造。

输入内容：
{input_content}
"""
