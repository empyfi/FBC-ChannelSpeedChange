"""Tests for the v0.6.3 standby-transition handling.

The controller cannot detect standby via a session hook on openatv
7.6 - session.onEnterStandby / onLeaveStandby do not exist. Instead,
``_wire_standby_hooks`` monkey-patches ``Screens.Standby.Standby.__init__``
so every standby-screen instantiation notifies the controller. The
tests below stub Screens.Standby with a minimal class hierarchy so
the wrapper install path exercises against a real (albeit stubbed)
Standby class.

Covers:
  * ``sanity_check_standby_hook`` returns everything as optional when
    Screens.Standby is importable and reports an optional entry when
    it is not.
  * ``_wire_standby_hooks`` marks the class patched and dispatches
    enter/leave through the module-level Controller.peek() lookup.
  * Entering standby releases every pool slot and blocks re-arm.
  * ``_do_rearm`` and ``pretune_external`` short-circuit while in
    standby.
  * Leaving standby clears the block and schedules a fresh re-arm.
"""

import sys
import types
import unittest

from _enigma_stubs import bootstrap
bootstrap()


class FakeRef:
    def __init__(self, s):
        self._s = s
        try:
            self.type = int(s.split(":", 1)[0])
        except (ValueError, IndexError):
            self.type = 1

    def toString(self):
        return self._s


class FakeNav:
    def __init__(self):
        self.event = []
        self._live_ref = None
        self.allocations = []
        self.played = []
        self.stopped = []

    def getCurrentlyPlayingServiceReference(self):
        return self._live_ref

    def recordService(self, ref, simulate=False, type=None):
        rec = _FakeRecordable()
        self.allocations.append((ref, rec))
        return rec

    def stopRecordService(self, rec):
        rec.stopped = True
        self.stopped.append(rec)

    def playService(self, ref):
        self.played.append(ref)


class _FakeRecordable:
    def __init__(self):
        self.stopped = False
        self.prepared = False
        self.started = False
        self.stop_called = False
        self.error_code = 0
        self.prepare_args = None

    def getError(self):
        return self.error_code

    def prepare(self, *args):
        self.prepared = True
        self.prepare_args = args
        return 0

    def start(self):
        self.started = True
        return 0

    def stop(self):
        self.stop_called = True
        return 0

    def frontendInfo(self):
        return None


class _FakeNim:
    def isFBCTuner(self):
        return True

    def isFBCRoot(self):
        return False

    def isFBCLink(self):
        return False

    def isEnabled(self):
        return True


class _FakeNimManager:
    def __init__(self):
        self.nim_slots = [_FakeNim()]


# Reuse the NavigationInstance module the other tests already
# registered when present.
if "NavigationInstance" in sys.modules:
    _nav_module = sys.modules["NavigationInstance"]
    _FAKE_NAV = _nav_module.instance
else:
    _FAKE_NAV = FakeNav()
    _nav_module = types.ModuleType("NavigationInstance")
    _nav_module.instance = _FAKE_NAV
    sys.modules["NavigationInstance"] = _nav_module


# Stub Screens.Standby with a minimal Standby class the wire path can
# patch. Every test resets the class-patch marker in setUp so tests
# stay independent of the run order.
if "Screens" not in sys.modules:
    _screens_pkg = types.ModuleType("Screens")
    _screens_pkg.__path__ = []
    sys.modules["Screens"] = _screens_pkg


class _StubStandby:
    """Bare stand-in that mirrors the openatv Screens.Standby.Standby
    contract used by the wrapper: a base Screen with onClose = [] set
    in __init__.
    """

    def __init__(self, session):
        self.session = session
        self.onClose = []


_standby_mod = types.ModuleType("Screens.Standby")
_standby_mod.Standby = _StubStandby
sys.modules["Screens.Standby"] = _standby_mod


from FBCChannelSpeedChange.config import cfg as _cfg
from Components.config import ConfigYesNo, ConfigInteger

if not hasattr(_cfg, "accept_external_pretune"):
    _cfg.accept_external_pretune = ConfigYesNo(default=True)
if not hasattr(_cfg, "external_slot_ttl_min"):
    _cfg.external_slot_ttl_min = ConfigInteger(default=5)
if not hasattr(_cfg, "external_max_calls_per_sec"):
    _cfg.external_max_calls_per_sec = ConfigInteger(default=10)

