"""Environment-backed service configuration."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    incoming_dir: Path
    api_token: str
    timezone: ZoneInfo
    daily_ingest_time: str
    archive_retention_days: int

    @classmethod
    def from_environment(cls) -> "Settings":
        timezone_name = os.getenv("IG_TRENDS_TIMEZONE", "Asia/Taipei")
        schedule_time = os.getenv("IG_TRENDS_DAILY_INGEST_TIME", "03:30")
        if len(schedule_time) != 5 or schedule_time[2] != ":":
            raise ValueError("IG_TRENDS_DAILY_INGEST_TIME 必須為 HH:MM")
        hour, minute = (int(value) for value in schedule_time.split(":"))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("IG_TRENDS_DAILY_INGEST_TIME 必須為有效時間")
        try:
            retention = int(os.getenv("IG_TRENDS_ARCHIVE_RETENTION_DAYS", "90"))
        except ValueError as exc:
            raise ValueError("IG_TRENDS_ARCHIVE_RETENTION_DAYS 必須為整數") from exc
        if retention < 0:
            raise ValueError("IG_TRENDS_ARCHIVE_RETENTION_DAYS 不可為負數")
        return cls(
            data_dir=Path(os.getenv("IG_TRENDS_DATA_DIR", "/data")),
            incoming_dir=Path(os.getenv("IG_TRENDS_INCOMING_DIR", "/incoming")),
            api_token=os.getenv("IG_TRENDS_API_TOKEN", ""),
            timezone=ZoneInfo(timezone_name),
            daily_ingest_time=schedule_time,
            archive_retention_days=retention,
        )
