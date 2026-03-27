"""
 * @Module: app/memory/config
 * @Description: 短期记忆压缩阈值、摘要模型与持久化路径的集中默认项（可被环境变量覆盖）
 * @Interface: TURN_LIMIT、TOKEN_COMPRESSION_THRESHOLD、SUMMARY_MODEL、MEMORY_STORAGE_PATH 等常量
"""

# --- 压缩触发阈值 ---
# 当对话轮数超过此值时触发摘要
TURN_LIMIT = 8

# 当估算 Token 超过此值时触发摘要
TOKEN_COMPRESSION_THRESHOLD = 3000

# Phase 2.6：摘要 + 历史 + 本轮载荷估算 Token 达此值则阻断主模型（与 MEMORY_HARD_STOP_LIMIT 对齐）
HARD_STOP_LIMIT = 7000

# --- 模型选型 ---
# 用于生成摘要的轻量级模型
SUMMARY_MODEL = "xiaomi/mimo-v2-flash"

# --- 持久化配置 ---
# 模拟 PostgresSaver 的本地存储路径（相对仓库根目录）
MEMORY_STORAGE_PATH = "config/memory_state.json"

# --- Redis 结构化存储（Phase 2.9）---
# Redis 基础连接
REDIS_HOST = "localhost"
REDIS_PORT = 6379
REDIS_DB = 0

# 存储前缀，用于命名空间隔离（Key: SESSION_PREFIX + {thread_id}）
SESSION_PREFIX = "elk:agent:session:"

# 记忆有效期（TTL）：86400 秒（1天）后自动清理，防止爆炸
MEMORY_TTL = 86400

# 每日压缩文档目录（相对仓库根目录）
MEMORY_DAILY_ARCHIVE_DIR = "docs/memory-daily"
