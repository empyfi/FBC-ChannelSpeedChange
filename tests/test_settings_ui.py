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
)


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

    def test_openatv_stem_recognised(self):
        # Anticipated OpenATV-flavoured package name.
        label = _classify_fccextender_content(_OPKG_STATUS_SAMPLE_OPENATV)
        self.assertIn("installed", label)

    def test_hyphen_variant_recognised(self):
        # Whether Oberhesse uses "fccextender" or "fcc-extender" as the
        # stem, the substring match catches both.
        label = _classify_fccextender_content(_OPKG_STATUS_SAMPLE_HYPHEN)
        self.assertIn("installed", label)

    def test_empty_file_returns_not_detected(self):
        label = _classify_fccextender_content("")
        self.assertIn("not detected", label)

    def test_case_insensitive_match(self):
        # opkg status normally writes the canonical lowercase package
        # name, but a defensive check against future opkg variants
        # that might mixed-case the name is cheap.
        label = _classify_fccextender_content("Package: FCCExtender\n")
        self.assertIn("installed", label)


if __name__ == "__main__":
    unittest.main()
