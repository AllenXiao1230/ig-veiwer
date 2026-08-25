"""Offline viewer for Instagram's downloaded follower and following data."""

from __future__ import annotations

import csv
from pathlib import Path
import sys
from urllib.parse import quote
import webbrowser

from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
from PySide6.QtCore import QDate, QModelIndex, QThread, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence, QPainter
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from igviewer.data import (
    KEY_FOLLOWERS,
    KEY_FOLLOWING,
    KEY_MUTUAL,
    KEY_NOT_FOLLOWED_BACK,
    KEY_NOT_FOLLOWING_BACK,
    SENSITIVE_DATASETS,
    ImportResult,
    Record,
    build_insights,
    discover_export_files,
    import_files,
    relationship_metrics,
)
from igviewer.models import RecordFilterProxyModel, RecordTableModel
from igviewer.storage import (
    append_snapshot,
    application_data_dir,
    clear_cache as clear_safe_cache,
    compare_with_previous,
    load_snapshots,
    load_valid_cache,
    save_cache,
)


DARK_STYLE = """
    QWidget { background-color: #1e1e1e; color: #d4d4d4; font-family: Helvetica; font-size: 13pt; }
    QMainWindow { background-color: #1e1e1e; }
    QGroupBox { font-size: 12pt; color: #b8b8b8; border: 1px solid #333333; border-radius: 5px; margin-top: 10px; }
    QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; padding: 0 5px; left: 10px; }
    QPushButton, QToolButton { background-color: #007acc; color: white; border: none; padding: 8px 12px; border-radius: 4px; }
    QPushButton:hover, QToolButton:hover { background-color: #005999; }
    QPushButton:pressed, QToolButton:pressed { background-color: #004c8c; }
    QPushButton#actionButton { background-color: #146c2e; }
    QPushButton#actionButton:hover { background-color: #105724; }
    QPushButton#actionButton:pressed { background-color: #0b451d; }
    QPushButton:disabled, QToolButton:disabled { background-color: #3a3d41; color: #a0a0a0; }
    QLineEdit, QDateEdit { background-color: #2d2d2d; border: 1px solid #3e3e42; padding: 5px; border-radius: 4px; color: white; }
    QListWidget, QTreeView { background-color: #252526; border: none; }
    QListWidget::item { padding: 8px; }
    QListWidget::item:selected, QTreeView::item:selected { background-color: #094771; color: white; }
    QTreeView { alternate-background-color: #2a2a2b; }
    QTreeView::item { padding: 4px 5px; }
    QHeaderView::section { background-color: #333333; color: white; padding: 5px; border: 1px solid #3e3e42; }
    QComboBox { background-color: #2d2d2d; border: 1px solid #3e3e42; padding: 6px 34px 6px 10px; min-height: 22px; border-radius: 4px; }
    QComboBox::drop-down { width: 30px; border: none; border-left: 1px solid #3e3e42; }
    QComboBox QAbstractItemView { background-color: #2d2d2d; border: 1px solid #3e3e42; border-radius: 5px; outline: 0; padding: 4px; }
    QComboBox QAbstractItemView::item { color: #d4d4d4; padding: 6px 10px; margin: 2px 0; min-height: 22px; border: 1px solid transparent; border-radius: 4px; }
    QComboBox QAbstractItemView::item:hover { background-color: #3a3d41; }
    QComboBox QAbstractItemView::item:selected { background-color: #007acc; color: white; }
    QMenu#toolsMenu { background-color: #252526; border: 1px solid #3e3e42; border-radius: 6px; padding: 6px; }
    QMenu#toolsMenu::item { color: #d4d4d4; padding: 8px 34px 8px 12px; margin: 1px 0; min-height: 22px; border: 1px solid transparent; border-radius: 4px; }
    QMenu#toolsMenu::item:selected { background-color: #094771; color: #ffffff; }
    QMenu#toolsMenu::item:disabled { color: #777777; }
    QMenu#toolsMenu::separator { height: 1px; background-color: #3e3e42; margin: 6px 8px; }
    QSplitter::handle { background-color: #3e3e42; }
    QSplitter::handle:hover { background-color: #007acc; }
    QStatusBar, QStatusBar::item { color: #a0a0a0; border: none; }
    QWidget#topControls { background-color: #1e1e1e; }
    QWidget#emptyState { background-color: #252526; }
    QLabel#emptyTitle { font-size: 20pt; font-weight: bold; color: #f0f0f0; }
    QLabel#emptyDescription { color: #b8b8b8; }
    QLabel#privacyNotice { color: #8bd5a3; }
    QPushButton:focus, QToolButton:focus, QLineEdit:focus, QDateEdit:focus, QComboBox:focus, QListWidget:focus, QTreeView:focus {
        border: 2px solid #66d9ff;
    }
    QComboBox:focus { border: 1px solid #66d9ff; }
    QLabel#followersLabel, QLabel#followingLabel, QLabel#mutualLabel, QLabel#unrequitedLabel, QLabel#notFollowedBackLabel {
        font-size: 15pt; font-weight: bold;
    }
    QLabel#followersLabel { color: #4fc1ff; }
    QLabel#followingLabel { color: #ce9178; }
    QLabel#mutualLabel { color: #4ec9b0; }
    QLabel#unrequitedLabel { color: #f44747; }
    QLabel#notFollowedBackLabel { color: #c586c0; }
"""


