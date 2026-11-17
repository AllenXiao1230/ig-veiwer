# IG Trends Docker Service

IG Trends 是一個自架、離線優先的 Instagram 匯出資料趨勢服務。它不登入 Instagram、不保存帳號密碼、不使用爬蟲；只接收你已合法取得的 `followers*.json` 與 `following*.json`。

## 服務設計

```text
Instagram 官方外部匯出
  ├─ Google Drive 一組完整 JSON → /incoming/current
  ├─ 其他受信任來源 → /incoming（掛載目錄）
  └─ 帶 Token 的 HTTPS 上傳 API
                │
                ▼
解析、分段合併、去重、內容雜湊
                │
                ▼
SQLite 快照 + 原始 JSON 歸檔（Docker Volume）
                │
                ├─ 關係趨勢：新增／流失粉絲、追蹤變化
                ├─ 清單 API：雙向、未回追、尚未回追
                └─ Web 儀表板與健康檢查
```

每份快照必須同時包含完整可讀取的 `followers_*.json` 與 `following.json`，合法空清單也會計入。服務會合併粉絲分段、依使用者名稱去重，並先固定來源副本再解析及歸檔。來源檔案順序不影響 SHA-256；連續相同內容會回傳 `unchanged`，A → B → A 會建立三筆時間紀錄並重用 A 的歸檔。

## Instagram 資料來源的安全邊界

Instagram 並沒有可通用地每日抓取完整關係匯出 JSON 的公開 API。這個服務刻意不實作帳密登入、Cookie 自動化或爬蟲。請在下列受控來源中擇一：

1. **Google Drive 官方匯出（建議）**：Instagram 在 Accounts Center 授權後定時寫入專用 Drive 資料夾；可選的 `gdrive-sync` 容器以 Drive 唯讀 OAuth Token 進行複製，且只會在完整新快照準備好後替換 `current`。
2. **掛載收件目錄**：你自己的排程器將「完整的一組」JSON 以原子方式同步到 `server/incoming/current/`；服務依 `.env` 的時間每日掃描。
3. **受 Token 保護的上傳 API**：使用你自己的受信任裝置／工作流程將 JSON POST 到服務。

若 Instagram 匯出取得流程需要登入、驗證碼或人工操作，請保留在你的裝置上完成；不可把密碼或驗證碼放進 `.env`、排程器或容器。

## 遠端部署前置條件

- Docker Engine 與 Docker Compose v2。
- HTTPS 反向代理或 VPN；Compose 預設只綁定 `127.0.0.1:8085`，不公開到 Internet。若要只限 Tailscale 網路存取，設定 `IG_TRENDS_BIND_HOST` 為該主機的 Tailscale IP（例如 `100.93.76.48`），不要使用 `0.0.0.0`。
- 至少一個來源流程，能每天提供完整 JSON 快照。
- 容器資料將保存在 `ig-trends-data` named volume；請納入伺服器備份。

## 部署步驟

以下假設已把整個專案複製或 Git clone 到伺服器的 `/srv/ig-trends`。不要覆寫既有服務的 `.env`、反向代理設定或 Docker volumes。

```bash
cd /srv/ig-trends/server
cp .env.example .env
# 以編輯器填入一個長隨機值：IG_TRENDS_API_TOKEN
docker compose up -d --build
curl http://127.0.0.1:8085/api/health
```

更新程式時：先確認變更範圍，再執行 `git pull --ff-only` 和 `docker compose up -d --build`。日常更新不要使用 `docker compose down -v`，以免刪除資料庫與歸檔。

## 每日收件目錄流程

將完整快照先同步到暫存目錄，全部完成後再原子改名為 `current`，避免服務讀到半份資料：

```text
server/incoming/
  current/
    followers_1.json
    followers_2.json   # 若 Instagram 有分段
    following.json
```

設定 `.env`：

```dotenv
IG_TRENDS_TIMEZONE=Asia/Taipei
IG_TRENDS_DAILY_INGEST_TIME=03:30
```

服務啟動時也會掃描一次；後續每天固定時間掃描。來源未變時會回傳／記錄 `unchanged`，不會新增重複快照。

## Google Drive 官方匯出同步

1. 在 Instagram Accounts Center 選擇「匯出到外部服務」→ **Google Drive**，選 JSON 並確認包含 followers 與 following 資料。請使用專用的 Drive 資料夾，不要混用其他檔案。
2. 在伺服器設定 rclone。此步驟會在你的瀏覽器完成 Google OAuth；選 **`drive.readonly`** scope，不存放 Instagram 密碼。無圖形介面伺服器請依 rclone 的 remote-auth 流程完成。

   ```bash
   cd /srv/ig-trends/server
   docker compose --profile google-drive run --rm --entrypoint rclone gdrive-sync config
   ```

   在 rclone 設定中命名 remote 為 `igdrive`、選 Google Drive、scope 選 `drive.readonly`，並將 `IG_TRENDS_GDRIVE_PATH` 設為 Instagram 實際匯出的那個專用資料夾路徑。

3. 確認 `.env` 中的路徑後啟動同步服務：

   ```bash
   docker compose --profile google-drive up -d gdrive-sync
   ```

   同步容器每 15 分鐘複製一次，下載失敗或檔案不完整時會保留上一份 `current` 快照。主服務仍依 `IG_TRENDS_DAILY_INGEST_TIME` 每日建立一次趨勢快照。

## API 上傳

`IG_TRENDS_API_TOKEN` 不可留在 shell history、程式碼或 commit 中。以下只示範格式，請以安全方式提供 Token：

```bash
curl --fail-with-body \
  -H "X-API-Token: $IG_TRENDS_API_TOKEN" \
  -F "files=@followers_1.json;type=application/json" \
  -F "files=@following.json;type=application/json" \
  https://your-host.example/api/v1/ingest/upload
```

受 Token 保護的端點：

- `POST /api/v1/ingest/upload`：上傳一組 JSON（完整 request body 上限 250 MB，包含 multipart 編碼）。驗證與大小限制會在檔案解析前及串流接收時執行。
- `POST /api/v1/ingest/scan`：立即掃描收件目錄。
- `GET /api/v1/latest`：最新快照與指標。
- `GET /api/v1/trends`：快照趨勢，預設最多 90 筆。
- `GET /api/v1/accounts?view=mutual|not_following_back|not_followed_back`：最新關係清單，可用 `query`、`limit` 與 `offset` 搜尋及分頁。
- `GET /api/health`：健康檢查，包含最近匯入狀態、最近成功時間及錯誤摘要。

首頁 `/` 是摘要儀表板。若你將它透過反向代理公開，務必在代理層要求 HTTPS 與登入／存取控制。

## 保留與備份

- 原始 JSON 歸檔預設保留 90 天，可用 `IG_TRENDS_ARCHIVE_RETENTION_DAYS` 調整；設為 `0` 代表不自動刪除。
- SQLite 快照記錄會持續保留。
- 使用 SQLite 備份功能建立一致副本，並另行保存原始 JSON 歸檔目錄：

```bash
cd /srv/ig-trends/server
docker compose exec -T ig-trends python -m app.backup /data/ig-trends-backup.sqlite3
docker compose cp ig-trends:/data/ig-trends-backup.sqlite3 ./ig-trends-backup.sqlite3
```

先測試還原檔案，再建立任何保留／刪除排程。

## 本機驗證

Docker CLI 不可用時，仍可測試核心與 API：

```bash
python3 -m pip install -r server/requirements-dev.txt
python3 -m unittest discover -s server/tests -v
```
