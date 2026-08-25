"""HTTP dashboard, protected ingestion API, and daily inbox scheduler."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
import hmac
import html
import logging
from pathlib import Path
import shutil
from tempfile import mkdtemp
from typing import AsyncIterator

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import HTMLResponse

from .config import Settings
from .ingest import ingest_paths, json_files_in, remove_expired_archives
from .store import TrendStore


LOGGER = logging.getLogger("ig_trends")
MAX_UPLOAD_BYTES = 250 * 1024 * 1024


class Service:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = TrendStore(settings.data_dir / "ig-trends.sqlite3")
        self.archive_root = settings.data_dir / "archives"
        self._ingest_lock = asyncio.Lock()

    def initialize(self) -> None:
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.archive_root.mkdir(parents=True, exist_ok=True)
        self.store.initialize()

    async def scan_incoming(self, source_label: str = "incoming directory") -> dict:
        async with self._ingest_lock:
            paths = json_files_in(self.settings.incoming_dir) if self.settings.incoming_dir.exists() else []
            result = await asyncio.to_thread(
                ingest_paths,
                self.store,
                paths,
                self.archive_root,
                "incoming-directory",
                source_label,
            )
            removed = await asyncio.to_thread(
                remove_expired_archives, self.archive_root, self.settings.archive_retention_days
            )
            result["archives_removed"] = removed
            LOGGER.info("incoming scan result: %s", result.get("status"))
            return result

    async def ingest_upload(self, uploads: list[UploadFile]) -> dict:
        temporary_root = Path(mkdtemp(prefix="ig-trends-upload-", dir=self.settings.data_dir))
        try:
            total_bytes = 0
            paths: list[Path] = []
            for index, upload in enumerate(uploads, start=1):
                filename = Path(upload.filename or f"upload-{index}.json").name
                if not filename.lower().endswith(".json"):
                    raise HTTPException(status_code=415, detail=f"僅接受 JSON 檔案：{filename}")
                # Preserve the export filename: the parser uses names such as
                # followers_1.json and following.json to identify each dataset.
                upload_dir = temporary_root / f"{index:03d}"
                upload_dir.mkdir()
                target = upload_dir / filename
                with target.open("wb") as handle:
                    while chunk := await upload.read(1024 * 1024):
                        total_bytes += len(chunk)
                        if total_bytes > MAX_UPLOAD_BYTES:
                            raise HTTPException(status_code=413, detail="上傳總量超過 250 MB 限制")
                        handle.write(chunk)
                paths.append(target)
            async with self._ingest_lock:
                return await asyncio.to_thread(
                    ingest_paths,
                    self.store,
                    paths,
                    self.archive_root,
                    "api-upload",
                    "protected API upload",
                )
        finally:
            shutil.rmtree(temporary_root, ignore_errors=True)

    async def daily_loop(self) -> None:
        """Run once per configured local calendar day without a third-party scheduler."""
        while True:
            now = datetime.now(self.settings.timezone)
            hour, minute = (int(value) for value in self.settings.daily_ingest_time.split(":"))
            next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if next_run <= now:
                next_run += timedelta(days=1)
            await asyncio.sleep((next_run - now).total_seconds())
            try:
                await self.scan_incoming("scheduled daily scan")
            except Exception:  # pragma: no cover - must keep scheduler alive
                LOGGER.exception("daily incoming scan failed")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_environment()
    service = Service(settings)
    scheduler_task: asyncio.Task | None = None

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        nonlocal scheduler_task
        service.initialize()
        try:
            await service.scan_incoming("startup scan")
        except Exception:
            LOGGER.exception("startup incoming scan failed")
        scheduler_task = asyncio.create_task(service.daily_loop())
        try:
            yield
        finally:
            if scheduler_task:
                scheduler_task.cancel()
                try:
                    await scheduler_task
                except asyncio.CancelledError:
                    pass

    app = FastAPI(title="IG Trends", version="1.0.0", lifespan=lifespan)
    app.state.service = service

    def require_api_token(x_api_token: str | None = Header(default=None)) -> None:
        if not settings.api_token:
            raise HTTPException(status_code=503, detail="服務尚未設定 IG_TRENDS_API_TOKEN")
        if not x_api_token or not hmac.compare_digest(x_api_token, settings.api_token):
            raise HTTPException(status_code=401, detail="無效的 API Token")

    @app.get("/api/health")
    def health() -> dict:
        latest = service.store.latest_snapshot()
        return {
            "status": "ok",
            "latest_snapshot_at": latest["imported_at"] if latest else None,
            "daily_ingest_time": settings.daily_ingest_time,
            "timezone": str(settings.timezone),
        }

    @app.get("/api/v1/latest", dependencies=[Depends(require_api_token)])
    def latest() -> dict:
        return {"snapshot": service.store.latest_snapshot()}

    @app.get("/api/v1/trends", dependencies=[Depends(require_api_token)])
    def trends(limit: int = 90) -> dict:
        safe_limit = max(1, min(limit, 365))
        return {"snapshots": list(reversed(service.store.snapshot_history(safe_limit)))}

    @app.get("/api/v1/accounts", dependencies=[Depends(require_api_token)])
    def accounts(view: str = "not_following_back", query: str = "", limit: int = 500) -> dict:
        """Return one relationship list from the latest snapshot, without raw export data."""
        snapshot = service.store.latest_snapshot()
        if snapshot is None:
            return {"snapshot": None, "view": view, "accounts": []}
        followers = service.store.memberships_for(snapshot["id"], "followers")
        following = service.store.memberships_for(snapshot["id"], "following")
        views = {
            "mutual": followers & following,
            "not_following_back": following - followers,
            "not_followed_back": followers - following,
        }
        if view not in views:
            raise HTTPException(status_code=422, detail="view 必須為 mutual、not_following_back 或 not_followed_back")
        safe_limit = max(1, min(limit, 2000))
        normalized_query = query.casefold().strip()
        result = sorted(
            username for username in views[view]
            if not normalized_query or normalized_query in username
        )[:safe_limit]
        return {"snapshot": snapshot["id"], "view": view, "accounts": result}

    @app.post("/api/v1/ingest/scan", dependencies=[Depends(require_api_token)])
    async def scan() -> dict:
        return await service.scan_incoming("manual protected scan")

    @app.post("/api/v1/ingest/upload", dependencies=[Depends(require_api_token)])
    async def upload(files: list[UploadFile] = File(...)) -> dict:
        return await service.ingest_upload(files)

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        snapshot = service.store.latest_snapshot()
        history = list(reversed(service.store.snapshot_history(30)))
        metrics = snapshot["metrics"] if snapshot else {}
        cards = [
            ("粉絲", metrics.get("followers", 0)),
            ("追蹤中", metrics.get("following", 0)),
            ("雙向", metrics.get("mutual", 0)),
            ("未回追", metrics.get("not_following_back", 0)),
            ("尚未回追", metrics.get("not_followed_back", 0)),
        ]
        card_html = "".join(
            f"<section><small>{html.escape(label)}</small><strong>{value:,}</strong></section>"
            for label, value in cards
        )
        rows = "".join(
            "<tr>"
            f"<td>{html.escape(item['imported_at'])}</td>"
            f"<td>{item['metrics'].get('followers', 0):,}</td>"
            f"<td>{item['metrics'].get('following', 0):,}</td>"
            f"<td>{item['metrics'].get('mutual', 0):,}</td>"
            f"<td>{item['metrics'].get('not_following_back', 0):,}</td>"
            "</tr>"
            for item in reversed(history)
        )
        latest_label = html.escape(snapshot["imported_at"]) if snapshot else "尚未匯入資料"
        return f"""<!doctype html>
