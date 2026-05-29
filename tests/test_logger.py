import os
import shutil
import tempfile
import unittest

from _enigma_stubs import bootstrap
bootstrap()

from FBCChannelSpeedChange import logger


class LogRotationTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._log = os.path.join(self._tmpdir, "fbc_csc.log")
        self._orig = (logger.LOG_PATH, logger._MAX_BYTES, logger._BACKUP_COUNT)
        logger.LOG_PATH = self._log
        logger._MAX_BYTES = 200          # small so a couple of lines rotate
        logger._BACKUP_COUNT = 3

    def tearDown(self):
        logger.LOG_PATH, logger._MAX_BYTES, logger._BACKUP_COUNT = self._orig
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_rotation_creates_capped_backup_chain(self):
        # Each line is well over _MAX_BYTES/2, so writing many lines
        # forces repeated rotations.
        for _ in range(50):
            logger.info("y" * 100)
        self.assertTrue(os.path.exists(self._log))
        self.assertTrue(os.path.exists(self._log + ".1"))
        self.assertTrue(os.path.exists(self._log + ".2"))
        self.assertTrue(os.path.exists(self._log + ".3"))
        # The chain is capped at _BACKUP_COUNT; the oldest is dropped.
        self.assertFalse(os.path.exists(self._log + ".4"))

    def test_recent_content_survives_rotation(self):
        logger.info("MARKER_OLD")
        for _ in range(30):
            logger.info("z" * 100)
        logger.info("MARKER_NEW")
        recent = ""
        for p in (self._log, self._log + ".1"):
            if os.path.exists(p):
                with open(p) as fh:
                    recent += fh.read()
        # The latest line is always in the live log or the freshest
        # backup; delete-on-overflow would have lost it.
        self.assertIn("MARKER_NEW", recent)

    def test_robust_against_missing_intermediate_backup(self):
        # Pre-seed an inconsistent chain: current + .1 present, .2 gone.
        with open(self._log, "w") as fh:
            fh.write("a" * 300)
        with open(self._log + ".1", "w") as fh:
            fh.write("b" * 300)
        # A write triggers rotation; a missing intermediate must not raise.
        logger.info("c" * 100)
        self.assertTrue(os.path.exists(self._log))
        self.assertTrue(os.path.exists(self._log + ".1"))


if __name__ == "__main__":
    unittest.main()
