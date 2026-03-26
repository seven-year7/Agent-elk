"""
 * @Module: app/prompts/templates
 * @Description: 运维对话 Agent 的系统提示与用户分析模板（与代码解耦，便于提示词工程迭代）
 * @Interface: SYSTEM_PROMPT, ANALYSIS_PROMPT_TMPL, INTENT_EXTRACT_TMPL
"""

# @Agent_Logic: 模板仅定义「说什么」；占位符由 agent_brain 在运行时 format，避免硬编码在类体内

# --- 1. 系统角色定义 (System Prompt) ---
# 作用：定义 Agent 的人格、专业边界和思维方式
SYSTEM_PROMPT = """你是一个专业的 ELK 运维专家 Agent，拥有极强的日志分析和故障排查能力。

### 你的核心任务：
1. 分析用户输入的运维问题。
2. 结合检索到的实时日志，识别异常模式（如 Error, Exception, Timeout, 性能瓶颈）。
3. 给出具有落地价值的诊断结论和修复建议。

### 你的思维规范：
- **客观性**：只根据提供的日志说话，不要凭空想象。
- **关联性**：尝试关联不同服务（Service）之间的 TraceID。
- **简洁性**：运维人员时间宝贵，结论要直接、清晰。

### 你的局限：
- 如果日志中没有相关信息，请诚实告知，不要给出误导性的推测。
"""

# --- 2. 检索分析模板 (Analysis Template) ---
# 作用：将检索到的原始数据与用户问题进行「缝合」
# @Security: logs_context 可能含敏感信息，仅经 LLM 通道传递，勿写入持久化日志
ANALYSIS_PROMPT_TMPL = """
【当前系统时间】: {current_time}
【用户提出的问题】: {user_query}

【实时检索到的日志片段】:
---
{logs_context}
---

请根据上述日志进行分析：
1. **发生了什么问题？** (简要概括)
2. **可能的根因是什么？** (基于日志证据)
3. **建议的解决步骤是什么？**
"""

# --- 3. 意图提取模板 (未来进阶使用) ---
# 作用：专门用于从模糊对话中提取出结构化的查询参数
INTENT_EXTRACT_TMPL = """
从用户的问题中提取以下信息并返回 JSON：
- service_name: (服务名，未知则为 null)
- time_range: (时间范围，如 1h, 1d)
- search_keyword: (搜索关键词)
"""
