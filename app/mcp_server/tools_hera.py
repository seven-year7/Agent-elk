"""
 * @Module: app/mcp_server/tools_hera
 * @Description: Hera 相关 tools：queryByRequestId / queryByIamId（通过 Hera API 获取 ES 索引列表）
 * @Interface: handle_query_by_request_id, handle_query_by_iam_id
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests
import concurrent.futures

from app.mcp_server.auth import AuthInfo
from app.mcp_server.es_provider import get_es_client
from app.mcp_server.models import ToolError, ToolResponse, ToolSummary

logger = logging.getLogger(__name__)

_ES_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=8)


def _es_search_fast_fail(es, *, timeout_seconds: int = 5, **kwargs: Any) -> dict[str, Any]:
    fut = _ES_EXECUTOR.submit(lambda: es.search(**kwargs))
    try:
        return fut.result(timeout=timeout_seconds)
    except concurrent.futures.TimeoutError as exc:
        fut.cancel()
        raise TimeoutError(f"ES request timed out after {timeout_seconds}s") from exc


def _env_str(name: str) -> str | None:
    raw = os.getenv(name)
    if not isinstance(raw, str):
        return None
    stripped = raw.strip()
    return stripped or None


def _clamp_size(size: Any, *, default: int, max_size: int) -> int:
    try:
        v = int(size)
    except Exception:
        return default
    if v <= 0:
        return default
    return min(v, max_size)


def _hera_list_indices(*, app_name: str, iam_tree_path: str, iam_id: str | None) -> list[str]:
    """
    Hera 索引查询：GET {MCP_HERA_BASE_URL}/es/indices?appName=...&iamTreePath=...&iamId=...
    兼容返回：{indices:[...]} 或 {data:{indices:[...]}}。
    """
    base = _env_str("MCP_HERA_BASE_URL")
    if not base:
        raise ValueError("MCP_HERA_BASE_URL is required for Hera tools")

    url = base.rstrip("/") + "/es/indices"
    params: dict[str, str] = {"appName": app_name, "iamTreePath": iam_tree_path}
    if iam_id:
        params["iamId"] = iam_id

    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    payload = resp.json()

    indices: list[str] = []
    if isinstance(payload, dict):
        raw = payload.get("indices")
        if isinstance(raw, list):
            indices = [str(x).strip() for x in raw if str(x).strip()]
        data = payload.get("data")
        if not indices and isinstance(data, dict):
            raw2 = data.get("indices")
            if isinstance(raw2, list):
                indices = [str(x).strip() for x in raw2 if str(x).strip()]

    return indices


def handle_query_by_request_id(auth: AuthInfo, args: dict[str, Any]) -> ToolResponse:
    tool = "queryByRequestId"
    request_id = str(args.get("requestId", "") or "").strip()
    service_name = str(args.get("serviceName", "") or "").strip()
    index_name = str(args.get("indexName", "") or "database_v").strip() or "database_v"
    level = str(args.get("level", "") or "").strip()
    size = _clamp_size(args.get("size"), default=100, max_size=1000)

    resp = ToolResponse(
        tool=tool,
        request={"requestId": request_id, "serviceName": service_name, "indexName": index_name, "size": size},
    )
    if not request_id or not service_name:
        resp.errors.append(ToolError(code="INVALID_ARGUMENT", message="requestId/serviceName are required"))
        return resp

    es = get_es_client(auth)
    # @Step: 直接按 ES 查询（去 Hera）：requestId + servicename
    dsl: dict[str, Any] = {
        "size": size,
        "sort": [{"@timestamp": {"order": "desc"}}],
        "query": {
            "bool": {
                "filter": [
                    {"term": {"requestId": request_id}},
                    {
                        "bool": {
                            "should": [
                                {"term": {"servicename": service_name}},
                                {"term": {"service_name": service_name}},
                            ],
                            "minimum_should_match": 1,
                        }
                    },
                ],
            }
        },
    }
    if level:
        dsl["query"]["bool"]["filter"].append({"term": {"log_level": level}})

    try:
        raw = _es_search_fast_fail(es, index=index_name, body=dsl)
    except Exception as exc:
        resp.errors.append(ToolError(code="ES_REQUEST_FAILED", message=str(exc)))
        return resp

    hits = (raw.get("hits") or {}).get("hits") or []
    total = (raw.get("hits") or {}).get("total") or {}
    total_val = total.get("value") if isinstance(total, dict) else None
    resp.hits = hits
    resp.summary = ToolSummary(
        total_hits=int(total_val) if isinstance(total_val, int) else len(hits),
        returned_hits=len(hits),
        index=index_name,
        notes="queryByRequestId direct ES by requestId + servicename",
    )
    return resp


def handle_query_by_iam_id(auth: AuthInfo, args: dict[str, Any]) -> ToolResponse:
    tool = "queryByIamId"
    iam_id = str(args.get("iamId", "") or "").strip()
    iam_tree_path = str(args.get("iamTreePath", "") or "").strip()
    app_name = str(args.get("appName", "") or "").strip()
    keyword = str(args.get("keyword", "") or "").strip()
    level = str(args.get("level", "") or "").strip()
    size = _clamp_size(args.get("size"), default=100, max_size=1000)

    resp = ToolResponse(tool=tool, request={"iamId": iam_id, "iamTreePath": iam_tree_path, "appName": app_name, "size": size})
    if not iam_id or not iam_tree_path or not app_name:
        resp.errors.append(ToolError(code="INVALID_ARGUMENT", message="iamId/iamTreePath/appName are required"))
        return resp

    try:
        indices = _hera_list_indices(app_name=app_name, iam_tree_path=iam_tree_path, iam_id=iam_id)
    except Exception as exc:
        resp.errors.append(ToolError(code="HERA_FAILED", message=str(exc)))
        return resp

    if not indices:
        resp.errors.append(ToolError(code="NO_INDICES", message="Hera returned empty indices"))
        return resp

    es = get_es_client(auth)
    filters: list[dict[str, Any]] = []
    if level:
        filters.append({"term": {"log_level": level}})
    must: list[dict[str, Any]] = []
    if keyword:
        must.append({"query_string": {"query": keyword}})

    dsl: dict[str, Any] = {
        "size": size,
        "sort": [{"@timestamp": {"order": "desc"}}],
        "query": {"bool": {**({"filter": filters} if filters else {}), **({"must": must} if must else {})}},
    }

    try:
        raw = _es_search_fast_fail(es, index=indices, body=dsl)
    except Exception as exc:
        resp.errors.append(ToolError(code="ES_REQUEST_FAILED", message=str(exc)))
        return resp

    hits = (raw.get("hits") or {}).get("hits") or []
    resp.hits = hits
    resp.summary = ToolSummary(total_hits=None, returned_hits=len(hits), index=",".join(indices))
    return resp

