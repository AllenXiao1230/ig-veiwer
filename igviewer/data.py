"""Parsing, normalization, and relationship analysis for Instagram exports."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import json
import re
from typing import Any, Iterable


Record = tuple[str, str, str]

KEY_FOLLOWERS = "粉絲"
KEY_FOLLOWING = "追蹤中"
KEY_NOT_FOLLOWING_BACK = "對方未回追"
KEY_MUTUAL = "雙向追蹤"
KEY_NOT_FOLLOWED_BACK = "你尚未回追"

FILENAME_MAP = {
    "following": KEY_FOLLOWING,
    "following_hashtags": "追蹤的標籤",
    "blocked_profiles": "已封鎖的帳號",
    "close_friends": "摯友",
    "pending_follow_requests": "待處理的追蹤要求",
    "recent_follow_requests": "近期的追蹤要求",
    "recently_unfollowed_profiles": "最近取消追蹤的帳號",
    "restricted_profiles": "已限制的帳號",
    "removed_suggestions": "已移除的建議",
    "hide_story_from": "向...隱藏限時動態",
    "profiles_you've_favorited": "已加星號的個人檔案",
}

SENSITIVE_DATASETS = {
    "已封鎖的帳號",
    "已限制的帳號",
    "摯友",
    "向...隱藏限時動態",
}

USERNAME_LABELS = {"用戶名稱", "用户名", "username", "user name", "account username"}
DETAIL_LABELS = {"名稱", "名称", "name", "display name", "full name"}


@dataclass(frozen=True)
class ImportIssue:
    path: Path
    message: str


@dataclass
class FileParseResult:
    records: list[Record] = field(default_factory=list)
    issues: list[ImportIssue] = field(default_factory=list)


@dataclass
class ImportResult:
    datasets: dict[str, list[Record]] = field(default_factory=dict)
    sources: list[Path] = field(default_factory=list)
    issues: list[ImportIssue] = field(default_factory=list)
    files_loaded: int = 0
    records_before_deduplication: int = 0

    @property
    def records_after_deduplication(self) -> int:
        return sum(len(records) for records in self.datasets.values())

    @property
    def duplicate_count(self) -> int:
        return self.records_before_deduplication - self.records_after_deduplication


def fix_ig_encoding(text: Any) -> str:
    """Repair the common UTF-8-as-Latin-1 mojibake without damaging Unicode."""
    if text is None:
        return ""
    value = str(text)
    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def _timestamp_to_text(timestamp: Any) -> str:
    try:
        if not timestamp:
            return ""
        return datetime.fromtimestamp(float(timestamp)).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, TypeError, ValueError):
        return ""


def _record_from_string_list(item: dict[str, Any]) -> Record | None:
    values = item.get("string_list_data")
    if not isinstance(values, list) or not values:
        return None
    first = values[0] if isinstance(values[0], dict) else {}
    username = fix_ig_encoding(item.get("title")) or fix_ig_encoding(first.get("value"))
    if not username:
        return None
    return (username.strip(), username.strip(), _timestamp_to_text(first.get("timestamp")))


def _record_from_label_values(item: dict[str, Any]) -> Record | None:
    username = ""
    detail = ""
    values = item.get("label_values")
    if not isinstance(values, list):
        return None
    for value in values:
        if not isinstance(value, dict):
            continue
        label = fix_ig_encoding(value.get("label")).strip().casefold()
        text = fix_ig_encoding(value.get("value")).strip()
        if label in USERNAME_LABELS:
            username = text
        elif label in DETAIL_LABELS:
            detail = text
    if not username:
        return None
    return (username, detail, _timestamp_to_text(item.get("timestamp")))


def _record_lists(value: Any) -> Iterable[list[Any]]:
    """Yield record lists in both current and nested Instagram export layouts."""
    if isinstance(value, list):
        if value and all(isinstance(item, dict) for item in value):
            yield value
        for item in value:
            if isinstance(item, dict):
                yield from _record_lists(item)
    elif isinstance(value, dict):
        for child in value.values():
            yield from _record_lists(child)


def parse_file(path: str | Path) -> FileParseResult:
    """Parse one export JSON file and return useful diagnostics instead of failing silently."""
    source = Path(path)
    try:
        with source.open("r", encoding="utf-8") as handle:
            content = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return FileParseResult(issues=[ImportIssue(source, f"無法讀取 JSON：{exc}")])

    records: list[Record] = []
    seen_lists: set[int] = set()
    for items in _record_lists(content):
        marker = id(items)
        if marker in seen_lists:
            continue
        seen_lists.add(marker)
        for item in items:
            if "string_list_data" in item:
                record = _record_from_string_list(item)
            elif "label_values" in item:
                record = _record_from_label_values(item)
            else:
                continue
            if record is not None:
                records.append(record)

    if not records:
        return FileParseResult(
            issues=[ImportIssue(source, "找不到支援的 Instagram 帳號資料格式")]
        )
    return FileParseResult(records=records)


def dataset_name_for_path(path: str | Path) -> str:
    stem = Path(path).stem.casefold()
    # Instagram splits larger follower exports into followers_1, followers_2, ... .
    if re.fullmatch(r"followers(?:_\d+)?", stem):
        return KEY_FOLLOWERS
    if re.fullmatch(r"following(?:_\d+)?", stem):
        return KEY_FOLLOWING
    return FILENAME_MAP.get(stem, stem.replace("_", " ").title())


def _deduplicate(records: Iterable[Record]) -> list[Record]:
    result: list[Record] = []
    seen: set[str] = set()
    for username, detail, timestamp in records:
        normalized = username.strip().casefold()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append((username.strip(), detail.strip(), timestamp))
    return result


def import_files(paths: Iterable[str | Path], progress_callback: Any = None) -> ImportResult:
    """Parse, categorize, and merge selected JSON files, including follower shards."""
    selected_paths = [Path(path) for path in paths]
    grouped: defaultdict[str, list[Record]] = defaultdict(list)
    result = ImportResult()
    for index, path in enumerate(selected_paths, start=1):
        parsed = parse_file(path)
        result.issues.extend(parsed.issues)
        if parsed.records:
            result.sources.append(path.resolve())
            result.files_loaded += 1
            result.records_before_deduplication += len(parsed.records)
            grouped[dataset_name_for_path(path)].extend(parsed.records)
        if progress_callback is not None:
            progress_callback(index, len(selected_paths))
    result.datasets = {name: _deduplicate(records) for name, records in grouped.items()}
    return result


def discover_export_files(directory: str | Path) -> list[Path]:
    """Find JSON exports below a user-selected export directory."""
    root = Path(directory)
    return sorted(path for path in root.rglob("*.json") if path.is_file())


def build_insights(datasets: dict[str, list[Record]]) -> dict[str, list[Record]]:
    """Return source datasets plus the three derived follower relationship views."""
    result = {name: list(records) for name, records in datasets.items()}
    followers = result.get(KEY_FOLLOWERS, [])
    following = result.get(KEY_FOLLOWING, [])
    if not followers or not following:
        result.pop(KEY_NOT_FOLLOWING_BACK, None)
        result.pop(KEY_MUTUAL, None)
        result.pop(KEY_NOT_FOLLOWED_BACK, None)
        return result

    follower_names = {record[0].casefold() for record in followers if record[0]}
    following_names = {record[0].casefold() for record in following if record[0]}
    result[KEY_NOT_FOLLOWING_BACK] = [
        record for record in following if record[0].casefold() not in follower_names
    ]
    result[KEY_MUTUAL] = [
        record for record in following if record[0].casefold() in follower_names
    ]
    result[KEY_NOT_FOLLOWED_BACK] = [
        record for record in followers if record[0].casefold() not in following_names
    ]
    return result


def relationship_metrics(datasets: dict[str, list[Record]]) -> dict[str, int]:
    return {
        "followers": len(datasets.get(KEY_FOLLOWERS, [])),
        "following": len(datasets.get(KEY_FOLLOWING, [])),
        "mutual": len(datasets.get(KEY_MUTUAL, [])),
        "not_following_back": len(datasets.get(KEY_NOT_FOLLOWING_BACK, [])),
        "not_followed_back": len(datasets.get(KEY_NOT_FOLLOWED_BACK, [])),
    }
