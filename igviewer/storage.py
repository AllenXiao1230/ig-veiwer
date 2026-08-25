"""Safe local storage for the viewer's cache and relationship snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import os
from typing import Any

from .data import KEY_FOLLOWERS, KEY_FOLLOWING, Record, relationship_metrics


CACHE_VERSION = 1
HISTORY_VERSION = 1


def application_data_dir() -> Path:
    override = os.environ.get("IG_VIEWER_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys_platform() == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return root / "IG Viewer"


def sys_platform() -> str:
    import sys

    return sys.platform


def cache_path() -> Path:
    return application_data_dir() / "cache.json"


def history_path() -> Path:
    return application_data_dir() / "snapshots.json"


def source_fingerprint(path: str | Path) -> dict[str, Any]:
    source = Path(path).resolve()
    stat = source.stat()
    return {"path": str(source), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _atomic_json_write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    temporary.replace(path)


def save_cache(datasets: dict[str, list[Record]], sources: list[Path]) -> None:
    payload = {
        "version": CACHE_VERSION,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "sources": [source_fingerprint(path) for path in sources],
        "datasets": {name: [list(record) for record in records] for name, records in datasets.items()},
    }
    _atomic_json_write(cache_path(), payload)


@dataclass(frozen=True)
class CacheLoad:
    datasets: dict[str, list[Record]] | None
    message: str


def load_valid_cache() -> CacheLoad:
    path = cache_path()
    if not path.exists():
        return CacheLoad(None, "尚未建立快取。請匯入 Instagram 匯出資料。")
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if payload.get("version") != CACHE_VERSION:
            return CacheLoad(None, "快取版本已更新，請重新匯入資料。")
        sources = payload.get("sources", [])
        if not sources:
            return CacheLoad(None, "快取缺少來源資訊，請重新匯入資料。")
        for fingerprint in sources:
            source = Path(fingerprint["path"])
            if not source.exists() or source_fingerprint(source) != fingerprint:
                return CacheLoad(None, "原始 JSON 已變更或不存在，請重新匯入資料。")
        raw_datasets = payload.get("datasets", {})
        datasets: dict[str, list[Record]] = {}
        for name, records in raw_datasets.items():
            if not isinstance(name, str) or not isinstance(records, list):
                raise ValueError("資料集格式錯誤")
            datasets[name] = [tuple(map(str, record)) for record in records if isinstance(record, list) and len(record) == 3]
        return CacheLoad(datasets, "已從安全快取載入資料。")
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        return CacheLoad(None, f"快取無法使用：{exc}。請重新匯入資料。")


def clear_cache() -> bool:
    path = cache_path()
    if not path.exists():
        return False
    path.unlink()
    return True


def _membership_signature(datasets: dict[str, list[Record]]) -> str:
    memberships = {
        "followers": sorted(record[0].casefold() for record in datasets.get(KEY_FOLLOWERS, [])),
        "following": sorted(record[0].casefold() for record in datasets.get(KEY_FOLLOWING, [])),
    }
    encoded = json.dumps(memberships, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def load_snapshots() -> list[dict[str, Any]]:
    path = history_path()
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if payload.get("version") != HISTORY_VERSION or not isinstance(payload.get("snapshots"), list):
            return []
        return payload["snapshots"]
    except (OSError, json.JSONDecodeError, TypeError):
        return []


def append_snapshot(datasets: dict[str, list[Record]]) -> tuple[dict[str, Any], bool]:
    """Persist one relationship snapshot unless it is identical to the latest one."""
    snapshots = load_snapshots()
    signature = _membership_signature(datasets)
    if snapshots and snapshots[-1].get("signature") == signature:
        return snapshots[-1], False
    snapshot = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "signature": signature,
        "metrics": relationship_metrics(datasets),
        "followers": sorted(record[0].casefold() for record in datasets.get(KEY_FOLLOWERS, [])),
        "following": sorted(record[0].casefold() for record in datasets.get(KEY_FOLLOWING, [])),
    }
    snapshots.append(snapshot)
    _atomic_json_write(history_path(), {"version": HISTORY_VERSION, "snapshots": snapshots[-50:]})
    return snapshot, True


def compare_with_previous(snapshot: dict[str, Any]) -> dict[str, int] | None:
    snapshots = load_snapshots()
    if len(snapshots) < 2:
        return None
    previous = snapshots[-2]
    current_followers = set(snapshot.get("followers", []))
    previous_followers = set(previous.get("followers", []))
    current_following = set(snapshot.get("following", []))
    previous_following = set(previous.get("following", []))
    return {
        "new_followers": len(current_followers - previous_followers),
        "lost_followers": len(previous_followers - current_followers),
        "new_following": len(current_following - previous_following),
        "unfollowed": len(previous_following - current_following),
    }
