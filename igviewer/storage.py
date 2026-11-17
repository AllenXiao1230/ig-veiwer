"""Safe local storage for the viewer's cache and relationship snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import os
from tempfile import NamedTemporaryFile
from typing import Any, Iterator, TextIO

from .data import KEY_FOLLOWERS, KEY_FOLLOWING, Record, discover_export_files, relationship_metrics
from .locking import file_lock


CACHE_VERSION = 2
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
    digest = sha256()
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    after = source.stat()
    if (stat.st_size, stat.st_mtime_ns, stat.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise OSError("來源檔案在檢查期間變更")
    return {"path": str(source), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": digest.hexdigest()}


@contextmanager
def atomic_text_writer(path: Path, *, encoding: str = "utf-8", newline: str | None = None) -> Iterator[TextIO]:
    """Replace a text file only after its new contents have been fully written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding=encoding, newline=newline, dir=path.parent,
            prefix=path.name + ".", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _atomic_json_write(path: Path, payload: Any) -> None:
    with file_lock(path.with_suffix(path.suffix + ".lock")), atomic_text_writer(path) as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))


def _read_json(path: Path) -> Any:
    with file_lock(path.with_suffix(path.suffix + ".lock")), path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_cache(
    datasets: dict[str, list[Record]], sources: list[Path],
    directory: Path | None = None, fingerprints: list[dict[str, Any]] | None = None,
    directory_files: list[Path] | None = None,
) -> None:
    discovered = directory_files
    if directory is not None and discovered is None:
        discovered = discover_export_files(directory)
    payload = {
        "version": CACHE_VERSION,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "sources": fingerprints if fingerprints is not None else [source_fingerprint(path) for path in sources],
        "directory": str(directory.resolve()) if directory else None,
        "directory_files": [str(path.resolve()) for path in discovered] if discovered is not None else None,
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
        payload = _read_json(path)
        if not isinstance(payload, dict):
            raise ValueError("快取必須是 JSON 物件")
        if payload.get("version") != CACHE_VERSION:
            return CacheLoad(None, "快取版本已更新，請重新匯入資料。")
        sources = payload.get("sources", [])
        if not isinstance(sources, list) or not sources:
            return CacheLoad(None, "快取缺少來源資訊，請重新匯入資料。")
        for fingerprint in sources:
            if not isinstance(fingerprint, dict) or not isinstance(fingerprint.get("path"), str):
                raise ValueError("來源資訊格式錯誤")
            source = Path(fingerprint["path"])
            if not source.exists() or source_fingerprint(source) != fingerprint:
                return CacheLoad(None, "原始 JSON 已變更或不存在，請重新匯入資料。")
        directory = payload.get("directory")
        if directory is not None:
            if not isinstance(directory, str) or not isinstance(payload.get("directory_files"), list):
                raise ValueError("匯入目錄資訊格式錯誤")
            actual = [str(source.resolve()) for source in discover_export_files(directory)]
            if not Path(directory).is_dir() or actual != payload["directory_files"]:
                return CacheLoad(None, "匯入資料夾中的 JSON 清單已變更，請重新匯入資料。")
        raw_datasets = payload.get("datasets", {})
        if not isinstance(raw_datasets, dict):
            raise ValueError("資料集格式錯誤")
        datasets: dict[str, list[Record]] = {}
        for name, records in raw_datasets.items():
            if not isinstance(name, str) or not isinstance(records, list):
                raise ValueError("資料集格式錯誤")
            if any(not isinstance(record, list) or len(record) != 3 or not all(isinstance(value, str) for value in record) for record in records):
                raise ValueError("帳號紀錄格式錯誤")
            datasets[name] = [tuple(record) for record in records]
        return CacheLoad(datasets, "已從安全快取載入資料。")
    except (OSError, UnicodeError, KeyError, TypeError, ValueError, RecursionError) as exc:
        return CacheLoad(None, f"快取無法使用：{exc}。請重新匯入資料。")


def clear_cache() -> bool:
    path = cache_path()
    with file_lock(path.with_suffix(path.suffix + ".lock")):
        if not path.exists():
            return False
        path.unlink()
        return True


def membership_signature(datasets: dict[str, list[Record]]) -> str:
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
        payload = _read_json(path)
        if not isinstance(payload, dict) or payload.get("version") != HISTORY_VERSION or not isinstance(payload.get("snapshots"), list):
            return []
        for snapshot in payload["snapshots"]:
            if not isinstance(snapshot, dict):
                return []
            if not isinstance(snapshot.get("signature"), str) or not isinstance(snapshot.get("created_at"), str):
                return []
            datetime.fromisoformat(snapshot["created_at"])
            metrics = snapshot.get("metrics")
            if not isinstance(metrics, dict) or not relationship_metrics({}).keys() <= metrics.keys():
                return []
            if any(type(value) is not int or value < 0 for value in metrics.values()):
                return []
            for key in ("followers", "following"):
                if not isinstance(snapshot.get(key), list) or not all(isinstance(name, str) for name in snapshot[key]):
                    return []
        return payload["snapshots"]
    except (OSError, UnicodeError, TypeError, ValueError, RecursionError):
        return []


def append_snapshot(datasets: dict[str, list[Record]]) -> tuple[dict[str, Any], bool]:
    """Persist one relationship snapshot unless it is identical to the latest one."""
    with file_lock(history_path().with_suffix(".lock")):
        return _append_snapshot_locked(datasets)


def _append_snapshot_locked(datasets: dict[str, list[Record]]) -> tuple[dict[str, Any], bool]:
    snapshots = load_snapshots()
    signature = membership_signature(datasets)
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


def compare_with_previous(
    snapshot: dict[str, Any], snapshots: list[dict[str, Any]] | None = None,
) -> dict[str, int] | None:
    snapshots = load_snapshots() if snapshots is None else snapshots
    index = next((
        index for index, item in enumerate(snapshots)
        if item.get("created_at") == snapshot.get("created_at") and item.get("signature") == snapshot.get("signature")
    ), -1)
    if index < 1:
        return None
    previous = snapshots[index - 1]
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
