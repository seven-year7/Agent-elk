"""
 * @Module: app/core
 * @Description: Agent 核心逻辑（检索 + LLM 推理、可选交互 CLI）
 * @Interface: ELKAgent, agent, run_interactive_cli（agent_brain，惰性加载）
"""

from __future__ import annotations

from typing import Any

__all__ = ["ELKAgent", "agent", "run_interactive_cli"]


def __getattr__(name: str) -> Any:
    # @Agent_Logic: 避免 import app.core 时立刻构造 LLM；首次访问 agent / ELKAgent 再加载 agent_brain
    if name in ("ELKAgent", "agent", "run_interactive_cli"):
        import importlib

        module = importlib.import_module("app.core.agent_brain")
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
