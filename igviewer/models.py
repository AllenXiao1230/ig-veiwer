"""Qt table models for responsive local-record browsing."""

from __future__ import annotations

from datetime import date, datetime

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QBrush

from .data import Record


class RecordTableModel(QAbstractTableModel):
    """Keep imported records in a model instead of recreating table widgets on each filter."""

    HEADERS = ("用戶名稱 (Username)", "顯示名稱 / 相關資訊", "時間紀錄")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._records: list[Record] = []
        self._opened_usernames: set[str] = set()
        self._username_rows: dict[str, list[int]] = {}
        self._search_values: list[tuple[str, str]] = []
        self._dates: list[date | None] = []

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._records)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if not index.isValid() or not 0 <= index.row() < len(self._records):
            return None
        record = self._records[index.row()]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return record[index.column()]
        if role == Qt.ItemDataRole.ForegroundRole and record[0].casefold() in self._opened_usernames:
            return QBrush(Qt.GlobalColor.gray)
        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return super().headerData(section, orientation, role)

    def set_records(self, records: list[Record]) -> None:
        self.beginResetModel()
        self._records = list(records)
        self._opened_usernames.clear()
        self._username_rows = {}
        self._search_values = []
        self._dates = []
        for row, (username, detail, timestamp) in enumerate(self._records):
            self._username_rows.setdefault(username.casefold(), []).append(row)
            self._search_values.append((username.casefold(), detail.casefold()))
            try:
                self._dates.append(datetime.fromisoformat(timestamp).date())
            except ValueError:
                self._dates.append(None)
        self.endResetModel()

    def record_at(self, row: int) -> Record | None:
        return self._records[row] if 0 <= row < len(self._records) else None

    def mark_opened(self, username: str) -> None:
        normalized = username.casefold()
        if not normalized or normalized in self._opened_usernames:
            return
        self._opened_usernames.add(normalized)
        for row in self._username_rows.get(normalized, []):
            index = self.index(row, 0)
            self.dataChanged.emit(index, self.index(row, len(self.HEADERS) - 1), [Qt.ItemDataRole.ForegroundRole])

    def matches(self, row: int, query: str, use_dates: bool, start: date | None, end: date | None) -> bool:
        username, detail = self._search_values[row]
        if query and query not in username and query not in detail:
            return False
        if not use_dates:
            return True
        item_date = self._dates[row]
        return bool(start and end and item_date and start <= item_date <= end)

    def clear_opened(self) -> None:
        if not self._opened_usernames:
            return
        self._opened_usernames.clear()
        if self._records:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self._records) - 1, len(self.HEADERS) - 1),
                [Qt.ItemDataRole.ForegroundRole],
            )


class RecordFilterProxyModel(QSortFilterProxyModel):
    """Filter records without rebuilding the visual table for every keystroke."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._query = ""
        self._use_dates = False
        self._start_date: date | None = None
        self._end_date: date | None = None
        self.setDynamicSortFilter(True)

    def set_filters(
        self,
        query: str,
        use_dates: bool,
        start_date: date | None,
        end_date: date | None,
    ) -> None:
        next_values = (query.casefold().strip(), use_dates, start_date, end_date)
        current_values = (self._query, self._use_dates, self._start_date, self._end_date)
        if next_values == current_values:
            return
        self.beginFilterChange()
        self._query, self._use_dates, self._start_date, self._end_date = next_values
        self.endFilterChange(QSortFilterProxyModel.Direction.Rows)

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:  # noqa: N802
        model = self.sourceModel()
        if not isinstance(model, RecordTableModel):
            return True
        record = model.record_at(source_row)
        if record is None:
            return False
        return model.matches(source_row, self._query, self._use_dates, self._start_date, self._end_date)

    def filtered_records(self) -> list[Record]:
        model = self.sourceModel()
        if not isinstance(model, RecordTableModel):
            return []
        records: list[Record] = []
        for row in range(self.rowCount()):
            record = model.record_at(self.mapToSource(self.index(row, 0)).row())
            if record is not None:
                records.append(record)
        return records
