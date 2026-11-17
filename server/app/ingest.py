"""Import stable source copies and archive the bytes used for each snapshot."""

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory, mkdtemp
from typing import Iterable

from igviewer.data import KEY_FOLLOWERS, KEY_FOLLOWING, build_insights, dataset_name_for_path, discover_export_files, import_files
from igviewer.locking import file_lock
from .store import TrendStore


def json_files_in(directory: Path) -> list[Path]:
    current = directory / "current"
    if current.is_dir():
        directory = current
    elif (directory / ".gdrive-stage").exists() or (directory / ".gdrive-previous").exists():
        return []
    return discover_export_files(directory)


def _file_digest(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint_files(paths: Iterable[Path]) -> str:
    identities = sorted((path.name.casefold(), _file_digest(path)) for path in paths)
    return sha256(json.dumps(identities, separators=(",", ":")).encode("utf-8")).hexdigest()


def archive_sources(paths: list[Path], archive_root: Path, fingerprint: str) -> Path:
    archive_root.mkdir(parents=True, exist_ok=True)
    prefix = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}-{fingerprint[:12]}-"
    archive_dir = Path(mkdtemp(prefix=prefix, dir=archive_root))
    try:
        manifest = []
        for index, source in enumerate(paths, start=1):
            target = archive_dir / f"{index:03d}-{source.name}"
            shutil.copyfile(source, target)
            manifest.append({"source": source.name, "archive": target.name, "sha256": _file_digest(target)})
        (archive_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return archive_dir
    except BaseException:
        shutil.rmtree(archive_dir)
        raise


def _source_state(path: Path) -> tuple[int, int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_ino


def ingest_paths(store: TrendStore, paths: list[Path], archive_root: Path, source_kind: str, source_label: str) -> dict:
    if not paths:
        return {"status": "skipped", "reason": "找不到 JSON 檔案", "issues": []}
    with file_lock(store.lock_path):
        archive_root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix=".pending-", dir=archive_root) as temporary:
            try:
                states = {path: _source_state(path) for path in paths}
                staged = []
                skipped = []
                for index, source in enumerate(paths, start=1):
                    if dataset_name_for_path(source) is None:
                        skipped.append(f"{source.name} 為未支援的資料類型，已略過")
                        continue
                    target = Path(temporary) / str(index) / source.name
                    target.parent.mkdir()
                    shutil.copyfile(source, target)
                    staged.append(target)
                if any(_source_state(path) != state for path, state in states.items()):
                    raise OSError("來源在匯入期間變更，請等待同步完成後重試")
            except OSError as exc:
                return {"status": "rejected", "reason": "無法取得完整且穩定的匯出資料", "issues": [str(exc)]}
            return _ingest_staged(store, staged, archive_root, source_kind, source_label, skipped)


def _ingest_staged(
    store: TrendStore, paths: list[Path], archive_root: Path,
    source_kind: str, source_label: str, skipped: list[str],
) -> dict:
    parsed = import_files(paths)
    issues = skipped + [f"{issue.path.name}：{issue.message}" for issue in parsed.issues]
    if parsed.has_blocking_issues or KEY_FOLLOWERS not in parsed.datasets or KEY_FOLLOWING not in parsed.datasets:
        return {"status": "rejected", "reason": "匯入必須包含完整可讀取的 followers 與 following 資料", "issues": issues}
    datasets = build_insights(parsed.datasets)
    fingerprint = fingerprint_files(parsed.sources)
    existing = store.latest_snapshot()
    if existing and existing["fingerprint"] == fingerprint:
        return {"status": "unchanged", "snapshot": existing, "issues": issues}
    archived = store.archived_snapshot(fingerprint)
    archive_path = Path(archived["archive_path"]) if archived else archive_sources(parsed.sources, archive_root, fingerprint)
    try:
        snapshot, created, delta = store.add_snapshot(fingerprint, datasets, source_kind, source_label, archive_path)
    except BaseException:
        if archived is None:
            shutil.rmtree(archive_path)
        raise
    return {
        "status": "created" if created else "unchanged", "snapshot": snapshot, "delta": delta,
        "files_loaded": parsed.files_loaded, "records": parsed.records_after_deduplication,
        "duplicates": parsed.duplicate_count, "issues": issues,
    }


def remove_expired_archives(archive_root: Path, retention_days: int) -> int:
    if retention_days == 0 or not archive_root.exists():
        return 0
    cutoff = datetime.now(timezone.utc).timestamp() - retention_days * 86400
    removed = 0
    for path in archive_root.iterdir():
        if not path.name.startswith(".") and path.is_dir() and not path.is_symlink() and path.stat().st_mtime < cutoff:
            shutil.rmtree(path)
            removed += 1
    return removed