_cfg.allow_pretune.value = True
_cfg.accept_external_pretune.value = True


from FBCChannelSpeedChange.controller import (
    Controller, sanity_check_standby_hook,
    _standby_enter_dispatch, _standby_leave_dispatch,
)
from FBCChannelSpeedChange.fbc_pretune_pool import (
    FBCPreTunePool, Role, SlotState,
)


def _fresh_standby_class():
    """Reinstall a clean stub Standby class in Screens.Standby before
    each test. The wire path leaves a class-level patch and a marker
    that would otherwise leak across tests.
    """

    class _Standby:
        def __init__(self, session):
            self.session = session
            self.onClose = []

    _standby_mod.Standby = _Standby
    return _Standby


def _make_controller():
    Controller._instance = None
    session = object()
    c = Controller(session)
    c._enabled = True
    pool = FBCPreTunePool(
        nav_provider=lambda: _FAKE_NAV,
        nim_manager_provider=lambda: _FakeNimManager(),
    )
    pool.configure({Role.EXTERNAL: 1, Role.NEXT: 1, Role.PREV: 1,
                    Role.HISTORY: 1})
    c._pool = pool
    return c, pool


class StandbySanityCheckTests(unittest.TestCase):

    def setUp(self):
        _fresh_standby_class()

    def test_sanity_passes_when_standby_importable(self):
        crit, opt = sanity_check_standby_hook()
        self.assertEqual(crit, [])
        self.assertEqual(opt, [])

    def test_sanity_reports_optional_when_standby_missing(self):
        # Drop the stub temporarily to simulate a build without the
        # Screens.Standby module.
        saved = sys.modules.pop("Screens.Standby")
        try:
            crit, opt = sanity_check_standby_hook()
            self.assertEqual(crit, [])
            self.assertTrue(any("Standby" in o for o in opt))
        finally:
            sys.modules["Screens.Standby"] = saved


class WirePatchTests(unittest.TestCase):

    def setUp(self):
        _FAKE_NAV.allocations = []
        _cfg.allow_pretune.value = True
        _fresh_standby_class()

    def test_wire_patches_standby_init(self):
        c, _ = _make_controller()
        c._wire_standby_hooks()
        StandbyCls = sys.modules["Screens.Standby"].Standby
        self.assertTrue(getattr(StandbyCls, "_fbc_csc_wrapped", False))

    def test_wire_is_idempotent(self):
        c, _ = _make_controller()
        c._wire_standby_hooks()
        StandbyCls = sys.modules["Screens.Standby"].Standby
        first_init = StandbyCls.__init__
        c._wire_standby_hooks()
        # Second wire must not re-wrap the class or replace __init__.
        self.assertIs(StandbyCls.__init__, first_init)

    def test_unwire_is_noop_by_contract(self):
        c, _ = _make_controller()
        c._wire_standby_hooks()
        StandbyCls = sys.modules["Screens.Standby"].Standby
        wrapped_init = StandbyCls.__init__
        c._unwire_standby_hooks()
        # Wrapper stays installed for the process lifetime.
        self.assertIs(StandbyCls.__init__, wrapped_init)
        self.assertTrue(getattr(StandbyCls, "_fbc_csc_wrapped", False))


class EnterStandbyReleasesPoolTests(unittest.TestCase):

    def setUp(self):
        _FAKE_NAV.allocations = []
        _FAKE_NAV.stopped = []
        _cfg.allow_pretune.value = True
        _fresh_standby_class()

    def test_enter_standby_releases_every_slot(self):
        c, pool = _make_controller()
        ref_next = FakeRef("1:0:1:A:0:0:0:0:0:0:")
        ref_prev = FakeRef("1:0:1:B:0:0:0:0:0:0:")
        pool.arm({Role.NEXT: [ref_next], Role.PREV: [ref_prev]})
        self.assertEqual(pool._slots_by_role[Role.NEXT][0].state,
                         SlotState.TUNING)
        c._on_enter_standby()
        self.assertTrue(c._in_standby)
        self.assertEqual(pool._slots_by_role[Role.NEXT][0].state,
                         SlotState.IDLE)
        self.assertEqual(pool._slots_by_role[Role.PREV][0].state,
                         SlotState.IDLE)

    def test_enter_standby_releases_external_slot_too(self):
        c, pool = _make_controller()
        ref = FakeRef("1:0:1:X:0:0:0:0:0:0:")
        c.pretune_external(ref)
        self.assertEqual(pool._slots_by_role[Role.EXTERNAL][0].state,
                         SlotState.TUNING)
        c._on_enter_standby()
        self.assertEqual(pool._slots_by_role[Role.EXTERNAL][0].state,
                         SlotState.IDLE)


