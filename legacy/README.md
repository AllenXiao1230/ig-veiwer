# 歷史程式

這個目錄只保存已被目前桌面應用程式取代的舊實作，方便追查歷史行為；它不屬於正式執行路徑。

`main_legacy_pickle.py` 使用不安全的 pickle 快取格式。請不要執行它，也不要載入舊的 `ig_dashboard_data.cache`。目前版本使用 `main.py`、`igviewer/` 與 JSON 快取。
