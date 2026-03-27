"""
 * @Module: ingest_industrial_logs
 * @Description: 将 app/data/industrial_logs_100.csv 批量写入 ES 索引（默认 database_v）
 * @Interface: main
"""

from __future__ import annotations

import argparse
import csv
import logging
from pathlib import Path
from typing import Any

from elasticsearch import helpers

from app.tools.es_client import ESNervousSystem

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent


def _parse_labels(raw: str) -> dict[str, str]:
    # labels 形如: "team=sre,version=v2.0"
    text = (raw or "").strip()
    if not text:
        return {}
    out: dict[str, str] = {}
    for part in text.split(","):
        token = part.strip()
        if not token:
            continue
        if "=" in token:
            k, v = token.split("=", 1)
            k = k.strip()
            v = v.strip()
            if k:
                out[k] = v
        else:
            out[token] = "true"
    return out


def _clean_row(row: dict[str, str]) -> dict[str, Any]:
    def s(key: str) -> str | None:
        val = str(row.get(key, "") or "").strip()
        return val or None

    status_raw = s("status_code")
    status_code: int | None = None
    if status_raw is not None:
        try:
            status_code = int(status_raw)
        except ValueError:
            status_code = None

    doc: dict[str, Any] = {
        "@timestamp": s("@timestamp"),
        "ingest_time": s("ingest_time"),
        "log_level": s("log_level"),
        "service_name": s("service_name"),
        "env": s("env"),
        "region": s("region"),
        "zone": s("zone"),
        "host_name": s("host_name"),
        "host_ip": s("host_ip"),
        "logip": s("logip"),
        "pod_name": s("pod_name"),
        "container_id": s("container_id"),
        "node_name": s("node_name"),
        "trace_id": s("trace_id"),
        "span_id": s("span_id"),
        "requestId": s("requestId"),
        "request_id": s("request_id"),
        "session_id": s("session_id"),
        "logger": s("logger"),
        "thread": s("thread"),
        "error_code": s("error_code"),
        "error_type": s("error_type"),
        "status_code": status_code,
        "message": s("message"),
        "raw_message": s("raw_message"),
        "labels": _parse_labels(str(row.get("labels", "") or "")),
        "schema_version": s("schema_version"),
    }

    # 去掉 None，避免无意义空字段占空间
    return {k: v for k, v in doc.items() if v is not None}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ingest industrial logs CSV into Elasticsearch index.")
    parser.add_argument(
        "--csv",
        default=str(_PROJECT_ROOT / "app" / "data" / "industrial_logs_100.csv"),
        help="CSV file path",
    )
    parser.add_argument("--index", default="database_v", help="Target ES index")
    parser.add_argument("--batch-size", type=int, default=500, help="Bulk batch size")
    parser.add_argument("--dry-run", action="store_true", help="Only parse and validate CSV, do not write ES")
    return parser


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    args = _build_parser().parse_args()

    csv_path = Path(args.csv)
    if not csv_path.is_file():
        raise FileNotFoundError(f"CSV 不存在: {csv_path}")

    docs: list[dict[str, Any]] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            docs.append(_clean_row(row))

    logger.info("[INFO][IngestCSV]: 解析完成（rows=%s, csv=%s）", len(docs), csv_path)
    if args.dry_run:
        print(f"[OK] Dry-run passed: parsed {len(docs)} rows, no ES write.")
        return 0

    es = ESNervousSystem().client
    actions = ({"_index": args.index, "_source": d} for d in docs)
    ok_count, errors = helpers.bulk(
        es,
        actions,
        chunk_size=max(1, int(args.batch_size)),
        raise_on_error=False,
    )
    fail_count = len(errors)
    logger.info("[INFO][IngestCSV]: bulk 完成（ok=%s, fail=%s, index=%s）", ok_count, fail_count, args.index)
    print(f"[OK] Ingest done: ok={ok_count}, fail={fail_count}, index={args.index}")
    if fail_count > 0:
        print("[WARN] Failure samples (up to 3):")
        for item in errors[:3]:
            print(item)
    return 0 if fail_count == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

