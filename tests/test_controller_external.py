"""Tests for the controller's v0.5.0 external pretune slot lifecycle.

Covers:
  * pretune_external arms the EXTERNAL slot when no other slot
    already holds the same ref
  * convergence with NEXT / PREV / HISTORY targets - the call is a
    no-op because the eventual zap is satisfied by channel-share
  * race-safe release: release_external(ref) only fires when the
    EXTERNAL slot currently holds that exact ref; release_external()
    without arg releases unconditionally
  * TTL refresh on repeated pretune_external, timer expiry releases
    the slot
  * evNewProgramInfo handler releases the EXTERNAL slot when the
    live service matches the armed ref - covers the shortcut-zap
    path where the ZapInterceptor does not see the swap

The Controller is instantiated with a stub session and the heavy
sub-objects (interceptor, arbiter) are not started. Tests reach into
controller._pool to swap in a test pool and to inspect slot state -
same pattern other tests use.
"""

import sys
import types
import unittest

from _enigma_stubs import bootstrap
bootstrap()


# ---------------------------------------------------------------
# Fakes / stubs needed before the controller module is imported
# ---------------------------------------------------------------

class FakeRef:
    def __init__(self, s):
        self._s = s

    def toString(self):
        return self._s


class FakeNav:
    """Stand-in for NavigationInstance.instance with an event list."""

    def __init__(self):
        self.event = []          # list of callback fns
        self._live_ref = None    # set by tests to drive getCurrentlyPlaying
        self.allocations = []    # list of (ref, FakeRecordable)
        self.played = []
        self.stopped = []

    def getCurrentlyPlayingServiceReference(self):
        return self._live_ref

    def recordService(self, ref):
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


# Inject the NavigationInstance module before controller imports it.
_FAKE_NAV = FakeNav()
_nav_module = types.ModuleType("NavigationInstance")
_nav_module.instance = _FAKE_NAV
sys.modules["NavigationInstance"] = _nav_module


# Phase 4 lands these config keys for real; tests stub them so the
# controller code path can be exercised pre-Phase-4.
from FBCChannelSpeedChange.config import cfg as _cfg
from Components.config import ConfigYesNo, ConfigInteger

if not hasattr(_cfg, "accept_external_pretune"):
    _cfg.accept_external_pretune = ConfigYesNo(default=False)
if not hasattr(_cfg, "external_slot_ttl_min"):
    _cfg.external_slot_ttl_min = ConfigInteger(default=5)
if not hasattr(_cfg, "prewarm_descrambler_external"):
    _cfg.prewarm_descrambler_external = ConfigYesNo(default=False)

_cfg.allow_pretune.value = True
_cfg.accept_external_pretune.value = True


from FBCChannelSpeedChange.controller import Controller
from FBCChannelSpeedChange.fbc_pretune_pool import (
    FBCPreTunePool, Role, SlotState,
)


def _make_controller_with_test_pool():
    """Build a Controller without going through start().

    Pool is swapped for one that uses an injected fake nav so the
    recordable lifecycle can be observed; the rest of the controller
    sub-objects stay as default - the methods under test only touch
    self._pool, self._enabled and self._external_ttl_timer.
    """
    session = object()                # opaque - controller stores but does not call
    c = Controller(session)
    c._enabled = True                 # bypass start() so the test reaches the API path
    # Swap in a pool wired to the fake nav + a single FBC slot so
    # allocation works without the real enigma2 stack.
    pool = FBCPreTunePool(
        nav_provider=lambda: _FAKE_NAV,
        nim_manager_provider=lambda: _FakeNimManager(),
    )
    pool.configure({Role.EXTERNAL: 1, Role.NEXT: 1, Role.PREV: 1,
                    Role.HISTORY: 1})
    c._pool = pool
    return c, pool


