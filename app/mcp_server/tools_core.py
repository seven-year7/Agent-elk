"""
 * @Module: app/mcp_server/tools_core
 * @Description: MCP 核心 tools 实现：queryByTimeRange / executeDsl
 * @Interface: handle_query_by_time_range, handle_execute_dsl
"""

from __future__ import annotations

import json
import logging
import concurrent.futures
import threading
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.mcp_server.auth import AuthInfo
from app.mcp_server.es_provider import get_es_client
from app.mcp_server.models import ToolError, ToolResponse, ToolSummary
from app.mcp_server.time_utils import parse_local_time_range_to_epoch_millis

logger = logging.getLogger(__name__)

_ES_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=8)
DEFAULT_INDEX_NAME = "database_v"
_MAPPING_CACHE_TTL_SECONDS = 120
_MAPPING_CACHE_LOCK = threading.Lock()
_MAPPING_CACHE: dict[str, tuple[float, dict[str, str]]] = {}

_NUMERIC_TYPES = {
    "byte",
    "short",
    "integer",
    "long",
    "unsigned_long",
    "float",
    "half_float",
    "scaled_float",
    "double",
}
_DATE_TYPES = {"date", "date_nanos"}
_KEYWORD_TYPES = {"keyword", "constant_keyword", "wildcard"}
_TEXT_TYPES = {"text", "match_only_text"}
_BOOL_TYPES = {"boolean"}
_TERM_ALLOWED_TYPES = _NUMERIC_TYPES | _DATE_TYPES | _KEYWORD_TYPES | _BOOL_TYPES
_RANGE_ALLOWED_TYPES = _NUMERIC_TYPES | _DATE_TYPES
_AGG_TERMS_ALLOWED_TYPES = _NUMERIC_TYPES | _DATE_TYPES | _KEYWORD_TYPES | _BOOL_TYPES


def _es_search_fast_fail(es, *, timeout_seconds: int = 5, **kwargs: Any) -> dict[str, Any]:
    """
    @Agent_Logic: 服务端必须快速失败，避免 Agent 卡死；用线程超时包裹 ES 调用。
    """
    fut = _ES_EXECUTOR.submit(lambda: es.search(**kwargs))
    try:
        return fut.result(timeout=timeout_seconds)
    except concurrent.futures.TimeoutError as exc:
        fut.cancel()
        raise TimeoutError(f"ES request timed out after {timeout_seconds}s") from exc


def _flatten_mapping_properties(props: dict[str, Any], *, prefix: str = "") -> dict[str, str]:
    flat: dict[str, str] = {}
    for field_name, body in props.items():
        if not isinstance(body, dict):
            continue
        full_name = f"{prefix}.{field_name}" if prefix else field_name
        field_type = body.get("type")
        if isinstance(field_type, str):
            flat[full_name] = field_type

        # multi-fields: e.g. message.keyword
        fields = body.get("fields")
        if isinstance(fields, dict):
            flat.update(_flatten_mapping_properties(fields, prefix=full_name))

        # object/nested sub-properties
        sub_props = body.get("properties")
        if isinstance(sub_props, dict):
            flat.update(_flatten_mapping_properties(sub_props, prefix=full_name))
    return flat


def fetch_mapping_field_types(es, index_name: str) -> dict[str, str]:
    now = time.time()
    with _MAPPING_CACHE_LOCK:
        cached = _MAPPING_CACHE.get(index_name)
        if cached is not None:
            expire_at, data = cached
            if now < expire_at:
                return data

    raw = es.indices.get_mapping(index=index_name)
    field_types: dict[str, str] = {}
    if isinstance(raw, dict):
        for _, index_body in raw.items():
            if not isinstance(index_body, dict):
                continue
            mappings = index_body.get("mappings")
            if not isinstance(mappings, dict):
                continue
            properties = mappings.get("properties")
            if isinstance(properties, dict):
                field_types.update(_flatten_mapping_properties(properties))

    with _MAPPING_CACHE_LOCK:
        _MAPPING_CACHE[index_name] = (now + _MAPPING_CACHE_TTL_SECONDS, field_types)
    return field_types


