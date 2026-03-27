"""
 * @Module: app/mcp_server/tools_kb
 * @Description: 知识库 RAG 检索工具：BM25 + 向量召回并做融合排序
 * @Interface: handle_query_knowledge_base_hybrid
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import logging
import os
from typing import Any

from elasticsearch import Elasticsearch

from app.database.schema import INDEX_NAME as KNOWLEDGE_INDEX_NAME
from app.database.vector_service import get_embedding
from app.mcp_server.auth import AuthInfo
from app.mcp_server.es_provider import get_es_client
from app.mcp_server.models import ToolError, ToolResponse, ToolSummary

_ES_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=8)
logger = logging.getLogger(__name__)


def _es_search_fast_fail(es, *, timeout_seconds: int = 8, **kwargs: Any) -> dict[str, Any]:
    fut = _ES_EXECUTOR.submit(lambda: es.search(**kwargs))
    try:
        return fut.result(timeout=timeout_seconds)
    except concurrent.futures.TimeoutError as exc:
        fut.cancel()
        raise TimeoutError(f"ES request timed out after {timeout_seconds}s") from exc


def _clamp_int(value: Any, *, default: int, min_value: int, max_value: int) -> int:
    try:
        v = int(value)
    except Exception:
        return default
    if v < min_value:
        return min_value
    if v > max_value:
        return max_value
    return v


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


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


def _text_sha8(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:8]


def _truncate_text(value: str, limit: int = 80) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "..."


def _build_sparse_query_text(args: dict[str, Any]) -> str:
    # @Step: 1 - 为 BM25 构造更短的关键词查询，减少模板前缀词污染
    fields = [
        _safe_text(args.get("errorTitle")),
        _safe_text(args.get("symptom")),
        _safe_text(args.get("suspectedRootCause")),
        _safe_text(args.get("query")),
        _safe_text(args.get("errorCode")),
    ]
    compact = " ".join(v for v in fields if v).strip()
    return compact


def _fallback_es_client(auth: AuthInfo) -> Elasticsearch | None:
    # @Agent_Logic: 当 MCP_ES_URL 与入库 ES 不一致时，回退 ES_URL 以保证知识库检索可用
    es_url = _env_str("ES_URL")
    if not es_url:
        return None
    kwargs: dict[str, Any] = {"hosts": [es_url], "request_timeout": 8}
    verify_certs = _env_bool("ES_VERIFY_CERTS", True)
    if not verify_certs:
        kwargs["verify_certs"] = False
    ca_certs = _env_str("ES_CA_CERTS")
    if ca_certs:
        kwargs["ca_certs"] = ca_certs

    if auth.authorization_header:
        kwargs["headers"] = {"Authorization": auth.authorization_header}
    else:
        api_key = _env_str("ES_API_KEY")
        username = _env_str("ES_USERNAME")
        password = _env_str("ES_PASSWORD")
        if api_key:
            kwargs["api_key"] = api_key
        elif username and password:
            kwargs["basic_auth"] = (username, password)
    return Elasticsearch(**kwargs)


def _ensure_index_visible(es: Elasticsearch, auth: AuthInfo, index_name: str) -> Elasticsearch:
    try:
        if es.indices.exists(index=index_name):
            return es
    except Exception:
        pass
    fb = _fallback_es_client(auth)
    if fb is None:
        return es
    try:
        if fb.indices.exists(index=index_name):
            return fb
    except Exception:
        return es
    return es


def _has_index(es: Elasticsearch, index_name: str) -> bool:
    try:
        return bool(es.indices.exists(index=index_name))
    except Exception:
        return False


def _build_summary_query_text(args: dict[str, Any]) -> str:
    # @Step: 1 - 统一 mcp_summary_only 查询文本模板，尽量对齐离线向量构造语义空间
    error_title = _safe_text(args.get("errorTitle"))
    symptom = _safe_text(args.get("symptom"))
    suspected_root_cause = _safe_text(args.get("suspectedRootCause"))
    log_excerpt = _safe_text(args.get("logExcerpt"))
    candidate_resolution = _safe_text(args.get("candidateResolution"))
    fallback_query = _safe_text(args.get("query"))

    parts: list[str] = []
    if error_title:
        parts.append(f"error_title: {error_title}")
    if symptom:
        parts.append(f"symptom: {symptom}")
    if suspected_root_cause:
        parts.append(f"suspected_root_cause: {suspected_root_cause}")
    if log_excerpt:
        parts.append(f"log_excerpt: {log_excerpt}")
    if candidate_resolution:
        parts.append(f"candidate_resolution: {candidate_resolution}")
    if fallback_query:
        parts.append(f"query: {fallback_query}")
    return "\n".join(parts).strip()


def _to_hit_record(hit: dict[str, Any], *, bm25: float, vector: float, fused: float) -> dict[str, Any]:
    source = hit.get("_source") if isinstance(hit, dict) else {}
    source = source if isinstance(source, dict) else {}
    return {
        "_id": hit.get("_id"),
        "_score": hit.get("_score"),
        "fused_score": fused,
        "bm25_score": bm25,
        "vector_score": vector,
        "kb_id": source.get("kb_id"),
        "title": source.get("title"),
        "content": source.get("content"),
        "servicename": source.get("servicename"),
        "error_code": source.get("error_code"),
        "tags": source.get("tags"),
        "root_cause": source.get("root_cause"),
        "resolution_steps": source.get("resolution_steps"),
        "verification_steps": source.get("verification_steps"),
        "source": source.get("source"),
        "timestamp": source.get("timestamp"),
    }


def _collect_hits(raw: dict[str, Any]) -> list[dict[str, Any]]:
    return ((raw.get("hits") or {}).get("hits") or []) if isinstance(raw, dict) else []


def _apply_min_score(hits: list[dict[str, Any]], min_score: float) -> list[dict[str, Any]]:
    if min_score <= 0:
        return hits
    filtered: list[dict[str, Any]] = []
    for hit in hits:
        score = float(hit.get("_score") or 0.0)
        if score >= min_score:
            filtered.append(hit)
    return filtered


def _build_filter(service_name: str, error_code: str) -> list[dict[str, Any]]:
    filters: list[dict[str, Any]] = []
    if service_name:
        filters.append({"term": {"servicename": service_name}})
    if error_code:
        filters.append({"term": {"error_code": error_code}})
    return filters


def _build_soft_should(service_name: str, error_code: str) -> list[dict[str, Any]]:
    should_terms: list[dict[str, Any]] = []
    if service_name:
        should_terms.append({"term": {"servicename": {"value": service_name, "boost": 2.0}}})
    if error_code:
        should_terms.append({"term": {"error_code": {"value": error_code, "boost": 2.0}}})
    return should_terms


def _debug_enabled(args: dict[str, Any]) -> bool:
    # @Security: 仅在显式参数或环境变量开启时输出调试摘要，避免默认泄露检索细节
    debug_arg = args.get("debug")
    if isinstance(debug_arg, bool):
        return debug_arg
    return _env_bool("MCP_KB_DEBUG", False)


def _dsl_shape(dsl: dict[str, Any]) -> dict[str, Any]:
    query = dsl.get("query") if isinstance(dsl, dict) else {}
    query_type = next(iter(query.keys()), "unknown") if isinstance(query, dict) and query else "unknown"
    return {
        "size": dsl.get("size"),
        "queryType": query_type,
        "hasFilter": "filter" in str(query),
    }


def _run_bm25(
    es: Elasticsearch,
    *,
    index_name: str,
    query_text: str,
    filters: list[dict[str, Any]],
    candidate_k: int,
    min_score: float,
    soft_should: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    bm25_dsl: dict[str, Any] = {
        "size": candidate_k,
        "query": {
            "bool": {
                "must": [
                    {
                        "multi_match": {
                            "query": query_text,
                            "fields": [
                                "title^3",
                                "symptom^2",
                                "root_cause^2",
                                "content",
                                "resolution_steps",
                                "verification_steps",
                            ],
                        }
                    }
                ],
                **({"should": soft_should} if soft_should else {}),
                **({"filter": filters} if filters else {}),
            }
        },
    }
    bm25_raw = _es_search_fast_fail(es, index=index_name, body=bm25_dsl)
    hits = _apply_min_score(_collect_hits(bm25_raw), min_score)
    return hits, bm25_dsl


def _run_vector(
    es: Elasticsearch,
    *,
    index_name: str,
    query_vec: list[float] | None,
    filters: list[dict[str, Any]],
    candidate_k: int,
    min_score: float,
    soft_should: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    if not query_vec:
        return [], None
    vector_dsl: dict[str, Any] = {
        "size": candidate_k,
        "query": {
            "script_score": {
                "query": {
                    "bool": {
                        "must": [{"match_all": {}}],
                        **({"should": soft_should} if soft_should else {}),
                        **({"filter": filters} if filters else {}),
                    }
                },
                "script": {
                    "source": "cosineSimilarity(params.query_vector, 'content_vector') + 1.0",
                    "params": {"query_vector": query_vec},
                },
            }
        },
    }
    vector_raw = _es_search_fast_fail(es, index=index_name, body=vector_dsl)
    hits = _apply_min_score(_collect_hits(vector_raw), min_score)
    return hits, vector_dsl


def _rrf_fuse(
    bm25_hits: list[dict[str, Any]],
    vector_hits: list[dict[str, Any]],
    *,
    rrf_k: int = 60,
) -> list[dict[str, Any]]:
    # @Agent_Logic: 使用 RRF 减少单一召回模式偏差，提升诊断证据覆盖面
    rank_score: dict[str, float] = {}
    bm25_score_map: dict[str, float] = {}
    vector_score_map: dict[str, float] = {}
    hit_map: dict[str, dict[str, Any]] = {}

    for rank, hit in enumerate(bm25_hits, start=1):
        hit_id = str(hit.get("_id"))
        hit_map[hit_id] = hit
        bm25_score_map[hit_id] = float(hit.get("_score") or 0.0)
        rank_score[hit_id] = rank_score.get(hit_id, 0.0) + (1.0 / (rrf_k + rank))

    for rank, hit in enumerate(vector_hits, start=1):
        hit_id = str(hit.get("_id"))
        hit_map[hit_id] = hit
        vector_score_map[hit_id] = float(hit.get("_score") or 0.0)
        rank_score[hit_id] = rank_score.get(hit_id, 0.0) + (1.0 / (rrf_k + rank))

    sorted_ids = sorted(rank_score.keys(), key=lambda x: rank_score[x], reverse=True)
    fused_hits: list[dict[str, Any]] = []
    for hit_id in sorted_ids:
        fused_hits.append(
            _to_hit_record(
                hit_map[hit_id],
                bm25=bm25_score_map.get(hit_id, 0.0),
                vector=vector_score_map.get(hit_id, 0.0),
                fused=rank_score[hit_id],
            )
        )
    return fused_hits


def handle_query_knowledge_base_hybrid(auth: AuthInfo, args: dict[str, Any]) -> ToolResponse:
    tool = "queryKnowledgeBaseHybrid"
    dense_query = _build_summary_query_text(args)
    sparse_query = _build_sparse_query_text(args) or dense_query
    service_name = _safe_text(args.get("serviceName"))
    error_code = _safe_text(args.get("errorCode"))
    index_name = _safe_text(args.get("indexName")) or KNOWLEDGE_INDEX_NAME
    top_k = _clamp_int(args.get("topK"), default=5, min_value=1, max_value=20)
    candidate_k = _clamp_int(args.get("candidateK"), default=30, min_value=5, max_value=200)
    bm25_min_score = float(args.get("bm25MinScore") or 0.1)
    vector_min_score = float(args.get("vectorMinScore") or 0.7)
    if candidate_k < top_k:
        candidate_k = top_k
    strict_filter = bool(args.get("strictFilter"))
    debug_enabled = _debug_enabled(args)

    resp = ToolResponse(
        tool=tool,
        request={
            "query": _safe_text(args.get("query")),
            "summaryQueryText": dense_query,
            "sparseQueryText": sparse_query,
            "errorTitle": _safe_text(args.get("errorTitle")),
            "symptom": _safe_text(args.get("symptom")),
            "suspectedRootCause": _safe_text(args.get("suspectedRootCause")),
            "logExcerpt": _safe_text(args.get("logExcerpt")),
            "candidateResolution": _safe_text(args.get("candidateResolution")),
            "serviceName": service_name,
            "errorCode": error_code,
            "indexName": index_name,
            "topK": top_k,
            "candidateK": candidate_k,
            "bm25MinScore": bm25_min_score,
            "vectorMinScore": vector_min_score,
            "strictFilter": strict_filter,
            "debug": debug_enabled,
        },
    )
    if not dense_query:
        resp.errors.append(ToolError(code="INVALID_ARGUMENT", message="query is required"))
        return resp

    primary_es = get_es_client(auth)
    fallback_es = _fallback_es_client(auth)
    es = _ensure_index_visible(primary_es, auth, index_name)
    index_visible_in_primary = _has_index(primary_es, index_name)
    index_visible_in_effective = _has_index(es, index_name)
    index_visible_in_fallback = _has_index(fallback_es, index_name) if fallback_es else False
    client_source = "primary"
    if es is not primary_es:
        client_source = "fallback_es_url"

    logger.info(
        "[INFO][KBHybrid]: start index=%s client=%s primary_visible=%s effective_visible=%s dense_len=%s sparse_len=%s dense_sha=%s sparse_sha=%s",
        index_name,
        client_source,
        index_visible_in_primary,
        index_visible_in_effective,
        len(dense_query),
        len(sparse_query),
        _text_sha8(dense_query),
        _text_sha8(sparse_query),
    )
    logger.debug(
        "[DEBUG][KBHybrid]: dense_preview=%s sparse_preview=%s",
        _truncate_text(dense_query),
        _truncate_text(sparse_query),
    )
    filters = _build_filter(service_name, error_code)
    soft_should = _build_soft_should(service_name, error_code)
    if not strict_filter:
        filters = []

    bm25_dsl: dict[str, Any] = {}
    try:
        bm25_hits, bm25_dsl = _run_bm25(
            es,
            index_name=index_name,
            query_text=sparse_query,
            filters=filters,
            candidate_k=candidate_k,
            min_score=bm25_min_score,
            soft_should=soft_should,
        )
    except Exception as exc:
        resp.errors.append(ToolError(code="ES_REQUEST_FAILED", message=str(exc)))
        return resp

    vector_hits: list[dict[str, Any]] = []
    query_vec = get_embedding(dense_query)
    vector_dsl: dict[str, Any] | None = None
    if query_vec:
        try:
            vector_hits, vector_dsl = _run_vector(
                es,
                index_name=index_name,
                query_vec=query_vec,
                filters=filters,
                candidate_k=candidate_k,
                min_score=vector_min_score,
                soft_should=soft_should,
            )
        except Exception as exc:
            resp.errors.append(ToolError(code="VECTOR_SEARCH_FAILED", message=str(exc)))
    else:
        resp.errors.append(ToolError(code="EMBEDDING_FAILED", message="failed to generate query embedding"))

    # @Step: 2 - 主客户端空命中时，尝试 fallback 客户端，解决“同名索引不同视图”问题
    if (
        not bm25_hits
        and not vector_hits
        and fallback_es is not None
        and fallback_es is not es
        and _has_index(fallback_es, index_name)
    ):
        try:
            fallback_bm25_hits, fallback_bm25_dsl = _run_bm25(
                fallback_es,
                index_name=index_name,
                query_text=sparse_query,
                filters=filters,
                candidate_k=candidate_k,
                min_score=bm25_min_score,
                soft_should=soft_should,
            )
            fallback_vector_hits: list[dict[str, Any]] = []
            fallback_vector_dsl: dict[str, Any] | None = None
            if query_vec:
                fallback_vector_hits, fallback_vector_dsl = _run_vector(
                    fallback_es,
                    index_name=index_name,
                    query_vec=query_vec,
                    filters=filters,
                    candidate_k=candidate_k,
                    min_score=vector_min_score,
                    soft_should=soft_should,
                )
            if fallback_bm25_hits or fallback_vector_hits:
                bm25_hits = fallback_bm25_hits
                vector_hits = fallback_vector_hits
                bm25_dsl = fallback_bm25_dsl
                vector_dsl = fallback_vector_dsl
                client_source = "fallback_es_url_on_empty"
                resp.errors.append(
                    ToolError(
                        code="CLIENT_FALLBACK_USED",
                        message="primary client returned empty; fallback client produced hits",
                    )
                )
        except Exception as exc:
            resp.errors.append(ToolError(code="CLIENT_FALLBACK_FAILED", message=str(exc)))

    used_filter_fallback = False
    if not bm25_hits and not vector_hits and filters:
        try:
            fallback_filters: list[dict[str, Any]] = []
            fallback_bm25_hits, fallback_bm25_dsl = _run_bm25(
                es,
                index_name=index_name,
                query_text=sparse_query,
                filters=fallback_filters,
                candidate_k=candidate_k,
                min_score=bm25_min_score,
                soft_should=soft_should,
            )
            fallback_vector_hits: list[dict[str, Any]] = []
            fallback_vector_dsl: dict[str, Any] | None = None
            if query_vec:
                fallback_vector_hits, fallback_vector_dsl = _run_vector(
                    es,
                    index_name=index_name,
                    query_vec=query_vec,
                    filters=fallback_filters,
                    candidate_k=candidate_k,
                    min_score=vector_min_score,
                    soft_should=soft_should,
                )
            if fallback_bm25_hits or fallback_vector_hits:
                used_filter_fallback = True
                bm25_hits = fallback_bm25_hits
                vector_hits = fallback_vector_hits
                bm25_dsl = fallback_bm25_dsl
                vector_dsl = fallback_vector_dsl
                resp.errors.append(
                    ToolError(
                        code="FILTER_RELAXED",
                        message="strict filters returned no hits; fallback without filters produced hits",
                    )
                )
        except Exception as exc:
            resp.errors.append(ToolError(code="FILTER_RELAX_FAILED", message=str(exc)))

    debug_details = {
        "clientSource": client_source,
        "indexVisibleInPrimary": index_visible_in_primary,
        "indexVisibleInEffective": index_visible_in_effective,
        "indexVisibleInFallback": index_visible_in_fallback,
        "denseQueryLength": len(dense_query),
        "sparseQueryLength": len(sparse_query),
        "denseQuerySha8": _text_sha8(dense_query),
        "sparseQuerySha8": _text_sha8(sparse_query),
        "bm25DslShape": _dsl_shape(bm25_dsl),
        "vectorDslShape": _dsl_shape(vector_dsl) if vector_dsl else {"size": candidate_k, "queryType": "none", "hasFilter": bool(filters)},
        "filterCount": len(filters),
        "usedFilterFallback": used_filter_fallback,
        "bm25Hits": len(bm25_hits),
        "vectorHits": len(vector_hits),
    }
    if debug_enabled:
        resp.request["debugDetails"] = debug_details

    # @Step: 2 - 空检索快速退出，避免无意义融合与额外开销
    if not bm25_hits and not vector_hits:
        resp.hits = []
        empty_reason = "EMPTY_RETRIEVAL_EXECUTED_NO_HITS"
        if query_vec is None:
            empty_reason = "EMPTY_RETRIEVAL_EMBEDDING_FAILED"
        resp.errors.append(ToolError(code=empty_reason, message="retrieval executed but no hits"))
        resp.summary = ToolSummary(
            total_hits=0,
            returned_hits=0,
            index=index_name,
            mode="hybrid_rrf",
            top1_title=None,
            notes=(
                f"empty retrieval fast exit (reason={empty_reason}, bm25_hits={len(bm25_hits)}, "
                f"vector_hits={len(vector_hits)}, client={client_source})"
            ),
        )
        return resp

    fused = _rrf_fuse(bm25_hits, vector_hits) if vector_hits else _rrf_fuse(bm25_hits, [])
    resp.hits = fused[:top_k]
    resp.summary = ToolSummary(
        total_hits=len(fused),
        returned_hits=len(resp.hits),
        index=index_name,
        mode="hybrid_rrf",
        top1_title=(resp.hits[0].get("title") if resp.hits else None),
        notes=(
            f"BM25+Vector fused by RRF (bm25_hits={len(bm25_hits)}, vector_hits={len(vector_hits)}, "
            f"client={client_source})"
        ),
    )
    return resp
