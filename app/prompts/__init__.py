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

__all__ = [
    "ANALYSIS_PROMPT_TMPL",
    "INTENT_EXTRACT_TMPL",
    "SYSTEM_PROMPT",
]