def _first_type_error(dsl: Any, field_types: dict[str, str], *, path: str = "$") -> dict[str, str] | None:
    if isinstance(dsl, dict):
        for key, value in dsl.items():
            clause_path = f"{path}.{key}"
            if key in ("term", "terms") and isinstance(value, dict):
                for field, _ in value.items():
                    actual = field_types.get(str(field), "UNKNOWN")
                    if actual not in _TERM_ALLOWED_TYPES:
                        return {
                            "field": str(field),
                            "expected": "keyword/numeric/date/boolean",
                            "actual": actual,
                            "clause_path": clause_path,
                        }
            elif key in ("match", "match_phrase") and isinstance(value, dict):
                for field, _ in value.items():
                    actual = field_types.get(str(field), "UNKNOWN")
                    if actual not in _TEXT_TYPES:
                        return {
                            "field": str(field),
                            "expected": "text",
                            "actual": actual,
                            "clause_path": clause_path,
                        }
            elif key == "range" and isinstance(value, dict):
                for field, _ in value.items():
                    actual = field_types.get(str(field), "UNKNOWN")
                    if actual not in _RANGE_ALLOWED_TYPES:
                        return {
                            "field": str(field),
                            "expected": "date/numeric",
                            "actual": actual,
                            "clause_path": clause_path,
                        }
            elif key in ("aggs", "aggregations") and isinstance(value, dict):
                for agg_name, agg_body in value.items():
                    if isinstance(agg_body, dict):
                        terms_clause = agg_body.get("terms")
                        if isinstance(terms_clause, dict):
                            agg_field = terms_clause.get("field")
                            if isinstance(agg_field, str):
                                actual = field_types.get(agg_field, "UNKNOWN")
                                if actual not in _AGG_TERMS_ALLOWED_TYPES:
                                    return {
                                        "field": agg_field,
                                        "expected": "aggregatable(keyword/numeric/date/boolean)",
                                        "actual": actual,
                                        "clause_path": f"{clause_path}.{agg_name}.terms.field",
                                    }
            child_err = _first_type_error(value, field_types, path=clause_path)
            if child_err is not None:
                return child_err
    elif isinstance(dsl, list):
        for idx, item in enumerate(dsl):
            child_err = _first_type_error(item, field_types, path=f"{path}[{idx}]")
            if child_err is not None:
                return child_err
    return None


def _clamp_size(size: Any, *, default: int, max_size: int) -> int:
    try:
        v = int(size)
    except Exception:
        return default
    if v <= 0:
        return default
    return min(v, max_size)


def _safe_fields(fields: Any, *, default_fields: list[str]) -> list[str]:
    if isinstance(fields, list) and all(isinstance(x, str) for x in fields):
        cleaned = [x.strip() for x in fields if x.strip()]
        return cleaned or default_fields
    return default_fields


def _parse_keyword_expr(keyword: str) -> dict[str, Any]:
    """
    支持轻量 AND/OR：A AND B / A OR B（不支持括号嵌套）。
    输出一个 bool 子句（用于 must / should）。
    """
    text = (keyword or "").strip()
    if not text:
        return {}

    upper = text.upper()
    if " AND " in upper:
        parts = [p.strip() for p in text.split("AND") if p.strip()]
        return {"bool": {"must": [{"match_phrase": {"message": p}} for p in parts]}}
    if " OR " in upper:
        parts = [p.strip() for p in text.split("OR") if p.strip()]
        return {
            "bool": {
                "should": [{"match_phrase": {"message": p}} for p in parts],
                "minimum_should_match": 1,
            }
        }
    return {"match_phrase": {"message": text}}


def _resolve_time_range(
    *,
    start_time: str,
    end_time: str,
    time_zone: str,
    last_minutes_raw: Any,
    last_hours_raw: Any,
) -> tuple[str, str]:
    """
    @Agent_Logic: 优先处理相对时间（lastMinutes/lastHours），由服务端真实时钟锚定，避免模型“当前日期”幻觉。
    """
    has_absolute = bool(start_time and end_time)
    has_relative = last_minutes_raw is not None or last_hours_raw is not None

    if has_absolute and has_relative:
        raise ValueError("startTime/endTime cannot be used together with lastMinutes/lastHours")

    if has_absolute:
        return start_time, end_time

    if not has_relative:
        raise ValueError("either startTime/endTime or lastMinutes/lastHours is required")

    try:
        tz = ZoneInfo(time_zone)
    except Exception as exc:
        raise ValueError(f"invalid timeZone: {time_zone}") from exc

    now_local = datetime.now(tz)
    if last_minutes_raw is not None:
        last_minutes = int(last_minutes_raw)
        if last_minutes <= 0:
            raise ValueError("lastMinutes must be > 0")
        start_local = now_local - timedelta(minutes=last_minutes)
    else:
        last_hours = int(last_hours_raw)
        if last_hours <= 0:
            raise ValueError("lastHours must be > 0")
        start_local = now_local - timedelta(hours=last_hours)

    end_local = now_local
    return start_local.strftime("%Y-%m-%d %H:%M:%S"), end_local.strftime("%Y-%m-%d %H:%M:%S")


