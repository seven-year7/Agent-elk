"""
 * @Module: app/memory/redis_store
 * @Description: Redis Hash session store (Summary / History / Token count / Meta)
 * @Interface: RedisMemoryStore
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from app.memory.config import (
    MEMORY_TTL,
    REDIS_DB,
    REDIS_HOST,
    REDIS_PORT,
    SESSION_PREFIX,
)

logger = logging.getLogger(__name__)

try:
    import redis  # type: ignore
except Exception as exc:  # pragma: no cover
    redis = None  # type: ignore
    logger.warning("[WARN][RedisStore]: 未安装 redis 依赖，将进入内存回退：%s", exc)


class RedisMemoryStore:
    """
    Redis session store wrapper: one thread_id maps to one Redis Hash.

    Key: elk:agent:session:{thread_id}
    Fields:
    - summary: string
    - history: JSON (list[dict(role, content)])
    - stats:token_count: int (cumulative estimate)
    - meta:service: string
    - meta:updated_at: int (unix seconds)
    """

    def __init__(self, thread_id: str = "default_user") -> None:
        self.thread_id = thread_id
        self.key = f"{SESSION_PREFIX}{thread_id}"
        self.ttl = MEMORY_TTL

        # @Agent_Logic: 提供安全兜底（redis 不可用时退回到进程内存），避免直接崩溃。
        self._memory_fallback: dict[str, Any] = {}

        if redis is None:
            self._redis = None
            return

        self._redis = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=REDIS_DB,
            decode_responses=True,
        )

    @staticmethod
    def _next_day_end_ts() -> int:
        """
        返回“当前本地时区当天 23:59:59”对应的 unix 时间戳。
        """
        now_local = datetime.now().astimezone()
        end_local = now_local.replace(hour=23, minute=59, second=59, microsecond=0)
        return int(end_local.timestamp())

    def save_state(
        self,
        history: list[dict[str, str]],
        summary: str,
        tokens: int,
        service: str | None = None,
    ) -> None:
        """
        核心写入：HSET（字段级） + EXPIRE（TTL）；
        通过 pipeline 提升效率并减少往返次数。
        """
        now_ts = int(time.time())
        end_of_day_ts = self._next_day_end_ts()
        history_json = json.dumps(history, ensure_ascii=False)

        mapping: dict[str, Any] = {
            "history": history_json,
            "summary": summary,
            "stats:token_count": int(tokens),
            "meta:updated_at": now_ts,
        }
        if service:
            mapping["meta:service"] = service

        # @Step: 1 - 写入 Redis Hash（或内存回退）
        if self._redis is None:
            self._memory_fallback[self.key] = {
                **mapping,
                "_expires_at": end_of_day_ts,
            }
            logger.warning(
                "[WARN][RedisStore]: Redis 不可用，已写入内存回退（thread_id=%s）",
                self.thread_id,
            )
            return

        try:
            pipe = self._redis.pipeline()
            pipe.hset(self.key, mapping=mapping)
            # @Step: 2 - 按“日切”过期：每天 23:59:59 到期
            pipe.expireat(self.key, end_of_day_ts)
            pipe.execute()
            logger.debug(
                "[DEBUG][RedisStore]: save_state OK（key=%s, expire_at=%s）",
                self.key,
                datetime.fromtimestamp(end_of_day_ts, tz=timezone.utc).isoformat(),
            )
        except Exception as exc:  # pragma: no cover
            # @Security: 不在日志中输出任何敏感内容（history 可能含对话）。
            logger.error("[ERROR][RedisStore]: Redis 写入失败（key=%s）：%s", self.key, exc)

    def load_state(self) -> dict[str, Any] | None:
        """读取会话状态；无数据返回 None。"""
        now_ts = int(time.time())

        if self._redis is None:
            state = self._memory_fallback.get(self.key)
            if not state:
                return None
            expires_at = int(state.get("_expires_at", 0))
            if expires_at and now_ts >= expires_at:
                self._memory_fallback.pop(self.key, None)
                return None
            return self._normalize_loaded_state(state)

        try:
            data = self._redis.hgetall(self.key)
        except Exception as exc:  # pragma: no cover
            logger.error("[ERROR][RedisStore]: Redis 读取失败（key=%s）：%s", self.key, exc)
            return None

        if not data:
            return None

        # @Step: 2 - 反序列化与字段归一
        return self._normalize_loaded_state(data)

    def _normalize_loaded_state(self, data: dict[str, Any]) -> dict[str, Any]:
        raw_history = data.get("history", "[]")
        try:
            history = json.loads(raw_history) if isinstance(raw_history, str) else raw_history
        except Exception:
            history = []

        if not isinstance(history, list):
            history = []

        summary = str(data.get("summary", "") or "")
        try:
            tokens = int(data.get("stats:token_count", 0) or 0)
        except Exception:
            tokens = 0

        service = str(data.get("meta:service", "unknown") or "unknown")

        cleaned_history: list[dict[str, str]] = []
        if isinstance(history, list):
            for item in history:
                if isinstance(item, dict) and "role" in item and "content" in item:
                    cleaned_history.append({"role": str(item["role"]), "content": str(item["content"])})

        return {
            "history": cleaned_history,
            "summary": summary,
            "tokens": tokens,
            "service": service,
            "updated_at": int(data.get("meta:updated_at", 0) or 0),
        }

    def clear(self) -> None:
        """手动清理该 thread_id 的记忆。"""
        if self._redis is None:
            self._memory_fallback.pop(self.key, None)
            return

        try:
            self._redis.delete(self.key)
        except Exception as exc:  # pragma: no cover
            logger.error("[ERROR][RedisStore]: Redis 删除失败（key=%s）：%s", self.key, exc)