<html lang='zh-Hant'><meta charset='utf-8'><title>IG Trends</title>
<style>
body{{margin:0;background:#1e1e1e;color:#d4d4d4;font:16px -apple-system,sans-serif;padding:32px}}
main{{max-width:1100px;margin:auto}}h1{{margin:0 0 4px}}p{{color:#a0a0a0}}
.cards{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin:28px 0}}
section{{background:#252526;border:1px solid #3e3e42;border-radius:8px;padding:18px}}small{{display:block;color:#a0a0a0}}strong{{font-size:28px;display:block;margin-top:8px;color:#4fc1ff}}
table{{width:100%;border-collapse:collapse;background:#252526}}th,td{{padding:12px;text-align:left;border-bottom:1px solid #3e3e42}}th{{color:#b8bec9}}
@media(max-width:800px){{.cards{{grid-template-columns:repeat(2,1fr)}}body{{padding:16px}}}}
</style><main><h1>IG Trends</h1><p>最新快照：{latest_label}</p><div class='cards'>{card_html}</div>
<h2>最近 30 筆快照</h2><table><thead><tr><th>時間（UTC）</th><th>粉絲</th><th>追蹤</th><th>雙向</th><th>未回追</th></tr></thead><tbody>{rows}</tbody></table>
</main></html>"""

    return app


app = create_app()
