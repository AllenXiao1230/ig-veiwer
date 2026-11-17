"""Authenticate uploads before parsing and bound streamed multipart bodies."""

import hmac

from starlette.formparsers import MultiPartException
from starlette.responses import JSONResponse


class UploadGuardMiddleware:
    def __init__(self, app, api_token: str, max_bytes: int):
        self.app = app
        self.api_token = api_token.encode("utf-8")
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] != "/api/v1/ingest/upload" or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        token = headers.get(b"x-api-token", b"")
        if not self.api_token:
            await JSONResponse({"detail": "服務尚未設定 IG_TRENDS_API_TOKEN"}, 503)(scope, receive, send)
            return
        if not token or not hmac.compare_digest(token, self.api_token):
            await JSONResponse({"detail": "無效的 API Token"}, 401)(scope, receive, send)
            return
        try:
            content_length = int(headers.get(b"content-length", b"0"))
            if content_length < 0:
                raise ValueError()
        except ValueError:
            await JSONResponse({"detail": "Content-Length 格式錯誤"}, 400)(scope, receive, send)
            return
        if content_length > self.max_bytes:
            await JSONResponse({"detail": "上傳總量超過 250 MB 限制"}, 413)(scope, receive, send)
            return
        received = 0
        exceeded = False

        async def limited_receive():
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    exceeded = True
                    # The multipart parser closes its temporary files on this exception.
                    raise MultiPartException("上傳總量超過 250 MB 限制")
            return message

        async def limited_send(message):
            if exceeded and message["type"] == "http.response.start":
                message = {**message, "status": 413}
            await send(message)

        await self.app(scope, limited_receive, limited_send)