class ExternalSlotLifecycleTests(unittest.TestCase):

    def setUp(self):
        # Each test starts with a fresh allocations list so cross-test
        # state never leaks via the module-level _FAKE_NAV.
        _FAKE_NAV.allocations = []
        _FAKE_NAV.played = []
        _FAKE_NAV.stopped = []
        _FAKE_NAV._live_ref = None
        _FAKE_NAV.event = []
        _cfg.allow_pretune.value = True
        _cfg.accept_external_pretune.value = True

    # ---- pretune_external happy path ----

    def test_pretune_external_arms_slot(self):
        c, pool = _make_controller_with_test_pool()
        ref = FakeRef("1:0:1:X:0:0:0:0:0:0:")
        c.pretune_external(ref)
        slots = pool._slots_by_role[Role.EXTERNAL]
        self.assertEqual(slots[0].state, SlotState.TUNING)
        self.assertEqual(len(_FAKE_NAV.allocations), 1)

    def test_pretune_external_no_op_when_disabled(self):
        c, pool = _make_controller_with_test_pool()
        c._enabled = False
        c.pretune_external(FakeRef("1:0:1:X:0:0:0:0:0:0:"))
        self.assertEqual(len(_FAKE_NAV.allocations), 0)

    # ---- convergence with internal roles ----

    def test_pretune_external_skips_if_ref_in_next(self):
        c, pool = _make_controller_with_test_pool()
        ref = FakeRef("1:0:1:X:0:0:0:0:0:0:")
        pool.arm({Role.NEXT: [ref]})
        pool._mark_locked_optimistic(pool._slots_by_role[Role.NEXT][0])
        n_before = len(_FAKE_NAV.allocations)
        c.pretune_external(ref)
        # No new allocation - the convergence check found it in NEXT.
        self.assertEqual(len(_FAKE_NAV.allocations), n_before)
        # EXTERNAL slot stayed IDLE.
        self.assertEqual(pool._slots_by_role[Role.EXTERNAL][0].state,
                         SlotState.IDLE)

    def test_pretune_external_skips_if_ref_in_prev(self):
        c, pool = _make_controller_with_test_pool()
        ref = FakeRef("1:0:1:X:0:0:0:0:0:0:")
        pool.arm({Role.PREV: [ref]})
        pool._mark_locked_optimistic(pool._slots_by_role[Role.PREV][0])
        n_before = len(_FAKE_NAV.allocations)
        c.pretune_external(ref)
        self.assertEqual(len(_FAKE_NAV.allocations), n_before)

    def test_pretune_external_skips_if_ref_in_history(self):
        c, pool = _make_controller_with_test_pool()
        ref = FakeRef("1:0:1:X:0:0:0:0:0:0:")
        pool.arm({Role.HISTORY: [ref]})
        pool._mark_locked_optimistic(pool._slots_by_role[Role.HISTORY][0])
        n_before = len(_FAKE_NAV.allocations)
        c.pretune_external(ref)
        self.assertEqual(len(_FAKE_NAV.allocations), n_before)

    def test_pretune_external_same_ref_repeated_is_idempotent(self):
        c, pool = _make_controller_with_test_pool()
        ref = FakeRef("1:0:1:X:0:0:0:0:0:0:")
        c.pretune_external(ref)
        pool._mark_locked_optimistic(pool._slots_by_role[Role.EXTERNAL][0])
        n_after_first = len(_FAKE_NAV.allocations)
        c.pretune_external(ref)
        self.assertEqual(len(_FAKE_NAV.allocations), n_after_first,
                         "repeat call with same ref must not re-allocate")

    # ---- release_external ----

    def test_release_external_with_matching_ref_releases(self):
        c, pool = _make_controller_with_test_pool()
        ref = FakeRef("1:0:1:X:0:0:0:0:0:0:")
        c.pretune_external(ref)
        rec = _FAKE_NAV.allocations[0][1]
        c.release_external(ref)
        self.assertTrue(rec.stopped,
                        "matching ref must stop the recordable")
        self.assertEqual(pool._slots_by_role[Role.EXTERNAL][0].state,
                         SlotState.IDLE)

    def test_release_external_with_wrong_ref_no_op(self):
        c, pool = _make_controller_with_test_pool()
        ref = FakeRef("1:0:1:X:0:0:0:0:0:0:")
        c.pretune_external(ref)
        rec = _FAKE_NAV.allocations[0][1]
        c.release_external(FakeRef("1:0:1:Y:0:0:0:0:0:0:"))
        self.assertFalse(rec.stopped,
                         "non-matching ref must leave the slot alone "
                         "(race-safe against stale close events)")
        # State stays TUNING / LOCKED, not IDLE.
        self.assertIn(pool._slots_by_role[Role.EXTERNAL][0].state,
                      (SlotState.TUNING, SlotState.LOCKED))

    def test_release_external_without_arg_releases_unconditionally(self):
        c, pool = _make_controller_with_test_pool()
        c.pretune_external(FakeRef("1:0:1:X:0:0:0:0:0:0:"))
        rec = _FAKE_NAV.allocations[0][1]
        c.release_external(None)
        self.assertTrue(rec.stopped)
        self.assertEqual(pool._slots_by_role[Role.EXTERNAL][0].state,
                         SlotState.IDLE)

    def test_release_external_when_slot_empty_silent(self):
        c, pool = _make_controller_with_test_pool()
        # Must not raise even though no slot is armed.
        c.release_external(FakeRef("1:0:1:X:0:0:0:0:0:0:"))
        c.release_external(None)

    # ---- TTL refresh and expiry ----

    def test_ttl_timer_starts_on_pretune(self):
        c, pool = _make_controller_with_test_pool()
        c.pretune_external(FakeRef("1:0:1:X:0:0:0:0:0:0:"))
        self.assertIsNotNone(c._external_ttl_timer)
        self.assertTrue(c._external_ttl_timer._running)
        self.assertEqual(c._external_ttl_timer._interval, 300000)

    def test_ttl_timer_refreshes_on_repeated_pretune(self):
        c, pool = _make_controller_with_test_pool()
        ref = FakeRef("1:0:1:X:0:0:0:0:0:0:")
        c.pretune_external(ref)
        timer = c._external_ttl_timer
        # Simulate the timer running for a while (not actually firing).
        c.pretune_external(ref)
        # Same timer object, restarted. We can detect the restart by
        # the _running flag and that no expiry callback fired.
        self.assertIs(c._external_ttl_timer, timer)
        self.assertTrue(c._external_ttl_timer._running)

    def test_ttl_expiry_releases_external_slot(self):
        c, pool = _make_controller_with_test_pool()
        ref = FakeRef("1:0:1:X:0:0:0:0:0:0:")
        c.pretune_external(ref)
        rec = _FAKE_NAV.allocations[0][1]
        # Fire the TTL callback directly (the stub eTimer does not
        # actually count time).
        c._handle_external_ttl()
        self.assertTrue(rec.stopped, "TTL expiry must release the slot")
        self.assertEqual(pool._slots_by_role[Role.EXTERNAL][0].state,
                         SlotState.IDLE)

    # ---- evNewProgramInfo handler ----

    def test_evnewproginfo_releases_external_on_live_match(self):
        c, pool = _make_controller_with_test_pool()
        ref = FakeRef("1:0:1:X:0:0:0:0:0:0:")
        c.pretune_external(ref)
        rec = _FAKE_NAV.allocations[0][1]
        # Wire the event handler so _on_nav_event has the constant.
        c._wire_evnewproginfo()
        # Simulate the OK-press path: live service is now the EXTERNAL
        # ref and evNewProgramInfo fires from NavigationInstance.
        _FAKE_NAV._live_ref = ref
        from enigma import iPlayableService
        c._on_nav_event(iPlayableService.evNewProgramInfo)
        self.assertTrue(rec.stopped,
                        "live-ref match must release the EXTERNAL slot")
        self.assertEqual(pool._slots_by_role[Role.EXTERNAL][0].state,
                         SlotState.IDLE)

    def test_evnewproginfo_ignores_unrelated_event(self):
        c, pool = _make_controller_with_test_pool()
        ref = FakeRef("1:0:1:X:0:0:0:0:0:0:")
        c.pretune_external(ref)
        rec = _FAKE_NAV.allocations[0][1]
        c._wire_evnewproginfo()
        from enigma import iPlayableService
        # An unrelated event code must not touch the EXTERNAL slot.
        c._on_nav_event(iPlayableService.evTunedIn)
        self.assertFalse(rec.stopped,
                         "non-evNewProgramInfo event must leave the "
                         "EXTERNAL slot armed")

    def test_evnewproginfo_no_op_when_live_does_not_match(self):
        c, pool = _make_controller_with_test_pool()
        c.pretune_external(FakeRef("1:0:1:X:0:0:0:0:0:0:"))
        rec = _FAKE_NAV.allocations[0][1]
        c._wire_evnewproginfo()
        _FAKE_NAV._live_ref = FakeRef("1:0:1:Y:0:0:0:0:0:0:")
        from enigma import iPlayableService
        c._on_nav_event(iPlayableService.evNewProgramInfo)
        self.assertFalse(rec.stopped,
                         "live ref differs from EXTERNAL - slot must "
                         "stay armed")


if __name__ == "__main__":
    unittest.main()
