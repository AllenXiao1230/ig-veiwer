import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo


FASTAPI_AVAILABLE = importlib.util.find_spec("fastapi") is not None


@unittest.skipUnless(FASTAPI_AVAILABLE, "install server/requirements-dev.txt to run API tests")
class ApiTests(unittest.TestCase):
    @staticmethod
    def settings(root):
        from server.app.config import Settings

        return Settings(root / "data", root / "incoming", "test-token", ZoneInfo("Asia/Taipei"), "03:30", 90)

    def test_upload_authentication_and_limit_run_before_multipart_parsing(self):
        from fastapi.testclient import TestClient
        from starlette.datastructures import UploadFile
        from server.app.main import create_app

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch("server.app.main.MAX_UPLOAD_BYTES", 100):
                app = create_app(self.settings(root))
            with TestClient(app) as client, patch.object(UploadFile, "write", side_effect=AssertionError("multipart body was parsed")):
                files = {"files": ("followers_1.json", b"x" * 1000, "application/json")}
                self.assertEqual(client.post("/api/v1/ingest/upload", files=files).status_code, 401)
                self.assertEqual(client.post("/api/v1/ingest/upload", headers={"X-API-Token": "test-token"}, files=files).status_code, 413)
                response = client.get("/api/v1/accounts?view=invalid", headers={"X-API-Token": "test-token"})
                self.assertEqual(response.status_code, 422)

    def test_chunked_upload_limit_closes_multipart_temporary_files(self):
        import asyncio
        from server.app.main import create_app

        async def request(app):
            prefix = b'--boundary\r\nContent-Disposition: form-data; name="files"; filename="followers_1.json"\r\nContent-Type: application/json\r\n\r\n'
            messages = iter([
                {"type": "http.request", "body": prefix + b"x" * 300, "more_body": True},
                {"type": "http.request", "body": b"x" * 2000 + b"\r\n--boundary--\r\n", "more_body": False},
            ])
            responses = []
            async def receive():
                return next(messages)
            async def send(message):
                responses.append(message)
            scope = {
                "type": "http", "asgi": {"version": "3.0"}, "method": "POST",
                "path": "/api/v1/ingest/upload", "raw_path": b"/api/v1/ingest/upload",
                "root_path": "", "query_string": b"", "scheme": "http", "http_version": "1.1",
                "server": ("test", 80), "client": ("test", 123),
                "headers": [(b"x-api-token", b"test-token"), (b"content-type", b"multipart/form-data; boundary=boundary")],
            }
            await app(scope, receive, send)
            return responses

        with tempfile.TemporaryDirectory() as temporary:
            with patch("server.app.main.MAX_UPLOAD_BYTES", 1000):
                app = create_app(self.settings(Path(temporary)))
            opened = []
            original_spool = tempfile.SpooledTemporaryFile
            def spool(*args, **kwargs):
                handle = original_spool(*args, **kwargs)
                opened.append(handle)
                return handle
            with patch("starlette.formparsers.SpooledTemporaryFile", spool):
                responses = asyncio.run(request(app))
            self.assertEqual(responses[0]["status"], 413)
            self.assertEqual([handle.closed for handle in opened], [True])

    def test_cancelling_ingestion_keeps_lock_until_background_operation_finishes(self):
        import asyncio
        import threading
        from server.app.main import Service

        async def exercise(service):
            started = threading.Event()
            release = threading.Event()
            calls = []
            def operation(_uploads):
                calls.append(True)
                started.set()
                release.wait(2)
                return {"status": "unchanged"}
            with patch.object(service, "_ingest_upload", operation):
                first = asyncio.create_task(service.ingest_upload([]))
                self.assertTrue(await asyncio.to_thread(started.wait, 1))
                first.cancel()
                second = asyncio.create_task(service.ingest_upload([]))
                await asyncio.sleep(0.02)
                self.assertEqual(len(calls), 1)
                release.set()
                results = await asyncio.gather(first, second, return_exceptions=True)
                self.assertIsInstance(results[0], asyncio.CancelledError)
                self.assertEqual(results[1]["status"], "unchanged")
        with tempfile.TemporaryDirectory() as temporary:
            service = Service(self.settings(Path(temporary)))
            asyncio.run(exercise(service))

    def test_upload_trends_and_account_views(self):
        from fastapi.testclient import TestClient

        from server.app.config import Settings
        from server.app.main import create_app

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = Settings(
                data_dir=root / "data",
                incoming_dir=root / "incoming",
                api_token="test-token",
                timezone=ZoneInfo("Asia/Taipei"),
                daily_ingest_time="03:30",
                archive_retention_days=90,
            )
            with TestClient(create_app(settings)) as client:
                files = [
                    ("files", ("followers_1.json", json.dumps([
                        {"title": "alice", "string_list_data": [{"value": "alice", "timestamp": 1}]},
                        {"title": "bob", "string_list_data": [{"value": "bob", "timestamp": 1}]},
                    ]), "application/json")),
                    ("files", ("following.json", json.dumps({"relationships_following": [
                        {"title": "alice", "string_list_data": [{"value": "alice", "timestamp": 1}]},
                        {"title": "carol", "string_list_data": [{"value": "carol", "timestamp": 1}]},
                    ]}), "application/json")),
                ]
                response = client.post(
                    "/api/v1/ingest/upload", headers={"X-API-Token": "test-token"}, files=files
                )
                self.assertEqual(response.status_code, 200, response.text)
                accounts = client.get(
                    "/api/v1/accounts?view=not_following_back",
                    headers={"X-API-Token": "test-token"},
                )
                self.assertEqual(accounts.status_code, 200, accounts.text)
                self.assertEqual(accounts.json()["accounts"], ["carol"])


if __name__ == "__main__":
    unittest.main()
