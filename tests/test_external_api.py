"""Tests for the public PreTuneSingleChannel / ReleaseSingleChannel
entry points.

The api module is a thin pass-through to the controller. Tests here
verify only the public contract observable from outside:
  * both gates (allow_pretune, accept_external_pretune) must be on
  * a missing controller (early boot) is a silent no-op
  * exceptions raised by the controller are caught, not propagated
  * the ref argument is routed verbatim to the controller method

The convergence / idempotency logic lives inside the controller and
is exercised in test_controller_external.py (Phase 3).
"""

import unittest

from _enigma_stubs import bootstrap
bootstrap()

# Add the cfg.accept_external_pretune attribute up-front so the
# tests can flip its .value the same way other tests flip
# pre-existing config attributes. The real config will gain the
# entry in Phase 4; this stub is shaped to look identical to a
# ConfigYesNo instance from the test stubs.
from FBCChannelSpeedChange.config import cfg as _cfg
from Components.config import ConfigYesNo

if not hasattr(_cfg, "accept_external_pretune"):
    _cfg.accept_external_pretune = ConfigYesNo(default=False)

# Master switch must be on for any test to reach the controller path.
_cfg.allow_pretune.value = True

from FBCChannelSpeedChange import api


class FakeRef:
    def __init__(self, s):
        self._s = s

    def toString(self):
        return self._s


class FakeController:
    """Captures the controller surface the api routes into."""

    def __init__(self, pretune_raises=None, release_raises=None):
        self.pretune_calls = []   # list of refs
        self.release_calls = []   # list of refs (None when no-arg)
        self._pretune_raises = pretune_raises
        self._release_raises = release_raises

    def pretune_external(self, ref):
        self.pretune_calls.append(ref)
        if self._pretune_raises:
            raise self._pretune_raises

    def release_external(self, ref):
        self.release_calls.append(ref)
        if self._release_raises:
            raise self._release_raises


