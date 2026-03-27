"""
 * @Module: app/tool_calling/orchestrator
 * @Description: Tool-calling 编排器：LLM 产出 tool_calls → 调用注册工具（MCP）→ 回喂 tool 结果 → 直到最终回答
 * @Interface: ToolCallingOrchestrator.run
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from openai import APIConnectionError as OpenAIAPIConnectionError
from openai import APITimeoutError as OpenAIAPITimeoutError

from app.tool_calling.registry import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OrchestratorResult:
    answer: str
    rounds_used: int
    tool_errors: list[dict[str, str]]
    executed_dsls: list[str]


class ToolCallingOrchestrator:
    def __init__(
        self,
        *,
        llm_client: Any,
        llm_model: str,
        registry: ToolRegistry,
        temperature: float,
        max_tool_rounds: int = 5,
    ) -> None:
        self._llm_client = llm_client
        self._llm_model = llm_model
        self._registry = registry
        self._temperature = temperature
        self._max_tool_rounds = max_tool_rounds
        self._max_dsl_rewrite_retries = 2
        self._max_llm_network_retries = 2

    def run(self, *, messages: list[dict[str, Any]]) -> OrchestratorResult:
        """
        @Agent_Logic: 只给模型 tools 注册描述，不给“如何选工具”的 policy；让模型自行选择工具。
        """
        tools = self._registry.tools_schema()
        collected_tool_errors: list[dict[str, str]] = []
        executed_dsls: list[str] = []
        dsl_rewrite_retries = 0
        for round_idx in range(self._max_tool_rounds):
            resp = None
            for retry_idx in range(self._max_llm_network_retries + 1):
                try:
                    resp = self._llm_client.chat.completions.create(
                        model=self._llm_model,
                        messages=messages,
                        tools=tools,
                        tool_choice="auto",
                        temperature=self._temperature,
                    )
                    break
                except (OpenAIAPIConnectionError, OpenAIAPITimeoutError) as exc:
                    if retry_idx >= self._max_llm_network_retries:
                        raise
                    wait_s = 0.6 * (retry_idx + 1)
                    logger.warning(
                        "[WARN][ToolCalling]: LLM network unstable, retry=%s/%s, wait=%.1fs, err=%s",
                        retry_idx + 1,
                        self._max_llm_network_retries,
                        wait_s,
                        exc,
                    )
                    time.sleep(wait_s)

            if resp is None:
                raise RuntimeError("LLM response is empty after retries")

            msg = resp.choices[0].message
            tool_calls = getattr(msg, "tool_calls", None)
            if not tool_calls:
                content = (msg.content or "").strip()
                return OrchestratorResult(
                    answer=content or "模型未返回有效文本。",
                    rounds_used=round_idx + 1,
                    tool_errors=collected_tool_errors,
                    executed_dsls=executed_dsls,
                )

            messages.append(msg)  # assistant(tool_calls)

            for call in tool_calls:
                tool_name = call.function.name
                raw_args = call.function.arguments or "{}"
                try:
                    tool_args = json.loads(raw_args)
                except Exception:
                    tool_args = {}
                if tool_name == "executeDsl" and isinstance(tool_args, dict):
                    dsl_value = tool_args.get("dsl")
                    if isinstance(dsl_value, str):
                        dsl_text = dsl_value.strip()
                        if dsl_text:
                            executed_dsls.append(dsl_text)

                reg = self._registry.get(tool_name)
                if reg is None:
                    tool_result = {
                        "tool": tool_name,
                        "request": {"_raw_arguments": raw_args},
                        "summary": {},
                        "hits": [],
                        "errors": [{"code": "TOOL_NOT_REGISTERED", "message": f"tool not registered: {tool_name}"}],
                    }
                else:
                    try:
                        tool_result = reg.executor(tool_args if isinstance(tool_args, dict) else {})
                    except Exception as exc:
                        logger.warning("[WARN][ToolCalling]: tool_failed tool=%s err=%s", tool_name, exc)
                        tool_result = {
                            "tool": tool_name,
                            "request": {"_raw_arguments": raw_args},
                            "summary": {},
                            "hits": [],
                            "errors": [{"code": "TOOL_EXEC_FAILED", "message": str(exc)}],
                        }

                # @Step: 收集工具层原始错误，供上层直接透传，避免模型“脑补”故障原因
                if isinstance(tool_result, dict):
                    errors = tool_result.get("errors")
                    if isinstance(errors, list):
                        needs_dsl_rewrite = False
                        for err in errors:
                            if not isinstance(err, dict):
                                continue
                            code = str(err.get("code", "UNKNOWN")).strip() or "UNKNOWN"
                            message = str(err.get("message", "")).strip()
                            collected_tool_errors.append(
                                {
                                    "tool": tool_name,
                                    "code": code,
                                    "message": message,
                                }
                            )
                            if tool_name == "executeDsl" and code in (
                                "DSL_FIELD_TYPE_MISMATCH",
                                "DSL_NOT_ALLOWED",
                                "INVALID_DSL_JSON",
                            ):
                                needs_dsl_rewrite = True

                        if needs_dsl_rewrite:
                            dsl_rewrite_retries += 1
                            if dsl_rewrite_retries > self._max_dsl_rewrite_retries:
                                return OrchestratorResult(
                                    answer=(
                                        "DSL 重写重试已达上限（2）。请收窄查询范围，"
                                        "并明确字段名/字段类型后重试。"
                                    ),
                                    rounds_used=round_idx + 1,
                                    tool_errors=collected_tool_errors,
                                    executed_dsls=executed_dsls,
                                )

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(tool_result, ensure_ascii=False),
                    }
                )

        return OrchestratorResult(
            answer="工具调用轮数已达上限（5）。请缩小时间范围、明确索引名或提供 requestId 以继续排查。",
            rounds_used=self._max_tool_rounds,
            tool_errors=collected_tool_errors,
            executed_dsls=executed_dsls,
        )

