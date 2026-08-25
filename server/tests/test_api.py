import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo


FASTAPI_AVAILABLE = importlib.util.find_spec("fastapi") is not None


@unittest.skipUnless(FASTAPI_AVAILABLE, "install server/requirements-dev.txt to run API tests")
class ApiTests(unittest.TestCase):
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
