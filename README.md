# Instagram 數據分析（離線）

這是一個讀取 Instagram「下載你的資訊」JSON 檔案的桌面工具。它不需要登入、不使用 API、不爬取 Instagram，也不會執行追蹤或取消追蹤操作。

## 專案結構

```text
.
├── main.py                 # PySide6 桌面應用程式入口
├── igviewer/               # 資料解析、關係計算與安全本機儲存
├── tests/                  # 桌面應用程式核心測試
├── scripts/                # 本機建置腳本
├── server/                 # 可選的 Docker 趨勢服務與 API
├── legacy/                 # 僅供歷史參考的舊版程式，不要執行
├── data/                   # 個人 Instagram 匯出資料（已忽略，不提交）
└── dist/、build/           # 本機產生物（已忽略，可重新建置）
```

`main.py` 與 `igviewer/` 是目前桌面應用程式的唯一正式來源。`server/` 為可選部署，不是啟動桌面程式的必要條件。

## 啟動

需要 Python 3.10 以上：

```bash
python3 -m pip install -r requirements.txt
python3 main.py
```

也可安裝成命令列入口：

```bash
python3 -m pip install .
ig-viewer
```

## 匯入資料

建議點選「匯入匯出資料夾」，選取 Instagram 下載資料的最上層資料夾。程式會遞迴尋找 JSON，並：

- 合併 `followers_1.json`、`followers_2.json` 等分段檔案。
- 自動去除同一資料集裡重複的帳號。
- 顯示無法解析或不支援檔案的原因。
- 建立粉絲、追蹤中、雙向追蹤、對方未回追、你尚未回追等清單。

目前支援資料中的繁中、簡中與英文使用者名稱欄位。未知檔案不會悄悄併入結果；它會在匯入完成後列出。

## 使用方式

- 首次使用時，中央畫面會說明資料只在本機讀取；建議使用「匯入 Instagram 資料夾」，讓程式自動找出 JSON。已知檔案時才使用「選擇 JSON 檔案」。
- 使用左側清單切換資料集；`⌘F`／`Ctrl+F` 可聚焦搜尋。沒有符合的帳號時，可直接清除搜尋與日期篩選。
- 點選表格帳號後，右側會預覽其 Instagram 個人頁面；這可能需要 Instagram 登入。預覽工具列可返回、重新整理或改在系統瀏覽器開啟。
- 批次開啟前須明確選擇「已選取帳號」或「篩選結果」以及數量；確認後仍可按「取消批次開啟」停止後續分頁。
- 「資料與分析」選單包含目前篩選結果 CSV、關係摘要、快照紀錄、趨勢圖與本機快取操作。敏感資料集（如封鎖、限制、摯友）匯出前會再次確認。
- 累積至少兩個不同快照後，可從「查看關係趨勢圖」查看各關係數字的本機時間序列。

## 本機資料與隱私

快取與快照使用 JSON，而不是可執行反序列化的 pickle，並會檢查原始 JSON 是否變更。資料儲存在：

- macOS：`~/Library/Application Support/IG Viewer/`
- Windows：`%APPDATA%\\IG Viewer\\`
- Linux：`$XDG_DATA_HOME/IG Viewer/` 或 `~/.local/share/IG Viewer/`

快取會在原始 JSON 變更、移動或刪除後失效，避免顯示過期資料。快照最多保留 50 筆；相同追蹤關係不會重複新增快照。舊版 `ig_dashboard_data.cache` 不會被讀取，因為它是非安全的 pickle 格式；請使用資料夾匯入重新建立安全快取。

內嵌瀏覽器會將 Instagram 登入工作階段與網站快取保存在上述資料夾下的 `browser-profile/`。它不會將帳號密碼寫入專案資料夾；如需登出或清除網站工作階段，請在內嵌 Instagram 頁面中登出，或手動移除該 `browser-profile/` 目錄。

`data/`、快取和 `.DS_Store` 已列入 `.gitignore`。Instagram 匯出資料含有個人社交關係，請勿直接上傳或提交到 Git。

## 開發驗證

```bash
python3 -m unittest discover -v
python3 -m py_compile main.py igviewer/*.py
```

測試涵蓋分段粉絲檔合併、雙向關係結果、來源異動造成的快取失效，以及防止重複建立相同快照。

## 遠端每日趨勢服務

可部署的 Docker 服務位於 [server/README.md](server/README.md)。它接收受控的 Instagram 匯出 JSON、每日建立去重快照並提供趨勢與關係清單 API；不會使用 Instagram 帳密、Cookie 自動化或爬蟲。

## macOS App 封裝

可建立不含任何 Instagram JSON 或快取的未簽署本機 App：

```bash
bash scripts/build_macos_app.sh
```

產物位於 `dist/IG Viewer.zip`。解壓縮後取得 `IG Viewer.app`。它是未 notarize 的本機建置；若 macOS 顯示安全提示，請在 Finder 對 App 按右鍵並選擇「打開」。若要散布給其他人，仍需另外處理 Apple Developer 簽署與 notarization。
