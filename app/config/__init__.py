"""
 * @Module: app/config
 * @Description: 配置与提示词模板的统一出口
 * @Interface: get_core_settings, prompt_templates 模块
"""

from app.config.prompt_templates import (
    AGENT_SYSTEM_PROMPT,
    ES_DSL_CORRECTION_PROMPT,
    NL_TO_ES_DSL_PROMPT,
)
from app.config.settings import CoreSettings, get_core_settings

__all__ = [
    "AGENT_SYSTEM_PROMPT",
    "CoreSettings",
    "ES_DSL_CORRECTION_PROMPT",
    "NL_TO_ES_DSL_PROMPT",
    "get_core_settings",
]
