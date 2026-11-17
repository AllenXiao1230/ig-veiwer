import json
import os
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from igviewer.data import (
    KEY_FOLLOWERS,
    KEY_FOLLOWING,
    KEY_MUTUAL,
    KEY_NOT_FOLLOWED_BACK,
    KEY_NOT_FOLLOWING_BACK,
    build_insights,
    dataset_name_for_path,
    discover_export_files,
    import_files,
)
from igviewer.storage import append_snapshot, compare_with_previous, load_snapshots, load_valid_cache, save_cache, source_fingerprint
from igviewer.models import RecordFilterProxyModel, RecordTableModel


class ImportDataTests(unittest.TestCase):
    def test_discovery_supports_uppercase_extensions_and_ignores_hidden_staging(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            visible = root / "followers_1.JSON"
            visible.write_text("[]", encoding="utf-8")
            hidden = root / ".gdrive-stage"
            hidden.mkdir()
            (hidden / "following.json").write_text("[]", encoding="utf-8")
            self.assertEqual(discover_export_files(root), [visible])

    def test_empty_relationship_exports_are_valid_and_analyzed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "followers_1.json").write_text("[]", encoding="utf-8")
            (root / "following.json").write_text(json.dumps({"relationships_following": [
                {"title": "alice", "string_list_data": [{"value": "alice"}]},
            ]}), encoding="utf-8")
            imported = import_files(root.glob("*.json"))
            self.assertEqual(imported.files_loaded, 2)
            self.assertEqual(imported.issues, [])
            insights = build_insights(imported.datasets)
            self.assertEqual(insights[KEY_FOLLOWERS], [])
            self.assertEqual([record[0] for record in insights[KEY_NOT_FOLLOWING_BACK]], ["alice"])
            self.assertEqual(insights[KEY_MUTUAL], [])

    def test_corrupt_required_shard_is_blocking_and_unknown_files_are_skipped(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "followers_1.json").write_text("[]", encoding="utf-8")
            (root / "followers_2.json").write_text("{", encoding="utf-8")
            (root / "unknown.json").write_text('[{"title":"alice","string_list_data":[{"value":"alice"}]}]', encoding="utf-8")
            result = import_files(root.glob("*.json"))
            self.assertTrue(result.has_blocking_issues)
            self.assertEqual(result.datasets, {KEY_FOLLOWERS: []})
            self.assertEqual(len(result.issues), 2)

    def test_cache_detects_content_changes_with_preserved_metadata_and_new_shards(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"IG_VIEWER_DATA_DIR": temporary}):
            root = Path(temporary)
            sources = root / "export"
            sources.mkdir()
            source = sources / "followers_1.json"
            source.write_text("[]", encoding="utf-8")
            save_cache({KEY_FOLLOWERS: []}, [source], directory=sources)
            stat = source.stat()
            source.write_text("{}", encoding="utf-8")
            os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            self.assertIsNone(load_valid_cache().datasets)
            save_cache({KEY_FOLLOWERS: []}, [source], directory=sources)
            (sources / "followers_2.json").write_text("[]", encoding="utf-8")
            self.assertIsNone(load_valid_cache().datasets)

    def test_malformed_storage_recovers_without_exceptions(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"IG_VIEWER_DATA_DIR": temporary}):
            root = Path(temporary)
            source = root / "followers_1.json"
            source.write_text("[]", encoding="utf-8")
            malformed_cache = {"version": 2, "sources": [source_fingerprint(source)], "datasets": {"test": [[None, "", ""]]}}
            for payload in ([], None, malformed_cache):
                (root / "cache.json").write_text(json.dumps(payload), encoding="utf-8")
                self.assertIsNone(load_valid_cache().datasets)
            for payload in ([], {"version": 1, "snapshots": [None]}, {"version": 1, "snapshots": [{"created_at": "invalid"}]}):
                (root / "snapshots.json").write_text(json.dumps(payload), encoding="utf-8")
                self.assertEqual(load_snapshots(), [])

    def test_concurrent_snapshot_updates_and_cache_writes_preserve_valid_files(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"IG_VIEWER_DATA_DIR": temporary}):
            source = Path(temporary) / "followers_1.json"
            source.write_text("[]", encoding="utf-8")
            def persist(index):
                datasets = build_insights({KEY_FOLLOWERS: [(f"user_{index}", "", "")], KEY_FOLLOWING: []})
                save_cache(datasets, [source])
                return append_snapshot(datasets)
            with ThreadPoolExecutor(max_workers=4) as executor:
                results = list(executor.map(persist, range(4)))
            self.assertTrue(all(created for _, created in results))
            snapshots = load_snapshots()
            self.assertEqual(len(snapshots), 4)
            self.assertEqual(len({snapshot["signature"] for snapshot in snapshots}), 4)
            self.assertIsNotNone(load_valid_cache().datasets)
            self.assertEqual(list(Path(temporary).glob("*.tmp")), [])

    def test_partial_cached_export_does_not_reuse_a_complete_snapshot(self):
        from igviewer.workers import FileLoaderWorker

        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"IG_VIEWER_DATA_DIR": temporary}):
            root = Path(temporary)
            source = root / "followers_1.json"
            source.write_text("[]", encoding="utf-8")
            partial = {KEY_FOLLOWERS: [("alice", "", "")]}
            append_snapshot(build_insights({**partial, KEY_FOLLOWING: []}))
            save_cache(partial, [source])
            worker = FileLoaderWorker(from_cache=True)
            loaded = worker._load_cache()
            self.assertIsNone(loaded.snapshot)

    def test_follower_shards_are_merged_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "followers_1.json").write_text(json.dumps([
                {"title": "alice", "string_list_data": [{"value": "alice", "timestamp": 1}]},
                {"title": "duplicate", "string_list_data": [{"value": "duplicate", "timestamp": 1}]},
            ]))
            (root / "followers_2.json").write_text(json.dumps([
                {"title": "Alice", "string_list_data": [{"value": "Alice", "timestamp": 2}]},
                {"title": "bob", "string_list_data": [{"value": "bob", "timestamp": 3}]},
            ]))
            result = import_files(sorted(root.glob("*.json")))
            self.assertEqual(dataset_name_for_path(root / "followers_2.json"), KEY_FOLLOWERS)
            self.assertEqual([row[0] for row in result.datasets[KEY_FOLLOWERS]], ["alice", "duplicate", "bob"])
            self.assertEqual(result.duplicate_count, 1)

    def test_relationship_views_include_both_directions(self):
        datasets = {
            KEY_FOLLOWERS: [("alice", "", ""), ("bob", "", "")],
            KEY_FOLLOWING: [("alice", "", ""), ("carol", "", "")],
        }
        result = build_insights(datasets)
        self.assertEqual([row[0] for row in result[KEY_MUTUAL]], ["alice"])
        self.assertEqual([row[0] for row in result[KEY_NOT_FOLLOWING_BACK]], ["carol"])
        self.assertEqual([row[0] for row in result[KEY_NOT_FOLLOWED_BACK]], ["bob"])

    def test_cache_requires_unchanged_source_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "followers_1.json"
            source.write_text("[]")
            with patch.dict("os.environ", {"IG_VIEWER_DATA_DIR": str(root / "app")}, clear=False):
                save_cache({KEY_FOLLOWERS: [("alice", "", "")]}, [source])
                self.assertIsNotNone(load_valid_cache().datasets)
                source.write_text("[ ]")
                self.assertIsNone(load_valid_cache().datasets)

    def test_identical_snapshot_is_not_added_twice(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict("os.environ", {"IG_VIEWER_DATA_DIR": str(Path(temporary) / "app")}, clear=False):
                datasets = build_insights({
                    KEY_FOLLOWERS: [("alice", "", "")],
                    KEY_FOLLOWING: [("alice", "", "")],
                })
                _, created = append_snapshot(datasets)
                self.assertTrue(created)
                _, created = append_snapshot(datasets)
                self.assertFalse(created)

    def test_snapshot_comparison_reports_membership_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict("os.environ", {"IG_VIEWER_DATA_DIR": str(Path(temporary) / "app")}, clear=False):
                first = build_insights({
                    KEY_FOLLOWERS: [("alice", "", "")],
                    KEY_FOLLOWING: [("alice", "", "")],
                })
                append_snapshot(first)
                second = build_insights({
                    KEY_FOLLOWERS: [("alice", "", ""), ("bob", "", "")],
                    KEY_FOLLOWING: [("alice", "", ""), ("carol", "", "")],
                })
                snapshot, created = append_snapshot(second)
                self.assertTrue(created)
                self.assertEqual(compare_with_previous(snapshot), {
                    "new_followers": 1,
                    "lost_followers": 0,
                    "new_following": 1,
                    "unfollowed": 0,
                })


class RecordTableModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QCoreApplication.instance() or QApplication([])

    def setUp(self):
        self.model = RecordTableModel()
        self.model.set_records([
            ("alice", "Alice", "2026-08-01 09:00:00"),
            ("bob", "Bob", "2026-08-03 09:00:00"),
            ("carol", "", ""),
        ])
        self.proxy = RecordFilterProxyModel()
        self.proxy.setSourceModel(self.model)

    def test_search_and_date_filter_without_rebuilding_source_records(self):
        self.proxy.set_filters("bo", False, None, None)
        self.assertEqual(self.proxy.rowCount(), 1)
        self.assertEqual(self.proxy.filtered_records(), [("bob", "Bob", "2026-08-03 09:00:00")])

        self.proxy.set_filters("", True, datetime(2026, 8, 2).date(), datetime(2026, 8, 4).date())
        self.assertEqual(self.proxy.filtered_records(), [("bob", "Bob", "2026-08-03 09:00:00")])
        self.assertEqual(self.model.rowCount(), 3)

    def test_invalid_date_range_hides_records_without_mutating_model(self):
        self.proxy.set_filters("", True, datetime(2026, 8, 4).date(), datetime(2026, 8, 2).date())
        self.assertEqual(self.proxy.rowCount(), 0)
        self.assertEqual(self.model.rowCount(), 3)

    def test_filter_changes_preserve_sort_and_accept_new_matches(self):
        from PySide6.QtCore import Qt

        self.proxy.sort(0, Qt.SortOrder.DescendingOrder)
        self.proxy.set_filters("bob", False, None, None)
        self.assertEqual([record[0] for record in self.proxy.filtered_records()], ["bob"])
        self.proxy.set_filters("", False, None, None)
        self.assertEqual([record[0] for record in self.proxy.filtered_records()], ["carol", "bob", "alice"])


if __name__ == "__main__":
    unittest.main()
