"""Trusted JSON ingestion and immutable archival."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import shutil
from typing import Iterable

from igviewer.data import ImportIssue, build_insights, import_files

from .store import TrendStore


def json_files_in(directory: Path) -> list[Path]:
    return sorted(path for path in directory.rglob("*.json") if path.is_file())


def fingerprint_files(paths: Iterable[Path]) -> str:
    digest = sha256()
    for path in sorted(paths):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def archive_sources(paths: list[Path], archive_root: Path, fingerprint: str) -> Path:
    archive_dir = archive_root / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{fingerprint[:12]}"
    archive_dir.mkdir(parents=True, exist_ok=False)
    manifest = []
    for index, source in enumerate(paths, start=1):
        target = archive_dir / f"{index:03d}-{source.name}"
        shutil.copy2(source, target)
        manifest.append({"source": source.name, "archive": target.name, "sha256": sha256(target.read_bytes()).hexdigest()})
    (archive_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return archive_dir


def ingest_paths(
    store: TrendStore,
    paths: list[Path],
    archive_root: Path,
    source_kind: str,
    source_label: str,
) -> dict:
    if not paths:
        return {"status": "skipped", "reason": "找不到 JSON 檔案", "issues": []}
    parsed = import_files(paths)
    if not parsed.datasets:
        return {
            "status": "rejected",
            "reason": "找不到支援的 Instagram 關係資料",
            "issues": [issue.message for issue in parsed.issues],
        }
    datasets = build_insights(parsed.datasets)
    if "粉絲" not in datasets or "追蹤中" not in datasets:
        return {
            "status": "rejected",
            "reason": "匯入必須同時包含 followers 與 following 資料",
            "issues": [issue.message for issue in parsed.issues],
        }
    fingerprint = fingerprint_files(paths)
    existing = store.latest_snapshot()
    if existing and existing["fingerprint"] == fingerprint:
        return {
            "status": "unchanged",
            "snapshot": existing,
            "issues": [issue.message for issue in parsed.issues],
        }
    archive_path = archive_sources(paths, archive_root, fingerprint)
    snapshot, created, delta = store.add_snapshot(
        fingerprint, datasets, source_kind, source_label, archive_path
    )
    return {
        "status": "created" if created else "unchanged",
        "snapshot": snapshot,
        "delta": delta,
        "files_loaded": parsed.files_loaded,
        "records": parsed.records_after_deduplication,
        "duplicates": parsed.duplicate_count,
        "issues": [issue.message for issue in parsed.issues],
    }


def remove_expired_archives(archive_root: Path, retention_days: int) -> int:
    if retention_days == 0 or not archive_root.exists():
        return 0
    cutoff = datetime.now(timezone.utc).timestamp() - retention_days * 86400
    removed = 0
    for path in archive_root.iterdir():
        if path.is_dir() and path.stat().st_mtime < cutoff:
            shutil.rmtree(path)
            removed += 1
    return removed
