"""
 * @Module: app/mcp_server/es_provider
 * @Description: MCP Server 侧 ES 客户端提供器：基于 Authorization Header 与服务端 ES URL 构造 client
 * @Interface: get_es_client
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from elasticsearch import Elasticsearch

from app.mcp_server.auth import AuthInfo

logger = logging.getLogger(__name__)

# @Step: 与主程序一致，从仓库根目录加载 .env，避免 MCP 进程读不到 ES 配置。
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")


def _env_str(name: str) -> str | None:
    raw = os.getenv(name)
    if not isinstance(raw, str):
        return None
    stripped = raw.strip()
    return stripped or None


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not isinstance(raw, str):
        return default
    token = raw.strip().lower()
    if token in ("1", "true", "yes", "on"):
        return True
    if token in ("0", "false", "no", "off"):
        return False
    return default


@lru_cache(maxsize=8)
def _build_base_client(es_url: str, authorization_header: str | None) -> Elasticsearch:
    headers: dict[str, str] = {}
    if authorization_header:
        headers["Authorization"] = authorization_header
    # @Agent_Logic: MCP Server 作为中间层，必须快速失败，避免挂死上游 Agent。
    kwargs: dict[str, Any] = {"hosts": [es_url], "request_timeout": 5}

    # @Step: TLS 与认证配置与主程序对齐，避免“脚本可连/MCP 不可连”的环境偏差。
    verify_certs = _env_bool("ES_VERIFY_CERTS", True)
    ca_certs_path = _env_str("ES_CA_CERTS")
    if ca_certs_path:
        kwargs["ca_certs"] = ca_certs_path
    if not verify_certs:
        kwargs["verify_certs"] = False

    # @Agent_Logic: 优先使用 Authorization Header（Agent 透传或 MCP_ES_AUTHORIZATION），
    # 若没有 Header，再回退到 ES_API_KEY / ES_USERNAME+ES_PASSWORD。
    if headers:
        kwargs["headers"] = headers
    else:
        api_key = _env_str("ES_API_KEY")
        username = _env_str("ES_USERNAME")
        password = _env_str("ES_PASSWORD")
        if api_key:
            kwargs["api_key"] = api_key
        elif username and password:
            kwargs["basic_auth"] = (username, password)

    logger.info(
        "[INFO][McpESProvider]: init_client es_url=%s verify_certs=%s has_auth_header=%s has_basic=%s has_api_key=%s",
        es_url,
        kwargs.get("verify_certs", True),
        bool(headers),
        bool(kwargs.get("basic_auth")),
        bool(kwargs.get("api_key")),
    )
    return Elasticsearch(**kwargs)


def get_es_client(auth: AuthInfo) -> Elasticsearch:
    """
    @Agent_Logic: 客户端按 (ES_URL, Authorization) 维度缓存，避免每次请求重建连接池。
    """
    es_url = _env_str("MCP_ES_URL") or _env_str("ES_URL") or "http://localhost:9200"
    logger.info(
        "[INFO][McpESProvider]: select_client es_url=%s auth_from_request=%s",
        es_url,
        bool(auth.authorization_header),
    )
    return _build_base_client(es_url, auth.authorization_header)

