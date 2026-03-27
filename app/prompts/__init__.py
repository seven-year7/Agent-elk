"""
 * @Module: app/prompts
 * @Description: 提示词仓库（与业务代码解耦）
 * @Interface: templates 模块中的 SYSTEM_PROMPT、ANALYSIS_PROMPT_TMPL、INTENT_EXTRACT_TMPL
"""

from app.prompts.templates import (
    ANALYSIS_PROMPT_TMPL,
    INTENT_EXTRACT_TMPL,
    SYSTEM_PROMPT,
)
from app.prompts.tool_calling_prompts import (
    EXECUTE_DSL_TOOL_DESCRIPTION,
    FORCE_SIMPLE_DSL_PROMPT,
    RAG_EXECUTION_CONSTRAINT_PROMPT,
)

__all__ = [
    "ANALYSIS_PROMPT_TMPL",
    "INTENT_EXTRACT_TMPL",
    "SYSTEM_PROMPT",
    "RAG_EXECUTION_CONSTRAINT_PROMPT",
    "FORCE_SIMPLE_DSL_PROMPT",
    "EXECUTE_DSL_TOOL_DESCRIPTION",
]
