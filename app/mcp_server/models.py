"""
 * @Module: app/mcp_server/models
 * @Description: MCP Server 的统一入参/出参数据结构（对齐 MCP-server.md 的 request/summary/hits/errors 形态）
 * @Interface: ToolResponse, ToolError
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolError(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ToolSummary(BaseModel):
    total_hits: int | None = None
    returned_hits: int | None = None
    index: str | None = None
    time_range: dict[str, Any] | None = None
    mode: str | None = None
    top1_title: str | None = None
    notes: str | None = None


class ToolResponse(BaseModel):
    tool: str = Field(..., description="Tool name, e.g. queryByTimeRange")
    request: dict[str, Any] = Field(default_factory=dict, description="Sanitized request echo back")
    summary: ToolSummary = Field(default_factory=ToolSummary)
    hits: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[ToolError] = Field(default_factory=list)


ToolName = Literal[
    "queryByTimeRange",
    "executeDsl",
    "queryByRequestId",
    "queryByIamId",
    "queryKnowledgeBaseHybrid",
]

