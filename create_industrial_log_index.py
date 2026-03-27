"""
 * @Module: create_industrial_log_index
 * @Description: 在 Elasticsearch 中创建工业级日志索引结构（intelligent_logs_v1）
 * @Interface: main
"""

from __future__ import annotations

import argparse
import logging

from app.tools.es_client import ESNervousSystem

logger = logging.getLogger(__name__)


INDUSTRIAL_MAPPING: dict = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 1,
        "analysis": {
            "analyzer": {
                "default": {"type": "ik_smart"},
            }
        },
    },
    "mappings": {
        "dynamic": False,
        "properties": {
            "@timestamp": {"type": "date"},
            "ingest_time": {"type": "date"},
            "log_level": {"type": "keyword"},
            "service_name": {"type": "keyword"},
            "env": {"type": "keyword"},
            "region": {"type": "keyword"},
            "zone": {"type": "keyword"},
            "host_name": {"type": "keyword"},
            "host_ip": {"type": "ip"},
            "logip": {"type": "ip"},
            "pod_name": {"type": "keyword"},
            "container_id": {"type": "keyword"},
            "node_name": {"type": "keyword"},
            "trace_id": {"type": "keyword"},
            "span_id": {"type": "keyword"},
            "requestId": {"type": "keyword"},
            "request_id": {"type": "keyword"},
            "session_id": {"type": "keyword"},
            "logger": {"type": "keyword"},
            "thread": {"type": "keyword"},
            "error_code": {"type": "keyword"},
            "error_type": {"type": "keyword"},
            "status_code": {"type": "integer"},
            "message": {
                "type": "text",
                "analyzer": "ik_smart",
                "fields": {
                    "keyword": {"type": "keyword", "ignore_above": 1024},
                },
            },
            "raw_message": {"type": "keyword", "ignore_above": 8192},
            "labels": {"type": "flattened"},
            "schema_version": {"type": "keyword"},
        },
    },
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create industrial ES log index mapping.",
    )
    parser.add_argument("--index", default="database_v", help="ES index name")
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="Delete existing index then recreate",
    )
    return parser


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    args = _build_parser().parse_args()

    es = ESNervousSystem().client
    index_name = args.index

    exists = es.indices.exists(index=index_name)
    if exists and not args.recreate:
        logger.info("[INFO][IndexScript]: 索引已存在，跳过创建（index=%s）", index_name)
        print(f"[INFO] Index already exists: {index_name} (use --recreate to rebuild)")
        return 0

    if exists and args.recreate:
        logger.warning("[WARN][IndexScript]: 即将删除并重建索引（index=%s）", index_name)
        es.indices.delete(index=index_name)

    # @Step: 1 - 使用显式 settings/mappings 参数，避免 body 兼容歧义
    es.indices.create(
        index=index_name,
        settings=INDUSTRIAL_MAPPING["settings"],
        mappings=INDUSTRIAL_MAPPING["mappings"],
    )
    logger.info("[INFO][IndexScript]: 工业级日志索引创建成功（index=%s）", index_name)
    print(f"[OK] Industrial log index created: {index_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

