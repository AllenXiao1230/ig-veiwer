"""CSV exports with formula protection and atomic replacement."""

import csv
from pathlib import Path
from typing import Iterable

from .data import Record, relationship_metrics
from .storage import atomic_text_writer


def safe_csv_value(value: str) -> str:
    return f"'{value}" if value.lstrip().startswith(("=", "+", "-", "@")) else value


def export_records(path: str | Path, records: Iterable[Record]) -> None:
    with atomic_text_writer(Path(path), encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["用戶名稱 (Username)", "顯示名稱 / 相關資訊", "時間紀錄"])
        writer.writerows([safe_csv_value(value) for value in record] for record in records)


def export_relationship_summary(path: str | Path, datasets: dict[str, list[Record]], comparison: dict[str, int] | None) -> None:
    metrics = relationship_metrics(datasets)
    rows = [
        ("指標", "數值"),
        ("粉絲", metrics["followers"]),
        ("追蹤中", metrics["following"]),
        ("雙向互相追蹤", metrics["mutual"]),
        ("對方未回追", metrics["not_following_back"]),
        ("你尚未回追", metrics["not_followed_back"]),
    ]
    if comparison:
        rows.extend([
            ("相較前次新增粉絲", comparison["new_followers"]),
            ("相較前次流失粉絲", comparison["lost_followers"]),
            ("相較前次新增追蹤", comparison["new_following"]),
            ("相較前次取消追蹤", comparison["unfollowed"]),
        ])
    with atomic_text_writer(Path(path), encoding="utf-8-sig", newline="") as handle:
        csv.writer(handle).writerows(rows)
