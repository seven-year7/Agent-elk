"""
 * @Module: app/core/config
 * @Description: Core 层默认项：主模型温度、默认模型 ID、网关关键词与相似度阈值（可被环境变量覆盖）
 * @Interface: MAIN_LLM_TEMPERATURE、DEFAULT_LLM_MODEL、OPS_ACTION_KEYWORDS、OPS_OBJECT_KEYWORDS、OPS_SERVICE_KEYWORDS、OPS_DIRECT_KEYWORDS、LOG_SIMILARITY_THRESHOLD
"""

# 主对话补全温度（压低以降低胡编风险）
MAIN_LLM_TEMPERATURE = 0.3

# 未设置 LLM_MODEL / DEFAULT_LLM_MODEL 时的回退模型 ID
DEFAULT_LLM_MODEL = "openai/gpt-4o-mini"

# --- Phase 2.15：第一阶段网关 - 关键词直通 ---
# A类：动作词 (Action Keywords)
OPS_ACTION_KEYWORDS = (
    "查",
    "看",
    "找",
    "检索",
    "分析",
    "诊断",
    "统计",
)

# B类：运维核心对象 (Domain Keywords)
OPS_OBJECT_KEYWORDS = (
    "error",
    "exception",
    "warn",
    "fail",
    "timeout",
    "trace",
    "transaction",
    "transaction_id",
    "service",
    "traceid",
    "trace_id",
    "stack",
    "stacktrace",
    "报错",
    "异常",
    "超时",
    "日志",
    "堆栈",
)

# C类：环境/服务名 (Context Keywords)
# @Agent_Logic: 后续可从 ES 索引动态提取 service_name（当前先用样例覆盖常见服务）。
OPS_SERVICE_KEYWORDS = (
    "pay-service",
    "auth-service",
    "gateway",
    "order-service",
    "db-master",
)

# 汇总所有直通词（命中则 100% 视为查日志，不走 AI 判定）
OPS_DIRECT_KEYWORDS = OPS_ACTION_KEYWORDS + OPS_OBJECT_KEYWORDS + OPS_SERVICE_KEYWORDS

# 向量检索相似度阈值：top_score >= 阈值则认为值得走 RAG 分析
LOG_SIMILARITY_THRESHOLD = 0.75
