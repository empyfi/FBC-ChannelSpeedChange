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
if not hasattr(_cfg, "external_max_calls_per_sec"):
    _cfg.external_max_calls_per_sec = ConfigInteger(default=10)

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

    # ---- rate-limit defense ----

    def test_rate_limit_same_ref_in_quick_succession_no_extra_arm(self):
        """Same ref hammered in a tight loop must collapse: the
        first call arms, every follow-up within 100 ms reports as
        'idempotent' inside the limiter and never touches the
        pool.lookup / arm path.
        """
        c, pool = _make_controller_with_test_pool()
        ref = FakeRef("1:0:1:X:0:0:0:0:0:0:")
        c.pretune_external(ref)
        n_after_first = len(_FAKE_NAV.allocations)
        for _ in range(50):
            c.pretune_external(ref)
        self.assertEqual(len(_FAKE_NAV.allocations), n_after_first,
                         "50 same-ref re-calls must not allocate again")

    def test_rate_limit_burst_drops_calls_over_cap(self):
        """11 distinct refs in a tight loop with a cap of 10 must
        leave the 11th unhandled at the controller layer (i.e. no
        new pool.arm).
        """
        c, pool = _make_controller_with_test_pool()
        _cfg.external_max_calls_per_sec.value = 10
        try:
            for i in range(11):
                c.pretune_external(FakeRef("1:0:1:%d:0:0:0:0:0:0:" % i))
            armed_keys = {r.toString().split(":")[3]
                          for r, _ in _FAKE_NAV.allocations}
            self.assertLessEqual(len(armed_keys), 10,
                                 "burst cap (10) must drop the 11th "
                                 "distinct ref in the same window")
        finally:
            _cfg.external_max_calls_per_sec.value = 10

    def test_rate_limit_does_not_block_release(self):
        """Release calls must NOT be rate-limited - dropping a
        release would leak a slot. The limiter only guards the
        pretune path.
        """
        c, pool = _make_controller_with_test_pool()
        ref = FakeRef("1:0:1:X:0:0:0:0:0:0:")
        c.pretune_external(ref)
        rec = _FAKE_NAV.allocations[0][1]
        # 20 release calls in a tight loop - all must reach the slot.
        for _ in range(20):
            c.release_external(ref)
        self.assertTrue(rec.stopped,
                        "release path must not be throttled")

    # ---- watchdog isolation ----

    # ---- stats / heartbeat ----

    def test_stats_armed_counter_increments_on_arm(self):
        c, pool = _make_controller_with_test_pool()
        c.pretune_external(FakeRef("1:0:1:X:0:0:0:0:0:0:"))
        self.assertEqual(c._external_stats.calls_armed, 1)

    def test_stats_idempotent_counter_increments_on_repeat(self):
        c, pool = _make_controller_with_test_pool()
        ref = FakeRef("1:0:1:X:0:0:0:0:0:0:")
        c.pretune_external(ref)
        c.pretune_external(ref)  # repeat within 100 ms -> idempotent
        self.assertEqual(c._external_stats.calls_idempotent, 1)

    def test_stats_throttled_counter_increments_on_burst(self):
        c, pool = _make_controller_with_test_pool()
        _cfg.external_max_calls_per_sec.value = 3
        try:
            for i in range(5):
                c.pretune_external(FakeRef("1:0:1:%d:0:0:0:0:0:0:" % i))
            self.assertGreater(c._external_stats.calls_throttled, 0,
                               "throttled counter must reflect drops")
        finally:
            _cfg.external_max_calls_per_sec.value = 10

    def test_stats_convergence_skip_counter(self):
        c, pool = _make_controller_with_test_pool()
        ref = FakeRef("1:0:1:X:0:0:0:0:0:0:")
        pool.arm({Role.NEXT: [ref]})
        pool._mark_locked_optimistic(pool._slots_by_role[Role.NEXT][0])
        c.pretune_external(ref)
        self.assertEqual(c._external_stats.calls_convergence_skip, 1)

    def test_stats_explicit_release_counter(self):
        c, pool = _make_controller_with_test_pool()
        ref = FakeRef("1:0:1:X:0:0:0:0:0:0:")
        c.pretune_external(ref)
        c.release_external(ref)
        self.assertEqual(c._external_stats.releases_explicit, 1)

    def test_stats_evnewproginfo_release_counter(self):
        c, pool = _make_controller_with_test_pool()
        ref = FakeRef("1:0:1:X:0:0:0:0:0:0:")
        c.pretune_external(ref)
        c._wire_evnewproginfo()
        _FAKE_NAV._live_ref = ref
        from enigma import iPlayableService
        c._on_nav_event(iPlayableService.evNewProgramInfo)
        self.assertEqual(c._external_stats.releases_via_evnewproginfo, 1)
        self.assertEqual(c._external_stats.releases_explicit, 0,
                         "evNewProgramInfo path must NOT count as "
                         "an explicit release")

    def test_stats_ttl_release_counter(self):
        c, pool = _make_controller_with_test_pool()
        c.pretune_external(FakeRef("1:0:1:X:0:0:0:0:0:0:"))
        c._handle_external_ttl()
        self.assertEqual(c._external_stats.releases_via_ttl, 1)
        self.assertEqual(c._external_stats.releases_explicit, 0,
                         "TTL path must NOT count as an explicit "
                         "release (the controller bookkeeping "
                         "compensates for release_external itself "
                         "crediting explicit)")

    def test_stats_format_carries_key_terms(self):
        c, pool = _make_controller_with_test_pool()
        c.pretune_external(FakeRef("1:0:1:X:0:0:0:0:0:0:"))
        line = c._external_stats.format()
        # Forum reporters grep on these terms - keep them stable.
        for token in ("external stats", "calls", "armed", "throttled",
                      "TTL refreshes", "releases"):
            self.assertIn(token, line)

    def test_stats_reset_zeroes_everything(self):
        c, pool = _make_controller_with_test_pool()
        c.pretune_external(FakeRef("1:0:1:X:0:0:0:0:0:0:"))
        self.assertTrue(c._external_stats.has_activity())
        c._external_stats.reset()
        self.assertFalse(c._external_stats.has_activity())

    def test_flush_no_op_on_quiet_minute(self):
        c, pool = _make_controller_with_test_pool()
        captured = []
        import FBCChannelSpeedChange.controller as ctrl
        original = ctrl.info
        ctrl.info = lambda msg: captured.append(msg)
        try:
            c._flush_external_stats()
        finally:
            ctrl.info = original
        self.assertEqual(captured, [],
                         "no activity -> no info line, keeps the log "
                         "clean on idle minutes")

    def test_flush_emits_summary_when_active(self):
        c, pool = _make_controller_with_test_pool()
        c.pretune_external(FakeRef("1:0:1:X:0:0:0:0:0:0:"))
        captured = []
        import FBCChannelSpeedChange.controller as ctrl
        original = ctrl.info
        ctrl.info = lambda msg: captured.append(msg)
        try:
            c._flush_external_stats()
        finally:
            ctrl.info = original
        self.assertTrue(
            any("external stats" in m for m in captured),
            "active minute -> one info summary line")

    # ---- watchdog isolation ----

    def test_external_failure_does_not_increment_watchdog(self):
        """A pretune_external that raises inside the controller must
        not bump _consecutive_failures. The watchdog protects
        against internal bugs; external-caller misuse must not
        trip the 3-failure self-disable.
        """
        c, pool = _make_controller_with_test_pool()
        # Force the pool path to raise so the except branch fires.
        def raise_arm(plan):
            raise RuntimeError("simulated external misuse")
        c._pool.arm = raise_arm
        failures_before = c._consecutive_failures
        # Three calls would normally cross the self-disable threshold.
        for i in range(3):
            c.pretune_external(FakeRef("1:0:1:%d:0:0:0:0:0:0:" % i))
        self.assertEqual(c._consecutive_failures, failures_before,
                         "external failures must NOT touch the "
                         "internal watchdog counter")
        self.assertFalse(c._disabled_by_watchdog,
                         "external failures must NOT trigger the "
                         "self-disable")


if __name__ == "__main__":
    unittest.main()
