"""
 * @Module: app/tool_calling/registry
 * @Description: 工具注册表：以装饰器方式注册 tool schema 与执行器（执行器负责调用 MCP）
 * @Interface: ToolRegistry, tool
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


ToolExecutor = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class RegisteredTool:
    name: str
    schema: dict[str, Any]
    executor: ToolExecutor


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, name: str, schema: dict[str, Any], executor: ToolExecutor) -> None:
        if not name:
            raise ValueError("tool name is required")
        if name in self._tools:
            raise ValueError(f"duplicate tool: {name}")
        self._tools[name] = RegisteredTool(name=name, schema=schema, executor=executor)

    def get(self, name: str) -> RegisteredTool | None:
        return self._tools.get(name)

    def tools_schema(self) -> list[dict[str, Any]]:
        return [t.schema for t in self._tools.values()]


def tool(*, registry: ToolRegistry, name: str, schema: dict[str, Any]) -> Callable[[ToolExecutor], ToolExecutor]:
    """
    装饰器：注册一个工具。
    """

    def _decorator(executor: ToolExecutor) -> ToolExecutor:
        registry.register(name=name, schema=schema, executor=executor)
        return executor

    return _decorator

