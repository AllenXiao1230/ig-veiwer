import json
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication

from igviewer.data import (
    KEY_FOLLOWERS,
    KEY_FOLLOWING,
    KEY_MUTUAL,
    KEY_NOT_FOLLOWED_BACK,
    KEY_NOT_FOLLOWING_BACK,
    build_insights,
    dataset_name_for_path,
    import_files,
)
from igviewer.storage import append_snapshot, compare_with_previous, load_valid_cache, save_cache
from igviewer.models import RecordFilterProxyModel, RecordTableModel


class ImportDataTests(unittest.TestCase):
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
        cls.application = QCoreApplication.instance() or QCoreApplication([])

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


if __name__ == "__main__":
    unittest.main()
