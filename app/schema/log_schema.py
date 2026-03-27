"""
 * @Module: app/schema/log_schema
 * @Description: 智能日志索引名称与 Elasticsearch Mapping（IK 全文）
 * @Interface: LOG_INDEX_NAME, INDEX_MAPPING
"""

# @Agent_Logic: message 使用 ik_smart（细粒度分词）；集群须安装 analysis-ik 插件，否则 init_index 会失败
# @Security: 本文件仅含结构定义，不包含密钥

LOG_INDEX_NAME = "intelligent_logs_v1"

INDEX_MAPPING = {
    "mappings": {
        "properties": {
            "@timestamp": {"type": "date"},
            "log_level": {"type": "keyword"},
            "service_name": {"type": "keyword"},
            "trace_id": {"type": "keyword"},
            "message": {
                "type": "text",
                "analyzer": "ik_smart",
                "fields": {
                    "keyword": {"type": "keyword", "ignore_above": 256},
                },
            },
        },
    },
}