class ExternalApiTests(unittest.TestCase):

    def setUp(self):
        # Reset gates to a known on state and inject a fresh fake
        # controller per test so cross-test state never leaks.
        _cfg.allow_pretune.value = True
        _cfg.accept_external_pretune.value = True
        self._original_provider = api._controller_provider
        self.fake = FakeController()
        api._controller_provider = lambda: self.fake

    def tearDown(self):
        api._controller_provider = self._original_provider
        _cfg.accept_external_pretune.value = False

    # ---- gate logic ----

    def test_pretune_no_op_when_master_switch_off(self):
        _cfg.allow_pretune.value = False
        try:
            api.PreTuneSingleChannel(FakeRef("1:0:1:A:0:0:0:0:0:0:"))
            self.assertEqual(self.fake.pretune_calls, [])
        finally:
            _cfg.allow_pretune.value = True

    def test_pretune_no_op_when_external_gate_off(self):
        _cfg.accept_external_pretune.value = False
        api.PreTuneSingleChannel(FakeRef("1:0:1:A:0:0:0:0:0:0:"))
        self.assertEqual(self.fake.pretune_calls, [])

    def test_release_no_op_when_master_switch_off(self):
        _cfg.allow_pretune.value = False
        try:
            api.ReleaseSingleChannel(FakeRef("1:0:1:A:0:0:0:0:0:0:"))
            self.assertEqual(self.fake.release_calls, [])
        finally:
            _cfg.allow_pretune.value = True

    def test_release_no_op_when_external_gate_off(self):
        _cfg.accept_external_pretune.value = False
        api.ReleaseSingleChannel(FakeRef("1:0:1:A:0:0:0:0:0:0:"))
        self.assertEqual(self.fake.release_calls, [])

    # ---- missing controller (early boot) ----

    def test_pretune_silent_when_controller_missing(self):
        api._controller_provider = lambda: None
        # Must not raise.
        api.PreTuneSingleChannel(FakeRef("1:0:1:A:0:0:0:0:0:0:"))

    def test_release_silent_when_controller_missing(self):
        api._controller_provider = lambda: None
        api.ReleaseSingleChannel(FakeRef("1:0:1:A:0:0:0:0:0:0:"))

    # ---- happy path routing ----

    def test_pretune_routes_to_controller(self):
        ref = FakeRef("1:0:1:A:0:0:0:0:0:0:")
        api.PreTuneSingleChannel(ref)
        self.assertEqual(self.fake.pretune_calls, [ref],
                         "exact ref forwarded to controller.pretune_external")

    def test_release_with_ref_routes_to_controller(self):
        ref = FakeRef("1:0:1:A:0:0:0:0:0:0:")
        api.ReleaseSingleChannel(ref)
        self.assertEqual(self.fake.release_calls, [ref])

    def test_release_without_arg_passes_none(self):
        api.ReleaseSingleChannel()
        self.assertEqual(self.fake.release_calls, [None],
                         "no-arg release forwards None - controller "
                         "treats it as unconditional empty")

    # ---- exception handling ----

    def test_pretune_swallows_controller_exception(self):
        api._controller_provider = lambda: FakeController(
            pretune_raises=RuntimeError("boom"))
        # Must not propagate.
        api.PreTuneSingleChannel(FakeRef("1:0:1:A:0:0:0:0:0:0:"))

    def test_release_swallows_controller_exception(self):
        api._controller_provider = lambda: FakeController(
            release_raises=RuntimeError("boom"))
        api.ReleaseSingleChannel(FakeRef("1:0:1:A:0:0:0:0:0:0:"))

    # ---- input validation (defensive against garbage callers) ----

    def test_pretune_no_op_on_none_ref(self):
        api.PreTuneSingleChannel(None)
        self.assertEqual(self.fake.pretune_calls, [])

    def test_pretune_no_op_on_non_serviceref(self):
        # Primitives, collections and arbitrary objects must be
        # rejected before they reach the controller / pool / SWIG
        # layers. Strings are validated separately by the shape
        # whitelist; see test_pretune_accepts_valid_string_ref and
        # test_pretune_rejects_malformed_string_ref below.
        for garbage in (42, [], {"ref": "A"}, object(), 3.14, b"bytes"):
            api.PreTuneSingleChannel(garbage)
        self.assertEqual(self.fake.pretune_calls, [],
                         "non-eServiceReference inputs must not reach "
                         "the controller")

    def test_release_no_op_on_non_serviceref(self):
        for garbage in (42, [], object(), 3.14):
            api.ReleaseSingleChannel(garbage)
        self.assertEqual(self.fake.release_calls, [])

    def test_release_with_none_still_passes_through(self):
        # None on Release is the documented "release whatever is in
        # the slot" shape - it must NOT be rejected by the input
        # validator.
        api.ReleaseSingleChannel(None)
        self.assertEqual(self.fake.release_calls, [None])

    # ---- string-form input (whitelist-gated SWIG construction) ----

    def test_pretune_accepts_valid_string_ref(self):
        # A string matching the canonical DVB broadcast shape is
        # coerced into an eServiceReference by the api layer and
        # forwarded to the controller. Callers that lack an
        # eServiceReference object (e.g. FCC-Extender's ChannelSelection
        # hooks read the cursor as a string) get to use the api
        # without wrapping the constructor themselves.
        api.PreTuneSingleChannel("1:0:1:6DCA:44D:1:C00000:0:0:0:")
        self.assertEqual(len(self.fake.pretune_calls), 1,
                         "valid string ref must reach the controller")
        forwarded = self.fake.pretune_calls[0]
        self.assertTrue(hasattr(forwarded, "toString"),
                        "controller receives an eServiceReference-shaped "
                        "object, never the raw string")
        self.assertEqual(forwarded.toString(),
                         "1:0:1:6DCA:44D:1:C00000:0:0:0:")

    def test_pretune_accepts_string_ref_with_name_suffix(self):
        # The full canonical toString shape carries an optional
        # `::<name>` display suffix; eServiceReference.toString() on
        # a live cursor returns this form. Callers that read the
        # cursor via getCurrentSelection().toString() must be able
        # to pass the raw string without stripping the suffix.
        for s in ("1:0:19:283D:3FB:1:C00000:0:0:0::Das Erste",
                  "1:0:19:2B66:3F3:1:C00000:0:0:0::ZDF",
                  "1:0:1:445D:453:1:C00000:0:0:0::ProSieben",
                  "1:0:19:283D:3FB:1:C00000:0:0:0::"):
            self.fake.pretune_calls = []
            api.PreTuneSingleChannel(s)
            self.assertEqual(len(self.fake.pretune_calls), 1,
                             "string ref with name suffix must reach "
                             "the controller: %s" % s)
        # Length must still be respected on the name-suffix path.
        self.fake.pretune_calls = []
        api.PreTuneSingleChannel(
            "1:0:1:6DCA:44D:1:C00000:0:0:0::" + "X" * 600)
        self.assertEqual(self.fake.pretune_calls, [],
                         "oversize name suffix must be rejected")

    def test_release_accepts_valid_string_ref(self):
        api.ReleaseSingleChannel("1:0:1:6DCA:44D:1:C00000:0:0:0:")
        self.assertEqual(len(self.fake.release_calls), 1)
        forwarded = self.fake.release_calls[0]
        self.assertTrue(hasattr(forwarded, "toString"))
        self.assertEqual(forwarded.toString(),
                         "1:0:1:6DCA:44D:1:C00000:0:0:0:")

    def test_pretune_rejects_malformed_string_ref(self):
        # The whitelist is deliberately tight to keep service-type
        # spoofing, file-path injection and unparseable garbage out
        # of the SWIG constructor. Each rejected shape covers one
        # concrete attack pattern; see the rationale in api.py.
        rejected = [
            "",                                           # empty
            "garbage",                                    # not even close
            "4097:0:1:A:B:C:D:0:0:0:",                    # IPTV type spoof
            "1:7:1:A:B:C:D:0:0:0:",                       # bouquet (flags=7)
            "2:0:1:A:B:C:D:0:0:0:",                       # marker type
            "1:0:1:A:B:C:",                               # too few fields
            "1:0:Z:A:B:C:D:0:0:0:",                       # non-hex stype
            "1:0:1:A:B:C:D:0:0:0:/etc/shadow:name",       # path injection (non-empty path)
            "1:0:1:A:B:C:D:0:0:0:file.ts:name",           # file-backed ref
            "1:0:1:A:B:C:D:0:0:0::" + "X" * 600,          # oversize name suffix
        ]
        for s in rejected:
            api.PreTuneSingleChannel(s)
        self.assertEqual(self.fake.pretune_calls, [],
                         "no malformed string may reach the controller")

    def test_release_rejects_malformed_string_ref(self):
        for s in ("", "garbage", "4097:0:1:A:B:C:D:0:0:0:",
                  "1:7:1:A:B:C:D:0:0:0:", "1:0:1:A:B:C:"):
            api.ReleaseSingleChannel(s)
        self.assertEqual(self.fake.release_calls, [])

    # ---- caller-frame diagnostic (debug_log mode) ----

    def test_caller_frame_logged_when_debug_log_on(self):
        """With cfg.debug_log on, the api logs the caller's
        filename:line so a forum reporter can answer "which plugin
        made the call" when something goes wrong.
        """
        _cfg.debug_log.value = True
        captured = []
        original = api.debug
        api.debug = lambda msg: captured.append(msg)
        try:
            api.PreTuneSingleChannel(FakeRef("1:0:1:A:0:0:0:0:0:0:"))
        finally:
            api.debug = original
            _cfg.debug_log.value = False
        self.assertTrue(
            any("PreTuneSingleChannel" in m and "called from" in m
                for m in captured),
            "expected a 'PreTuneSingleChannel(...) called from ...' "
            "debug line, got: %r" % captured)

    def test_caller_frame_not_logged_when_debug_log_off(self):
        _cfg.debug_log.value = False
        captured = []
        original = api.debug
        api.debug = lambda msg: captured.append(msg)
        try:
            api.PreTuneSingleChannel(FakeRef("1:0:1:A:0:0:0:0:0:0:"))
        finally:
            api.debug = original
        self.assertFalse(
            any("called from" in m for m in captured),
            "caller-frame must stay out of the log when debug_log "
            "is off (avoid inspect.stack cost on hot path)")


if __name__ == "__main__":
    unittest.main()
