"""
 * @Module: app/memory/manager
 * @Description: 对话记忆：双指标触发摘要、Redis Hash 持久化、上下文硬上限兜底
 * @Interface: MemoryManager, memory_bus
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from app.memory.config import (
    MEMORY_DAILY_ARCHIVE_DIR,
    HARD_STOP_LIMIT,
    MEMORY_STORAGE_PATH,
    SUMMARY_MODEL,
    TOKEN_COMPRESSION_THRESHOLD,
    TURN_LIMIT,
)
from app.prompts import MEMORY_KEYPOINT_JSON_PROMPT_TMPL, MEMORY_SUMMARY_PROMPT_TMPL
from app.memory.redis_store import RedisMemoryStore

logger = logging.getLogger(__name__)

# @Step: 1 - 与全项目一致：从仓库根目录加载 .env
# @Security: 禁止在日志中输出 API Key；memory_state.json 可能含敏感对话，勿提交 Git
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env")

_OPENROUTER_DEFAULT_BASE = "https://openrouter.ai/api/v1"


def _env_str(name: str) -> str | None:
    raw = os.getenv(name)
    if not isinstance(raw, str):
        return None
    stripped = raw.strip()
    return stripped or None


def _env_int(name: str, default: int) -> int:
    raw = _env_str(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("[WARN][Memory]: 无效整数环境变量 %s=%r，使用默认 %s", name, raw, default)
        return default


def _build_summary_openai_client() -> OpenAI:
    router_key = _env_str("OPENROUTER_API_KEY")
    openai_key = _env_str("OPENAI_API_KEY")

    default_headers: dict[str, str] = {}
    referer = _env_str("OPENROUTER_HTTP_REFERER")
    app_title = _env_str("OPENROUTER_APP_NAME")
    if referer:
        default_headers["HTTP-Referer"] = referer
    if app_title:
        default_headers["X-Title"] = app_title

    if router_key:
        base_url = _env_str("OPENROUTER_BASE_URL") or _OPENROUTER_DEFAULT_BASE
        kwargs: dict = {"api_key": router_key, "base_url": base_url}
        if default_headers:
            kwargs["default_headers"] = default_headers
        logger.info("[INFO][Memory]: 摘要客户端使用 OpenRouter（base_url=%s）", base_url)
        return OpenAI(**kwargs)

    if not openai_key:
        raise ValueError("摘要模型需要 OPENROUTER_API_KEY 或 OPENAI_API_KEY")

    kwargs = {"api_key": openai_key}
    base_url = _env_str("OPENAI_BASE_URL")
    if base_url:
        kwargs["base_url"] = base_url
    if default_headers:
        kwargs["default_headers"] = default_headers
    logger.info("[INFO][Memory]: 摘要客户端使用 OPENAI_API_KEY（base_url_set=%s）", bool(base_url))
    return OpenAI(**kwargs)


def _resolve_default_thread_id() -> str:
    # @Step: 默认会话隔离 ID，避免多用户共享 default_user
    return (
        _env_str("AGENT_THREAD_ID")
        or _env_str("THREAD_ID")
        or _env_str("USER")
        or _env_str("USERNAME")
        or _env_str("COMPUTERNAME")
        or "default_user"
    )


class MemoryManager:
    """双指标（轮数 / 估算 Token）触发摘要；落盘 JSON；主请求前可检查硬上限。"""

    def __init__(
        self,
        token_limit: int | None = None,
        turn_limit: int | None = None,
        hard_stop_limit: int | None = None,
        storage_path: Path | str | None = None,
    ) -> None:
        # @Agent_Logic: 简易 char/4 估算；生产可换 tiktoken
        self.token_limit = (
            token_limit if token_limit is not None else _env_int("MEMORY_TOKEN_LIMIT", TOKEN_COMPRESSION_THRESHOLD)
        )
        self.turn_limit = turn_limit if turn_limit is not None else _env_int("MEMORY_HISTORY_THRESHOLD", TURN_LIMIT)
        self.hard_stop_limit = (
            hard_stop_limit if hard_stop_limit is not None else _env_int("MEMORY_HARD_STOP_LIMIT", HARD_STOP_LIMIT)
        )

        self.history: list[dict[str, str]] = []
        self.summary: str = ""

        raw_path = _env_str("MEMORY_STATE_PATH")
        if storage_path is not None:
            self.storage_path = Path(storage_path)
        elif raw_path:
            self.storage_path = Path(raw_path)
        else:
            self.storage_path = _PROJECT_ROOT / Path(MEMORY_STORAGE_PATH)

        self._summary_client = _build_summary_openai_client()
        self.summary_model = _env_str("MEMORY_SUMMARY_MODEL") or SUMMARY_MODEL

        # @Agent_Logic: 默认使用 Redis session；每次交互可通过 thread_id 实现隔离。
        self._default_thread_id = _resolve_default_thread_id()
        self._lock = threading.RLock()
        self._stores: dict[str, RedisMemoryStore] = {}
        self._daily_archive_dir = _PROJECT_ROOT / Path(MEMORY_DAILY_ARCHIVE_DIR)
        logger.info(
            "[INFO][Memory]: Redis 存储已启用（default_thread_id=%s）",
            self._default_thread_id,
        )

    @property
    def threshold(self) -> int:
        """与早期 MEMORY_HISTORY_THRESHOLD 语义一致（消息条数上限）。"""
        return self.turn_limit

    def _resolve_thread_id(self, thread_id: str | None) -> str:
        return thread_id or self._default_thread_id

    def _get_store(self, thread_id: str) -> RedisMemoryStore:
        if thread_id not in self._stores:
            self._stores[thread_id] = RedisMemoryStore(thread_id=thread_id)
        return self._stores[thread_id]

    def _load_state_from_store(self, thread_id: str) -> int:
        """
        @Step: 0 - 从 Redis 加载当前 thread_id 的 summary/history 到实例缓存。
        :return: stats:token_count（累计估算 Token）
        """
        store = self._get_store(thread_id)
        state = store.load_state()
        if not state:
            self.summary = ""
            self.history = []
            return 0

        self.summary = str(state.get("summary", "") or "")
        raw_history = state.get("history", [])
        if isinstance(raw_history, list):
            cleaned: list[dict[str, str]] = []
            for item in raw_history:
                if isinstance(item, dict) and "role" in item and "content" in item:
                    cleaned.append({"role": str(item["role"]), "content": str(item["content"])})
            self.history = cleaned
        else:
            self.history = []

        try:
            return int(state.get("tokens", 0) or 0)
        except Exception:
            return 0

    def _save_state_to_store(self, thread_id: str, tokens: int) -> None:
        """
        @Step: 0b - 将当前实例缓存（summary/history）写回 Redis。
        @Security: 不打印 history 内容，避免敏感数据落日志。
        """
        store = self._get_store(thread_id)
        store.save_state(self.history, self.summary, tokens=tokens, service="unknown")

    @staticmethod
    def _safe_file_stem(raw: str) -> str:
        stem = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._-")
        return stem or "default_user"

    @staticmethod
    def _normalize_text(raw: str, max_len: int = 260) -> str:
        text = re.sub(r"\s+", " ", (raw or "").strip())
        if len(text) <= max_len:
            return text
        return text[: max_len - 1] + "…"

    def _build_recent_rounds(self, limit_rounds: int = 4) -> list[dict[str, str]]:
        """
        从 history 中提取最近 N 轮 user-assistant 对，确保“最近几轮”可读清晰。
        """
        rounds: list[dict[str, str]] = []
        pending_user: str | None = None
        for item in self.history:
            role = str(item.get("role", "")).strip().lower()
            content = self._normalize_text(str(item.get("content", "")))
            if role == "user":
                pending_user = content
            elif role == "assistant":
                if pending_user is not None:
                    rounds.append({"user": pending_user, "assistant": content})
                    pending_user = None
                else:
                    rounds.append({"user": "（无）", "assistant": content})
        return rounds[-limit_rounds:]

    def _extract_key_points_by_rules(self, summary_text: str, recent_rounds: list[dict[str, str]]) -> list[str]:
        """
        规则兜底：从 summary + 最近轮次提取高价值信号，避免原文直接存储。
        """
        corpus = " ".join([summary_text] + [f"{r['user']} {r['assistant']}" for r in recent_rounds])
        if not corpus.strip():
            return ["暂无可提取的关键内容"]

        service_names = sorted(
            {m.group(0) for m in re.finditer(r"\b[a-zA-Z0-9_-]+(?:service|svc)\b", corpus, flags=re.IGNORECASE)}
        )
        error_codes = sorted({m.group(0) for m in re.finditer(r"\b[A-Z]{1,8}-?\d{2,8}\b", corpus)})
        trace_ids = sorted(
            {
                m.group(1)
                for m in re.finditer(
                    r"(?:trace[_-]?id|request[_-]?id)\s*[:=]?\s*([A-Za-z0-9_-]{6,64})",
                    corpus,
                    flags=re.IGNORECASE,
                )
            }
        )

        points: list[str] = []
        points.append(
            "核心结论: " + self._normalize_text(summary_text or "暂无明确结论，需结合最近轮次继续追问。", max_len=220)
        )
        points.append("服务范围: " + (", ".join(service_names[:6]) if service_names else "未识别到明确服务名"))
        points.append("错误码: " + (", ".join(error_codes[:8]) if error_codes else "未识别到标准错误码"))
        points.append("链路标识: " + (", ".join(trace_ids[:6]) if trace_ids else "未识别到 trace/request id"))

        action_lines: list[str] = []
        for r in recent_rounds[-2:]:
            for sentence in re.split(r"[。；;!?]\s*", r["assistant"]):
                s = sentence.strip()
                if any(k in s for k in ("建议", "执行", "排查", "检查", "修复", "回滚", "重启")):
                    action_lines.append(self._normalize_text(s, max_len=120))
        if action_lines:
            points.append("行动项: " + "；".join(action_lines[:4]))
        else:
            points.append("行动项: 暂无明确操作建议")
        return points

    def _extract_key_points(self, summary_text: str, recent_rounds: list[dict[str, str]]) -> list[str]:
        """
        小模型优先提取关键内容；失败时回退规则提取。
        """
        corpus = " ".join([summary_text] + [f"{r['user']} {r['assistant']}" for r in recent_rounds]).strip()
        if not corpus:
            return ["暂无可提取的关键内容"]

        prompt = MEMORY_KEYPOINT_JSON_PROMPT_TMPL.format(input_content=corpus)

        try:
            response = self._summary_client.chat.completions.create(
                model=self.summary_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
            )
            raw = (response.choices[0].message.content or "").strip()
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError("invalid key-point json root")

            core = self._normalize_text(str(parsed.get("core_conclusion", "")).strip(), max_len=220)
            services = [self._normalize_text(str(x), max_len=60) for x in parsed.get("service_scope", []) if str(x).strip()]
            codes = [self._normalize_text(str(x), max_len=60) for x in parsed.get("error_codes", []) if str(x).strip()]
            traces = [self._normalize_text(str(x), max_len=80) for x in parsed.get("trace_ids", []) if str(x).strip()]
            actions = [self._normalize_text(str(x), max_len=120) for x in parsed.get("action_items", []) if str(x).strip()]

            points: list[str] = []
            points.append("核心结论: " + (core or "暂无明确结论，需结合最近轮次继续追问。"))
            points.append("服务范围: " + (", ".join(services[:6]) if services else "未识别到明确服务名"))
            points.append("错误码: " + (", ".join(codes[:8]) if codes else "未识别到标准错误码"))
            points.append("链路标识: " + (", ".join(traces[:6]) if traces else "未识别到 trace/request id"))
            points.append("行动项: " + ("；".join(actions[:4]) if actions else "暂无明确操作建议"))
            logger.debug("[DEBUG][Memory]: 关键内容提取使用小模型成功")
            return points
        except Exception as exc:
            logger.warning("[WARN][Memory]: 小模型关键提取失败，回退规则提取: %s", exc)
            return self._extract_key_points_by_rules(summary_text, recent_rounds)

    def _save_daily_markdown(
        self,
        thread_id: str,
        summary_text: str | None = None,
        history: list[dict[str, str]] | None = None,
        archive_dt: datetime | None = None,
    ) -> None:
        """
        将当前会话压缩为“每日一个 md 文件（关键内容提取 + 最近轮次）”。
        文件名：YYYYMMDD_HHMMSS_{thread_id}.md（时间 + THREAD_ID）
        """
        local_now = archive_dt or datetime.now(timezone.utc).astimezone()
        date_key = local_now.strftime("%Y-%m-%d")
        file_ts = local_now.strftime("%Y%m%d_%H%M%S")
        safe_tid = self._safe_file_stem(thread_id)
        target = self._daily_archive_dir / f"{file_ts}_{safe_tid}.md"
        self._daily_archive_dir.mkdir(parents=True, exist_ok=True)

        effective_summary = (summary_text if summary_text is not None else self.summary).strip()
        effective_history = history if history is not None else self.history
        original_history = self.history
        self.history = effective_history
        recent_rounds = self._build_recent_rounds(limit_rounds=4)
        self.history = original_history
        key_points = self._extract_key_points(effective_summary, recent_rounds)
        lines = [
            "# 每日记忆压缩",
            "",
            f"- 日期: {date_key}",
            f"- thread_id: `{thread_id}`",
            f"- 更新于: {local_now.replace(microsecond=0).isoformat().replace('+00:00', 'Z')}",
            "",
            "## 关键内容提取",
        ]
        for point in key_points:
            lines.append(f"- {point}")

        lines.extend(
            [
                "",
                "## 最近几轮（清晰保留）",
            ]
        )
        if recent_rounds:
            for idx, round_item in enumerate(recent_rounds, start=1):
                lines.append(f"### 第{idx}轮")
                lines.append(f"- 用户: {round_item['user']}")
                lines.append(f"- 助手: {round_item['assistant']}")
                lines.append("")
        else:
            lines.append("- （暂无最近轮次）")
            lines.append("")

        lines.extend(
            [
                "## 原始摘要（供追溯）",
                effective_summary or "（暂无摘要）",
                "",
            ]
        )

        try:
            target.write_text("\n".join(lines), encoding="utf-8")
            logger.debug("[DEBUG][Memory]: 已写入每日压缩文档 %s", target)
        except OSError as exc:
            logger.warning("[WARN][Memory]: 写入每日压缩文档失败: %s", exc)

    def _archive_and_clear_if_cross_day(self, thread_id: str) -> None:
        """
        若 Redis 中最后更新时间属于前一天，则先归档为 md，再清空 Redis（先压缩后删除）。
        """
        store = self._get_store(thread_id)
        state = store.load_state()
        if not state:
            return

        updated_at = int(state.get("updated_at", 0) or 0)
        if updated_at <= 0:
            return

        last_local = datetime.fromtimestamp(updated_at).astimezone()
        now_local = datetime.now(timezone.utc).astimezone()
        if last_local.date() >= now_local.date():
            return

        end_of_day_local = last_local.replace(hour=23, minute=59, second=59, microsecond=0)
        archived_summary = str(state.get("summary", "") or "")
        archived_history = state.get("history", [])
        if not isinstance(archived_history, list):
            archived_history = []

        self._save_daily_markdown(
            thread_id=thread_id,
            summary_text=archived_summary,
            history=archived_history,
            archive_dt=end_of_day_local,
        )
        store.clear()
        logger.info(
            "[INFO][Memory]: 跨天归档完成并清理 Redis（thread_id=%s, archive_time=%s）",
            thread_id,
            end_of_day_local.isoformat(),
        )

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        if not text:
            return 0
        n = len(text) // 4
        return max(1, n)

    def _history_token_estimate(self) -> int:
        return sum(self._estimate_tokens(m.get("content", "")) for m in self.history)

    def _trim_history_for_token_budget(self) -> None:
        # @Step: 2b - 摘要模型失败或消息过少时，从前部丢弃直到估算 Token 回落
        while len(self.history) > 1 and self._history_token_estimate() > self.token_limit:
            dropped = self.history.pop(0)
            logger.info("[INFO][Memory]: Trim 丢弃最早一条 role=%s", dropped.get("role", "?"))
        # @Agent_Logic: 持久化由 add_message 在最后统一完成，避免重复写入。

    def add_message(self, role: str, content: str, thread_id: str | None = None) -> None:
        tid = self._resolve_thread_id(thread_id)
        with self._lock:
            self._archive_and_clear_if_cross_day(tid)
            existing_tokens = self._load_state_from_store(tid)
            self.history.append({"role": role, "content": content})

            # @Agent_Logic: 累计 token_count 只增不减；即使发生 Trim/摘要压缩也不回退。
            next_tokens_total = existing_tokens + self._estimate_tokens(content)

            history_tokens = self._history_token_estimate()
            turn_exceeded = len(self.history) > self.turn_limit
            token_exceeded = history_tokens > self.token_limit

            # @Step: 3 - 双指标：任一条满足即尝试压缩
            if turn_exceeded or token_exceeded:
                if len(self.history) > 2:
                    self._generate_summary()
                elif token_exceeded:
                    logger.warning(
                        "[WARN][Memory]: 消息条数≤2 但估算 Token 超标，执行机械 Trim（tokens≈%s）",
                        history_tokens,
                    )
                    self._trim_history_for_token_budget()

            self._save_state_to_store(tid, tokens=next_tokens_total)

    def append_key_log_snippets(self, snippets: str, thread_id: str | None = None) -> None:
        """
        将关键日志片段写入 summary，并落 Redis。

        @Agent_Logic: Chat 路径不再实时查 ES，因此需要把“关键日志片段”固化到 summary，
        使后续追问/闲聊仍携带运维上下文。
        """
        tid = self._resolve_thread_id(thread_id)
        cleaned = (snippets or "").strip()
        if not cleaned:
            return

        with self._lock:
            self._archive_and_clear_if_cross_day(tid)
            existing_tokens = self._load_state_from_store(tid)

            prefix = "【关键日志片段】\n"
            block = f"{prefix}{cleaned}\n"
            merged = block if not self.summary else f"{block}\n{self.summary}"

            # 控制 summary 长度，避免无限增长（按字符近似，保守裁剪）
            if len(merged) > 4000:
                merged = merged[:4000] + "…"

            self.summary = merged
            self._save_state_to_store(tid, tokens=existing_tokens)

    def _generate_summary(self) -> None:
        if len(self.history) <= 2:
            return

        logger.info(
            "[INFO][Memory]: 触发摘要压缩（model=%s, history_len=%s, tokens≈%s）",
            self.summary_model,
            len(self.history),
            self._history_token_estimate(),
        )

        prompt_lines: list[str] = []
        for msg in self.history[:-2]:
            prompt_lines.append(f"{msg['role']}: {msg['content']}")
        prompt = MEMORY_SUMMARY_PROMPT_TMPL.format(dialog_content="\n".join(prompt_lines))

        try:
            response = self._summary_client.chat.completions.create(
                model=self.summary_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            new_summary = (response.choices[0].message.content or "").strip()
            if new_summary:
                self.summary = new_summary
            self.history = self.history[-2:]
        except Exception as exc:
            logger.error("[ERROR][Memory]: 摘要生成失败: %s", exc)
            self.history = self.history[-self.turn_limit :]
            self._trim_history_for_token_budget()

    def check_safety_overflow(
        self, additional_content: str = "", thread_id: str | None = None
    ) -> bool:
        """
        估算「摘要 + 历史 + 额外内容（如 system + 本轮 user）」总 Token。
        若不低于 hard_stop_limit，返回 False，调用方应阻断主模型请求。
        """
        tid = self._resolve_thread_id(thread_id)
        with self._lock:
            self._archive_and_clear_if_cross_day(tid)
            _ = self._load_state_from_store(tid)
            total = self._estimate_tokens(self.summary)
            total += self._history_token_estimate()
            total += self._estimate_tokens(additional_content)
        safe = total < self.hard_stop_limit
        if not safe:
            logger.warning(
                "[WARN][Memory]: 安全兜底触发（估算总 tokens≈%s >= 硬上限 %s）",
                total,
                self.hard_stop_limit,
            )
        return safe

    def _save_to_disk(self) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "summary": self.summary,
            "history": self.history,
            "last_updated": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        }
        try:
            with self.storage_path.open("w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
            logger.debug("[DEBUG][Memory]: 已持久化至 %s", self.storage_path)
        except OSError as exc:
            logger.error("[ERROR][Memory]: 写入记忆文件失败: %s", exc)

    def _load_from_disk(self) -> None:
        if not self.storage_path.is_file():
            return
        try:
            with self.storage_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("[WARN][Memory]: 加载记忆文件失败，将使用空状态: %s", exc)
            return

        if not isinstance(data, dict):
            return

        self.summary = str(data.get("summary", "") or "")
        raw_history = data.get("history", [])
        if isinstance(raw_history, list):
            cleaned: list[dict[str, str]] = []
            for item in raw_history:
                if isinstance(item, dict) and "role" in item and "content" in item:
                    cleaned.append({"role": str(item["role"]), "content": str(item["content"])})
            self.history = cleaned

        logger.info("[INFO][Memory]: 已从磁盘恢复记忆（history=%s 条）", len(self.history))
        print("💾 [系统] 已成功从磁盘恢复对话记忆。")

    def get_context(self, thread_id: str | None = None) -> list[dict[str, str]]:
        tid = self._resolve_thread_id(thread_id)
        with self._lock:
            self._archive_and_clear_if_cross_day(tid)
            _ = self._load_state_from_store(tid)
            context: list[dict[str, str]] = []
            if self.summary:
                context.append({"role": "system", "content": f"之前的对话摘要：{self.summary}"})
            context.extend(self.history)
            return context


memory_bus = MemoryManager()
