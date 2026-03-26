"""
 * @Module: test_redis_memory
 * @Description: Phase 2.10 Redis 结构化记忆系统集成测试（多 thread 隔离 / token 触发摘要 / 持久化恢复）
 * @Interface: test_memory_system
"""

from __future__ import annotations

import time
from typing import Any

from app.memory.config import TOKEN_COMPRESSION_THRESHOLD
from app.memory.manager import MemoryManager


def _get_store_for_thread(manager: MemoryManager, thread_id: str) -> Any:
    """
    测试专用：通过 manager 内部 store 缓存拿到与当前实例一致的 RedisMemoryStore。
    """
    get_store = getattr(manager, "_get_store", None)
    if callable(get_store):
        return get_store(thread_id)
    # fallback：退回到直接构造（可能不满足内存回退跨实例一致性，但通常 Redis 可用时无影响）
    from app.memory.redis_store import RedisMemoryStore

    return RedisMemoryStore(thread_id=thread_id)


def test_memory_system() -> None:
    print("=== Redis 结构化记忆集成测试开始 ===")

    # --- 测试点 1：多线程隔离性 ---
    print("测试 1：验证多线程隔离...")
    mem = MemoryManager(token_limit=10**9, turn_limit=100, hard_stop_limit=10**9)

    user_a = "thread_a"
    user_b = "thread_b"

    mem.add_message("user", "Alice is focusing on pay-service.", thread_id=user_a)
    mem.add_message("user", "Bob is focusing on db-cluster.", thread_id=user_b)

    store_a = _get_store_for_thread(mem, user_a)
    store_b = _get_store_for_thread(mem, user_b)

    state_a = store_a.load_state()
    state_b = store_b.load_state()
    assert state_a is not None, "Thread A 期望存在持久化状态"
    assert state_b is not None, "Thread B 期望存在持久化状态"

    assert state_a["history"][0]["content"].startswith("Alice"), state_a["history"]
    assert "Alice" not in state_b["history"][0]["content"], state_b["history"]
    assert state_b["history"][0]["content"].startswith("Bob"), state_b["history"]

    print("OK：Thread A / Thread B 记忆隔离通过。")

    # --- 测试点 2：Token 阈值触发摘要压缩 ---
    print(
        f"测试 2：验证 Token 压缩触发（阈值常量={TOKEN_COMPRESSION_THRESHOLD}，本测试将使用较小 token_limit 强制触发）..."
    )
    mem2 = MemoryManager(token_limit=50, turn_limit=100, hard_stop_limit=10**9)

    thread_id = "thread_token"

    # 让历史先达到 2 条（确保第三条触发时 len(history)>2，从而走摘要而不是机械 Trim）
    mem2.add_message("user", "short-1", thread_id=thread_id)
    mem2.add_message("assistant", "short-2", thread_id=thread_id)

    def fake_generate_summary() -> None:
        # @Agent_Logic: 测试不依赖真实 LLM；只验证摘要触发与字段落库。
        mem2.summary = "mock summary: token compression triggered"
        mem2.history = mem2.history[-2:]

    mem2._generate_summary = fake_generate_summary  # type: ignore[attr-defined]

    # 生成足够长的文本，确保追加后 history_tokens > mem2.token_limit
    base = "x" * 200
    long_text = base
    while mem2._estimate_tokens(long_text) <= mem2.token_limit:  # type: ignore[attr-defined]
        long_text = long_text + base

    mem2.add_message("assistant", long_text, thread_id=thread_id)

    store = _get_store_for_thread(mem2, thread_id)
    updated = store.load_state()
    assert updated is not None, "期望有更新后的会话状态"
    assert updated["summary"], "期望 summary 已生成（mock）"
    assert len(updated["history"]) == 2, f"期望压缩后保留最近两条，实际={len(updated['history'])}"

    print("OK：Token 触发摘要压缩通过。")

    # --- 测试点 3：持久化与恢复 ---
    print("测试 3：验证持久化恢复（模拟重启）...")
    store2 = _get_store_for_thread(mem2, thread_id)

    # 若当前处于内存回退（没有 redis），跨 manager 实例无法恢复，这里跳过该断言。
    if getattr(store2, "_redis", None) is None:
        print("跳过：Redis 未启用（内存回退模式），无法验证跨实例恢复。")
        return

    mem3 = MemoryManager(token_limit=10**9, turn_limit=100, hard_stop_limit=10**9)
    store3 = _get_store_for_thread(mem3, thread_id)

    restored = store3.load_state()
    assert restored is not None, "期望重启后仍能加载会话状态"
    assert restored["summary"] == updated["summary"], "summary 应保持一致"
    assert len(restored["history"]) == 2, "history 应保持压缩后的长度"

    print("OK：持久化恢复通过。")


if __name__ == "__main__":
    start = time.time()
    test_memory_system()
    print(f"=== 测试结束，用时 {time.time() - start:.2f}s ===")

