"""
 * @Module: app/mcp_server/auth
 * @Description: MCP Server 侧鉴权解析：从 HTTP Header Authorization 读取；缺失时使用服务端默认配置兜底
 * @Interface: resolve_authorization_header
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthInfo:
    auth_type: str  # basic / apikey / bearer / none
    authorization_header: str | None


def _env_str(name: str) -> str | None:
    raw = os.getenv(name)
    if not isinstance(raw, str):
        return None
    stripped = raw.strip()
    return stripped or None


def resolve_authorization_header(incoming_authorization: str | None) -> AuthInfo:
    """
    @Agent_Logic: 优先使用 Agent→MCP 传入的 Authorization；若缺失，则读取服务端默认配置。
    @Security: 不记录 token/密码原文；仅记录 auth_type。
    """
    if isinstance(incoming_authorization, str) and incoming_authorization.strip():
        token = incoming_authorization.strip()
        lower = token.lower()
        if lower.startswith("basic "):
            return AuthInfo(auth_type="basic", authorization_header=token)
        if lower.startswith("apikey "):
            return AuthInfo(auth_type="apikey", authorization_header=token)
        if lower.startswith("bearer "):
            return AuthInfo(auth_type="bearer", authorization_header=token)
        # 未识别也透传（可能是自定义前缀），但标注 unknown
        return AuthInfo(auth_type="unknown", authorization_header=token)

    # 服务端兜底：三选一
    default_auth = _env_str("MCP_ES_AUTHORIZATION")
    if default_auth:
        lower = default_auth.lower()
        if lower.startswith("basic "):
            auth_type = "basic"
        elif lower.startswith("apikey "):
            auth_type = "apikey"
        elif lower.startswith("bearer "):
            auth_type = "bearer"
        else:
            auth_type = "unknown"
        logger.info("[INFO][MCPAuth]: using_default_auth_type=%s", auth_type)
        return AuthInfo(auth_type=auth_type, authorization_header=default_auth)

    logger.info("[INFO][MCPAuth]: no_authorization_provided")
    return AuthInfo(auth_type="none", authorization_header=None)

