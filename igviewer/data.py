"""Parsing, normalization, and relationship analysis for Instagram exports."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from pathlib import Path
import json
import os
import re
from typing import Any, Callable, Iterable


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
    blocking: bool = False


@dataclass
class FileParseResult:
    records: list[Record] = field(default_factory=list)
    issues: list[ImportIssue] = field(default_factory=list)
    recognized: bool = False
    fingerprint: dict[str, Any] | None = None


@dataclass
class ImportResult:
    datasets: dict[str, list[Record]] = field(default_factory=dict)
    sources: list[Path] = field(default_factory=list)
    issues: list[ImportIssue] = field(default_factory=list)
    files_loaded: int = 0
    records_before_deduplication: int = 0
    fingerprints: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_blocking_issues(self) -> bool:
        return any(issue.blocking for issue in self.issues)

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
        if any(
            isinstance(item, dict) and ("string_list_data" in item or "label_values" in item)
            for item in value
        ):
            yield value
            return
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
        with source.open("rb") as handle:
            before = os.fstat(handle.fileno())
            raw = handle.read()
            after = source.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise OSError("檔案在讀取期間變更，請等待匯出或同步完成")
        content = json.loads(raw)
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        return FileParseResult(issues=[ImportIssue(source, f"無法讀取 JSON：{exc}")])

    records: list[Record] = []
    invalid_records = 0
    for items in _record_lists(content):
        for item in items:
            if not isinstance(item, dict):
                invalid_records += 1
                continue
            if "string_list_data" in item:
                record = _record_from_string_list(item)
            elif "label_values" in item:
                record = _record_from_label_values(item)
            else:
                invalid_records += 1
                continue
            if record is not None:
                records.append(record)
            else:
                invalid_records += 1

    empty_export = content == [] or (
        isinstance(content, dict)
        and any(key.startswith("relationships_") and value == [] for key, value in content.items())
    )
    if invalid_records:
        return FileParseResult(issues=[ImportIssue(source, f"有 {invalid_records} 筆帳號資料格式不完整，請重新匯出完整 JSON")])
    if not records and not empty_export:
        return FileParseResult(
            issues=[ImportIssue(source, "找不到支援的 Instagram 帳號資料格式")]
        )
    fingerprint = {
        "path": str(source.resolve()), "size": before.st_size,
        "mtime_ns": before.st_mtime_ns, "sha256": sha256(raw).hexdigest(),
    }
    return FileParseResult(records=records, recognized=True, fingerprint=fingerprint)


def dataset_name_for_path(path: str | Path) -> str | None:
    stem = Path(path).stem.casefold()
    # Instagram splits larger follower exports into followers_1, followers_2, ... .
    if re.fullmatch(r"followers(?:_\d+)?", stem):
        return KEY_FOLLOWERS
    if re.fullmatch(r"following(?:_\d+)?", stem):
        return KEY_FOLLOWING
    return FILENAME_MAP.get(stem)


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


class ImportCancelled(Exception):
    """The user closed the viewer while an import was running."""


def import_files(
    paths: Iterable[str | Path],
    progress_callback: Any = None,
    cancel_callback: Callable[[], bool] | None = None,
) -> ImportResult:
    """Parse, categorize, and merge selected JSON files, including follower shards."""
    selected_paths = [Path(path) for path in paths]
    grouped: defaultdict[str, list[Record]] = defaultdict(list)
    result = ImportResult()
    for index, path in enumerate(selected_paths, start=1):
        if cancel_callback and cancel_callback():
            raise ImportCancelled()
        name = dataset_name_for_path(path)
        if name is None:
            result.issues.append(ImportIssue(path, "未支援的資料類型，已略過此檔案"))
            if progress_callback is not None:
                progress_callback(index, len(selected_paths))
            continue
        parsed = parse_file(path)
        result.issues.extend(
            ImportIssue(issue.path, issue.message, name in {KEY_FOLLOWERS, KEY_FOLLOWING})
            for issue in parsed.issues
        )
        if parsed.recognized:
            result.sources.append(path.resolve())
            result.fingerprints.append(parsed.fingerprint)
            result.files_loaded += 1
            result.records_before_deduplication += len(parsed.records)
            grouped[name].extend(parsed.records)
        if progress_callback is not None:
            progress_callback(index, len(selected_paths))
    if cancel_callback and cancel_callback():
        raise ImportCancelled()
    result.datasets = {name: _deduplicate(records) for name, records in grouped.items()}
    return result


def discover_export_files(directory: str | Path) -> list[Path]:
    """Find JSON exports below a user-selected export directory."""
    root = Path(directory)
    return sorted(
        path for path in root.rglob("*.[jJ][sS][oO][nN]")
        if path.is_file()
        and not any(part.startswith(".") for part in path.relative_to(root).parts)
    )


def build_insights(datasets: dict[str, list[Record]]) -> dict[str, list[Record]]:
    """Return source datasets plus the three derived follower relationship views."""
    result = {name: list(records) for name, records in datasets.items()}
    followers = result.get(KEY_FOLLOWERS, [])
    following = result.get(KEY_FOLLOWING, [])
    if KEY_FOLLOWERS not in result or KEY_FOLLOWING not in result:
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