def handle_query_by_time_range(auth: AuthInfo, args: dict[str, Any]) -> ToolResponse:
    tool = "queryByTimeRange"
    index_name = str(args.get("indexName", "") or DEFAULT_INDEX_NAME).strip() or DEFAULT_INDEX_NAME
    start_time = str(args.get("startTime", "") or "").strip()
    end_time = str(args.get("endTime", "") or "").strip()
    time_zone = str(args.get("timeZone", "") or "Asia/Shanghai").strip() or "Asia/Shanghai"
    last_minutes = args.get("lastMinutes")
    last_hours = args.get("lastHours")

    size = _clamp_size(args.get("size"), default=100, max_size=1000)
    level = str(args.get("level", "") or "").strip()
    logip = str(args.get("logip", "") or "").strip()
    keyword = str(args.get("keyword", "") or "").strip()
    fields = _safe_fields(
        args.get("fields"),
        default_fields=["@timestamp", "service_name", "log_level", "trace_id", "requestId", "message"],
    )

    resp = ToolResponse(
        tool=tool,
        request={
            "indexName": index_name,
            "startTime": start_time,
            "endTime": end_time,
            "lastMinutes": last_minutes,
            "lastHours": last_hours,
            "timeZone": time_zone,
            "size": size,
        },
    )
    try:
        resolved_start_time, resolved_end_time = _resolve_time_range(
            start_time=start_time,
            end_time=end_time,
            time_zone=time_zone,
            last_minutes_raw=last_minutes,
            last_hours_raw=last_hours,
        )
    except Exception as exc:
        resp.errors.append(ToolError(code="INVALID_ARGUMENT", message=str(exc)))
        return resp

    try:
        tr = parse_local_time_range_to_epoch_millis(
            resolved_start_time,
            resolved_end_time,
            time_zone=time_zone,
            max_window_seconds=24 * 60 * 60,
        )
    except Exception as exc:
        resp.errors.append(ToolError(code="INVALID_TIME_RANGE", message=str(exc)))
        return resp

    filters: list[dict[str, Any]] = [
        {"range": {"@timestamp": {"gte": tr.start_ms, "lte": tr.end_ms, "format": "epoch_millis"}}}
    ]
    if level:
        filters.append({"term": {"log_level": level}})
    if logip:
        filters.append({"term": {"logip": logip}})

    must_clause: list[dict[str, Any]] = []
    kw_clause = _parse_keyword_expr(keyword)
    if kw_clause:
        must_clause.append(kw_clause)

    dsl: dict[str, Any] = {
        "size": size,
        "sort": [{"@timestamp": {"order": "desc"}}],
        "query": {"bool": {"filter": filters, **({"must": must_clause} if must_clause else {})}},
        "_source": fields,
    }

    es = get_es_client(auth)
    try:
        raw = _es_search_fast_fail(es, index=index_name, body=dsl)
    except Exception as exc:
        resp.errors.append(ToolError(code="ES_REQUEST_FAILED", message=str(exc)))
        return resp

    hits = (raw.get("hits") or {}).get("hits") or []
    resp.hits = hits
    total = (raw.get("hits") or {}).get("total") or {}
    total_val = total.get("value") if isinstance(total, dict) else None
    resp.summary = ToolSummary(
        total_hits=int(total_val) if isinstance(total_val, int) else None,
        returned_hits=len(hits),
        index=index_name,
        time_range={"start": resolved_start_time, "end": resolved_end_time, "timeZone": time_zone},
    )
    return resp


_EXECUTE_DSL_ALLOWLIST = {
    "query",
    "bool",
    "filter",
    "must",
    "should",
    "must_not",
    "minimum_should_match",
    "range",
    "term",
    "terms",
    "match",
    "match_phrase",
    "query_string",
    "sort",
    "aggs",
    "aggregations",
    "size",
    "_source",
    "from",
    "track_total_hits",
    # common leaf params
    "order",
    "gte",
    "lte",
    "gt",
    "lt",
    "format",
}

