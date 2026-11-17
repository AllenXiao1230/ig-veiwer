from pathlib import Path
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from igviewer.data import build_insights
from server.app.ingest import archive_sources, fingerprint_files, ingest_paths, json_files_in
from server.app.store import TrendStore


class IngestTests(unittest.TestCase):
    def test_current_export_creates_one_snapshot_then_deduplicates(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            source_paths = self.write_export(work / "export", ["alice", "bob"], ["alice", "carol"])
            store = TrendStore(work / "db.sqlite3")
            store.initialize()
            first = ingest_paths(store, source_paths, work / "archives", "test", "fixture")
            self.assertEqual(first["status"], "created")
            self.assertEqual(first["snapshot"]["metrics"], {
                "followers": 2,
                "following": 2,
                "mutual": 1,
                "not_following_back": 1,
                "not_followed_back": 1,
            })
            second = ingest_paths(store, source_paths, work / "archives", "test", "fixture")
            self.assertEqual(second["status"], "unchanged")

    @staticmethod
    def write_export(root, followers, following):
        root.mkdir(parents=True, exist_ok=True)
        for name, usernames in [("followers_1.json", followers), ("following.json", following)]:
            (root / name).write_text(json.dumps([
                {"title": name, "string_list_data": [{"value": name}]} for name in usernames
            ]), encoding="utf-8")
        return sorted(root.glob("*.json"))

    def test_scanner_reads_current_and_excludes_sync_staging(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            current = self.write_export(root / "current", ["alice"], ["alice"])
            self.write_export(root / ".gdrive-stage", ["bob"], ["bob"])
            self.write_export(root / ".gdrive-previous", ["old"], ["old"])
            self.assertEqual(json_files_in(root), current)
            self.assertEqual(json_files_in(root / ".missing"), [])
            for path in current:
                path.unlink()
            (root / "current").rmdir()
            self.assertEqual(json_files_in(root), [])

    def test_returning_to_previous_state_creates_observation_and_reuses_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = TrendStore(root / "db.sqlite3")
            store.initialize()
            a = self.write_export(root / "a", ["alice"], ["alice"])
            b = self.write_export(root / "b", ["bob"], ["bob"])
            results = [ingest_paths(store, paths, root / "archives", "test", "test") for paths in (a, b, a)]
            self.assertEqual([result["status"] for result in results], ["created"] * 3)
            self.assertEqual(store.latest_snapshot()["id"], results[2]["snapshot"]["id"])
            self.assertEqual(results[0]["snapshot"]["archive_path"], results[2]["snapshot"]["archive_path"])
            self.assertEqual(len(list((root / "archives").iterdir())), 2)
            self.assertEqual(results[2]["delta"]["new_followers"], 1)

    def test_upload_order_does_not_change_fingerprint(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = self.write_export(root / "a", ["alice"], ["bob"])
            reordered = []
            for index, path in enumerate(reversed(paths)):
                target = root / "b" / str(index) / path.name
                target.parent.mkdir(parents=True)
                target.write_bytes(path.read_bytes())
                reordered.append(target)
            self.assertEqual(fingerprint_files(paths), fingerprint_files(reordered))

    def test_empty_export_is_accepted_and_broken_shard_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = TrendStore(root / "db.sqlite3")
            store.initialize()
            paths = self.write_export(root / "export", [], ["alice"])
            first = ingest_paths(store, paths, root / "archives", "test", "empty")
            self.assertEqual(first["status"], "created")
            self.assertEqual(first["snapshot"]["metrics"]["not_following_back"], 1)
            broken = root / "export" / "followers_2.json"
            broken.write_text("{", encoding="utf-8")
            second = ingest_paths(store, paths + [broken], root / "archives", "test", "broken")
            self.assertEqual(second["status"], "rejected")
            self.assertEqual(len(store.snapshot_history()), 1)

    def test_archive_and_memberships_use_the_same_staged_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = TrendStore(root / "db.sqlite3")
            store.initialize()
            originals = self.write_export(root / "export", ["alice"], ["alice"])
            def update_originals(staged):
                self.write_export(root / "export", ["bob"], ["bob"])
                return fingerprint_files(staged)
            with patch("server.app.ingest.fingerprint_files", update_originals):
                result = ingest_paths(store, originals, root / "archives", "test", "consistent")
            self.assertEqual(store.memberships_for(result["snapshot"]["id"], "followers"), {"alice"})
            archive = Path(result["snapshot"]["archive_path"])
            self.assertIn('"alice"', next(archive.glob("*followers_1.json")).read_text(encoding="utf-8"))

    def test_failed_database_write_removes_unreferenced_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = TrendStore(root / "db.sqlite3")
            store.initialize()
            paths = self.write_export(root / "export", ["alice"], ["alice"])
            with patch.object(store, "add_snapshot", side_effect=RuntimeError("synthetic database failure")):
                with self.assertRaises(RuntimeError):
                    ingest_paths(store, paths, root / "archives", "test", "failure")
            self.assertEqual(list((root / "archives").iterdir()), [])

    def test_legacy_database_migration_preserves_memberships_and_enforces_foreign_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "db.sqlite3"
            paths = self.write_export(root / "source", ["alice"], [])
            archive = archive_sources(paths, root / "archives", "legacy")
            with sqlite3.connect(database) as connection:
                connection.executescript("""
                    CREATE TABLE snapshots (id INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE,
                        imported_at TEXT NOT NULL, source_kind TEXT NOT NULL, source_label TEXT NOT NULL,
                        archive_path TEXT NOT NULL, metrics_json TEXT NOT NULL);
                    CREATE TABLE memberships (snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
                        relationship TEXT NOT NULL, username TEXT NOT NULL,
                        PRIMARY KEY(snapshot_id, relationship, username));
                    INSERT INTO snapshots VALUES(1, 'old', '2026-01-01', 'test', 'test', '', '{}');
                    INSERT INTO memberships VALUES(1, 'followers', 'alice');
                """)
                connection.execute("UPDATE snapshots SET archive_path = ? WHERE id = 1", (str(archive),))
            connection.close()
            store = TrendStore(database)
            store.initialize()
            self.assertEqual(store.memberships_for(1, "followers"), {"alice"})
            self.assertEqual(store.latest_snapshot()["fingerprint"], fingerprint_files(paths))
            with store.connection() as connection:
                self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("INSERT INTO memberships VALUES(999, 'followers', 'invalid')")
            store.backup(root / "backup.sqlite3")
            self.assertEqual(TrendStore(root / "backup.sqlite3").memberships_for(1, "followers"), {"alice"})

    def test_sql_account_views_support_literal_search_and_pagination(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = TrendStore(root / "db.sqlite3")
            store.initialize()
            datasets = build_insights({"粉絲": [("alice", "", ""), ("bob", "", "")], "追蹤中": [("alice", "", ""), ("carol", "", ""), ("dave", "", "")]})
            snapshot, _, _ = store.add_snapshot("test", datasets, "test", "test", root)
            self.assertEqual(store.accounts_for(snapshot["id"], "mutual", "ALI", 10), ["alice"])
            self.assertEqual(store.accounts_for(snapshot["id"], "not_following_back", "", 1, 1), ["dave"])
            self.assertEqual(store.accounts_for(snapshot["id"], "not_followed_back", "%", 10), [])

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
