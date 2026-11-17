"""Background loading, analysis, and persistence for the desktop viewer."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import QThread, Signal

from .data import (
    KEY_FOLLOWERS, KEY_FOLLOWING, ImportCancelled, ImportResult, Record,
    build_insights, discover_export_files, import_files,
)
from .storage import (
    append_snapshot, compare_with_previous, load_snapshots,
    load_valid_cache, membership_signature, save_cache,
)


@dataclass
class LoadedData:
    imported: ImportResult = field(default_factory=ImportResult)
    datasets: dict[str, list[Record]] = field(default_factory=dict)
    snapshots: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""
    snapshot: dict[str, Any] | None = None
    from_cache: bool = False


class FileLoaderWorker(QThread):
    result_ready = Signal(object)
    progress = Signal(int, int)
    status = Signal(str)
    error = Signal(str)

    def __init__(self, filepaths=None, parent=None, *, directory: Path | None = None, from_cache: bool = False):
        super().__init__(parent)
        self.filepaths = list(filepaths or [])
        self.directory = directory
        self.from_cache = from_cache

    def _check_cancelled(self) -> None:
        if self.isInterruptionRequested():
            raise ImportCancelled()

    def run(self) -> None:
        try:
            loaded = self._load_cache() if self.from_cache else self._import()
            self._check_cancelled()
            self.result_ready.emit(loaded)
        except ImportCancelled:
            pass
        except Exception as exc:
            self.error.emit(f"載入未完成，請確認 JSON 檔案與本機資料可讀取後重試。\n{exc}")

    def _load_cache(self) -> LoadedData:
        cache = load_valid_cache()
        self._check_cancelled()
        snapshots = load_snapshots()
        datasets = build_insights(cache.datasets) if cache.datasets is not None else {}
        snapshot = None
        complete_relationships = KEY_FOLLOWERS in datasets and KEY_FOLLOWING in datasets
        if complete_relationships and snapshots and snapshots[-1]["signature"] == membership_signature(datasets):
            snapshot = snapshots[-1]
        return LoadedData(
            imported=ImportResult(datasets=cache.datasets or {}), datasets=datasets,
            snapshots=snapshots, message=cache.message, snapshot=snapshot, from_cache=True,
        )

    def _import(self) -> LoadedData:
        self.status.emit("正在尋找 JSON 檔案…")
        paths = discover_export_files(self.directory) if self.directory else self.filepaths
        imported = import_files(paths, self.progress.emit, self.isInterruptionRequested)
        loaded = LoadedData(imported=imported)
        if imported.has_blocking_issues or not imported.datasets:
            return loaded
        self.status.emit("正在計算關係並儲存本機資料…")
        loaded.datasets = build_insights(imported.datasets)
        self._check_cancelled()
        warnings = []
        try:
            save_cache(imported.datasets, imported.sources, self.directory, imported.fingerprints, paths)
        except OSError:
            warnings.append("無法儲存本機快取，請檢查儲存空間與權限。")
        self._check_cancelled()
        if KEY_FOLLOWERS in imported.datasets and KEY_FOLLOWING in imported.datasets:
            try:
                loaded.snapshot, created = append_snapshot(loaded.datasets)
                loaded.snapshots = load_snapshots()
                loaded.message = "已建立快照。" if created else "資料與最近快照相同，未重複建立。"
                comparison = compare_with_previous(loaded.snapshot, loaded.snapshots)
                if created and comparison:
                    loaded.message += f" 與前次相比新增 {comparison['new_followers']} 位粉絲，流失 {comparison['lost_followers']} 位粉絲。"
            except OSError:
                warnings.append("無法儲存快照，請檢查儲存空間與權限。")
        if not loaded.snapshots:
            loaded.snapshots = load_snapshots()
        loaded.message += " ".join(warnings)
        return loaded
