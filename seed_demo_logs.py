"""
 * @Module: seed_demo_logs
 * @Description: Phase 1.2 演示：生成 10–20 条多场景模拟日志，嵌入后写入 intelligent_logs_v1
 * @Interface: main() -> int
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from typing import Any

# @Step: 1 - 先加载嵌入模块（会校验 OPENROUTER_API_KEY 或 OPENAI_API_KEY），再加载 ES 客户端
# @Agent_Logic: 顺序避免在无 Key 时仍先连 ES 产生误导性错误栈


def _ensure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _iso_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _build_demo_log_templates() -> list[dict[str, Any]]:
    # @Step: 2 - 支付 / 登录 / 数据库 三类业务，共 18 条（介于 10–20）
    payment = [
        ("payment-svc", "INFO", "订单 ord_10001 支付成功，用户 u_9001，渠道 wechat，金额 49.90 CNY"),
        ("payment-svc", "WARN", "订单 ord_10002 支付回调验签失败，重试第 2 次"),
        ("payment-svc", "ERROR", "订单 ord_10003 调用渠道超时，trace 已标记为待补偿"),
        ("payment-gateway", "INFO", "收到退款请求 ref_5001，原单 ord_9981，金额 12.00 CNY"),
        ("payment-gateway", "INFO", "风控规则命中：单笔超过阈值 5000 CNY，进入人工审核队列"),
        ("payment-svc", "ERROR", "渠道返回 INSUFFICIENT_FUNDS，用户 u_7712 扣款失败"),
    ]
    login_audit = [
        ("auth-service", "INFO", "用户 alice 登录成功，来源 IP 203.0.113.10，设备 Web"),
        ("auth-service", "WARN", "用户 bob 连续 5 次密码错误，已触发账号临时锁定 15 分钟"),
        ("auth-service", "ERROR", "SSO ticket 校验失败：ticket 已过期或已被消费"),
        ("auth-service", "INFO", "管理员 mike 通过 MFA 二次验证，登录管理后台"),
        ("auth-service", "WARN", "检测到异常登录地：用户 carol 从境外 IP 登录"),
        ("auth-service", "INFO", "OAuth2 授权码换 token 成功，client_id=mobile-app"),
    ]
    database = [
        ("order-db", "WARN", "慢查询：SELECT * FROM orders WHERE user_id=? 耗时 3200ms，rows_examined 120000"),
        ("user-db", "INFO", "连接池使用率 82%，active=41 max=50"),
        ("inventory-db", "ERROR", "死锁检测：事务 tx_4412 与 tx_4415 互相等待，已回滚较小事务"),
        ("reporting-db", "INFO", "夜间批处理 job_nightly_agg 开始，预计处理分区 p20260324"),
        ("order-db", "WARN", "主从延迟 6s，从库 replica-2 复制线程落后"),
        ("user-db", "ERROR", "唯一约束冲突：email 已存在，插入用户失败"),
    ]

    rows: list[tuple[str, str, str]] = []
    rows.extend(payment)
    rows.extend(login_audit)
    rows.extend(database)

    docs: list[dict[str, Any]] = []
    for service_name, level, message in rows:
        docs.append(
            {
                "@timestamp": _iso_timestamp(),
                "log_level": level,
                "service_name": service_name,
                "trace_id": uuid.uuid4().hex,
                "message": message,
            }
        )
    return docs


def main() -> int:
    _ensure_utf8_stdio()

    try:
        from app.utils.embedding import embedder
        from app.tools.es_client import es_node
    except ValueError as exc:
        print(f"❌ 初始化失败：{exc!s}")
        return 1

    print(es_node.init_index())

    templates = _build_demo_log_templates()
    total = len(templates)
    print(f"[INFO][Seed]: 准备写入 {total} 条演示日志（含向量）…")

    for index, base in enumerate(templates, start=1):
        message = str(base["message"])
        try:
            vector = embedder.get_embedding(message)
        except Exception as exc:
            print(f"❌ 第 {index}/{total} 条嵌入失败：{exc!s}")
            return 1

        doc = {**base}
        try:
            es_node.push_log(doc)
        except Exception as exc:
            print(f"❌ 第 {index}/{total} 条写入 ES 失败：{exc!s}")
            return 1

        print(f"  ✓ [{index}/{total}] {base['service_name']} | {base['log_level']}")

    print(f"🚀 完成：已向 intelligent_logs_v1 灌溉 {total} 条带向量的模拟日志。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