_EXECUTE_DSL_BLOCKLIST = {"script", "painless", "runtime_mappings", "stored_fields"}


_DYNAMIC_FIELD_CONTAINERS = {
    # 这些结构下的 key 往往是“字段名”，无法预枚举；但仍需禁止危险 key
    "sort",
    "range",
    "term",
    "terms",
    "match",
    "match_phrase",
    "aggs",
    "aggregations",
}


def _validate_dsl_keys(obj: Any, *, parent_key: str | None = None, path: str = "") -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = str(k)
            if key in _EXECUTE_DSL_BLOCKLIST:
                raise ValueError(f"DSL contains forbidden key: {path + key}")

            # 当父级容器是动态字段容器时，允许任意字段名（但继续递归检查子结构）
            if parent_key in _DYNAMIC_FIELD_CONTAINERS:
                _validate_dsl_keys(v, parent_key=key, path=path + key + ".")
                continue

            # 其它位置严格按 allowlist 控制结构 key
            if key not in _EXECUTE_DSL_ALLOWLIST:
                raise ValueError(f"DSL contains non-allowlisted key: {path + key}")

            _validate_dsl_keys(v, parent_key=key, path=path + key + ".")
    elif isinstance(obj, list):
        for i, it in enumerate(obj):
            _validate_dsl_keys(it, parent_key=parent_key, path=f"{path}[{i}].")


def handle_execute_dsl(auth: AuthInfo, args: dict[str, Any]) -> ToolResponse:
    tool = "executeDsl"
    index_name = str(args.get("indexName", "") or DEFAULT_INDEX_NAME).strip() or DEFAULT_INDEX_NAME
    dsl_raw = args.get("dsl")
    size = _clamp_size(args.get("size"), default=100, max_size=1000)

    resp = ToolResponse(tool=tool, request={"indexName": index_name})
    if dsl_raw is None:
        resp.errors.append(ToolError(code="INVALID_ARGUMENT", message="dsl is required"))
        return resp

    if isinstance(dsl_raw, str):
        try:
            dsl = json.loads(dsl_raw)
        except Exception as exc:
            resp.errors.append(ToolError(code="INVALID_DSL_JSON", message=str(exc)))
            return resp
    elif isinstance(dsl_raw, dict):
        dsl = dsl_raw
    else:
        resp.errors.append(ToolError(code="INVALID_DSL_TYPE", message="dsl must be json string or object"))
        return resp

    try:
        _validate_dsl_keys(dsl)
    except Exception as exc:
        resp.errors.append(ToolError(code="DSL_NOT_ALLOWED", message=str(exc)))
        return resp

    # size 强制上限（若 DSL 自带 size，也覆盖到 <=1000）
    if isinstance(dsl, dict):
        dsl["size"] = _clamp_size(dsl.get("size", size), default=size, max_size=1000)

    es = get_es_client(auth)
    try:
        field_types = fetch_mapping_field_types(es, index_name)
    except Exception as exc:
        resp.errors.append(ToolError(code="MAPPING_FETCH_FAILED", message=str(exc)))
        return resp

    type_err = _first_type_error(dsl, field_types)
    if type_err is not None:
        resp.errors.append(
            ToolError(
                code="DSL_FIELD_TYPE_MISMATCH",
                message=(
                    f"field '{type_err['field']}' type mismatch at {type_err['clause_path']}: "
                    f"expected {type_err['expected']}, actual {type_err['actual']}"
                ),
                details=type_err,
            )
        )
        return resp

    try:
        raw = _es_search_fast_fail(es, index=index_name, body=dsl)
    except Exception as exc:
        resp.errors.append(ToolError(code="ES_REQUEST_FAILED", message=str(exc)))
        return resp

    hits = (raw.get("hits") or {}).get("hits") or []
    resp.hits = hits
    total = (raw.get("hits") or {}).get("total") or {}
    total_val = total.get("value") if isinstance(total, dict) else None
    resp.summary = ToolSummary(
        total_hits=int(total_val) if isinstance(total_val, int) else None,
        returned_hits=len(hits),
        index=index_name,
        notes="executeDsl validated by allowlist; response trimmed to hits",
    )
    return resp



