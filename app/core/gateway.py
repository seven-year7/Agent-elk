"""
 * @Module: app/core/gateway
 * @Description: 多级智能网关：关键词直通 / 相似度阈值判定 / 普通聊天兜底
 * @Interface: MultiTierGateway.get_execution_strategy
"""

from __future__ import annotations

from app.core.config import LOG_SIMILARITY_THRESHOLD, OPS_DIRECT_KEYWORDS


class MultiTierGateway:
    @staticmethod
    def get_execution_strategy(user_query: str, top_score: float) -> str:
        """
        核心判定逻辑：
        返回策略: 'DIRECT_ANALYSIS', 'RAG_ANALYSIS', 'NORMAL_CHAT'
        """
        query_lower = (user_query or "").lower()

        # 1) 关键词直通车
        if any(k in query_lower for k in OPS_DIRECT_KEYWORDS):
            return "DIRECT_ANALYSIS"

        # 2) 向量相似度阈值
        if float(top_score) >= float(LOG_SIMILARITY_THRESHOLD):
            return "RAG_ANALYSIS"

        # 3) 兜底：普通对话
        return "NORMAL_CHAT"

