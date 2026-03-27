"""
 * @Module: app/core/agent_brain
 * @Description: ELK-Agent 对话大脑：先 kNN 拉取日志上下文，再由 LLM 归纳诊断
 * @Interface: ELKAgent, agent, run_interactive_cli
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI
from openai import APIConnectionError as OpenAIAPIConnectionError
from openai import APITimeoutError as OpenAIAPITimeoutError

from app.core.config import DEFAULT_LLM_MODEL, MAIN_LLM_TEMPERATURE
from app.mcp_client.client import get_mcp_client
from app.memory.manager import memory_bus
from app.prompts.templates import ANALYSIS_PROMPT_TMPL, SYSTEM_PROMPT
from app.tool_calling.mcp_tools import build_mcp_tool_registry
from app.tool_calling.orchestrator import OrchestratorResult, ToolCallingOrchestrator

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
        self._system_prompt = SYSTEM_PROMPT  # 保留专家身份
        self._mcp = get_mcp_client()
        self._registry = build_mcp_tool_registry(mcp=self._mcp)
        self._orchestrator = ToolCallingOrchestrator(
            llm_client=self._client,
            llm_model=self._model,
            registry=self._registry,
            temperature=MAIN_LLM_TEMPERATURE,
            max_tool_rounds=5,
        )

    def _tool_calling_chat(self, user_input: str, *, thread_id: str) -> OrchestratorResult:
        base_context = memory_bus.get_context(thread_id=thread_id)
        messages: list[dict[str, object]] = [{"role": "system", "content": self._system_prompt}]
        messages.append(
            {
                "role": "system",
                "content": (
                    "RAG 增强执行约束（必须遵守）：\n"
                    "1) 先通过 queryByTimeRange/queryByRequestId/executeDsl 拿到日志事实（LogFacts）。\n"
                    "2) 再调用 queryKnowledgeBaseHybrid，优先传入 errorTitle/symptom/suspectedRootCause/logExcerpt/"
                    "candidateResolution（mcp_summary_only），可附带 serviceName/errorCode 过滤。\n"
                    "3) 回答时必须分区：先写【LogFacts】，再写【KBEvidence】，最后写【DiagnosisAndActions】。\n"
                    "4) 若 LogFacts 与 KBEvidence 不一致，以 LogFacts 为准，并在结论中说明不一致点。"
                ),
            }
        )
        messages.extend(base_context[-6:])  # 控制 token
        messages.append({"role": "user", "content": user_input})
        return self._orchestrator.run(messages=messages)

    def _diagnose_runtime_error(self, exc: Exception) -> str:
        msg = str(exc).strip() or repr(exc)
        lower_msg = msg.lower()
        if isinstance(exc, (OpenAIAPIConnectionError, OpenAIAPITimeoutError)) or "connection error" in lower_msg:
            return (
                "tool-calling 执行失败：LLM 网关连接异常。\n"
                f"原始错误：{msg}\n"
                "建议检查：OPENROUTER_BASE_URL / OPENROUTER_API_KEY / 网络连通性。"
            )
        if "mcp request failed" in lower_msg or "127.0.0.1:8000" in lower_msg:
            return (
                "tool-calling 执行失败：MCP Server 连接异常。\n"
                f"原始错误：{msg}\n"
                "建议检查：MCP_SERVER_URL 是否正确、MCP 服务是否已启动。"
            )
        return f"tool-calling 执行失败：{msg}"

    def _format_tool_errors(self, tool_errors: list[dict[str, str]]) -> str:
        # @Agent_Logic: 工具层失败时优先原样透传关键错误，减少模型误判导致的错误修复建议
        if not tool_errors:
            return ""
        lines = ["【工具链路原始错误】"]
        for err in tool_errors[:5]:
            tool_name = err.get("tool", "unknown")
            code = err.get("code", "UNKNOWN")
            message = err.get("message", "")
            lines.append(f"- tool={tool_name}, code={code}, message={message}")
        return "\n".join(lines)

    def _format_executed_dsls(self, executed_dsls: list[str]) -> str:
        if not executed_dsls:
            return ""
        lines = ["【本次执行DSL】"]
        for idx, dsl in enumerate(executed_dsls[:3], start=1):
            lines.append(f"[DSL-{idx}]")
            lines.append(dsl)
        return "\n".join(lines)

    def chat(self, user_input: str, thread_id: str | None = None) -> str:
        text = (user_input or "").strip()
        if not text:
            return "请输入具体问题或现象描述。"

        effective_thread_id = thread_id or "default_user"
        try:
            result = self._tool_calling_chat(text, thread_id=effective_thread_id)
        except Exception as exc:
            logger.error("[ERROR][AgentBrain]: tool-calling 失败：%s", exc)
            return self._diagnose_runtime_error(exc)

        answer = result.answer
        raw_error_block = self._format_tool_errors(result.tool_errors)
        dsl_block = self._format_executed_dsls(result.executed_dsls)
        if dsl_block:
            answer = f"{dsl_block}\n\n{answer}"
        if raw_error_block:
            answer = f"{raw_error_block}\n\n{answer}"

        memory_bus.add_message("user", text, thread_id=effective_thread_id)
        memory_bus.add_message("assistant", answer, thread_id=effective_thread_id)
        return answer


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
