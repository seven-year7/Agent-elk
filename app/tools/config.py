"""
 * @Module: app/tools/config
 * @Description: ES 客户端与日志检索工具的默认 URL、top_k、kNN 候选规模等集中配置
 * @Interface: ES_DEFAULT_URL、SEARCH_LOGS_DEFAULT_TOP_K、KNN_NUM_CANDIDATES_MIN、KNN_NUM_CANDIDATES_PER_TOP_K
"""

# Elasticsearch 节点默认地址（可被 ES_URL 覆盖）
ES_DEFAULT_URL = "http://localhost:9200"

# kNN 返回条数默认值（与 agent_brain 注入上下文的 top_k 保持一致）
SEARCH_LOGS_DEFAULT_TOP_K = 3

# kNN num_candidates 下界与放大系数：max(KNN_NUM_CANDIDATES_MIN, top_k * KNN_NUM_CANDIDATES_PER_TOP_K)
KNN_NUM_CANDIDATES_MIN = 100
KNN_NUM_CANDIDATES_PER_TOP_K = 32
