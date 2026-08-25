import json
import sys
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QListWidget, QTreeWidget, QTreeWidgetItem,
    QSplitter, QGroupBox, QComboBox, QFileDialog, QMessageBox, QHeaderView, QListWidgetItem,
    QDateEdit
)
from PySide6.QtCore import Qt, QThread, Signal, QDate
from PySide6.QtGui import QFont, QBrush
from datetime import datetime
import csv
import os
import webbrowser
import pickle

# 亂碼修正函式保持不變
def fix_ig_encoding(text):
    if not text:
        return ""
    try:
        return text.encode('latin-1').decode('utf-8')
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text

def _parse_string_list_format(data_list):
    """
    解析格式一和二：處理包含 'string_list_data' 的檔案
    適用檔案：followers_1.json, following.json, following_hashtags.json
    """
    parsed_list = []
    for item in data_list:
        if not isinstance(item, dict) or 'string_list_data' not in item:
            continue

        username, details, time_str = "", "", ""
        
        # 優先從 'title' 獲取用戶名/標籤名，如果為空，則從 'string_list_data' 的 'value' 獲取
        username = fix_ig_encoding(item.get('title', ''))
        
        # 處理 timestamp 和備用 username
        if item['string_list_data']:
            s_data = item['string_list_data'][0]
            if not username: # 如果 title 為空 (如 followers_1.json)
                username = fix_ig_encoding(s_data.get('value', ''))
            
            ts = s_data.get('timestamp', 0)
            if ts:
                time_str = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')

        # 在這種格式下，我們沒有獨立的顯示名稱，所以將其設置為與用戶名相同
        details = username
        
        if username: # 只有在成功解析出用戶名/標籤時才添加
            parsed_list.append((username, details, time_str))
            
    return parsed_list

def _parse_label_values_format(data_list):
    """
    解析格式三：處理包含 'label_values' 的檔案
    適用檔案：blocked_profiles.json, close_friends.json 等
    """
    parsed_list = []
    for item in data_list:
        if not isinstance(item, dict) or 'label_values' not in item:
            continue
            
        username, details, time_str = "", "", ""
        ts = item.get('timestamp', 0)
        if ts:
            time_str = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
            
        for label_obj in item['label_values']:
            label = fix_ig_encoding(label_obj.get('label', ''))
            val = fix_ig_encoding(label_obj.get('value', ''))
            
            if '用戶名稱' in label:
                username = val
            elif '名稱' in label and '用戶' not in label:
                details = val
        
        if username: # 只有在成功解析出用戶名時才添加
            parsed_list.append((username, details, time_str))
    return parsed_list

def parse_file_content(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError) as e:
        print(f"錯誤：無法讀取或解析檔案 {filepath}: {e}")
        return None

    # 格式一：層級化物件結構 (Object-Rooted)
    if isinstance(data, dict):
        data_list = next((value for key, value in data.items() if key.startswith('relationships_') and isinstance(value, list)), None)
        return _parse_string_list_format(data_list) if data_list is not None else None
    # 格式二 & 三：純陣列結構 (Array-Rooted)
    elif isinstance(data, list):
        if not data: return []
        first_item = data[0]
        if 'string_list_data' in first_item: return _parse_string_list_format(data)
        elif 'label_values' in first_item: return _parse_label_values_format(data)
    
    return None # 未知或不支援的格式

class FileLoaderWorker(QThread):
    finished = Signal(dict)
    progress = Signal(int, int)
    error = Signal(str)

    def __init__(self, filepaths, filename_map, parent=None):
        super().__init__(parent)
        self.filepaths = filepaths
        self.filename_map = filename_map

    def run(self):
        datasets = {}
        try:
            total_files = len(self.filepaths)
            for i, path in enumerate(self.filepaths):
                filename_base = os.path.basename(path).replace(".json", "")
                parsed_data = parse_file_content(path)
                if parsed_data is not None:
                    display_name = self.filename_map.get(filename_base, filename_base.replace("_", " ").title())
                    datasets[display_name] = parsed_data
                self.progress.emit(i + 1, total_files)
            self.finished.emit(datasets)
        except Exception as e:
            import traceback
            self.error.emit(f"背景處理失敗: {e}\n{traceback.format_exc()}")

