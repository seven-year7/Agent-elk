"""
 * @Module: test/test_database_v_ingest
 * @Description: 验证 industrial_logs_100.csv 已成功写入 database_v
 * @Interface: main
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.tools.es_client import ESNervousSystem


def _json_print(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def main() -> int:
    index_name = "database_v"
    es = ESNervousSystem().client

    exists = es.indices.exists(index=index_name)
    if not exists:
        _json_print({"ok": False, "reason": "index_not_found", "index": index_name})
        return 2

    count = int(es.count(index=index_name)["count"])
    sample_res = es.search(index=index_name, size=1, query={"match_all": {}})
    hits = (sample_res.get("hits") or {}).get("hits") or []
    sample = (hits[0].get("_source") if hits else {}) or {}

    required = [
        "@timestamp",
        "ingest_time",
        "log_level",
        "service_name",
        "env",
        "region",
        "zone",
        "host_name",
        "host_ip",
        "logip",
        "trace_id",
        "requestId",
        "message",
        "schema_version",
    ]
    missing = [k for k in required if k not in sample]

    # 再做一个过滤查询验证：ERROR 日志应可查到
    error_query = {
        "size": 1,
        "query": {"term": {"log_level": "ERROR"}},
        "_source": ["@timestamp", "service_name", "log_level", "message", "error_code"],
    }
    error_hits = (es.search(index=index_name, body=error_query).get("hits") or {}).get("hits") or []

    report = {
        "ok": count >= 100 and len(missing) == 0,
        "index": index_name,
        "count": count,
        "sample_missing_required_fields": missing,
        "error_query_hit_count": len(error_hits),
        "sample_error_hit": (error_hits[0].get("_source") if error_hits else None),
    }
    _json_print(report)

    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

