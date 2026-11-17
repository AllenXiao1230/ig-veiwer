import csv
from pathlib import Path
import tempfile
import unittest

from igviewer.exporting import export_records


class CsvExportTests(unittest.TestCase):
    def test_csv_preserves_unicode_and_protects_formulas(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "export.csv"
            export_records(target, [("alice", "王小明", ""), ("=1+1", " +SUM(1,2)", "")])
            with target.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.reader(handle))
            self.assertEqual(rows[1], ["alice", "王小明", ""])
            self.assertEqual(rows[2], ["'=1+1", "' +SUM(1,2)", ""])

    def test_failed_export_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "export.csv"
            target.write_text("existing", encoding="utf-8")
            def broken_records():
                yield ("alice", "", "")
                raise OSError("synthetic write failure")
            with self.assertRaises(OSError):
                export_records(target, broken_records())
            self.assertEqual(target.read_text(encoding="utf-8"), "existing")
            self.assertEqual(list(Path(temporary).glob("*.tmp")), [])
