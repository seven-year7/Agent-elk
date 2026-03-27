"""
 * @Module: ingest_standardized_ops_kb
 * @Description: 将 standardized_ops_knowledge_base.csv 清洗、向量化并批量写入 ES 知识库索引
 * @Interface: main
"""

from __future__ import annotations

import argparse
import csv
import logging
from pathlib import Path
from typing import Any

from elasticsearch import helpers

from app.database.loader import es, init_index
from app.database.schema import CONTENT_VECTOR_DIMS, INDEX_NAME
from app.database.vector_service import get_embedding

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent


def _clean_text(value: Any) -> str:
    text = str(value or "").strip()
    if text.lower() == "nan":
        return ""
    return text


def _normalize_tags(raw: str) -> list[str]:
    text = _clean_text(raw)
    if not text:
        return []
    return [x.strip() for x in text.split("|") if x.strip()]


def _embedding_payload(row: dict[str, str]) -> str:
    parts = [
        _clean_text(row.get("title")),
        _clean_text(row.get("symptom")),
        _clean_text(row.get("root_cause")),
        _clean_text(row.get("content")),
        _clean_text(row.get("resolution_steps")),
    ]
    return "\n".join([p for p in parts if p])


def _to_doc(row: dict[str, str], vector: list[float]) -> dict[str, Any]:
    return {
        "kb_id": _clean_text(row.get("id")),
        "category_l1": _clean_text(row.get("category_l1")),
        "category_l2": _clean_text(row.get("category_l2")),
        "category_l3": _clean_text(row.get("category_l3")),
        "title": _clean_text(row.get("title")),
        "content": _clean_text(row.get("content")),
        "servicename": _clean_text(row.get("service_name")),
        "error_code": _clean_text(row.get("error_code")),
        "tags": _normalize_tags(str(row.get("tags", ""))),
        "symptom": _clean_text(row.get("symptom")),
        "root_cause": _clean_text(row.get("root_cause")),
        "resolution_steps": _clean_text(row.get("resolution_steps")),
        "verification_steps": _clean_text(row.get("verification_steps")),
        "source": _clean_text(row.get("source")),
        "timestamp": _clean_text(row.get("updated_at")),
        "content_vector": vector,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ingest standardized ops knowledge base CSV with vectors.")
    parser.add_argument(
        "--csv",
        default=str(_PROJECT_ROOT / "app" / "data" / "standardized_ops_knowledge_base.csv"),
        help="Knowledge CSV file path",
    )
    parser.add_argument("--index", default=INDEX_NAME, help="Knowledge index name")
    parser.add_argument("--batch-size", type=int, default=100, help="ES bulk chunk size")
    parser.add_argument("--limit", type=int, default=0, help="Only ingest first N rows (0 means all)")
    parser.add_argument("--dry-run", action="store_true", help="Parse and vectorize only, do not write ES")
    return parser


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    args = _build_parser().parse_args()

    csv_path = Path(args.csv)
    if not csv_path.is_file():
        raise FileNotFoundError(f"CSV 不存在: {csv_path}")

    init_index()

    ok_docs: list[dict[str, Any]] = []
    skipped = 0
    dim_mismatch = 0
    embed_failed = 0

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader, start=1):
            if args.limit > 0 and idx > args.limit:
                break

            payload = _embedding_payload(row)
            if not payload:
                skipped += 1
                continue

            vector = get_embedding(payload)
            if not vector:
                embed_failed += 1
                continue
            if len(vector) != CONTENT_VECTOR_DIMS:
                dim_mismatch += 1
                logger.warning(
                    "[WARN][KBIngest]: 向量维度不匹配 row=%s expected=%s actual=%s",
                    idx,
                    CONTENT_VECTOR_DIMS,
                    len(vector),
                )
                continue

            ok_docs.append(_to_doc(row, vector))

    logger.info(
        "[INFO][KBIngest]: 解析完成 rows_ok=%s skipped=%s embed_failed=%s dim_mismatch=%s",
        len(ok_docs),
        skipped,
        embed_failed,
        dim_mismatch,
    )

    if args.dry_run:
        print(
            f"[OK] Dry-run passed: rows_ok={len(ok_docs)}, skipped={skipped}, "
            f"embed_failed={embed_failed}, dim_mismatch={dim_mismatch}"
        )
        return 0

    actions = ({"_index": args.index, "_source": d} for d in ok_docs)
    success, errors = helpers.bulk(
        es,
        actions,
        chunk_size=max(1, int(args.batch_size)),
        raise_on_error=False,
    )
    failed = len(errors)
    print(
        f"[OK] KB ingest done: success={success}, failed={failed}, index={args.index}, "
        f"embed_failed={embed_failed}, dim_mismatch={dim_mismatch}, skipped={skipped}"
    )
    if failed > 0:
        print("[WARN] Failure samples (up to 3):")
        for item in errors[:3]:
            print(item)
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