class FileLoaderWorker(QThread):
    finished = Signal(object)
    progress = Signal(int, int)
    error = Signal(str)

    def __init__(self, filepaths: list[Path], parent: QWidget | None = None):
        super().__init__(parent)
        self.filepaths = filepaths

    def run(self) -> None:
        try:
            result = import_files(self.filepaths, self.progress.emit)
            self.finished.emit(result)
        except Exception as exc:  # pragma: no cover - defensive UI boundary
            self.error.emit(f"匯入未完成：{exc}。請確認 JSON 檔案可讀取後再試一次。")


class IGMultiFileDashboard(QMainWindow):
    """Desktop workflow for safely inspecting a local Instagram export."""

    DERIVED_KEYS = (KEY_NOT_FOLLOWING_BACK, KEY_MUTUAL, KEY_NOT_FOLLOWED_BACK)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Instagram 數據分析（離線）")
        self.resize(1320, 820)
        self.setMinimumSize(860, 600)
        self.raw_datasets: dict[str, list[Record]] = {}
        self.datasets: dict[str, list[Record]] = {}
        self.sources: list[Path] = []
        self.current_key: str | None = None
        self.current_data_source: list[Record] = []
        self.opened_usernames: set[str] = set()
        self.worker: FileLoaderWorker | None = None
        self._open_queue: list[str] = []
        self._batch_opening = False
        self.search_debounce = QTimer(self)
        self.search_debounce.setSingleShot(True)
        self.search_debounce.setInterval(180)
        self.search_debounce.timeout.connect(self.filter_data)

        self.create_widgets()
        self._load_cache()

    def create_widgets(self) -> None:
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        self._create_menus()
        main_layout = QVBoxLayout(central_widget)
        self.top_bar = self._create_top_bar()
        main_layout.addWidget(self.top_bar)
        main_layout.addWidget(self._create_dashboard())
        main_layout.addWidget(self._create_main_view(), 1)
        self._update_table_state()
        self._update_action_availability()

    def _create_menus(self) -> None:
        file_menu = self.menuBar().addMenu("檔案")
        self.action_import_folder = QAction("匯入 Instagram 資料夾…", self)
        self.action_import_folder.setShortcut(QKeySequence.StandardKey.Open)
        self.action_import_folder.triggered.connect(self.import_export_folder)
        file_menu.addAction(self.action_import_folder)
        self.action_import_files = QAction("選擇 JSON 檔案…", self)
        self.action_import_files.triggered.connect(self.import_multiple_files)
        file_menu.addAction(self.action_import_files)
        self.action_find = QAction("搜尋帳號或名稱", self)
        self.action_find.setShortcut(QKeySequence.StandardKey.Find)
        self.action_find.triggered.connect(lambda: self.search_entry.setFocus())
        self.addAction(self.action_find)

        self.tools_menu = self.menuBar().addMenu("資料與分析")
        self.tools_menu.setObjectName("toolsMenu")
        self.action_export = self.tools_menu.addAction("匯出目前篩選結果為 CSV…", self.export_csv)
        self.action_export.setShortcut(QKeySequence("Ctrl+E"))
        self.action_export_summary = self.tools_menu.addAction("匯出關係摘要…", self.export_summary)
        self.tools_menu.addSeparator()
        self.action_history = self.tools_menu.addAction("查看關係快照紀錄", self.show_snapshot_history)
        self.action_chart = self.tools_menu.addAction("查看關係趨勢圖", self.show_snapshot_chart)
        self.tools_menu.addSeparator()
        self.action_clear_cache = self.tools_menu.addAction("清除本機安全快取…", self.clear_cache)
        self.menuBar().addAction("關於", self.show_about_dialog)

    def _create_top_bar(self) -> QWidget:
        top_bar = QWidget()
        top_bar.setObjectName("topControls")
        top_bar.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(top_bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        action_row = QHBoxLayout()
        action_row.setSpacing(6)

        self.btn_import_folder = QPushButton("匯入 Instagram 資料夾")
        self.btn_import_folder.setObjectName("actionButton")
        self.btn_import_folder.setToolTip("建議：選取 Instagram 匯出資料夾，自動找出其中的 JSON")
        self.btn_import_folder.setAccessibleDescription("選取 Instagram 匯出資料夾並自動尋找 JSON 檔案")
        self.btn_import_folder.clicked.connect(self.import_export_folder)
        action_row.addWidget(self.btn_import_folder)
        self.btn_import = QPushButton("選擇 JSON 檔案")
        self.btn_import.setToolTip("只在已知需要的檔案時使用")
        self.btn_import.clicked.connect(self.import_multiple_files)
        action_row.addWidget(self.btn_import)
        action_row.addStretch(1)
        self.tools_button = QToolButton()
        self.tools_button.setText("資料與分析")
        self.tools_button.setToolTip("匯出、快照、趨勢與快取工具")
        self.tools_button.setAccessibleDescription("開啟資料匯出、快照、趨勢與快取選單")
        self.tools_button.setMenu(self.tools_menu)
        self.tools_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        action_row.addWidget(self.tools_button)
        layout.addLayout(action_row)

        search_row = QHBoxLayout()
        search_row.setSpacing(6)
        self.search_entry = QLineEdit()
        self.search_entry.setPlaceholderText("搜尋帳號或名稱")
        self.search_entry.setAccessibleName("搜尋帳號或名稱")
        self.search_entry.textChanged.connect(self._schedule_filter)
        self.search_entry.setMinimumWidth(180)
        self.search_label = QLabel("搜尋")
        self.search_label.setBuddy(self.search_entry)
        search_row.addWidget(self.search_label)
        search_row.addWidget(self.search_entry, 1)
        self.date_filter_enabled = QCheckBox("日期篩選")
        self.date_filter_enabled.toggled.connect(self.toggle_date_filter)
        search_row.addWidget(self.date_filter_enabled)
        self.date_start = QDateEdit(calendarPopup=True)
        self.date_start.setDate(QDate.currentDate().addYears(-10))
        self.date_start.dateChanged.connect(self.filter_data)
        self.date_start.setVisible(False)
        search_row.addWidget(self.date_start)
        self.date_separator = QLabel("至")
        self.date_separator.setVisible(False)
        search_row.addWidget(self.date_separator)
        self.date_end = QDateEdit(calendarPopup=True)
        self.date_end.setDate(QDate.currentDate())
        self.date_end.dateChanged.connect(self.filter_data)
        self.date_end.setVisible(False)
        search_row.addWidget(self.date_end)
        layout.addLayout(search_row)
        return top_bar

    def _create_dashboard(self) -> QWidget:
        dashboard = QWidget()
        dashboard.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout = QHBoxLayout(dashboard)
        labels = [
            ("followers", "followersLabel", "粉絲：0"),
            ("following", "followingLabel", "追蹤中：0"),
            ("mutual", "mutualLabel", "雙向追蹤：0"),
            ("unrequited", "unrequitedLabel", "對方未回追：0"),
            ("not_followed_back", "notFollowedBackLabel", "你尚未回追：0"),
        ]
        for attribute, object_name, initial_text in labels:
            label = QLabel(initial_text)
            label.setObjectName(object_name)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            setattr(self, f"lbl_{attribute}", label)
            layout.addWidget(label)
        return dashboard

    def _create_main_view(self) -> QSplitter:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        left_panel = QGroupBox("資料來源清單")
        left_layout = QVBoxLayout(left_panel)
        self.sidebar = QListWidget()
        self.sidebar.currentItemChanged.connect(self.on_sidebar_select)
        left_layout.addWidget(self.sidebar)
        splitter.addWidget(left_panel)

        list_panel = QWidget()
        list_panel.setMinimumWidth(280)
        list_layout = QVBoxLayout(list_panel)
        list_layout.setContentsMargins(0, 0, 0, 0)
        self.table_stack = QStackedWidget()
        self.empty_state = self._create_empty_state()
        self.table_stack.addWidget(self.empty_state)
        self.table_model = RecordTableModel(self)
        self.table_proxy = RecordFilterProxyModel(self)
        self.table_proxy.setSourceModel(self.table_model)
        self.tree = QTreeView()
        self.tree.setModel(self.table_proxy)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionBehavior(QTreeView.SelectionBehavior.SelectRows)
        self.tree.setSelectionMode(QTreeView.SelectionMode.ExtendedSelection)
        self.tree.setUniformRowHeights(True)
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.tree.header().setStretchLastSection(True)
        self.tree.setColumnWidth(0, 220)
        self.tree.setColumnWidth(1, 280)
        self.tree.setColumnWidth(2, 160)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.show_table_menu)
        self.tree.selectionModel().currentChanged.connect(self.on_tree_item_selected)
        self.tree.selectionModel().selectionChanged.connect(self._update_action_availability)
        self.table_stack.addWidget(self.tree)
        list_layout.addWidget(self.table_stack)
        self.batch_bar = self._create_batch_bar()
        list_layout.addWidget(self.batch_bar)
        splitter.addWidget(list_panel)

        browser_panel = QGroupBox("帳號預覽")
        browser_panel.setMinimumWidth(280)
        browser_layout = QVBoxLayout(browser_panel)
        browser_layout.setContentsMargins(6, 8, 6, 6)
        browser_toolbar = QHBoxLayout()
        self.browser_status = QLabel("選取帳號後可在此預覽 Instagram；預覽可能需要 Instagram 登入。")
        browser_toolbar.addWidget(self.browser_status, 1)
        self.btn_browser_back = QPushButton("上一頁")
        self.btn_browser_back.setToolTip("上一頁")
        self.btn_browser_back.setAccessibleName("預覽上一頁")
        self.btn_browser_back.clicked.connect(lambda: self.profile_browser.back())
        browser_toolbar.addWidget(self.btn_browser_back)
        self.btn_browser_refresh = QPushButton("重新整理")
        self.btn_browser_refresh.setToolTip("重新整理")
        self.btn_browser_refresh.setAccessibleName("重新整理帳號預覽")
        self.btn_browser_refresh.clicked.connect(lambda: self.profile_browser.reload())
        browser_toolbar.addWidget(self.btn_browser_refresh)
        self.btn_browser_external = QPushButton("在瀏覽器開啟")
        self.btn_browser_external.setToolTip("以系統瀏覽器開啟目前頁面")
        self.btn_browser_external.setAccessibleName("在系統瀏覽器開啟目前預覽頁面")
        self.btn_browser_external.clicked.connect(self.open_current_profile_externally)
        browser_toolbar.addWidget(self.btn_browser_external)
        browser_layout.addLayout(browser_toolbar)

        self.browser_profile = QWebEngineProfile(self)
        browser_data_dir = application_data_dir() / "browser-profile"
        self.browser_profile.setPersistentStoragePath(str(browser_data_dir))
        self.browser_profile.setCachePath(str(browser_data_dir / "cache"))
        self.profile_browser = QWebEngineView()
        self.profile_browser.setPage(QWebEnginePage(self.browser_profile, self.profile_browser))
        self.profile_browser.urlChanged.connect(self.on_browser_url_changed)
        self.profile_browser.loadFinished.connect(self.on_browser_load_finished)
        self.profile_browser.setHtml(
            "<body style='background:#252526;color:#d4d4d4;font-family:Helvetica;"
            "display:flex;align-items:center;justify-content:center;height:95vh;'>"
            "<p>選取帳號以預覽 Instagram 個人主頁；預覽可能需要登入。</p></body>"
        )
        browser_layout.addWidget(self.profile_browser, 1)
        splitter.addWidget(browser_panel)
        splitter.setSizes([220, 520, 500])
        return splitter

    def _create_empty_state(self) -> QWidget:
        state = QWidget()
        state.setObjectName("emptyState")
        layout = QVBoxLayout(state)
        layout.setContentsMargins(36, 36, 36, 36)
        layout.addStretch(1)
        self.empty_title = QLabel("開始分析你的 Instagram 匯出資料")
        self.empty_title.setObjectName("emptyTitle")
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_title.setWordWrap(True)
        layout.addWidget(self.empty_title)
        self.empty_description = QLabel(
            "選取 Instagram 匯出資料夾後，這裡會顯示粉絲、追蹤與關係清單。"
        )
        self.empty_description.setObjectName("emptyDescription")
        self.empty_description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_description.setWordWrap(True)
        layout.addWidget(self.empty_description)
        self.privacy_notice = QLabel("只讀取你選擇的本機 JSON；不登入、不使用 API，也不執行追蹤操作。")
        self.privacy_notice.setObjectName("privacyNotice")
        self.privacy_notice.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.privacy_notice.setWordWrap(True)
        layout.addWidget(self.privacy_notice)
        actions = QHBoxLayout()
        actions.addStretch(1)
        self.empty_import_folder = QPushButton("匯入 Instagram 資料夾")
        self.empty_import_folder.setObjectName("actionButton")
        self.empty_import_folder.clicked.connect(self.import_export_folder)
        actions.addWidget(self.empty_import_folder)
        self.empty_import_files = QPushButton("選擇 JSON 檔案")
        self.empty_import_files.clicked.connect(self.import_multiple_files)
        actions.addWidget(self.empty_import_files)
        self.empty_clear_filters = QPushButton("清除搜尋與日期篩選")
        self.empty_clear_filters.clicked.connect(self.clear_filters)
        self.empty_clear_filters.setVisible(False)
        actions.addWidget(self.empty_clear_filters)
        actions.addStretch(1)
        layout.addLayout(actions)
        layout.addStretch(1)
        return state

    def _create_batch_bar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(6)
        self.open_scope = QComboBox()
        self.open_scope.addItem("已選取帳號", "selected")
        self.open_scope.addItem("篩選結果", "filtered")
        self.open_scope.setAccessibleName("選擇批次開啟範圍")
        self.open_scope.setMinimumWidth(132)
        self.open_scope.currentIndexChanged.connect(self._update_action_availability)
        layout.addWidget(QLabel("開啟範圍"))
        layout.addWidget(self.open_scope)
        layout.addSpacing(6)
        self.open_combo = QComboBox()
        self.open_combo.addItems(["1", "5", "10", "20", "50"])
        self.open_combo.setCurrentText("5")
        self.open_combo.setAccessibleName("批次開啟數量")
        self.open_combo.setMinimumWidth(68)
        layout.addWidget(QLabel("數量"))
        layout.addWidget(self.open_combo)
        layout.addSpacing(8)
        self.btn_open_ig = QPushButton("在瀏覽器開啟")
        self.btn_open_ig.setObjectName("actionButton")
        self.btn_open_ig.clicked.connect(self.open_ig_pages)
        layout.addWidget(self.btn_open_ig)
        self.btn_cancel_open = QPushButton("取消批次開啟")
        self.btn_cancel_open.clicked.connect(self.cancel_opening_profiles)
        layout.addWidget(self.btn_cancel_open)
        self.btn_reset_opened = QPushButton("重設開啟記錄")
        self.btn_reset_opened.clicked.connect(self.reset_opened_status)
        layout.addWidget(self.btn_reset_opened)
        layout.addStretch(1)
        return bar

    def _set_import_busy(self, busy: bool) -> None:
        for control in (
            self.btn_import,
            self.btn_import_folder,
            self.empty_import_files,
            self.empty_import_folder,
        ):
            control.setEnabled(not busy)
        self.action_import_files.setEnabled(not busy)
        self.action_import_folder.setEnabled(not busy)
        self.action_clear_cache.setEnabled(not busy)

    def _schedule_filter(self) -> None:
        self.search_debounce.start()

    def clear_filters(self) -> None:
        self.search_debounce.stop()
        self.search_entry.clear()
        self.date_filter_enabled.setChecked(False)
        self.filter_data()

    def _show_empty_state(self, title: str, description: str, no_results: bool = False) -> None:
        self.empty_title.setText(title)
        self.empty_description.setText(description)
        self.privacy_notice.setVisible(not no_results)
        self.empty_import_folder.setVisible(not no_results)
        self.empty_import_files.setVisible(not no_results)
        self.empty_clear_filters.setVisible(no_results)
        self.table_stack.setCurrentWidget(self.empty_state)

    def _update_table_state(self) -> None:
        if self.current_key is None:
            self._show_empty_state(
                "開始分析你的 Instagram 匯出資料",
                "選取 Instagram 匯出資料夾後，這裡會顯示粉絲、追蹤與關係清單。",
            )
            return
        if self.table_proxy.rowCount() == 0:
            if self.date_filter_enabled.isChecked() and self.date_start.date() > self.date_end.date():
                self._show_empty_state("日期範圍不正確", "開始日期必須早於或等於結束日期。請調整日期後再試一次。", True)
            else:
                self._show_empty_state("找不到符合的帳號", "請調整搜尋文字或日期範圍；你的原始資料不會被更動。", True)
            return
        self.table_stack.setCurrentWidget(self.tree)

    def _update_action_availability(self, *_args) -> None:
        has_filtered_records = self.table_proxy.rowCount() > 0
        has_selection = bool(self.tree.selectionModel().selectedRows())
        opening = self._batch_opening
        scope_has_records = has_selection if self.open_scope.currentData() == "selected" else has_filtered_records
        self.btn_open_ig.setEnabled(scope_has_records and not opening)
        self.btn_cancel_open.setEnabled(opening)
        self.btn_reset_opened.setEnabled(bool(self.opened_usernames) and not opening)
        self.action_export.setEnabled(has_filtered_records)
        self.action_export_summary.setEnabled(bool(self.datasets))
        snapshots = load_snapshots()
        self.action_history.setEnabled(bool(snapshots))
        self.action_chart.setEnabled(len(snapshots) >= 2)

    def toggle_date_filter(self, enabled: bool) -> None:
        """Only reveal date inputs when the user has requested date filtering."""
        for control in (self.date_start, self.date_separator, self.date_end):
            control.setVisible(enabled)
        self.filter_data()

    def import_multiple_files(self) -> None:
        names, _ = QFileDialog.getOpenFileNames(self, "選取 Instagram JSON", "", "JSON Files (*.json)")
        self._start_import([Path(name) for name in names], "選取的 JSON")

    def import_export_folder(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "選取 Instagram 匯出資料夾")
        if not directory:
            return
        names = discover_export_files(directory)
        if not names:
            QMessageBox.warning(self, "找不到資料", "此資料夾及其子資料夾中沒有 JSON 檔案。")
            return
        self._start_import(names, f"資料夾中的 {len(names)} 個 JSON")

    def _start_import(self, filepaths: list[Path], source_description: str) -> None:
        if not filepaths:
            return
        self._set_import_busy(True)
        self.statusBar().showMessage(f"正在讀取{source_description}…")
        self.worker = FileLoaderWorker(filepaths, self)
        self.worker.progress.connect(self.on_import_progress)
        self.worker.finished.connect(self.on_import_finished)
        self.worker.error.connect(self.on_import_error)
        self.worker.start()

    def on_import_progress(self, count: int, total: int) -> None:
        self.statusBar().showMessage(f"正在匯入檔案…({count}/{total})")

    def on_import_error(self, message: str) -> None:
        self._set_import_busy(False)
        QMessageBox.critical(self, "匯入錯誤", message)
        self.statusBar().showMessage("匯入失敗。", 5000)

    def on_import_finished(self, result: ImportResult) -> None:
        self._set_import_busy(False)
        if not result.datasets:
            details = self._format_issues(result)
            QMessageBox.warning(self, "沒有可匯入的資料", details or "所選 JSON 不含支援的帳號資料。")
            self.statusBar().showMessage("沒有可匯入的資料。", 5000)
            return
        self.raw_datasets = result.datasets
        self.sources = result.sources
        self.datasets = build_insights(self.raw_datasets)
        persistence_warning = ""
        try:
            save_cache(self.raw_datasets, self.sources)
        except OSError:
            persistence_warning = " 無法儲存本機快取；請檢查本機儲存空間與權限。"
        snapshot_message = ""
        if KEY_FOLLOWERS in self.raw_datasets and KEY_FOLLOWING in self.raw_datasets:
            try:
                snapshot, created = append_snapshot(self.datasets)
                comparison = compare_with_previous(snapshot)
                if created:
                    snapshot_message = "已建立快照。"
                    if comparison:
                        snapshot_message += f" 與前次相比：+{comparison['new_followers']} 粉絲、-{comparison['lost_followers']} 粉絲。"
                else:
                    snapshot_message = "資料與最近快照相同，未重複建立。"
            except OSError:
                persistence_warning += " 無法儲存快照；請檢查本機儲存空間與權限。"
        self.refresh_dashboard()
        self.update_sidebar()
        details = self._format_issues(result)
        message = (
            f"已載入 {result.files_loaded} 個檔案、{result.records_after_deduplication} 筆資料"
            f"（合併去除 {result.duplicate_count} 筆重複）。{snapshot_message}{persistence_warning}"
        )
        self.statusBar().showMessage(message, 12000)
        if details:
            QMessageBox.information(self, "匯入完成，但有需注意的檔案", f"{message}\n\n{details}")

    def _format_issues(self, result: ImportResult) -> str:
        if not result.issues:
            return ""
        visible = result.issues[:12]
        text = "\n".join(f"• {issue.path.name}：{issue.message}" for issue in visible)
        if len(result.issues) > len(visible):
            text += f"\n• 另有 {len(result.issues) - len(visible)} 項問題"
        return f"以下檔案未被納入：\n{text}"

    def refresh_dashboard(self) -> None:
        metrics = relationship_metrics(self.datasets)
        self.lbl_followers.setText(f"粉絲：{metrics['followers']}")
        self.lbl_following.setText(f"追蹤中：{metrics['following']}")
        self.lbl_mutual.setText(f"雙向追蹤：{metrics['mutual']}")
        self.lbl_unrequited.setText(f"對方未回追：{metrics['not_following_back']}")
        self.lbl_not_followed_back.setText(f"你尚未回追：{metrics['not_followed_back']}")

    def update_sidebar(self) -> None:
        selected = self.current_key
        self.sidebar.clear()
        keys = sorted(self.datasets, key=lambda value: (value not in self.DERIVED_KEYS, value))
        for key in keys:
            item = QListWidgetItem(f" {key} ({len(self.datasets[key])})")
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.sidebar.addItem(item)
            if key == selected:
                self.sidebar.setCurrentItem(item)
        if self.sidebar.count() and self.sidebar.currentItem() is None:
            self.sidebar.setCurrentRow(0)

    def on_sidebar_select(self, current: QListWidgetItem | None, _: QListWidgetItem | None) -> None:
        if current is None:
            return
        key = current.data(Qt.ItemDataRole.UserRole)
        if key not in self.datasets:
            return
        self.current_key = key
        self.current_data_source = list(self.datasets[key])
        self.opened_usernames.clear()
        self.table_model.set_records(self.current_data_source)
        self.search_entry.blockSignals(True)
        self.search_entry.clear()
        self.search_entry.blockSignals(False)
        self.date_filter_enabled.setChecked(False)
        self.filter_data()
        self.statusBar().showMessage(f"顯示「{key}」：{len(self.current_data_source)} 筆", 3000)

    def _load_cache(self) -> None:
        cache = load_valid_cache()
        if cache.datasets is None:
            self.statusBar().showMessage(cache.message, 8000)
            return
        self.raw_datasets = cache.datasets
        self.datasets = build_insights(self.raw_datasets)
        self.refresh_dashboard()
        self.update_sidebar()
        self.statusBar().showMessage(cache.message, 5000)

    def clear_cache(self) -> None:
        reply = QMessageBox.question(
            self,
            "確認清除快取",
            f"確定要清除本機快取嗎？\n原始 JSON 與快照歷史不會被刪除。\n\n位置：{application_data_dir()}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        removed = clear_safe_cache()
        self.raw_datasets = {}
        self.datasets = {}
        self.sources = []
        self.current_key = None
        self.current_data_source = []
        self.sidebar.clear()
        self.table_model.set_records([])
        self.refresh_dashboard()
        self.filter_data()
        self.statusBar().showMessage("已清除快取。" if removed else "沒有快取可清除。", 5000)

    def filter_data(self) -> None:
        self.table_proxy.set_filters(
            self.search_entry.text(),
            self.date_filter_enabled.isChecked(),
            self.date_start.date().toPython(),
            self.date_end.date().toPython(),
        )
        self._update_table_state()
        self._update_action_availability()

    def on_tree_item_selected(
        self,
        current: QModelIndex,
        _: QModelIndex,
    ) -> None:
        """Load the selected account in the local embedded browser."""
        if not current.isValid():
            return
        source = self.table_proxy.mapToSource(current)
        record = self.table_model.record_at(source.row())
        username = record[0].strip() if record else ""
        if username:
            self.load_profile_in_browser(username)

    def load_profile_in_browser(self, username: str) -> None:
        self.browser_status.setText(f"正在開啟 @{username}…")
        self.profile_browser.setUrl(QUrl(self.profile_url(username)))

    def on_browser_url_changed(self, url: QUrl) -> None:
        if url.host().endswith("instagram.com"):
            self.btn_browser_external.setToolTip(f"以系統瀏覽器開啟：{url.toString()}")

    def on_browser_load_finished(self, success: bool) -> None:
        if success:
            self.browser_status.setText("帳號預覽已載入。登入狀態僅儲存在本機瀏覽器資料中。")
        else:
            self.browser_status.setText("預覽載入失敗；可改在系統瀏覽器開啟。")

    def open_current_profile_externally(self) -> None:
        url = self.profile_browser.url()
        if url.isValid() and url.scheme() in {"http", "https"}:
            webbrowser.open_new_tab(url.toString())

    def _usernames_for_open_scope(self, limit: int) -> list[str]:
        if self.open_scope.currentData() == "selected":
            indexes = self.tree.selectionModel().selectedRows()
        else:
            indexes = [self.table_proxy.index(row, 0) for row in range(min(limit, self.table_proxy.rowCount()))]
        usernames: list[str] = []
        for index in indexes:
            record = self.table_model.record_at(self.table_proxy.mapToSource(index).row())
            if not record:
                continue
            username = record[0].strip()
            if username and username.casefold() not in self.opened_usernames:
                usernames.append(username)
            if len(usernames) >= limit:
                break
        return usernames

    @staticmethod
    def profile_url(username: str) -> str:
        return f"https://www.instagram.com/{quote(username.strip(), safe='._')}/"

    def open_ig_pages(self) -> None:
        limit = int(self.open_combo.currentText())
        usernames = self._usernames_for_open_scope(limit)
        if not usernames:
            scope = "已選取帳號" if self.open_scope.currentData() == "selected" else "篩選結果"
            QMessageBox.information(self, "沒有可開啟的帳號", f"「{scope}」中沒有尚未開啟的帳號。")
            return
        scope = "已選取帳號" if self.open_scope.currentData() == "selected" else "篩選結果"
        reply = QMessageBox.question(
            self,
            "確認在瀏覽器開啟",
            f"將以每 0.8 秒一頁的節奏，在系統瀏覽器開啟「{scope}」中的 {len(usernames)} 個帳號。\n"
            "可在執行期間按「取消批次開啟」停止後續分頁。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._open_queue = usernames
            self._batch_opening = True
            self._update_action_availability()
            self._open_next_profile()

    def _open_next_profile(self) -> None:
        if not self._open_queue:
            self._batch_opening = False
            self._update_action_availability()
            self.statusBar().showMessage("已完成批次開啟。", 4000)
            return
        username = self._open_queue.pop(0)
        webbrowser.open_new_tab(self.profile_url(username))
        self.opened_usernames.add(username.casefold())
        self.table_model.mark_opened(username)
        self.statusBar().showMessage(f"正在開啟帳號；剩餘 {len(self._open_queue)} 個。")
        QTimer.singleShot(800, self._open_next_profile)

    def cancel_opening_profiles(self) -> None:
        if not self._batch_opening:
            return
        remaining = len(self._open_queue)
        self._open_queue.clear()
        self._batch_opening = False
        self._update_action_availability()
        self.statusBar().showMessage(f"已取消批次開啟；未開啟的帳號數：{remaining}。", 5000)

    def reset_opened_status(self) -> None:
        self.opened_usernames.clear()
        self.table_model.clear_opened()
        self._update_action_availability()
        self.statusBar().showMessage("已重置目前清單的瀏覽狀態。", 3000)

    def show_table_menu(self, position) -> None:
        index = self.tree.indexAt(position)
        if not index.isValid():
            return
        record = self.table_model.record_at(self.table_proxy.mapToSource(index).row())
        if record is None:
            return
        username = record[0]
        menu = QMenu(self)
        menu.addAction("複製帳號", lambda: self.copy_text(username))
        menu.addAction("複製個人頁網址", lambda: self.copy_text(self.profile_url(username)))
        menu.exec(self.tree.viewport().mapToGlobal(position))

    def copy_text(self, value: str) -> None:
        QApplication.clipboard().setText(value)
        self.statusBar().showMessage("已複製到剪貼簿。", 2500)

    @staticmethod
    def _safe_csv_value(value: str) -> str:
        return f"'{value}" if value.lstrip().startswith(("=", "+", "-", "@")) else value

    def export_csv(self) -> None:
        records = self.table_proxy.filtered_records()
        if not self.current_key or not records:
            QMessageBox.warning(self, "沒有資料可以匯出", "請先匯入資料，或調整搜尋與日期篩選。")
            return
        if self.current_key in SENSITIVE_DATASETS:
            reply = QMessageBox.warning(
                self,
                "敏感資料提醒",
                f"「{self.current_key}」可能包含私密關係資料。確定要匯出嗎？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        path, _ = QFileDialog.getSaveFileName(self, "匯出目前篩選結果", f"{self.current_key}.csv", "CSV Files (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.writer(handle)
                writer.writerow(["用戶名稱 (Username)", "顯示名稱 / 相關資訊", "時間紀錄"])
                for record in records:
                    writer.writerow([self._safe_csv_value(value) for value in record])
            self.statusBar().showMessage(f"已匯出目前篩選結果：{path}", 5000)
        except OSError:
            QMessageBox.critical(self, "匯出失敗", "無法寫入檔案。請檢查儲存位置與權限後再試一次。")

    def export_summary(self) -> None:
        if not self.datasets:
            QMessageBox.warning(self, "提示", "請先匯入資料再匯出摘要。")
            return
        path, _ = QFileDialog.getSaveFileName(self, "匯出關係摘要", "ig_relationship_summary.csv", "CSV Files (*.csv)")
        if not path:
            return
        metrics = relationship_metrics(self.datasets)
        latest = load_snapshots()[-1] if load_snapshots() else {}
        comparison = compare_with_previous(latest) if latest else None
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
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as handle:
                csv.writer(handle).writerows(rows)
            self.statusBar().showMessage(f"已匯出摘要：{path}", 5000)
        except OSError:
            QMessageBox.critical(self, "匯出失敗", "無法寫入檔案。請檢查儲存位置與權限後再試一次。")

    def show_snapshot_history(self) -> None:
        snapshots = load_snapshots()
        if not snapshots:
            QMessageBox.information(self, "快照紀錄", "尚無快照。匯入同時包含粉絲與追蹤中的資料後會自動建立。")
            return
        lines = ["快照會保留最近 50 筆，僅儲存在本機。", ""]
        for snapshot in reversed(snapshots):
            metrics = snapshot.get("metrics", {})
            timestamp = snapshot.get("created_at", "未知時間")
            lines.append(
                f"{timestamp}\n"
                f"粉絲 {metrics.get('followers', 0)}｜追蹤 {metrics.get('following', 0)}｜"
                f"雙向追蹤 {metrics.get('mutual', 0)}｜對方未回追 {metrics.get('not_following_back', 0)}"
            )
            lines.append("")
        dialog = QDialog(self)
        dialog.setWindowTitle("關係快照紀錄")
        dialog.resize(620, 420)
        layout = QVBoxLayout(dialog)
        content = QPlainTextEdit("\n".join(lines))
        content.setReadOnly(True)
        layout.addWidget(content)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def show_snapshot_chart(self) -> None:
        snapshots = load_snapshots()
        if len(snapshots) < 2:
            QMessageBox.information(
                self,
                "趨勢圖",
                "至少需要兩次不同的匯入快照才能繪製趨勢圖。",
            )
            return
        chart = QChart()
        chart.setTitle("關係快照趨勢（由舊到新）")
        chart.legend().setVisible(True)
        series_definitions = [
            ("粉絲", "followers", "#4fc1ff"),
            ("追蹤中", "following", "#ce9178"),
            ("雙向", "mutual", "#4ec9b0"),
            ("未回追", "not_following_back", "#f44747"),
            ("尚未回追", "not_followed_back", "#c586c0"),
        ]
        maximum = 1
        for label, metric, color in series_definitions:
            series = QLineSeries()
            series.setName(label)
            series.setColor(QColor(color))
            for index, snapshot in enumerate(snapshots, start=1):
                value = int(snapshot.get("metrics", {}).get(metric, 0))
                maximum = max(maximum, value)
                series.append(index, value)
            chart.addSeries(series)
        axis_x = QValueAxis()
        axis_x.setTitleText("快照順序")
        axis_x.setRange(1, len(snapshots))
        axis_x.setTickCount(min(len(snapshots), 6))
        axis_x.setLabelFormat("%.0f")
        axis_y = QValueAxis()
        axis_y.setTitleText("帳號數")
        axis_y.setRange(0, maximum)
        chart.addAxis(axis_x, Qt.AlignmentFlag.AlignBottom)
        chart.addAxis(axis_y, Qt.AlignmentFlag.AlignLeft)
        for series in chart.series():
            series.attachAxis(axis_x)
            series.attachAxis(axis_y)

        dialog = QDialog(self)
        dialog.setWindowTitle("關係趨勢圖")
        dialog.resize(760, 500)
        layout = QVBoxLayout(dialog)
        view = QChartView(chart)
        view.setRenderHint(QPainter.RenderHint.Antialiasing)
        layout.addWidget(view)
        first_time = snapshots[0].get("created_at", "未知時間")
        last_time = snapshots[-1].get("created_at", "未知時間")
        layout.addWidget(QLabel(f"快照 1：{first_time}　　快照 {len(snapshots)}：{last_time}"))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def show_about_dialog(self) -> None:
        QMessageBox.about(
            self,
            "關於 Instagram 數據分析",
            "<b>Instagram 數據分析（離線）</b><br><br>"
            "僅讀取使用者自行下載的 Instagram JSON；不登入、不呼叫 API、不執行追蹤操作。<br><br>"
            f"本機安全資料位置：<br>{application_data_dir()}",
        )


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLE)
    window = IGMultiFileDashboard()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
