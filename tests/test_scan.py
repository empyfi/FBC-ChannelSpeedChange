"""Tests for the v0.6.4 service-scan-transition handling.

The controller wraps ``Screens.ScanSetup.ScanSetup``, ``ScanSimple``
and ``Screens.ServiceScan.ServiceScan`` so every scan-screen instance
notifies the controller. On the 0->1 count edge the pool releases
all frontends; the tuner that the scan dialog wants to allocate
(typically Tuner A) is then guaranteed to be free.

Field-reproduced on 2026-07-16 22:32: pool held fe0 on a rearm cycle,
user opened Netzwerksuchlauf, dialog failed with "Fehler beim Start
der Suche" because fe0 was busy. This suite is the off-box regression
guard so the wrapper stays wired on every future build.

Covers:
  * ``sanity_check_scan_hook`` returns everything as optional when
    the scan modules are importable and reports optional entries when
    one or more are not.
  * ``_wire_scan_hooks`` marks each scan class patched idempotently.
  * The first scan-screen open releases every pool slot and blocks
    re-arm; subsequent overlapping scan-screen opens just bump the
    counter without re-releasing.
  * ``_do_rearm`` and ``pretune_external`` short-circuit while
    ``_in_scan`` is True.
  * The last scan-screen close (counter -> 0) clears the block and
    schedules a fresh re-arm; intermediate closes do not.
  * The end-to-end path with the class-level patch instantiates a
    scan screen after wiring and verifies the enter/leave dispatch
    both fire.
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


if "NavigationInstance" in sys.modules:
    _nav_module = sys.modules["NavigationInstance"]
    _FAKE_NAV = _nav_module.instance
else:
    _FAKE_NAV = FakeNav()
    _nav_module = types.ModuleType("NavigationInstance")
    _nav_module.instance = _FAKE_NAV
    sys.modules["NavigationInstance"] = _nav_module


# Stub Screens.ScanSetup and Screens.ServiceScan with minimal class
# hierarchies the wire path can patch. Every test resets the class-
# patch marker + Screens modules in setUp so tests stay independent
# of the run order.
if "Screens" not in sys.modules:
    _screens_pkg = types.ModuleType("Screens")
    _screens_pkg.__path__ = []
    sys.modules["Screens"] = _screens_pkg


class _StubScanSetup:
    def __init__(self, session):
        self.session = session
        self.onClose = []


class _StubScanSimple:
    def __init__(self, session):
        self.session = session
        self.onClose = []


class _StubServiceScan:
    def __init__(self, session, scanList):
        self.session = session
        self.scanList = scanList
        self.onClose = []


_scan_setup_mod = types.ModuleType("Screens.ScanSetup")
_scan_setup_mod.ScanSetup = _StubScanSetup
_scan_setup_mod.ScanSimple = _StubScanSimple
sys.modules["Screens.ScanSetup"] = _scan_setup_mod

_service_scan_mod = types.ModuleType("Screens.ServiceScan")
_service_scan_mod.ServiceScan = _StubServiceScan
sys.modules["Screens.ServiceScan"] = _service_scan_mod


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
    Controller, sanity_check_scan_hook,
    _scan_enter_dispatch, _scan_leave_dispatch,
)
from FBCChannelSpeedChange.fbc_pretune_pool import (
    FBCPreTunePool, Role, SlotState,
)


def _fresh_scan_classes():
    """Reinstall clean stub scan classes before each test. The wire
    path leaves class-level patches and markers that would otherwise
    leak across tests.
    """

    class _ScanSetup:
        def __init__(self, session):
            self.session = session
            self.onClose = []

    class _ScanSimple:
        def __init__(self, session):
            self.session = session
            self.onClose = []

    class _ServiceScan:
        def __init__(self, session, scanList):
            self.session = session
            self.scanList = scanList
            self.onClose = []

    _scan_setup_mod.ScanSetup = _ScanSetup
    _scan_setup_mod.ScanSimple = _ScanSimple
    _service_scan_mod.ServiceScan = _ServiceScan
    return _ScanSetup, _ScanSimple, _ServiceScan


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


class ScanSanityCheckTests(unittest.TestCase):

    def setUp(self):
        _fresh_scan_classes()

    def test_sanity_passes_when_all_scan_modules_present(self):
        crit, opt = sanity_check_scan_hook()
        self.assertEqual(crit, [])
        self.assertEqual(opt, [])

    def test_sanity_reports_optional_when_scansetup_missing(self):
        saved = sys.modules.pop("Screens.ScanSetup")
        try:
            crit, opt = sanity_check_scan_hook()
            self.assertEqual(crit, [])
            # Both ScanSetup and ScanSimple depend on the module.
            self.assertTrue(any("ScanSetup" in o for o in opt))
        finally:
            sys.modules["Screens.ScanSetup"] = saved

    def test_sanity_reports_optional_when_servicescan_missing(self):
        saved = sys.modules.pop("Screens.ServiceScan")
        try:
            crit, opt = sanity_check_scan_hook()
            self.assertEqual(crit, [])
            self.assertTrue(any("ServiceScan" in o for o in opt))
        finally:
            sys.modules["Screens.ServiceScan"] = saved


class WirePatchTests(unittest.TestCase):

    def setUp(self):
        _FAKE_NAV.allocations = []
        _cfg.allow_pretune.value = True
        _fresh_scan_classes()

    def test_wire_patches_all_three_scan_classes(self):
        c, _ = _make_controller()
        c._wire_scan_hooks()
        ScanSetup = sys.modules["Screens.ScanSetup"].ScanSetup
        ScanSimple = sys.modules["Screens.ScanSetup"].ScanSimple
        ServiceScan = sys.modules["Screens.ServiceScan"].ServiceScan
        self.assertTrue(getattr(ScanSetup, "_fbc_csc_scan_wrapped", False))
        self.assertTrue(getattr(ScanSimple, "_fbc_csc_scan_wrapped", False))
        self.assertTrue(getattr(ServiceScan, "_fbc_csc_scan_wrapped", False))

    def test_wire_is_idempotent(self):
        c, _ = _make_controller()
        c._wire_scan_hooks()
        ScanSetup = sys.modules["Screens.ScanSetup"].ScanSetup
        first_init = ScanSetup.__init__
        c._wire_scan_hooks()
        # Second wire must not re-wrap or stack another layer.
        self.assertIs(ScanSetup.__init__, first_init)

    def test_wire_skips_missing_scan_module(self):
        # Simulate a fork that lacks ServiceScan entirely - the other
        # two classes must still get wrapped.
        saved = sys.modules.pop("Screens.ServiceScan")
        try:
            c, _ = _make_controller()
            c._wire_scan_hooks()
            ScanSetup = sys.modules["Screens.ScanSetup"].ScanSetup
            self.assertTrue(getattr(ScanSetup, "_fbc_csc_scan_wrapped", False))
        finally:
            sys.modules["Screens.ServiceScan"] = saved

    def test_unwire_is_noop_by_contract(self):
        c, _ = _make_controller()
        c._wire_scan_hooks()
        ScanSetup = sys.modules["Screens.ScanSetup"].ScanSetup
        wrapped_init = ScanSetup.__init__
        c._unwire_scan_hooks()
        # Wrappers stay installed for the process lifetime, same
        # rationale as _unwire_standby_hooks.
        self.assertIs(ScanSetup.__init__, wrapped_init)
        self.assertTrue(getattr(ScanSetup, "_fbc_csc_scan_wrapped", False))


class EnterScanReleasesPoolTests(unittest.TestCase):

    def setUp(self):
        _FAKE_NAV.allocations = []
        _FAKE_NAV.stopped = []
        _cfg.allow_pretune.value = True
        _fresh_scan_classes()

    def test_enter_scan_releases_every_slot(self):
        c, pool = _make_controller()
        ref_next = FakeRef("1:0:1:A:0:0:0:0:0:0:")
        ref_prev = FakeRef("1:0:1:B:0:0:0:0:0:0:")
        pool.arm({Role.NEXT: [ref_next], Role.PREV: [ref_prev]})
        self.assertEqual(pool._slots_by_role[Role.NEXT][0].state,
                         SlotState.TUNING)
        c._on_enter_scan()
        self.assertTrue(c._in_scan)
        self.assertEqual(c._scan_active_count, 1)
        self.assertEqual(pool._slots_by_role[Role.NEXT][0].state,
                         SlotState.IDLE)
        self.assertEqual(pool._slots_by_role[Role.PREV][0].state,
                         SlotState.IDLE)

    def test_enter_scan_releases_external_slot_too(self):
        c, pool = _make_controller()
        ref = FakeRef("1:0:1:X:0:0:0:0:0:0:")
        c.pretune_external(ref)
        self.assertEqual(pool._slots_by_role[Role.EXTERNAL][0].state,
                         SlotState.TUNING)
        c._on_enter_scan()
        self.assertEqual(pool._slots_by_role[Role.EXTERNAL][0].state,
                         SlotState.IDLE)


class BlockWhileInScanTests(unittest.TestCase):

    def setUp(self):
        _FAKE_NAV.allocations = []
        _cfg.allow_pretune.value = True
        _fresh_scan_classes()

    def test_rearm_no_op_while_in_scan(self):
        c, _ = _make_controller()
        c._on_enter_scan()
        n_before = len(_FAKE_NAV.allocations)
        c._do_rearm()
        self.assertEqual(len(_FAKE_NAV.allocations), n_before,
                         "no allocation while service scan is active")

    def test_pretune_external_no_op_while_in_scan(self):
        c, pool = _make_controller()
        c._on_enter_scan()
        c.pretune_external(FakeRef("1:0:1:X:0:0:0:0:0:0:"))
        self.assertEqual(len(_FAKE_NAV.allocations), 0)
        self.assertEqual(pool._slots_by_role[Role.EXTERNAL][0].state,
                         SlotState.IDLE)


class LeaveScanReenablesTests(unittest.TestCase):

    def setUp(self):
        _FAKE_NAV.allocations = []
        _cfg.allow_pretune.value = True
        _fresh_scan_classes()

    def test_leave_scan_clears_flag(self):
        c, _ = _make_controller()
        c._on_enter_scan()
        self.assertTrue(c._in_scan)
        c._on_leave_scan()
        self.assertFalse(c._in_scan)

    def test_pretune_external_works_again_after_leave(self):
        c, pool = _make_controller()
        c._on_enter_scan()
        c._on_leave_scan()
        c.pretune_external(FakeRef("1:0:1:X:0:0:0:0:0:0:"))
        self.assertEqual(pool._slots_by_role[Role.EXTERNAL][0].state,
                         SlotState.TUNING)

    def test_leave_scan_schedules_rearm(self):
        c, _ = _make_controller()
        c._on_enter_scan()
        c._on_leave_scan()
        self.assertIsNotNone(c._rearm_timer)
        self.assertTrue(c._rearm_timer._running)


class OverlappingScanScreensTests(unittest.TestCase):
    """openatv stacks scan screens: user opens ScanSetup, hits OK,
    ServiceScan pushes on top. Close of ServiceScan pops back into
    ScanSetup which stays open. The counter-based flag must stay True
    across the entire stack lifetime and only release the block on the
    very last close.
    """

    def setUp(self):
        _FAKE_NAV.allocations = []
        _cfg.allow_pretune.value = True
        _fresh_scan_classes()

    def test_second_enter_does_not_re_release(self):
        c, pool = _make_controller()
        pool.arm({Role.NEXT: [FakeRef("1:0:1:A:0:0:0:0:0:0:")]})
        c._on_enter_scan()  # ScanSetup opens
        n_alloc_after_first_enter = len(_FAKE_NAV.allocations)
        c._on_enter_scan()  # ServiceScan pushes over
        self.assertEqual(c._scan_active_count, 2)
        # No new allocation, pool is still released.
        self.assertEqual(len(_FAKE_NAV.allocations), n_alloc_after_first_enter)

    def test_intermediate_leave_does_not_rearm(self):
        c, _ = _make_controller()
        c._on_enter_scan()   # ScanSetup
        c._on_enter_scan()   # ServiceScan pushed
        c._rearm_timer = None  # clear any prior timer
        c._on_leave_scan()   # ServiceScan closes, ScanSetup still open
        self.assertEqual(c._scan_active_count, 1)
        self.assertTrue(c._in_scan,
                        "in_scan stays True until every scan screen closes")
        self.assertIsNone(c._rearm_timer,
                          "no re-arm scheduled while ScanSetup still open")

    def test_final_leave_rearms(self):
        c, _ = _make_controller()
        c._on_enter_scan()
        c._on_enter_scan()
        c._on_leave_scan()   # ServiceScan close - still in_scan
        c._on_leave_scan()   # ScanSetup close - now zero
        self.assertEqual(c._scan_active_count, 0)
        self.assertFalse(c._in_scan)
        self.assertIsNotNone(c._rearm_timer)
        self.assertTrue(c._rearm_timer._running)

    def test_leave_below_zero_is_clamped(self):
        # Defensive: if a wrapper misfires or a screen close event
        # arrives without a paired open, the counter must not go
        # negative and get the plugin stuck in "in_scan forever".
        c, _ = _make_controller()
        c._on_leave_scan()
        c._on_leave_scan()
        self.assertEqual(c._scan_active_count, 0)


class EndToEndPatchDispatchTests(unittest.TestCase):
    """Instantiate a scan screen after wiring; the wrapper must fire
    the enter dispatcher, and the onClose list must contain the leave
    dispatcher so a subsequent screen-close notifies the controller.
    """

    def setUp(self):
        _FAKE_NAV.allocations = []
        _cfg.allow_pretune.value = True
        _fresh_scan_classes()

    def test_scansetup_open_triggers_release(self):
        c, pool = _make_controller()
        c._wire_scan_hooks()
        ref = FakeRef("1:0:1:A:0:0:0:0:0:0:")
        pool.arm({Role.NEXT: [ref]})
        self.assertEqual(pool._slots_by_role[Role.NEXT][0].state,
                         SlotState.TUNING)
        ScanSetup = sys.modules["Screens.ScanSetup"].ScanSetup
        screen = ScanSetup(session=object())
        self.assertTrue(c._in_scan)
        self.assertEqual(pool._slots_by_role[Role.NEXT][0].state,
                         SlotState.IDLE)
        self.assertIn(_scan_leave_dispatch, screen.onClose)

    def test_servicescan_open_triggers_release(self):
        c, pool = _make_controller()
        c._wire_scan_hooks()
        pool.arm({Role.NEXT: [FakeRef("1:0:1:A:0:0:0:0:0:0:")]})
        ServiceScan = sys.modules["Screens.ServiceScan"].ServiceScan
        # ServiceScan takes (session, scanList) - the wrapper must
        # tolerate any __init__ signature via *args, **kwargs.
        screen = ServiceScan(session=object(), scanList=[])
        self.assertTrue(c._in_scan)
        self.assertEqual(pool._slots_by_role[Role.NEXT][0].state,
                         SlotState.IDLE)
        self.assertIn(_scan_leave_dispatch, screen.onClose)

    def test_scanscreen_close_triggers_leave(self):
        c, _ = _make_controller()
        c._wire_scan_hooks()
        ScanSetup = sys.modules["Screens.ScanSetup"].ScanSetup
        screen = ScanSetup(session=object())
        self.assertTrue(c._in_scan)
        for cb in list(screen.onClose):
            cb()
        self.assertFalse(c._in_scan)

    def test_dispatch_noop_when_controller_missing(self):
        Controller._instance = None
        _scan_enter_dispatch()
        _scan_leave_dispatch()

    def test_stacked_open_close_sequence(self):
        # Simulate the real openatv screen stack: ScanSetup opens ->
        # user hits OK -> ServiceScan opens -> Scan runs -> ServiceScan
        # closes -> user backs out of ScanSetup -> ScanSetup closes.
        # Pool must be released for the whole stretch and re-armed
        # only after ScanSetup closes.
        c, pool = _make_controller()
        c._wire_scan_hooks()
        pool.arm({Role.NEXT: [FakeRef("1:0:1:A:0:0:0:0:0:0:")]})
        n_alloc_before = len(_FAKE_NAV.allocations)

        ScanSetup = sys.modules["Screens.ScanSetup"].ScanSetup
        ServiceScan = sys.modules["Screens.ServiceScan"].ServiceScan

        setup = ScanSetup(session=object())
        self.assertTrue(c._in_scan)

        runner = ServiceScan(session=object(), scanList=[])
        self.assertEqual(c._scan_active_count, 2)

        # ServiceScan closes first.
        for cb in list(runner.onClose):
            cb()
        self.assertTrue(c._in_scan, "still in scan while ScanSetup open")

        # No new allocation happened during scan.
        self.assertEqual(len(_FAKE_NAV.allocations), n_alloc_before)

        # ScanSetup closes second.
        for cb in list(setup.onClose):
            cb()
        self.assertFalse(c._in_scan)


if __name__ == "__main__":
    unittest.main()
