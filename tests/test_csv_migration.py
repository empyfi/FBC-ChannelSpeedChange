"""CSV header migration tests for the v0.4.0 schema change.

The 0.4.0 release adds a target_ref column. Existing installs have a
legacy CSV with a 4-column header and 4-column rows. _ensure_csv_header
detects that on first run after upgrade and rewrites the file in
place: header to the new shape, legacy rows padded with an empty
trailing field.
"""

import os
import tempfile
import unittest

from _enigma_stubs import bootstrap
bootstrap()

from FBCChannelSpeedChange import zap_interceptor as zi


class CsvMigrationTests(unittest.TestCase):

    def setUp(self):
        fd, self.path = tempfile.mkstemp(prefix="fbc_csc_csv_", suffix=".csv")
        os.close(fd)
        self._orig_path = zi._TIMING_CSV
        zi._TIMING_CSV = self.path

    def tearDown(self):
        zi._TIMING_CSV = self._orig_path
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def _read(self):
        with open(self.path, "r") as fh:
            return fh.read()

    def test_missing_file_gets_current_header(self):
        os.unlink(self.path)
        zi._ensure_csv_header()
        self.assertEqual(self._read(), "epoch,attr,result,delta_ms,target_ref\n")

    def test_current_header_left_untouched(self):
        sample = (
            "epoch,attr,result,delta_ms,target_ref\n"
            "1700000000,ext,EXT,100.0,1:0:19:A:B:C:D:0:0:0:\n"
        )
        with open(self.path, "w") as fh:
            fh.write(sample)
        zi._ensure_csv_header()
        self.assertEqual(self._read(), sample,
                         "current-shape CSV must not be rewritten")

    def test_legacy_header_migrated_in_place(self):
        legacy = (
            "epoch,attr,result,delta_ms\n"
            "1700000000,ext,EXT,100.0\n"
            "1700000001,zapDown,HIT,90.0\n"
        )
        with open(self.path, "w") as fh:
            fh.write(legacy)
        zi._ensure_csv_header()
        body = self._read()
        self.assertTrue(body.startswith("epoch,attr,result,delta_ms,target_ref\n"),
                        "new header must replace the legacy 4-column header")
        # All legacy rows padded with empty target_ref so the column
        # count is consistent across the whole file.
        for line in body.splitlines()[1:]:
            self.assertEqual(line.count(","), 4,
                             "every row must have 5 columns after migration")
            self.assertTrue(line.endswith(","),
                            "legacy rows are padded with an empty target_ref")

    def test_migration_is_idempotent(self):
        legacy = "epoch,attr,result,delta_ms\n1700000000,ext,EXT,100.0\n"
        with open(self.path, "w") as fh:
            fh.write(legacy)
        zi._ensure_csv_header()
        first_pass = self._read()
        zi._ensure_csv_header()
        self.assertEqual(self._read(), first_pass,
                         "running the migration twice must change nothing")

    def test_unrecognised_header_left_alone(self):
        # Defensive: a hand-edited or future-format CSV must not be
        # rewritten by mistake.
        weird = "something,else,entirely\n1,2,3\n"
        with open(self.path, "w") as fh:
            fh.write(weird)
        zi._ensure_csv_header()
        self.assertEqual(self._read(), weird,
                         "unrecognised header must survive untouched")


class CsvRotationTests(unittest.TestCase):
    """Size-capped rename-chain rotation for /tmp/fbc_csc_timing.csv,
    mirroring the logger.py pattern. Prevents the timing log from
    growing unbounded over weeks of hard zapping.
    """

    def setUp(self):
        import tempfile
        self._tmpdir = tempfile.mkdtemp()
        self._csv = os.path.join(self._tmpdir, "fbc_csc_timing.csv")
        self._orig = (zi._TIMING_CSV, zi._CSV_MAX_BYTES, zi._CSV_BACKUP_COUNT)
        zi._TIMING_CSV = self._csv
        zi._CSV_MAX_BYTES = 200       # small so a few rows force rotation
        zi._CSV_BACKUP_COUNT = 3

    def tearDown(self):
        import shutil
        zi._TIMING_CSV, zi._CSV_MAX_BYTES, zi._CSV_BACKUP_COUNT = self._orig
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_rotation_creates_capped_backup_chain(self):
        # Each row is well over _CSV_MAX_BYTES/2, so a handful of
        # rows force repeated rotations.
        for i in range(50):
            zi._emit_csv([i, "zapDown", "HIT", "100.0",
                          "1:0:1:A:B:C:D:0:0:0:padding"])
        self.assertTrue(os.path.exists(self._csv))
        self.assertTrue(os.path.exists(self._csv + ".1"))
        self.assertTrue(os.path.exists(self._csv + ".2"))
        self.assertTrue(os.path.exists(self._csv + ".3"))
        # Chain capped at _CSV_BACKUP_COUNT; oldest dropped.
        self.assertFalse(os.path.exists(self._csv + ".4"))

    def test_rotated_segments_carry_csv_header(self):
        # Every segment of the rotation chain must be self-describing
        # so off-box analysis tooling does not have to special-case
        # post-rotation segments.
        for i in range(20):
            zi._emit_csv([i, "zapDown", "HIT", "100.0",
                          "1:0:1:A:B:C:D:0:0:0:padding"])
        for p in (self._csv, self._csv + ".1"):
            if not os.path.exists(p):
                continue
            with open(p) as fh:
                first = fh.readline()
            self.assertEqual(first, zi._CSV_HEADER,
                             "%s must start with the canonical header" % p)

    def test_no_rotation_below_cap(self):
        # A tiny file is left alone.
        zi._CSV_MAX_BYTES = 100000
        zi._ensure_csv_header()
        zi._emit_csv([1, "zapDown", "HIT", "100.0", ""])
        self.assertFalse(os.path.exists(self._csv + ".1"),
                         "no rotation expected below the cap")


if __name__ == "__main__":
    unittest.main()
