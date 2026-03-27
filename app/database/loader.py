"""
 * @Module: app/database/loader
 * @Description: 负责 CSV/PDF 等原始数据的加载与清洗（结构化为标准文档列表）
 * @Interface: N/A
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import pandas as pd
from elasticsearch import Elasticsearch, helpers
from dotenv import load_dotenv

from app.database.schema import INDEX_NAME, KNOWLEDGE_MAPPING
from app.database.vector_service import get_embedding

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")


def _env_str(name: str) -> str | None:
    raw = os.getenv(name)
    if not isinstance(raw, str):
        return None
    stripped = raw.strip()
    return stripped or None


def _build_es_client() -> Elasticsearch:
    """
    @Agent_Logic: 该 loader 作为离线入库脚本，默认连接本地 ES；
    若存在 ES_URL / ES_API_KEY / ES_USERNAME / ES_PASSWORD 则按项目约定启用认证。
    """
    es_url = _env_str("ES_URL") or "http://localhost:9200"
    api_key = _env_str("ES_API_KEY")
    username = _env_str("ES_USERNAME")
    password = _env_str("ES_PASSWORD")

    ca_certs = _env_str("ES_CA_CERTS")
    verify_raw = _env_str("ES_VERIFY_CERTS")
    verify_certs = True if verify_raw is None else verify_raw.lower() not in ("0", "false", "no")

    kwargs: dict[str, Any] = {
        "hosts": [es_url],
        "verify_certs": verify_certs,
    }
    if ca_certs:
        kwargs["ca_certs"] = ca_certs
    if api_key:
        kwargs["api_key"] = api_key
    elif username and password:
        kwargs["basic_auth"] = (username, password)

    return Elasticsearch(**kwargs)


es = _build_es_client()


def init_index() -> None:
    """初始化索引：不存在则创建，已存在则跳过。"""
    try:
        exists = es.indices.exists(index=INDEX_NAME)
    except Exception as exc:
        logger.error("[ERROR][Loader]: 检查索引是否存在失败（index=%s）：%s", INDEX_NAME, exc)
        raise

    if exists:
        logger.info("[INFO][Loader]: 索引已存在：%s", INDEX_NAME)
        return

    try:
        # @Agent_Logic: elasticsearch-py 8.x 推荐显式传 settings/mappings；body 仍可用但更不透明
        settings = KNOWLEDGE_MAPPING.get("settings", {})
        mappings = KNOWLEDGE_MAPPING.get("mappings", {})
        es.indices.create(index=INDEX_NAME, settings=settings, mappings=mappings)
        logger.info("[INFO][Loader]: 索引创建成功：%s", INDEX_NAME)
    except Exception as exc:
        logger.error("[ERROR][Loader]: 索引创建失败（index=%s）：%s", INDEX_NAME, exc)
        raise


def _normalize_tags(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    text = str(raw).strip()
    if not text or text.lower() == "nan":
        return []
    return [x.strip() for x in text.split(",") if x.strip()]


def ingest_csv_data(file_path: str | Path) -> int:
    """
    读取 CSV 并执行批量入库。
    期望列名：title, content, servicename, error_code, tags, timestamp, sessionid

    :return: 成功写入的文档数
    """
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"CSV 文件不存在：{path}")

    logger.info("[INFO][Loader]: 开始处理 CSV：%s", str(path))

    df = pd.read_csv(path)
    actions: list[dict[str, Any]] = []

    for _, row in df.iterrows():
        title = str(row.get("title", "") or "").strip()
        content = str(row.get("content", "") or "").strip()
        if not title and not content:
            continue

        combined_text = f"故障标题: {title}\n故障详情: {content}".strip()
        vector = get_embedding(combined_text)
        if not vector:
            continue

        doc_source = {
            "title": title,
            "content": content,
            "servicename": str(row.get("servicename", "") or "").strip(),
            "error_code": str(row.get("error_code", "") or "").strip(),
            "tags": _normalize_tags(row.get("tags")),
            "timestamp": row.get("timestamp"),
            "sessionid": str(row.get("sessionid", "") or "").strip(),
            "content_vector": vector,
        }

        actions.append({"_index": INDEX_NAME, "_source": doc_source})

        if len(actions) % 10 == 0:
            logger.info("[INFO][Loader]: 已构建 %s 条待入库文档", len(actions))

    if not actions:
        logger.warning("[WARN][Loader]: 未产生任何可入库文档（CSV=%s）", str(path))
        return 0

    try:
        helpers.bulk(es, actions)
    except Exception as exc:
        logger.error("[ERROR][Loader]: 批量入库失败（count=%s）：%s", len(actions), exc)
        raise

    logger.info("[INFO][Loader]: 入库完成（count=%s, index=%s）", len(actions), INDEX_NAME)
    return len(actions)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_index()

    project_root = Path(__file__).resolve().parents[2]
    default_csv = project_root / "app" / "data" / "elk_error_logs_100.csv"
    ingest_csv_data(default_csv)