# 使用 QSS (Qt Style Sheets) 定義的暗黑主題
DARK_STYLE = """
    QWidget {
        background-color: #1e1e1e;
        color: #d4d4d4;
        font-family: Helvetica;
        font-size: 13pt;
    }
    QMainWindow {
        background-color: #1e1e1e;
    }
    QGroupBox {
        font-size: 12pt;
        color: #858585;
        border: 1px solid #333333;
        border-radius: 5px;
        margin-top: 10px;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        subcontrol-position: top left;
        padding: 0 5px;
        left: 10px;
    }
    QPushButton {
        background-color: #007acc;
        color: white;
        border: none;
        padding: 8px 12px;
        border-radius: 4px;
    }
    QPushButton:hover {
        background-color: #005999;
    }
    QPushButton:pressed {
        background-color: #004c8c;
    }
    QPushButton#actionButton {
        background-color: #2ea043;
    }
    QPushButton#actionButton:hover {
        background-color: #238636;
    }
    QPushButton#actionButton:pressed {
        background-color: #1a6e2b;
    }
    QLineEdit {
        background-color: #2d2d2d;
        border: 1px solid #3e3e42;
        padding: 5px;
        border-radius: 4px;
        color: white;
    }
    QListWidget {
        background-color: #252526;
        border: none;
    }
    QListWidget::item {
        padding: 8px;
    }
    QListWidget::item:selected {
        background-color: #094771;
        color: white;
    }
    QTreeWidget {
        background-color: #252526;
        border: none;
        alternate-background-color: #2a2a2b;
    }
    QTreeWidget::item {
        padding: 4px 5px;
    }
    QTreeWidget::item:selected {
        background-color: #094771;
        color: white;
    }
    QHeaderView::section {
        background-color: #333333;
        color: white;
        padding: 5px;
        border: 1px solid #3e3e42;
    }
    QComboBox {
        background-color: #2d2d2d;
        border: 1px solid #3e3e42;
        padding: 5px;
        border-radius: 4px;
    }
    QComboBox::drop-down {
        border: none;
    }
    QComboBox QAbstractItemView {
        background-color: #2d2d2d;
        selection-background-color: #007acc;
    }
    QSplitter::handle {
        background-color: #3e3e42;
    }
    QSplitter::handle:hover {
        background-color: #007acc;
    }
    QStatusBar {
        color: #a0a0a0;
    }
    QStatusBar::item {
        border: none;
    }
    #dashboardLabel {
        font-size: 16pt;
        font-weight: bold;
    }
    #followersLabel { color: #4fc1ff; }
    #followingLabel { color: #ce9178; }
    #mutualLabel { color: #4ec9b0; }
    #unrequitedLabel { color: #f44747; }
"""

