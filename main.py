"""
 * @Module: main
 * @Description: Phase 2.1.3 统一交互入口：运维对话式 CLI，串联 agent 检索与推理
 * @Interface: main
"""

from __future__ import annotations

import sys


def _ensure_utf8_stdio() -> None:
    # @Step: 0 - Windows 控制台默认编码下 emoji 易触发 UnicodeEncodeError
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def main() -> None:
    # @Step: 1 - 加载 Agent 单例（会初始化 ES / 嵌入 / LLM 客户端链）
    _ensure_utf8_stdio()
    from app.core.agent_brain import agent

    print("========================================")
    print("   🤖 ELK-Agent 智能运维专家已就绪")
    print("      (输入 'exit' 或 'quit' 退出)")
    print("========================================")

    # @Agent_Logic: 单次查询失败不退出进程，便于连续排障
    while True:
        try:
            user_query = input("\n👤 运维人员: ").strip()

            if not user_query:
                continue

            if user_query.lower() in ("exit", "quit", "q"):
                print("👋 再见，专家！系统已下线。")
                break

            answer = agent.chat(user_query)

            print("-" * 40)
            print(f"🤖 Agent 诊断结果:\n\n{answer}")
            print("-" * 40)

        except KeyboardInterrupt:
            print("\n检测到强制退出指令。")
            sys.exit(0)
        except EOFError:
            print("\n[INFO][Main]: 输入结束，退出。")
            break
        except Exception as exc:
            print(f"❌ 运行异常: {exc!s}")


if __name__ == "__main__":
    main()
