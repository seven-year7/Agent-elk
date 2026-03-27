"""
 * @Module: app/mcp_client/client
 * @Description: MCP HTTP Client：以 Authorization Header 调用 MCP tools（对齐 MCP-server.md）
 * @Interface: McpClient.call_tool
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import requests


def _env_str(name: str) -> str | None:
    raw = os.getenv(name)
    if not isinstance(raw, str):
        return None
    stripped = raw.strip()
    return stripped or None


@dataclass(frozen=True)
class McpClient:
    base_url: str
    authorization: str | None

    def call_tool(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        url = self.base_url.rstrip("/") + f"/tools/{tool_name}"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.authorization:
            headers["Authorization"] = self.authorization
        try:
            resp = requests.post(url, json=args, headers=headers, timeout=20)
        except requests.RequestException as exc:
            raise ConnectionError(f"MCP request failed: tool={tool_name}, url={url}, err={exc!s}") from exc
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, dict):
            raise ValueError("MCP response must be an object")
        return payload


def get_mcp_client() -> McpClient:
    base_url = _env_str("MCP_SERVER_URL") or "http://127.0.0.1:8000"
    authorization = _env_str("MCP_AUTHORIZATION")
    return McpClient(base_url=base_url, authorization=authorization)