class BlockWhileInStandbyTests(unittest.TestCase):

    def setUp(self):
        _FAKE_NAV.allocations = []
        _cfg.allow_pretune.value = True
        _fresh_standby_class()

    def test_rearm_no_op_while_in_standby(self):
        c, pool = _make_controller()
        c._on_enter_standby()
        n_before = len(_FAKE_NAV.allocations)
        c._do_rearm()
        self.assertEqual(len(_FAKE_NAV.allocations), n_before)

    def test_pretune_external_no_op_while_in_standby(self):
        c, pool = _make_controller()
        c._on_enter_standby()
        c.pretune_external(FakeRef("1:0:1:X:0:0:0:0:0:0:"))
        self.assertEqual(len(_FAKE_NAV.allocations), 0)
        self.assertEqual(pool._slots_by_role[Role.EXTERNAL][0].state,
                         SlotState.IDLE)


class LeaveStandbyReenablesTests(unittest.TestCase):

    def setUp(self):
        _FAKE_NAV.allocations = []
        _cfg.allow_pretune.value = True
        _fresh_standby_class()

    def test_leave_standby_clears_flag(self):
        c, _ = _make_controller()
        c._on_enter_standby()
        self.assertTrue(c._in_standby)
        c._on_leave_standby()
        self.assertFalse(c._in_standby)

    def test_pretune_external_works_again_after_leave(self):
        c, pool = _make_controller()
        c._on_enter_standby()
        c._on_leave_standby()
        c.pretune_external(FakeRef("1:0:1:X:0:0:0:0:0:0:"))
        self.assertEqual(pool._slots_by_role[Role.EXTERNAL][0].state,
                         SlotState.TUNING)

    def test_leave_standby_schedules_rearm(self):
        c, _ = _make_controller()
        c._on_enter_standby()
        c._on_leave_standby()
        self.assertIsNotNone(c._rearm_timer)
        self.assertTrue(c._rearm_timer._running)


class EndToEndPatchDispatchTests(unittest.TestCase):
    """Instantiate a Standby screen after wiring; the wrapper must
    fire the enter dispatcher, and the onClose list must contain the
    leave dispatcher so a subsequent screen-close notifies the
    controller too.
    """

    def setUp(self):
        _FAKE_NAV.allocations = []
        _cfg.allow_pretune.value = True
        _fresh_standby_class()

    def test_standby_screen_open_triggers_release(self):
        c, pool = _make_controller()
        c._wire_standby_hooks()
        ref = FakeRef("1:0:1:A:0:0:0:0:0:0:")
        pool.arm({Role.NEXT: [ref]})
        self.assertEqual(pool._slots_by_role[Role.NEXT][0].state,
                         SlotState.TUNING)
        StandbyCls = sys.modules["Screens.Standby"].Standby
        # Enigma2 side: user hits the power button, Standby screen is
        # instantiated. Wrapper must fire the enter dispatcher.
        screen = StandbyCls(session=object())
        self.assertTrue(c._in_standby)
        self.assertEqual(pool._slots_by_role[Role.NEXT][0].state,
                         SlotState.IDLE)
        # Leave dispatcher is in onClose so a wake-up fires it.
        self.assertIn(_standby_leave_dispatch, screen.onClose)

    def test_standby_screen_close_triggers_leave(self):
        c, _ = _make_controller()
        c._wire_standby_hooks()
        StandbyCls = sys.modules["Screens.Standby"].Standby
        screen = StandbyCls(session=object())
        self.assertTrue(c._in_standby)
        # Wake-up: enigma2 calls every onClose callback.
        for cb in list(screen.onClose):
            cb()
        self.assertFalse(c._in_standby)

    def test_dispatch_noop_when_controller_missing(self):
        # No live controller - dispatchers must not raise.
        Controller._instance = None
        _standby_enter_dispatch()
        _standby_leave_dispatch()


if __name__ == "__main__":
    unittest.main()
