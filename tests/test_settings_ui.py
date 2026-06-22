"""Tests for the FCC-Extender presence indicator.

Only the classifier helper is unit-tested - it takes opkg-status
file content as a string and returns the user-facing label. The
full _detect_fccextender_status() wrapper does file I/O against
/var/lib/opkg/status which is enigma2-on-box only.

The settings-screen injection itself runs against Screens.Setup
which is not importable off-box; on-box smoke is the right place
to verify the row actually appears in the Setup screen.
"""

import unittest

from _enigma_stubs import bootstrap
bootstrap()

from FBCChannelSpeedChange.settings_ui import (
    _classify_fccextender_content,
    _parse_fccextender_status,
    _fccextender_installed,
)
from FBCChannelSpeedChange import settings_ui


# A small slice of a real /var/lib/opkg/status file. Each package
# section starts with "Package:" and is separated by a blank line.
_OPKG_STATUS_SAMPLE_WITHOUT = """\
Package: enigma2
Version: 7.6.0
Status: install ok installed

Package: enigma2-plugin-extensions-fbc-channelspeedchange
Version: 0.5.0
Status: install ok installed

Package: openssh
Version: 9.6
Status: install ok installed
"""


_OPKG_STATUS_SAMPLE_VTI = """\
Package: enigma2
Version: 7.6.0
Status: install ok installed

Package: enigma2-plugin-extensions-vti-fccextender
Version: 0.3-Beta
Status: install ok installed
"""


_OPKG_STATUS_SAMPLE_OPENATV = """\
Package: enigma2-plugin-extensions-fccextender
Version: 0.4
Status: install ok installed
"""


_OPKG_STATUS_SAMPLE_HYPHEN = """\
Package: enigma2-plugin-extensions-fcc-extender
Version: 1.0
Status: install ok installed
"""


class FccExtenderClassifierTests(unittest.TestCase):

    def test_absent_returns_not_detected(self):
        label = _classify_fccextender_content(_OPKG_STATUS_SAMPLE_WITHOUT)
        self.assertIn("not detected", label)

    def test_vti_package_name_recognised(self):
        # The VTi build's exact package name - the OpenATV port will
        # most likely keep the same fccextender stem.
        label = _classify_fccextender_content(_OPKG_STATUS_SAMPLE_VTI)
        self.assertIn("installed", label)
        self.assertNotIn("not detected", label)

    def test_vti_version_extracted(self):
        # When a Package block carries a Version: line, the version is
        # folded into the rendered label.
        label = _classify_fccextender_content(_OPKG_STATUS_SAMPLE_VTI)
        self.assertIn("0.3-Beta", label)

    def test_openatv_stem_recognised(self):
        # Anticipated OpenATV-flavoured package name.
        label = _classify_fccextender_content(_OPKG_STATUS_SAMPLE_OPENATV)
        self.assertIn("installed", label)

    def test_openatv_version_extracted(self):
        label = _classify_fccextender_content(_OPKG_STATUS_SAMPLE_OPENATV)
        self.assertIn("0.4", label)

    def test_hyphen_variant_recognised(self):
        # Whether Oberhesse uses "fccextender" or "fcc-extender" as the
        # stem, the substring match catches both.
        label = _classify_fccextender_content(_OPKG_STATUS_SAMPLE_HYPHEN)
        self.assertIn("installed", label)

    def test_missing_version_line_falls_back_to_bare_label(self):
        # If the block has no Version: line (unusual but defensive),
        # the label drops the version suffix rather than crashing.
        sample = "Package: enigma2-plugin-extensions-fccextender\nStatus: install ok installed\n"
        label = _classify_fccextender_content(sample)
        self.assertIn("installed", label)
        self.assertNotIn("(v", label)

    def test_empty_file_returns_not_detected(self):
        label = _classify_fccextender_content("")
        self.assertIn("not detected", label)

    def test_case_insensitive_match(self):
        # opkg status normally writes the canonical lowercase package
        # name, but a defensive check against future opkg variants
        # that might mixed-case the name is cheap.
        label = _classify_fccextender_content("Package: FCCExtender\n")
        self.assertIn("installed", label)


class FccExtenderParserTests(unittest.TestCase):
    """Low-level (state, version) parse exposed for the yellow-button
    gate. The label classifier already covers the formatting path; the
    parser tests pin the data shape so the bool-detector and any future
    consumer share the same contract.
    """

    def test_absent_returns_not_detected_none(self):
        self.assertEqual(
            _parse_fccextender_status(_OPKG_STATUS_SAMPLE_WITHOUT),
            ("not_detected", None),
        )

    def test_present_returns_installed_with_version(self):
        self.assertEqual(
            _parse_fccextender_status(_OPKG_STATUS_SAMPLE_OPENATV),
            ("installed", "0.4"),
        )

    def test_present_without_version_returns_installed_none(self):
        sample = ("Package: enigma2-plugin-extensions-fccextender\n"
                  "Status: install ok installed\n")
        self.assertEqual(
            _parse_fccextender_status(sample),
            ("installed", None),
        )


class FccExtenderInstalledDetectorTests(unittest.TestCase):
    """Bool-detector used by the yellow-button gate. Drives the open()
    call by injecting a fake module-level path.
    """

    def setUp(self):
        self._original_path = settings_ui._OPKG_STATUS_PATH

    def tearDown(self):
        settings_ui._OPKG_STATUS_PATH = self._original_path

    def _write_status_file(self, content):
        import os
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".opkg-status")
        os.close(fd)
        with open(path, "w") as fh:
            fh.write(content)
        settings_ui._OPKG_STATUS_PATH = path
        self.addCleanup(os.unlink, path)

    def test_returns_true_when_package_present(self):
        self._write_status_file(_OPKG_STATUS_SAMPLE_OPENATV)
        self.assertTrue(_fccextender_installed())

    def test_returns_false_when_package_absent(self):
        self._write_status_file(_OPKG_STATUS_SAMPLE_WITHOUT)
        self.assertFalse(_fccextender_installed())

    def test_returns_false_when_file_missing(self):
        # I/O failure must collapse to False - never to True - so a
        # broken opkg-status file does not render a shortcut that the
        # click handler would silently fail on.
        settings_ui._OPKG_STATUS_PATH = "/nonexistent/path/opkg-status"
        self.assertFalse(_fccextender_installed())

    def test_vti_stem_still_counts(self):
        # Forward-compat: a future user with the VTi build (or any
        # other stem variant) still gets the shortcut.
        self._write_status_file(_OPKG_STATUS_SAMPLE_VTI)
        self.assertTrue(_fccextender_installed())


if __name__ == "__main__":
    unittest.main()
