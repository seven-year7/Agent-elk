"""
 * @Module: app/core/router
 * @Description: Phase 2.24 架构精简：关键词未命中时，用单模型判定 LOG/CHAT（取消投票）
 * @Interface: IntentRouter.judge_intent
"""

from __future__ import annotations

import logging
import os

from openai import OpenAI
from app.memory.config import SUMMARY_MODEL

logger = logging.getLogger(__name__)

_OPENROUTER_DEFAULT_BASE = "https://openrouter.ai/api/v1"


def _env_str(name: str) -> str | None:
    raw = os.getenv(name)
    if not isinstance(raw, str):
        return None
    stripped = raw.strip()
    return stripped or None


def _build_intent_client() -> OpenAI:
    """
    @Agent_Logic: Phase 2.24 单模型意图判定优先走 OpenRouter；无 OPENROUTER_API_KEY 时回退 OPENAI_API_KEY。
    """
    router_key = _env_str("OPENROUTER_API_KEY")
    openai_key = _env_str("OPENAI_API_KEY")

    default_headers: dict[str, str] = {}
    referer = _env_str("OPENROUTER_HTTP_REFERER")
    app_title = _env_str("OPENROUTER_APP_NAME")
    if referer:
        default_headers["HTTP-Referer"] = referer
    if app_title:
        default_headers["X-Title"] = app_title

    if router_key:
        base_url = _env_str("OPENROUTER_BASE_URL") or _OPENROUTER_DEFAULT_BASE
        kwargs: dict = {"api_key": router_key, "base_url": base_url}
        if default_headers:
            kwargs["default_headers"] = default_headers
        logger.info("[INFO][IntentRouter]: 意图判定客户端使用 OpenRouter（base_url=%s）", base_url)
        return OpenAI(**kwargs)

    if not openai_key:
        raise ValueError("IntentRouter 需要 OPENROUTER_API_KEY 或 OPENAI_API_KEY 以调用单模型意图判定")

    kwargs = {"api_key": openai_key}
    base_url = _env_str("OPENAI_BASE_URL")
    if base_url:
        kwargs["base_url"] = base_url
    if default_headers:
        kwargs["default_headers"] = default_headers

    logger.info("[INFO][IntentRouter]: 意图判定客户端使用 OPENAI_API_KEY（base_url_set=%s）", bool(base_url))
    return OpenAI(**kwargs)


system_prompt = """
# Role: 高级 SRE 运维调度专家

# Task: 意图分类
请根据提供的上下文，将用户的【当前输入】分类为 [LOG] 或 [CHAT]。

# Rules for [LOG]:
1. 包含明确运维词汇（如：报错、Service、Trace、日志、堆栈、超时等）。
2. 属于对之前诊断结论的【技术性追问】。
   - 示例：刚才查到了 500 错误，用户问：“它是怎么发生的？”、“能修复吗？”、“还有其他报错吗？”。
3. 任何涉及系统状态、故障原因、性能指标的探讨。

# Rules for [CHAT]:
1. 礼貌性社交（如：你好、谢谢、再见）。
2. 关于对话本身的元问题（如：“你是谁？”、“我第一个问题是什么？”、“清空记忆”）。
3. 纯粹的个人信息交流，与系统排障毫无逻辑关联。

# Constraints:
- 必须且只能输出一个单词：LOG 或 CHAT。
- 严禁输出任何解释、标点或引言。
- 如果意图处于模糊地带，只要与之前的运维话题有逻辑关联，即判定为 [LOG]。
"""


class IntentRouter:
    """
    单模型意图路由器（Phase 2.24）：
    - 未命中 OPS_DIRECT_KEYWORDS 时调用一次小模型
    - 只返回 LOG / CHAT
    """

    def __init__(self) -> None:
        # @Security: 不在日志中输出 key；仅依赖环境变量注入。
        self._client = _build_intent_client()
        self._model = _env_str("MEMORY_SUMMARY_MODEL") or SUMMARY_MODEL

    def judge_intent(self, user_input: str, summary: str | None, history: list[dict[str, str]] | None) -> str:
        """
        单模型专家级判定逻辑（只输出 LOG / CHAT）
        """
        cleaned = (user_input or "").strip()
        if not cleaned:
            return "CHAT"

        # @Step: 1 - 构造增强上下文，携带摘要 + 最近 2-3 轮
        # @Agent_Logic: 缩短上下文以控制 token，并保留指代解析所需的最近消息。
        recent = (history or [])[-3:]
        recent_lines: list[str] = []
        for msg in recent:
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role", "") or "")
            content = str(msg.get("content", "") or "")
            if role and content:
                recent_lines.append(f"{role}: {content}")

        context_block = "\n".join(
            [
                "### 历史背景摘要:",
                (summary or "").strip() or "暂无",
                "",
                "### 最近对话记录:",
                "\n".join(recent_lines) or "暂无",
                "",
                "### 当前用户输入:",
                cleaned,
            ]
        )

        try:
            res = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": context_block},
                ],
                temperature=0.01,
                max_tokens=5,
            )
            intent = (res.choices[0].message.content or "").strip().upper()
            return "LOG" if "LOG" in intent else "CHAT"
        except Exception as exc:
            logger.warning("[WARN][IntentRouter]: 意图判定异常，回退 CHAT（%s）", exc)
            return "CHAT"

