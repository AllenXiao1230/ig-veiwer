from pathlib import Path
import tempfile
import unittest

from server.app.ingest import ingest_paths
from server.app.store import TrendStore


class IngestTests(unittest.TestCase):
    def test_current_export_creates_one_snapshot_then_deduplicates(self):
        root = Path(__file__).parents[2]
        source_paths = sorted((root / "data").glob("*.json"))
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            store = TrendStore(work / "db.sqlite3")
            store.initialize()
            first = ingest_paths(store, source_paths, work / "archives", "test", "fixture")
            self.assertEqual(first["status"], "created")
            self.assertEqual(first["snapshot"]["metrics"], {
                "followers": 1455,
                "following": 3880,
                "mutual": 1058,
                "not_following_back": 2822,
                "not_followed_back": 397,
            })
            second = ingest_paths(store, source_paths, work / "archives", "test", "fixture")
            self.assertEqual(second["status"], "unchanged")

    def test_second_export_produces_membership_delta(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            store = TrendStore(work / "db.sqlite3")
            store.initialize()
            first_dir = work / "first"
            second_dir = work / "second"
            first_dir.mkdir()
            second_dir.mkdir()
            (first_dir / "followers_1.json").write_text(
                '[{"title":"alice","string_list_data":[{"value":"alice","timestamp":1}]}]'
            )
            (first_dir / "following.json").write_text(
                '{"relationships_following":[{"title":"alice","string_list_data":[{"value":"alice","timestamp":1}]}]}'
            )
            first = ingest_paths(store, list(first_dir.glob("*.json")), work / "archives", "test", "first")
            self.assertEqual(first["status"], "created")
            (second_dir / "followers_1.json").write_text(
                '[{"title":"alice","string_list_data":[{"value":"alice","timestamp":1}]},{"title":"bob","string_list_data":[{"value":"bob","timestamp":1}]}]'
            )
            (second_dir / "following.json").write_text(
                '{"relationships_following":[{"title":"alice","string_list_data":[{"value":"alice","timestamp":1}]},{"title":"carol","string_list_data":[{"value":"carol","timestamp":1}]}]}'
            )
            second = ingest_paths(store, list(second_dir.glob("*.json")), work / "archives", "test", "second")
            self.assertEqual(second["delta"], {
                "new_followers": 1,
                "lost_followers": 0,
                "new_following": 1,
                "unfollowed": 0,
            })


if __name__ == "__main__":
    unittest.main()
