"""
 * @Module: app/core/agent_brain
 * @Description: ELK-Agent 对话大脑：先 kNN 拉取日志上下文，再由 LLM 归纳诊断
 * @Interface: ELKAgent, agent, run_interactive_cli
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from app.core.config import DEFAULT_LLM_MODEL, MAIN_LLM_TEMPERATURE, OPS_DIRECT_KEYWORDS
from app.core.router import IntentRouter
from app.memory.manager import memory_bus
from app.prompts.templates import ANALYSIS_PROMPT_TMPL, SYSTEM_PROMPT
from app.tools.config import SEARCH_LOGS_DEFAULT_TOP_K
from app.tools.log_tools import search_logs_tool

logger = logging.getLogger(__name__)

# @Step: 1 - 与全项目一致：从仓库根目录加载 .env
# @Security: 禁止在日志中输出 API Key
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")

_OPENROUTER_DEFAULT_BASE = "https://openrouter.ai/api/v1"


def _env_str(name: str) -> str | None:
    raw = os.getenv(name)
    if not isinstance(raw, str):
        return None
    stripped = raw.strip()
    return stripped or None


def _build_chat_openai_client() -> OpenAI:
    # @Step: 2 - 对话与嵌入共用 OpenRouter 凭据时可只配 OPENROUTER_API_KEY；亦可直连 OpenAI
    # @Agent_Logic: OpenRouter 上聊天模型 ID 多为 vendor/model，如 openai/gpt-4o-mini
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
        client = OpenAI(**kwargs)
        logger.info("[INFO][AgentBrain]: 对话客户端使用 OpenRouter（base_url=%s）", base_url)
        return client

    if not openai_key:
        raise ValueError("请配置 OPENROUTER_API_KEY 或 OPENAI_API_KEY 以初始化对话客户端")

    kwargs = {"api_key": openai_key}
    base_url = _env_str("OPENAI_BASE_URL")
    if base_url:
        kwargs["base_url"] = base_url
    if default_headers:
        kwargs["default_headers"] = default_headers

    client = OpenAI(**kwargs)
    logger.info("[INFO][AgentBrain]: 对话客户端使用 OPENAI_API_KEY（base_url_set=%s）", bool(base_url))
    return client


def _resolve_llm_model() -> str:
    return _env_str("LLM_MODEL") or _env_str("DEFAULT_LLM_MODEL") or DEFAULT_LLM_MODEL


class ELKAgent:
    """先检索日志上下文，再调用聊天补全生成诊断说明。"""

    def __init__(self) -> None:
        self._client = _build_chat_openai_client()
        self._model = _resolve_llm_model()
        # @Step: 2b - 系统提示外置至 app/prompts/templates.py（Phase 2.2 解耦）
        self._system_prompt = SYSTEM_PROMPT
        self._intent_router = IntentRouter()

    def chat(self, user_input: str, thread_id: str | None = None) -> str:
        # @Step: 3 - 网关 ->（可选 ES 检索）-> 推理；temperature 压低以降低胡编风险
        text = (user_input or "").strip()
        if not text:
            return "请输入具体问题或现象描述。"

        effective_thread_id = thread_id or "default_user"
        query_lower = text.lower()
        direct_match = any(k in query_lower for k in OPS_DIRECT_KEYWORDS)

        current_time = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

        # 1) 无论走什么逻辑，【身份】和【历史】是永恒的基石
        base_context = memory_bus.get_context(thread_id=effective_thread_id)
        summary_text = ""
        short_history: list[dict[str, str]] = []
        for msg in base_context:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            content = msg.get("content", "")
            if role == "system" and isinstance(content, str) and content.startswith("之前的对话摘要："):
                summary_text = content.replace("之前的对话摘要：", "", 1).strip()
            elif role in ("user", "assistant"):
                short_history.append({"role": str(role), "content": str(content)})
        short_history = short_history[-4:]

        messages: list[dict[str, str]] = [{"role": "system", "content": self._system_prompt}]
        if summary_text:
            messages.append({"role": "system", "content": f"【关键历史摘要】: {summary_text}"})
        messages.extend(short_history)

        # 2) 路由逻辑判定
        final_user_content = text
        if direct_match:
            print("⚡ [DIRECT_LOG] 检测到明确运维指令，启动实时检索...")
            logs_context = search_logs_tool(text, top_k=SEARCH_LOGS_DEFAULT_TOP_K)
            logger.info("[INFO][AgentBrain]: 检索上下文已注入 LLM（长度约 %s 字符）", len(logs_context))

            # 将关键日志片段写入 summary，供后续 Chat/追问复用（No ES）
            memory_bus.append_key_log_snippets(logs_context, thread_id=effective_thread_id)

            final_user_content = (
                f"【用户问题】\n{text}\n\n"
                f"【实时抓取日志】\n{logs_context}\n\n"
                "请结合以上新证据和历史背景分析，给出根因判断与下一步排查/修复建议。"
            )
        else:
            # 第二级：单模型意图识别（No ES / No RAG）
            intent = self._intent_router.judge_intent(text, summary_text, short_history)

            if intent == "LOG":
                print("📋 [LOG_FOLLOW_UP] 运维追问模式，利用内存中的日志记录回答...")
                final_user_content = f"用户正在追问技术细节: {text}。请根据上下文已有的日志结论进行解答。"
            else:
                print("💬 [GENERAL_CHAT] 纯交流模式，维持专家身份回答...")
                final_user_content = text

        print("[推理中] 正在生成回复...")

        # @Step: 3a - Phase 2.6：主 system + 本轮载荷估算超硬上限则阻断
        safety_payload = f"{self._system_prompt}\n{final_user_content}"
        if not memory_bus.check_safety_overflow(
            additional_content=safety_payload, thread_id=effective_thread_id
        ):
            return (
                "【上下文过长】当前记忆摘要、近期对话与本次检索内容合计已接近模型窗口上限，"
                "已停止调用主模型以避免请求失败。请新开一次会话，或清理 Redis 会话后重试："
                f" elk:agent:session:{effective_thread_id}"
            )

        # 3) 组装并发送
        messages.append({"role": "user", "content": final_user_content})

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=MAIN_LLM_TEMPERATURE,
            )
        except Exception as exc:
            logger.error("[ERROR][AgentBrain]: LLM 调用失败: %s", exc)
            return f"模型推理失败：{exc!s}"

        choice = response.choices[0].message
        content = (choice.content or "").strip()
        if not content:
            logger.warning("[WARN][AgentBrain]: 模型返回空内容")
            return "模型未返回有效文本，请稍后重试或更换 LLM_MODEL。"

        # 仅记录「用户原话 + 助手结论」，避免把整段检索模板重复塞进摘要语料
        memory_bus.add_message("user", text, thread_id=effective_thread_id)
        memory_bus.add_message("assistant", content, thread_id=effective_thread_id)
        return content


agent = ELKAgent()


def _ensure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def run_interactive_cli() -> int:
    # @Step: 4 - 简易 REPL，作为「交互入口」
    _ensure_utf8_stdio()
    print("ELK-Agent 交互模式（输入 exit / quit 退出）")
    while True:
        try:
            user_line = input("\nELK-Agent> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[INFO][AgentBrain]: 已退出。")
            return 0

        if not user_line:
            continue
        if user_line.lower() in ("exit", "quit", "q"):
            print("[INFO][AgentBrain]: 再见。")
            return 0

        answer = agent.chat(user_line)
        print("\n---\n", answer, "\n---", sep="")


if __name__ == "__main__":
    raise SystemExit(run_interactive_cli())
