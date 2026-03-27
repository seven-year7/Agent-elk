"""
 * @Module: app/mcp_server/server
 * @Description: FastAPI 版 Elasticsearch MCP Server：提供 tools HTTP 调用入口与统一返回结构
 * @Interface: app (FastAPI)
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from app.mcp_server.auth import resolve_authorization_header
from app.mcp_server.models import ToolName, ToolResponse
from app.mcp_server.tools_core import (
    handle_execute_dsl,
    handle_query_by_time_range,
)
from app.mcp_server.tools_hera import handle_query_by_iam_id, handle_query_by_request_id

logger = logging.getLogger(__name__)

app = FastAPI(title="Elasticsearch MCP Server", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/tools/{tool_name}")
async def call_tool(tool_name: ToolName, request: Request) -> dict[str, Any]:
    """
    @Agent_Logic: HTTP 形态 MCP：每个 tool 对应一个 handler；统一包装成 ToolResponse。
    @Security: 不在服务端日志输出 Authorization 头；只记录 tool_name。
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Body must be an object")

    logger.info("[INFO][MCPServer]: tool_called=%s", tool_name)

    auth = resolve_authorization_header(request.headers.get("authorization"))
    logger.info("[INFO][MCPServer]: auth_type=%s", auth.auth_type)

    if tool_name == "queryByTimeRange":
        resp = handle_query_by_time_range(auth, payload)
        return resp.model_dump()
    if tool_name == "executeDsl":
        resp = handle_execute_dsl(auth, payload)
        return resp.model_dump()

    if tool_name == "queryByRequestId":
        resp = handle_query_by_request_id(auth, payload)
        return resp.model_dump()
    if tool_name == "queryByIamId":
        resp = handle_query_by_iam_id(auth, payload)
        return resp.model_dump()

    resp = ToolResponse(tool=str(tool_name), request={"tool": tool_name}, hits=[], errors=[])
    return resp.model_dump()