class IGMultiFileDashboard(QMainWindow):
    # 定義常數以避免魔法字串
    KEY_FOLLOWERS = "粉絲"
    KEY_FOLLOWING = "追蹤中"
    KEY_UNREQUITED = "💔 誰沒有回追你"
    KEY_MUTUAL = "🤝 雙向互相追蹤"

    # 檔名到中文名稱的映射
    FILENAME_MAP = {
        "followers_1": KEY_FOLLOWERS,
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
        "profiles_you've_favorited": "已加星號的個人檔案"
    }

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Instagram 數據分析")
        self.CACHE_FILE = "ig_dashboard_data.cache"
        self.setGeometry(100, 100, 1200, 800)
        
        self.datasets = {}
        self.current_key = None
        self.sort_column_index = -1 # -1 表示尚未選擇排序列
        self.current_data_source = [] # 用於存儲當前視圖的完整數據源
        self.sort_order = Qt.SortOrder.AscendingOrder
        self.opened_usernames = set() # 追蹤已開啟的用戶
        self.opened_brush = QBrush(Qt.GlobalColor.gray) # 用於反灰的筆刷
        self.worker = None

        self.create_widgets()
        self._apply_styles()
        self._load_cache()

    def create_widgets(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        menu_bar = self.menuBar()
        help_menu = menu_bar.addMenu("幫助")
        about_action = help_menu.addAction("關於")
        about_action.triggered.connect(self.show_about_dialog)

        main_layout = QVBoxLayout(central_widget)
        
        top_bar = self._create_top_bar()
        dashboard = self._create_dashboard()
        main_view = self._create_main_view()
        
        main_layout.addWidget(top_bar)
        main_layout.addWidget(dashboard)
        main_layout.addWidget(main_view)

    def _create_top_bar(self):
        top_bar_widget = QWidget()
        top_bar_widget.setFixedHeight(55) # 設定頂部區域的固定高度

        top_bar_layout = QHBoxLayout(top_bar_widget)
        top_bar_layout.setContentsMargins(0, 0, 0, 0)

        self.btn_import = QPushButton("📂 批次匯入 JSON")
        self.btn_import.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_import.clicked.connect(self.import_multiple_files)
        top_bar_layout.addWidget(self.btn_import)

        self.btn_export = QPushButton("📥 匯出 CSV")
        self.btn_export.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_export.clicked.connect(self.export_csv)
        top_bar_layout.addWidget(self.btn_export)

        self.btn_clear_cache = QPushButton("🗑️ 清除快取")
        self.btn_clear_cache.setToolTip("清除所有已匯入的資料並重置應用程式")
        self.btn_clear_cache.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear_cache.clicked.connect(self.clear_cache)
        top_bar_layout.addWidget(self.btn_clear_cache)

        # 批次開啟區塊
        open_group = QGroupBox("批次開啟")
        open_layout = QHBoxLayout(open_group)
        open_layout.setContentsMargins(5, 5, 5, 5)
        open_layout.addWidget(QLabel("數量:"))
        self.open_combo = QComboBox()
        self.open_combo.addItems(["1", "5", "10", "20", "50"])
        self.open_combo.setCurrentText("5")
        self.open_combo.setFixedWidth(70)
        open_layout.addWidget(self.open_combo)
        self.btn_open_ig = QPushButton("🌐 開啟主頁")
        self.btn_open_ig.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open_ig.setObjectName("actionButton")
        self.btn_open_ig.clicked.connect(self.open_ig_pages)
        open_layout.addWidget(self.btn_open_ig)

        self.btn_reset_opened = QPushButton("🔄 重置")
        self.btn_reset_opened.setToolTip("清除所有已開啟項目的反灰狀態")
        self.btn_reset_opened.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reset_opened.clicked.connect(self.reset_opened_status)
        open_layout.addWidget(self.btn_reset_opened)
        top_bar_layout.addWidget(open_group)

        top_bar_layout.addStretch(1)

        # 搜尋框
        search_group = QGroupBox("搜尋")
        search_layout = QHBoxLayout(search_group)
        search_layout.setContentsMargins(5, 5, 5, 5)
        self.search_entry = QLineEdit()
        self.search_entry.setPlaceholderText("在此搜尋...")
        self.search_entry.textChanged.connect(self.filter_data)
        search_layout.addWidget(self.search_entry)

        search_layout.addWidget(QLabel("從:"))
        self.date_start = QDateEdit(calendarPopup=True)
        self.date_start.setDate(QDate.currentDate().addYears(-10)) # 預設開始日期
        self.date_start.dateChanged.connect(self.filter_data)
        search_layout.addWidget(self.date_start)

        search_layout.addWidget(QLabel("到:"))
        self.date_end = QDateEdit(calendarPopup=True)
        self.date_end.setDate(QDate.currentDate()) # 預設結束日期
        self.date_end.dateChanged.connect(self.filter_data)
        search_layout.addWidget(self.date_end)


        self.btn_sort_order = QPushButton("正序")
        self.btn_sort_order.setToolTip("點擊切換排序順序 (正序/倒序)")
        self.btn_sort_order.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_sort_order.clicked.connect(self.toggle_sort_order_and_apply)
        search_layout.addWidget(self.btn_sort_order)

        top_bar_layout.addWidget(search_group)
        
        return top_bar_widget

    def _create_dashboard(self):
        dashboard_widget = QWidget()
        dashboard_widget.setFixedHeight(60)
        dashboard_layout = QHBoxLayout(dashboard_widget)
        
        self.lbl_followers = QLabel("👥 粉絲: 0")
        self.lbl_following = QLabel("👣 追蹤: 0")
        self.lbl_mutual = QLabel("🤝 雙向: 0")
        self.lbl_unrequited = QLabel("💔 沒回追: 0")

        for label in [self.lbl_followers, self.lbl_following, self.lbl_mutual, self.lbl_unrequited]:
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            dashboard_layout.addWidget(label)
        
        return dashboard_widget

    def _create_main_view(self):
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        # 左側選單
        left_panel = QGroupBox("資料來源清單")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(5, 5, 5, 5)
        self.sidebar = QListWidget()
        self.sidebar.currentItemChanged.connect(self.on_sidebar_select)
        left_layout.addWidget(self.sidebar)
        
        # 右側表格
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(['用戶名稱 (Username)', '顯示名稱 / 相關資訊', '時間紀錄'])
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionBehavior(QTreeWidget.SelectionBehavior.SelectRows)
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.tree.header().setStretchLastSection(False)
        self.tree.setColumnWidth(0, 220)
        self.tree.setColumnWidth(1, 280)
        self.tree.setColumnWidth(2, 160)
        self.tree.header().sectionClicked.connect(self.sort_column)
        self.tree.setSortingEnabled(False) # 我們手動處理排序邏輯
        right_layout.addWidget(self.tree)
        
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([250, 850])
        return splitter

    def _apply_styles(self):
        # 將樣式相關的設定集中，方便管理
        self.lbl_followers.setObjectName("dashboardLabel")
        self.lbl_followers.setProperty("class", "followersLabel")
        self.lbl_following.setObjectName("dashboardLabel")
        self.lbl_following.setProperty("class", "followingLabel")
        self.lbl_mutual.setObjectName("dashboardLabel")
        self.lbl_mutual.setProperty("class", "mutualLabel")
        self.lbl_unrequited.setObjectName("dashboardLabel")
        self.lbl_unrequited.setProperty("class", "unrequitedLabel")

    def import_multiple_files(self):
        filepaths, _ = QFileDialog.getOpenFileNames(self, "批次匯入 JSON", "", "JSON Files (*.json)")
        if not filepaths: return

        self.btn_import.setEnabled(False)
        self.btn_clear_cache.setEnabled(False)
        self.statusBar().showMessage("正在背景匯入檔案...")

        self.worker = FileLoaderWorker(filepaths, self.FILENAME_MAP)
        self.worker.finished.connect(self.on_import_finished)
        self.worker.progress.connect(self.on_import_progress)
        self.worker.error.connect(self.on_import_error)
        self.worker.start()

    def on_import_progress(self, count, total):
        self.statusBar().showMessage(f"正在匯入檔案... ({count}/{total})")

    def on_import_error(self, err_msg):
        self.btn_import.setEnabled(True)
        self.btn_clear_cache.setEnabled(True)
        QMessageBox.critical(self, "匯入錯誤", err_msg)
        self.statusBar().showMessage("匯入失敗。", 5000)

    def on_import_finished(self, new_datasets):
        loaded_count = len(new_datasets)
        self.datasets.update(new_datasets)
        self.auto_generate_insights()
        self.update_sidebar()
        self.btn_import.setEnabled(True)
        self.btn_clear_cache.setEnabled(True)
        self.statusBar().showMessage(f"成功匯入 {loaded_count} 個檔案。", 5000)

    def auto_generate_insights(self):
        print("偵錯：正在執行 auto_generate_insights...")
        followers_data = self.datasets.get(self.KEY_FOLLOWERS, [])
        following_data = self.datasets.get(self.KEY_FOLLOWING, [])

        # 無論如何都先更新粉絲和追蹤中的基礎計數
        self.lbl_followers.setText(f"👥 粉絲: {len(followers_data)}")
        self.lbl_following.setText(f"👣 追蹤: {len(following_data)}")

        # 如果缺少任一關鍵數據，則清除舊洞察並返回
        if not followers_data or not following_data:
            print("偵錯：缺少粉絲或追蹤中檔案。跳過洞察報告生成。")
            # 清除舊的洞察報告並重設儀表板
            self.datasets.pop(self.KEY_UNREQUITED, None)
            self.datasets.pop(self.KEY_MUTUAL, None)
            self.lbl_mutual.setText("🤝 雙向: 0")
            self.lbl_unrequited.setText("💔 沒回追: 0")
            return

        followers_set = {item[0].lower() for item in followers_data if item[0]}
        following_set = {item[0].lower() for item in following_data if item[0]}

        unrequited_usernames = following_set - followers_set
        unrequited = [item for item in following_data if item[0] and item[0].lower() in unrequited_usernames]

        mutual_usernames = following_set & followers_set
        mutual = [item for item in following_data if item[0] and item[0].lower() in mutual_usernames]

        print(f"偵錯：已生成洞察報告: 未回追={len(unrequited)}, 雙向={len(mutual)}")

        self.lbl_mutual.setText(f"🤝 雙向: {len(mutual)}")
        self.lbl_unrequited.setText(f"💔 沒回追: {len(unrequited)}")

        self.datasets[self.KEY_UNREQUITED] = unrequited
        self.datasets[self.KEY_MUTUAL] = mutual
        print(f"偵錯：資料集已更新。目前的鍵: {list(self.datasets.keys())}")

    def update_sidebar(self):
        self.sidebar.clear()
        keys = sorted(list(self.datasets.keys()))

        special_keys = [k for k in keys if k in (self.KEY_UNREQUITED, self.KEY_MUTUAL)]
        for sk in special_keys: keys.remove(sk)
        keys = special_keys + keys

        for key in keys:
            count = len(self.datasets[key])
            item = QListWidgetItem(f" {key} ({count})")
            # 將實際的 key 存儲在 item 的 UserRole 中，與顯示文字解耦
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.sidebar.addItem(item)

    def on_sidebar_select(self, current_item, previous_item):
        if not current_item:
            # 這是正常情況，例如當列表被清除時
            return

        # 直接從 item 的 data 中獲取 key，不再需要解析字串
        self.current_key = current_item.data(Qt.ItemDataRole.UserRole)
        if not self.current_key:
            print(f"錯誤：無法從側邊欄項目 '{current_item.text()}' 獲取鍵。")
            return
            
        print(f"偵錯：側邊欄項目被選取: '{current_item.text()}'，提取出的鍵: '{self.current_key}'")
        
        try:
            if self.current_key in self.datasets:
                self.opened_usernames.clear() # 為新視圖重置已開啟狀態
                self.current_data_source = self.datasets[self.current_key]
                
                # 重置排序狀態
                self.sort_column_index = -1
                self.tree.header().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
                
                # 清除搜尋並重新渲染，使用 blockSignals 確保行為穩定
                self.search_entry.blockSignals(True)
                self.search_entry.setText("")
                self.search_entry.blockSignals(False)
                self.filter_data() # 手動觸發渲染
                
                self.statusBar().showMessage(f"顯示 '{self.current_key}' ({len(self.current_data_source)} 個項目)", 3000)
            else:
                print(f"錯誤：在資料集中找不到鍵 '{self.current_key}'！")
                self.statusBar().showMessage(f"錯誤：在資料集中找不到鍵 '{self.current_key}'", 5000)
                self.tree.clear()
        except Exception as e:
            print(f"錯誤：在 on_sidebar_select 中發生未預期的錯誤: {e}")
            import traceback
            traceback.print_exc()
            self.statusBar().showMessage(f"處理選擇時發生未預期錯誤: {e}", 5000)
            self.tree.clear()

    def _load_cache(self):
        if os.path.exists(self.CACHE_FILE):
            try:
                with open(self.CACHE_FILE, 'rb') as f:
                    self.datasets = pickle.load(f)
                self.statusBar().showMessage("已從快取載入先前的資料。", 3000)
                self.auto_generate_insights()
                self.update_sidebar()
            except Exception as e:
                print(f"無法載入快取: {e}")
                self.datasets = {}
                self.statusBar().showMessage("就緒。請點擊上方按鈕批次匯入 JSON 檔案...", 5000)
        else:
            self.statusBar().showMessage("就緒。請點擊上方按鈕批次匯入 JSON 檔案...", 5000)

    def _save_cache(self):
        try:
            with open(self.CACHE_FILE, 'wb') as f:
                pickle.dump(self.datasets, f)
            print("資料已成功儲存至快取。")
        except Exception as e:
            print(f"無法儲存快取: {e}")

    def clear_cache(self):
        reply = QMessageBox.question(self, "確認", "確定要清除所有已匯入的資料快取嗎？\n這將會移除所有列表，但不會刪除原始 JSON 檔案。",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.datasets = {}
            self.current_data_source = []
            self.sidebar.clear()
            self.tree.clear()
            self.auto_generate_insights()
            
            if os.path.exists(self.CACHE_FILE):
                try:
                    os.remove(self.CACHE_FILE)
                    self.statusBar().showMessage("資料快取已清除。", 3000)
                except OSError as e:
                    self.statusBar().showMessage(f"清除快取失敗: {e}", 5000)
            else:
                self.statusBar().showMessage("沒有快取可清除。", 3000)

    def show_about_dialog(self):
        QMessageBox.about(
            self,
            "關於 Instagram 數據分析",
            "<b>Instagram 數據分析工具</b><br>"
            "版本: 2.0 (Gemini Enhanced)<br><br>"
            "一個用來分析您 Instagram 匯出資料的桌面應用程式。<br><br>"
            "作者: Xuan (由 Gemini Code Assist 協助開發)<br>"
            "版權 © 2024"
        )

    def render_table(self, data_list):
        self.tree.clear()
        items = []
        for item_data in data_list:
            username = str(item_data[0])
            tree_item = QTreeWidgetItem([str(col) for col in item_data])
            # 如果用戶已被開啟過，則將其反灰
            if username in self.opened_usernames:
                for i in range(self.tree.columnCount()):
                    tree_item.setForeground(i, self.opened_brush)
            items.append(tree_item)
        self.tree.addTopLevelItems(items)

    def open_ig_pages(self):
        if self.tree.topLevelItemCount() == 0:
            QMessageBox.warning(self, "提示", "目前表格沒有資料可以開啟！")
            return

        limit = int(self.open_combo.currentText())
        items_to_open = []

        selected_items = self.tree.selectedItems()
        if selected_items:
            # 如果用戶選取了項目，則僅從選取項中尋找未開啟的
            for item in selected_items:
                if len(items_to_open) >= limit: break
                username = item.text(0)
                if username and username not in self.opened_usernames:
                    items_to_open.append(item)
            msg_prefix = f"從您選取的項目中，將開啟接下來 {len(items_to_open)} 個未開啟的帳號主頁"
        else:
            # 如果未選取，則從所有可見項目中尋找未開啟的
            for i in range(self.tree.topLevelItemCount()):
                if len(items_to_open) >= limit: break
                item = self.tree.topLevelItem(i)
                username = item.text(0)
                if username and username not in self.opened_usernames:
                    items_to_open.append(item)
            msg_prefix = f"將按順序開啟接下來 {len(items_to_open)} 個未開啟的帳號主頁"

        if not items_to_open:
            QMessageBox.information(self, "提示", "所有符合條件的項目都已經開啟過了！")
            return

        reply = QMessageBox.question(self, "確認開啟", f"{msg_prefix}，確定要繼續嗎？",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            opened_count = 0
            for item in items_to_open:
                username = item.text(0)
                if username:
                    webbrowser.open_new_tab(f"https://instagram.com/{username}")
                    self.opened_usernames.add(username)
                    # 即時將該項目反灰
                    for i in range(self.tree.columnCount()):
                        item.setForeground(i, self.opened_brush)
                    opened_count += 1
            self.statusBar().showMessage(f"已成功在瀏覽器中開啟 {opened_count} 個 IG 主頁。", 5000)

    def reset_opened_status(self):
        """清除當前列表所有已開啟的反灰狀態"""
        if not self.opened_usernames:
            self.statusBar().showMessage("目前沒有已開啟的項目可重置。", 3000)
            return
        self.opened_usernames.clear()
        self.filter_data() # 重新渲染表格以移除反灰
        self.statusBar().showMessage("已重置所有項目的瀏覽狀態。", 3000)

    def filter_data(self):
        # 優化：篩選數據源，然後重新渲染表格，而不是隱藏/顯示UI項目
        if not self.current_data_source:
            self.tree.clear()
            return

        start_date = self.date_start.date().toPython()
        end_date = self.date_end.date().toPython()
        query = self.search_entry.text().lower()

        data_to_render = []
        for item in self.current_data_source:
            # 文字篩選
            text_match = (not query) or (query in str(item[0]).lower() or query in str(item[1]).lower())
            if not text_match:
                continue

            # 日期篩選
            time_str = item[2]
            date_match = False # 默認為不匹配
            if time_str:
                try:
                    item_date = datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S').date()
                    if start_date <= item_date <= end_date:
                        date_match = True
                except ValueError:
                    # 格式不正確的日期在篩選時不應匹配，保持 date_match 為 False
                    pass

            if text_match and date_match:
                data_to_render.append(item)

        self.render_table(data_to_render)

    def toggle_sort_order_and_apply(self):
        """點擊排序順序按鈕時觸發，切換順序並重新排序"""
        if self.sort_order == Qt.SortOrder.AscendingOrder:
            self.sort_order = Qt.SortOrder.DescendingOrder
            self.btn_sort_order.setText("倒序")
        else:
            self.sort_order = Qt.SortOrder.AscendingOrder
            self.btn_sort_order.setText("正序")
        
        # 如果從未點擊過表頭，則默認對第三列 (時間紀錄) 進行排序
        if self.sort_column_index < 0:
            self.sort_column_index = 2

        self.apply_sort()

    def sort_column(self, column_index):
        """點擊表頭時觸發"""
        if self.sort_column_index == column_index:
            # 點擊同一列，僅切換順序
            if self.sort_order == Qt.SortOrder.AscendingOrder:
                self.sort_order = Qt.SortOrder.DescendingOrder
                self.btn_sort_order.setText("倒序")
            else:
                self.sort_order = Qt.SortOrder.AscendingOrder
                self.btn_sort_order.setText("正序")
        else:
            # 點擊新的一列，設置新列並默認為正序
            self.sort_column_index = column_index
            self.sort_order = Qt.SortOrder.AscendingOrder
            self.btn_sort_order.setText("正序")
        
        self.apply_sort()

    def apply_sort(self):
        """根據目前的 self.sort_column_index 和 self.sort_order 排序數據"""
        # 如果沒有指定排序列，或沒有數據，則不執行任何操作
        if self.sort_column_index < 0 or not self.current_data_source:
            return

        is_reverse = (self.sort_order == Qt.SortOrder.DescendingOrder)

        # 根據列索引定義排序鍵
        if self.sort_column_index == 2:  # 時間列
            def sort_key(item):
                time_str = item[2]
                if not time_str:
                    return datetime.max  # 將空值排在最後
                try:
                    return datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    return datetime.max # 將格式不正確的值排在最後
        else:  # 其他列使用不區分大小寫的字串排序
            def sort_key(item):
                val = item[self.sort_column_index]
                return str(val).lower() if val is not None else ""

        # 排序數據源
        self.current_data_source.sort(key=sort_key, reverse=is_reverse)
        # 重新應用篩選，這會使用排序後的新數據重新渲染表格
        self.filter_data()
        # 更新表頭的排序指示器
        self.tree.header().setSortIndicator(self.sort_column_index, self.sort_order)

    def export_csv(self):
        if not self.current_key or self.tree.topLevelItemCount() == 0:
            QMessageBox.warning(self, "提示", "沒有資料可以匯出。")
            return
            
        filepath, _ = QFileDialog.getSaveFileName(self, "匯出 CSV", f"{self.current_key}.csv", "CSV Files (*.csv)")
        if not filepath: return

        try:
            with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow([self.tree.headerItem().text(i) for i in range(self.tree.columnCount())])
                
                for i in range(self.tree.topLevelItemCount()):
                    item = self.tree.topLevelItem(i)
                    if not item.isHidden():
                        writer.writerow([item.text(j) for j in range(self.tree.columnCount())])
                        
            self.statusBar().showMessage(f"成功匯出清單至: {filepath}", 5000)
        except Exception as e:
            QMessageBox.critical(self, "錯誤", f"匯出失敗: {e}")

    def closeEvent(self, event):
        self._save_cache()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLE) # 應用暗黑主題
    window = IGMultiFileDashboard()
    window.show()
    sys.exit(app.exec())
