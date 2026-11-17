import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from igviewer.data import ImportResult
from main import IGMultiFileDashboard


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {"IG_VIEWER_DATA_DIR": self.temporary.name})
        self.environment.start()
        self.window = IGMultiFileDashboard()
        self.window.load_profile_in_browser = lambda username: None
        self.wait_for_worker()

    def tearDown(self):
        self.window.close()
        self.wait_for_worker()
        self.window.deleteLater()
        self.application.processEvents()
        self.environment.stop()
        self.temporary.cleanup()

    def wait_for_worker(self):
        deadline = time.monotonic() + 5
        while self.window.worker is not None and time.monotonic() < deadline:
            self.application.processEvents()
            time.sleep(0.005)
        self.assertIsNone(self.window.worker)

    def test_closing_during_import_waits_for_thread(self):
        def slow_import(*args):
            time.sleep(0.1)
            return ImportResult()

        self.window.show()
        with patch("igviewer.workers.import_files", slow_import):
            self.window._start_import([Path("synthetic.json")], "test")
            self.window.close()
            self.assertTrue(self.window._closing)
            self.wait_for_worker()
            self.application.processEvents()
            self.assertFalse(self.window.isVisible())

    def test_filtered_batches_continue_after_previously_opened_accounts(self):
        self.window.datasets = {"test": [(f"user_{index}", "", "") for index in range(10)]}
        self.window.update_sidebar()
        self.window.open_scope.setCurrentIndex(1)
        first = self.window._usernames_for_open_scope(5)
        self.window.opened_usernames.update(first)
        second = self.window._usernames_for_open_scope(5)
        self.assertEqual(first, [f"user_{index}" for index in range(5)])
        self.assertEqual(second, [f"user_{index}" for index in range(5, 10)])

    def test_selection_and_filter_changes_do_not_read_snapshot_history(self):
        with patch("main.load_snapshots", side_effect=AssertionError("unexpected disk read")):
            self.window.filter_data()
            self.window._update_action_availability()

    def test_cancelling_batch_stops_pending_timer(self):
        self.window._batch_opening = True
        self.window._open_queue = ["alice"]
        self.window.batch_timer.start()
        self.window.cancel_opening_profiles()
        self.assertFalse(self.window.batch_timer.isActive())
        self.assertEqual(self.window._open_queue, [])

    def test_collapsed_preview_defers_network_navigation(self):
        preview = self.window.preview_panel
        self.window.show()
        self.window.action_preview.setChecked(False)
        with patch.object(preview, "navigate_preview") as navigate:
            preview.load_profile_in_browser("alice")
            navigate.assert_not_called()
            self.window.action_preview.setChecked(True)
            self.application.processEvents()
            navigate.assert_called_once()
