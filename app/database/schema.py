"""
 * @Module: app/database/schema
 * @Description: 定义 Elasticsearch 索引 Mapping（索引结构）与相关常量
 * @Interface: N/A
"""

from __future__ import annotations

import os


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(str(raw).strip())
    except Exception:
        return default


# 长期记忆（知识库）索引名
INDEX_NAME = "elk_agent_knowledge_base"

# Embedding 向量维度：需与实际 Embedding 模型一致
CONTENT_VECTOR_DIMS = _env_int("KNOWLEDGE_VECTOR_DIMS", 1536)


# 索引配置：支持中文分词（IK）、向量存储与结构化字段过滤
KNOWLEDGE_MAPPING: dict = {
    "settings": {
        "index": {
            "analysis": {
                "analyzer": {
                    # 默认使用 IK 中文分词（需 ES 服务端安装 analysis-ik）
                    "default": {"type": "ik_max_word"},
                }
            }
        }
    },
    "mappings": {
        "properties": {
            "title": {"type": "text", "analyzer": "ik_max_word"},
            "content": {"type": "text", "analyzer": "ik_max_word"},
            "content_vector": {
                "type": "dense_vector",
                "dims": CONTENT_VECTOR_DIMS,
                "index": True,
                "similarity": "cosine",
            },
            "servicename": {"type": "keyword"},
            "error_code": {"type": "keyword"},
            "tags": {"type": "keyword"},
            "timestamp": {"type": "date"},
            "sessionid": {"type": "keyword"},
        }
    },
}

