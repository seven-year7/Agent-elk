"""
 * @Module: test/eval_rag_business_recall
 * @Description: 评估知识库 RAG 业务召回指标（BM25-only / Vector-only / Hybrid）
 * @Interface: main
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.database.loader import es
from app.database.schema import INDEX_NAME
from app.database.vector_service import get_embedding


@dataclass(frozen=True)
class EvalSample:
    title: str
    symptom: str
    root_cause: str
    content: str
    resolution_steps: str
    verification_steps: str
    service_name: str
    error_code: str
    category_l3: str


def _text(v: Any) -> str:
    return str(v or "").strip()


def _load_samples(csv_path: Path, limit: int) -> list[EvalSample]:
    rows: list[EvalSample] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader, start=1):
            if limit > 0 and idx > limit:
                break
            rows.append(
                EvalSample(
                    title=_text(row.get("title")),
                    symptom=_text(row.get("symptom")),
                    root_cause=_text(row.get("root_cause")),
                    content=_text(row.get("content")),
                    resolution_steps=_text(row.get("resolution_steps")),
                    verification_steps=_text(row.get("verification_steps")),
                    service_name=_text(row.get("service_name")),
                    error_code=_text(row.get("error_code")),
                    category_l3=_text(row.get("category_l3")),
                )
            )
    return rows


def _build_summary_query(sample: EvalSample) -> str:
    return "\n".join(
        [
            f"error_title: {sample.title}",
            f"symptom: {sample.symptom}",
            f"suspected_root_cause: {sample.root_cause}",
            f"log_excerpt: {sample.content}",
            f"candidate_resolution: {sample.resolution_steps}",
        ]
    ).strip()


def _bm25_hits(index_name: str, query_text: str, top_k: int) -> list[dict[str, Any]]:
    dsl = {
        "size": top_k,
        "query": {
            "multi_match": {
                "query": query_text,
                "fields": ["title^3", "symptom^2", "root_cause^2", "content", "resolution_steps", "verification_steps"],
            }
        },
    }
    return ((es.search(index=index_name, body=dsl).get("hits") or {}).get("hits") or [])


def _vector_hits(index_name: str, query_text: str, top_k: int) -> list[dict[str, Any]]:
    vector = get_embedding(query_text)
    if not vector:
        return []
    dsl = {
        "size": top_k,
        "query": {
            "script_score": {
                "query": {"match_all": {}},
                "script": {
                    "source": "cosineSimilarity(params.query_vector, 'content_vector') + 1.0",
                    "params": {"query_vector": vector},
                },
            }
        },
    }
    return ((es.search(index=index_name, body=dsl).get("hits") or {}).get("hits") or [])


def _hybrid_hits_mcp(mcp_url: str, index_name: str, sample: EvalSample, top_k: int, candidate_k: int) -> list[dict[str, Any]]:
    payload = {
        "query": sample.title,
        "errorTitle": sample.title,
        "symptom": sample.symptom,
        "suspectedRootCause": sample.root_cause,
        "logExcerpt": sample.content,
        "candidateResolution": sample.resolution_steps,
        "serviceName": sample.service_name,
        "errorCode": sample.error_code,
        "indexName": index_name,
        "topK": top_k,
        "candidateK": candidate_k,
        "debug": True,
    }
    resp = requests.post(f"{mcp_url.rstrip('/')}/tools/queryKnowledgeBaseHybrid", json=payload, timeout=60)
    resp.raise_for_status()
    body = resp.json()
    hits = body.get("hits", [])
    if not hits:
        summary = body.get("summary") or {}
        errors = body.get("errors") or []
        request_meta = body.get("request") or {}
        print(
            "[DEBUG][HybridEmpty]: "
            + json.dumps(
                {
                    "title": sample.title,
                    "summary_notes": summary.get("notes"),
                    "errors": errors,
                    "debugDetails": request_meta.get("debugDetails"),
                },
                ensure_ascii=False,
            )
        )
    return hits


def _pick_hit_field(hit: dict[str, Any], field: str) -> str:
    source = hit.get("_source") or {}
    return _text(hit.get(field) or source.get(field))


def _print_bm25_hit_details(sample: EvalSample, hits: list[dict[str, Any]], detail_top_n: int) -> None:
    detail_hits = hits[:detail_top_n] if detail_top_n > 0 else hits
    rows: list[dict[str, Any]] = []
    for h in detail_hits:
        rows.append(
            {
                "_id": _text(h.get("_id")),
                "title": _pick_hit_field(h, "title"),
                "error_code": _pick_hit_field(h, "error_code"),
                "servicename": _pick_hit_field(h, "servicename"),
                "_score": h.get("_score"),
            }
        )
    print(
        "[DEBUG][BM25Hits]: "
        + json.dumps(
            {
                "sample_title": sample.title,
                "sample_service": sample.service_name,
                "sample_error_code": sample.error_code,
                "hit_count": len(hits),
                "top_hits": rows,
            },
            ensure_ascii=False,
        )
    )


def _is_category_match(sample: EvalSample, hit: dict[str, Any]) -> bool:
    # @Agent_Logic: 业务相关命中：优先看 error_code；否则看 category_l3
    hit_error_code = _text(hit.get("error_code") or (hit.get("_source") or {}).get("error_code"))
    hit_category = _text(hit.get("category_l3") or (hit.get("_source") or {}).get("category_l3"))
    if sample.error_code and hit_error_code and sample.error_code == hit_error_code:
        return True
    if sample.category_l3 and hit_category and sample.category_l3 == hit_category:
        return True
    return False


def _has_executable_steps(hit: dict[str, Any]) -> bool:
    resolution = _text(hit.get("resolution_steps") or (hit.get("_source") or {}).get("resolution_steps"))
    verification = _text(hit.get("verification_steps") or (hit.get("_source") or {}).get("verification_steps"))
    return bool(resolution and verification)


def _is_consistent(sample: EvalSample, hit: dict[str, Any]) -> bool:
    hit_service = _text(hit.get("servicename") or (hit.get("_source") or {}).get("servicename"))
    hit_error_code = _text(hit.get("error_code") or (hit.get("_source") or {}).get("error_code"))
    by_service = bool(sample.service_name and hit_service and sample.service_name == hit_service)
    by_error_code = bool(sample.error_code and hit_error_code and sample.error_code == hit_error_code)
    return by_service or by_error_code


def _evaluate(
    samples: list[EvalSample],
    *,
    retriever: str,
    top_k: int,
    index_name: str,
    candidate_k: int,
    mcp_url: str,
    print_bm_details: bool = False,
    bm_detail_top_n: int = 3,
) -> dict[str, float]:
    category_hits = 0
    executable_hits = 0
    consistent_hits = 0
    valid_total = 0

    for sample in samples:
        query_text = _build_summary_query(sample)
        if not query_text:
            continue
        valid_total += 1

        if retriever == "bm25":
            hits = _bm25_hits(index_name, query_text, top_k)
            if print_bm_details:
                _print_bm25_hit_details(sample, hits, bm_detail_top_n)
        elif retriever == "vector":
            hits = _vector_hits(index_name, query_text, top_k)
        else:
            hits = _hybrid_hits_mcp(mcp_url, index_name, sample, top_k, candidate_k)

        if any(_is_category_match(sample, h) for h in hits):
            category_hits += 1
        if any(_has_executable_steps(h) for h in hits):
            executable_hits += 1
        if any(_is_consistent(sample, h) for h in hits):
            consistent_hits += 1

    if valid_total == 0:
        return {"topk_category_recall": 0.0, "topk_executable_hit_rate": 0.0, "evidence_consistency_rate": 0.0}
    return {
        "topk_category_recall": category_hits / valid_total,
        "topk_executable_hit_rate": executable_hits / valid_total,
        "evidence_consistency_rate": consistent_hits / valid_total,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Evaluate business recall metrics for RAG retrievers.")
    parser.add_argument("--csv", default=str(_PROJECT_ROOT / "app" / "data" / "standardized_ops_knowledge_base.csv"))
    parser.add_argument("--index", default=INDEX_NAME)
    parser.add_argument("--mcp-url", default="http://127.0.0.1:8000")
    parser.add_argument("--limit", type=int, default=30, help="Evaluate first N samples (0 means all)")
    parser.add_argument("--candidate-k", type=int, default=30)
    parser.add_argument("--print-bm-details", action="store_true", help="Print BM25 top hits for each sample")
    parser.add_argument("--bm-detail-topn", type=int, default=3, help="Top N BM25 hits to print per sample")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.is_file():
        raise FileNotFoundError(f"CSV 不存在: {csv_path}")
    samples = _load_samples(csv_path, args.limit)
    if not samples:
        print("[FAIL] no samples loaded")
        return 2

    for top_k in (1, 3, 5):
        bm25_metrics = _evaluate(
            samples,
            retriever="bm25",
            top_k=top_k,
            index_name=args.index,
            candidate_k=args.candidate_k,
            mcp_url=args.mcp_url,
            print_bm_details=args.print_bm_details,
            bm_detail_top_n=args.bm_detail_topn,
        )
        vector_metrics = _evaluate(
            samples,
            retriever="vector",
            top_k=top_k,
            index_name=args.index,
            candidate_k=args.candidate_k,
            mcp_url=args.mcp_url,
        )
        hybrid_metrics = _evaluate(
            samples,
            retriever="hybrid",
            top_k=top_k,
            index_name=args.index,
            candidate_k=args.candidate_k,
            mcp_url=args.mcp_url,
        )
        print(f"Top{top_k} samples={len(samples)}")
        print(f"  BM25   category={bm25_metrics['topk_category_recall']:.3f} executable={bm25_metrics['topk_executable_hit_rate']:.3f} consistency={bm25_metrics['evidence_consistency_rate']:.3f}")
        print(f"  Vector category={vector_metrics['topk_category_recall']:.3f} executable={vector_metrics['topk_executable_hit_rate']:.3f} consistency={vector_metrics['evidence_consistency_rate']:.3f}")
        print(f"  Hybrid category={hybrid_metrics['topk_category_recall']:.3f} executable={hybrid_metrics['topk_executable_hit_rate']:.3f} consistency={hybrid_metrics['evidence_consistency_rate']:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
