"""
 * @Module: app/core/router
 * @Description: Phase 2.18 极简路由器：关键词未命中时，用小模型三票制判定 LOG/CHAT
 * @Interface: IntentVoter.get_consensus
"""

from __future__ import annotations

import logging
import os

from openai import OpenAI

logger = logging.getLogger(__name__)

_OPENROUTER_DEFAULT_BASE = "https://openrouter.ai/api/v1"


def _env_str(name: str) -> str | None:
    raw = os.getenv(name)
    if not isinstance(raw, str):
        return None
    stripped = raw.strip()
    return stripped or None


def _build_voter_client() -> OpenAI:
    """
    @Agent_Logic: Phase 2.18 投票统一走 OpenRouter，避免依赖 OPENAI_API_KEY。
    """
    router_key = _env_str("OPENROUTER_API_KEY")
    if not router_key:
        raise ValueError("IntentVoter 需要 OPENROUTER_API_KEY（统一经 OpenRouter 调用投票模型）")

    default_headers: dict[str, str] = {}
    referer = _env_str("OPENROUTER_HTTP_REFERER")
    app_title = _env_str("OPENROUTER_APP_NAME")
    if referer:
        default_headers["HTTP-Referer"] = referer
    if app_title:
        default_headers["X-Title"] = app_title

    base_url = _env_str("OPENROUTER_BASE_URL") or _OPENROUTER_DEFAULT_BASE
    kwargs: dict = {"api_key": router_key, "base_url": base_url}
    if default_headers:
        kwargs["default_headers"] = default_headers

    logger.info("[INFO][IntentVoter]: 投票客户端使用 OpenRouter（base_url=%s）", base_url)
    return OpenAI(**kwargs)


VOTER_SYSTEM_PROMPT = """# Role: 高级 SRE 运维调度专家

# Task: 意图识别
请根据【历史摘要】、【最近对话】以及【当前输入】，判断用户的真实意图。

# Context:
1. 【历史摘要】：这是之前对话的精华浓缩。
2. 【最近对话】：这是过去 2-3 轮的原始对话，用于捕捉指代词（如“它”、“那个”）。

# Classification Rules:
- [LOG]:
  - 用户直接提到运维关键词（Error, Trace, Service等）。
  - 用户在针对之前的诊断结论进行“技术性追问”。
    * 示例：历史在聊支付报错，用户问“为什么会这样？”或“能修复吗？”。
- [CHAT]:
  - 用户在进行礼貌性交流（你好、谢谢）。
  - 用户在询问关于对话本身的元问题（Meta-conversation）。
    * 示例：“我第一个问题是什么？”、“你叫什么名字？”、“清空记忆”。

# Constraints:
- 必须且只能输出一个单词：LOG 或 CHAT。
- 严禁任何解释、标点或换行。
- 即使意图模糊，只要与之前的运维话题有 1% 的逻辑关联，即判定为 [LOG]。

# Output:
(LOG/CHAT)
"""


class IntentVoter:
    """
    三票制意图投票器：
    - 连续投票 3 次
    - 少数服从多数：LOG >= 2 票则 LOG，否则 CHAT
    """

    def __init__(self) -> None:
        # @Security: 不在日志中输出 key；仅依赖环境变量注入。
        self.client = _build_voter_client()

    def get_consensus(
        self,
        user_input: str,
        *,
        thread_id: str | None = None,
        memory_manager: object | None = None,
    ) -> str:
        """执行 3 次投票，返回 'LOG' 或 'CHAT'。"""
        votes: list[str] = []
        cleaned = (user_input or "").strip()

        summary_text = ""
        recent_dialogue_lines: list[str] = []
        if memory_manager is not None:
            get_context = getattr(memory_manager, "get_context", None)
            if callable(get_context):
                try:
                    ctx = get_context(thread_id=thread_id)
                    if isinstance(ctx, list):
                        for msg in ctx:
                            if not isinstance(msg, dict):
                                continue
                            role = str(msg.get("role", "") or "")
                            content = str(msg.get("content", "") or "")
                            if role == "system" and content.startswith("之前的对话摘要："):
                                summary_text = content.replace("之前的对话摘要：", "", 1).strip()
                            elif role in ("user", "assistant"):
                                recent_dialogue_lines.append(f"{role}: {content}")
                except Exception as exc:
                    logger.warning("[WARN][IntentVoter]: 读取记忆上下文失败，忽略（%s）", exc)

        # 仅保留最近 2-3 轮（约 4-6 条消息），以满足“最近对话”要求并控制 token。
        recent_dialogue_lines = recent_dialogue_lines[-6:]

        user_payload = "\n".join(
            [
                "【历史摘要】",
                summary_text or "(空)",
                "",
                "【最近对话】",
                "\n".join(recent_dialogue_lines) or "(空)",
                "",
                "【当前输入】",
                cleaned,
            ]
        )

        voter_models = (
            "qwen/qwen3-4b:free",
            "qwen/qwen3-4b:free",
            "qwen/qwen3-4b:free",
        )

        for model_id in voter_models:
            try:
                res = self.client.chat.completions.create(
                    model=model_id,
                    messages=[
                        {
                            "role": "system",
                            "content": VOTER_SYSTEM_PROMPT,
                        },
                        {"role": "user", "content": user_payload},
                    ],
                    temperature=0.1,
                )
                raw_vote = (res.choices[0].message.content or "").strip().upper()
                votes.append("LOG" if "LOG" in raw_vote else "CHAT")
            except Exception as exc:
                logger.warning("[WARN][IntentVoter]: 投票失败（model=%s），默认 CHAT：%s", model_id, exc)
                votes.append("CHAT")

        winner = "LOG" if votes.count("LOG") >= 2 else "CHAT"
        print(f"🗳️  投票详情: {votes} -> 胜出: {winner}")
        return winner

