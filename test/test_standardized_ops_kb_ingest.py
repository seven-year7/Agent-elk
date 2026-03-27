"""
 * @Module: test/test_standardized_ops_kb_ingest
 * @Description: 校验知识库索引入库结果：文档数量、向量字段存在率、维度一致性
 * @Interface: main
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from collections.abc import Iterable

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.database.loader import es
from app.database.schema import CONTENT_VECTOR_DIMS, INDEX_NAME


def _iter_hits(index_name: str, sample_size: int) -> Iterable[dict]:
    resp = es.search(
        index=index_name,
        body={
            "size": sample_size,
            "_source": ["kb_id", "title", "content_vector"],
            "query": {"match_all": {}},
            "sort": [{"timestamp": {"order": "desc"}}],
        },
    )
    return ((resp.get("hits") or {}).get("hits") or [])


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate standardized ops knowledge base ingest result.")
    parser.add_argument("--index", default=INDEX_NAME, help="Knowledge index name")
    parser.add_argument("--sample-size", type=int, default=20, help="Sample docs to validate vectors")
    args = parser.parse_args()

    exists = es.indices.exists(index=args.index)
    if not exists:
        print(f"[FAIL] index not found: {args.index}")
        return 2

    count_resp = es.count(index=args.index, body={"query": {"match_all": {}}})
    total = int(count_resp.get("count", 0))
    if total <= 0:
        print(f"[FAIL] index has no documents: {args.index}")
        return 2

    hits = list(_iter_hits(args.index, max(1, args.sample_size)))
    if not hits:
        print(f"[FAIL] search returned 0 hits on index: {args.index}")
        return 2

    vector_missing = 0
    vector_bad_dim = 0
    for hit in hits:
        src = hit.get("_source") or {}
        vec = src.get("content_vector")
        if not isinstance(vec, list):
            vector_missing += 1
            continue
        if len(vec) != CONTENT_VECTOR_DIMS:
            vector_bad_dim += 1

    print(
        f"[OK] index={args.index}, total={total}, sample={len(hits)}, "
        f"vector_missing={vector_missing}, vector_bad_dim={vector_bad_dim}, expected_dim={CONTENT_VECTOR_DIMS}"
    )
    if vector_missing > 0 or vector_bad_dim > 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
