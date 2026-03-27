"""
 * @Module: app/mcp_server/time_utils
 * @Description: 时间与时区处理：解析 yyyy-MM-dd HH:mm:ss + timeZone → UTC epoch_millis；并做窗口限制
 * @Interface: parse_local_time_range_to_epoch_millis
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class TimeRangeEpochMillis:
    start_ms: int
    end_ms: int
    time_zone: str
    start_input: str
    end_input: str


def _parse_dt(text: str, time_zone: str) -> datetime:
    # 约定格式：yyyy-MM-dd HH:mm:ss
    dt = datetime.strptime(text.strip(), "%Y-%m-%d %H:%M:%S")
    tz = ZoneInfo(time_zone)
    return dt.replace(tzinfo=tz)


def parse_local_time_range_to_epoch_millis(
    start_time: str,
    end_time: str,
    *,
    time_zone: str = "Asia/Shanghai",
    max_window_seconds: int = 24 * 60 * 60,
) -> TimeRangeEpochMillis:
    start_dt = _parse_dt(start_time, time_zone)
    end_dt = _parse_dt(end_time, time_zone)

    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    if start_ms >= end_ms:
        raise ValueError("startTime must be < endTime")

    window_seconds = int((end_ms - start_ms) / 1000)
    if window_seconds > max_window_seconds:
        raise ValueError(f"time window too large: {window_seconds}s > {max_window_seconds}s")

    return TimeRangeEpochMillis(
        start_ms=start_ms,
        end_ms=end_ms,
        time_zone=time_zone,
        start_input=start_time,
        end_input=end_time,
    )

