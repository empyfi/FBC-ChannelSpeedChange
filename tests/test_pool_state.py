import unittest

from _enigma_stubs import bootstrap
bootstrap()

from FBCChannelSpeedChange.config import cfg as _cfg
_cfg.allow_pretune.value = True  # tests cover the active-allocation path

from FBCChannelSpeedChange.fbc_pretune_pool import FBCPreTunePool, Role, SlotState


class FakeRef:
    def __init__(self, s):
        self._s = s

    def toString(self):
        return self._s


class FakeRecordable:
    """Stand-in for iRecordableServicePtr."""

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


class FakeNav:
    def __init__(self):
        self.played = []
        self.allocations = []  # list of (ref, recordable)
        self.stopped = []

    def recordService(self, ref):
        rec = FakeRecordable()
        self.allocations.append((ref, rec))
        return rec

    def stopRecordService(self, rec):
        rec.stopped = True
        self.stopped.append(rec)

    def playService(self, ref):
        self.played.append(ref)


class FakeNim:
    def __init__(self, is_fbc, enabled=True):
        self._fbc = is_fbc
        self._enabled = enabled

    def isFBCTuner(self):
        return self._fbc

    def isFBCRoot(self):
        return False

    def isFBCLink(self):
        return False

    def isEnabled(self):
        return self._enabled


class FakeNimManager:
    def __init__(self, slots):
        self.nim_slots = slots


def make_pool(fbc_count=2):
    slots = [FakeNim(is_fbc=True) for _ in range(fbc_count)]
    nim = FakeNimManager(slots)
    nav = FakeNav()
    pool = FBCPreTunePool(
        nav_provider=lambda: nav,
        nim_manager_provider=lambda: nim,
    )
    return pool, nav, nim


class PoolTests(unittest.TestCase):

    def test_configure_creates_slots(self):
        pool, _, _ = make_pool()
        pool.configure({Role.NEXT: 1, Role.PREV: 1, Role.HISTORY: 1})
        self.assertEqual(len(pool._slots_by_role[Role.NEXT]), 1)
        self.assertEqual(len(pool._slots_by_role[Role.PREV]), 1)
        self.assertEqual(len(pool._slots_by_role[Role.HISTORY]), 1)

    def test_arm_allocates_and_transitions_to_locked(self):
        pool, nav, _ = make_pool()
        pool.configure({Role.NEXT: 1, Role.PREV: 0, Role.HISTORY: 0})
        ref = FakeRef("1:0:1:A:0:0:0:0:0:0:A")
        pool.arm({Role.NEXT: [ref]})
        slot = pool._slots_by_role[Role.NEXT][0]
        self.assertEqual(slot.state, SlotState.TUNING)
        self.assertEqual(len(nav.allocations), 1)
        # Simulate the 1500ms optimistic-lock timer firing.
        pool._mark_locked_optimistic(slot)
        self.assertEqual(slot.state, SlotState.LOCKED)

    def test_lookup_hits_on_matching_key(self):
        pool, nav, _ = make_pool()
        pool.configure({Role.NEXT: 1, Role.PREV: 0, Role.HISTORY: 0})
        ref = FakeRef("1:0:1:A:0:0:0:0:0:0:A")
        pool.arm({Role.NEXT: [ref]})
        pool._mark_locked_optimistic(pool._slots_by_role[Role.NEXT][0])
        result = pool.lookup(FakeRef("1:0:1:A:0:0:0:0:0:0:Renamed"))
        self.assertIsNotNone(result)
        self.assertEqual(result.state, SlotState.LOCKED)

    def test_lookup_misses_on_different_key(self):
        pool, _, _ = make_pool()
        pool.configure({Role.NEXT: 1, Role.PREV: 0, Role.HISTORY: 0})
        pool.arm({Role.NEXT: [FakeRef("1:0:1:A:0:0:0:0:0:0:A")]})
        self.assertIsNone(pool.lookup(FakeRef("1:0:1:Z:0:0:0:0:0:0:Z")))

    def test_swap_in_plays_without_stopping_recordable(self):
        # swap_in does NOT stop the recordable before calling
        # playService - stopping first destroys the pretune effect
        # because enigma2 then re-allocates a fresh demod. The
        # recordable stays alive so eDVBResourceManager's channel-
        # sharing path kicks in. The recordable is torn down later
        # by the next arm() cycle via _release_slot.
        pool, nav, _ = make_pool()
        pool.configure({Role.NEXT: 1, Role.PREV: 0, Role.HISTORY: 0})
        ref = FakeRef("1:0:1:A:0:0:0:0:0:0:A")
        pool.arm({Role.NEXT: [ref]})
        rec = nav.allocations[0][1]
        pool._mark_locked_optimistic(pool._slots_by_role[Role.NEXT][0])
        ok = pool.swap_in(FakeRef("1:0:1:A:0:0:0:0:0:0:A"))
        self.assertTrue(ok)
        self.assertEqual(len(nav.played), 1, "playService must fire exactly once")
        # The slot is logically released (its role is consumed) but the
        # implementation still calls stopRecordService on the way out so
        # the file handle does not leak. That happens AFTER playService.
        self.assertTrue(rec.stopped)
        self.assertEqual(pool._slots_by_role[Role.NEXT][0].state, SlotState.IDLE)

    def test_confirm_hit_does_not_touch_nav(self):
        # confirm_hit() returns the slot without calling playService
        # - the caller (interceptor) must call the original zap
        # method instead so servicelist.history and other
        # bookkeeping stay correct.
        pool, nav, _ = make_pool()
        pool.configure({Role.NEXT: 1, Role.PREV: 0, Role.HISTORY: 0})
        pool.arm({Role.NEXT: [FakeRef("1:0:1:A:0:0:0:0:0:0:A")]})
        pool._mark_locked_optimistic(pool._slots_by_role[Role.NEXT][0])
        slot = pool.confirm_hit(FakeRef("1:0:1:A:0:0:0:0:0:0:A"))
        self.assertIsNotNone(slot)
        self.assertEqual(slot.role, Role.NEXT)
        self.assertEqual(len(nav.played), 0,
                         "confirm_hit must NOT call playService")
        # release_after_swap finalises the slot once the caller has
        # done the zap themselves
        pool.release_after_swap(slot)
        self.assertEqual(pool._slots_by_role[Role.NEXT][0].state, SlotState.IDLE)

    def test_swap_in_calls_playservice_before_stopping(self):
        # Verify ordering: playService must happen BEFORE stopRecordService
        # so the channel-sharing path inside eDVBResourceManager sees the
        # recordable still active on the target transponder.
        events = []
        class OrderingNav:
            def __init__(self):
                self.played = []
                self.allocations = []
                self.stopped = []
            def recordService(self, ref):
                rec = FakeRecordable()
                self.allocations.append((ref, rec))
                return rec
            def stopRecordService(self, rec):
                events.append("stopRecordService")
                rec.stopped = True
                self.stopped.append(rec)
            def playService(self, ref):
                events.append("playService")
                self.played.append(ref)

        nim = FakeNimManager([FakeNim(is_fbc=True)])
        nav = OrderingNav()
        pool = FBCPreTunePool(nav_provider=lambda: nav, nim_manager_provider=lambda: nim)
        pool.configure({Role.NEXT: 1, Role.PREV: 0, Role.HISTORY: 0})
        pool.arm({Role.NEXT: [FakeRef("1:0:1:A:0:0:0:0:0:0:A")]})
        pool._mark_locked_optimistic(pool._slots_by_role[Role.NEXT][0])
        pool.swap_in(FakeRef("1:0:1:A:0:0:0:0:0:0:A"))
        # The first nav-touching call after the hit must be playService.
        self.assertEqual(events[0], "playService",
                         "playService must happen before stopRecordService")

    def test_swap_in_returns_false_on_miss(self):
        pool, nav, _ = make_pool()
        pool.configure({Role.NEXT: 1, Role.PREV: 0, Role.HISTORY: 0})
        ok = pool.swap_in(FakeRef("1:0:1:Z:0:0:0:0:0:0:Z"))
        self.assertFalse(ok)
        self.assertEqual(len(nav.played), 0)

    def test_release_for_stops_all_recordables(self):
        pool, nav, _ = make_pool()
        pool.configure({Role.NEXT: 1, Role.PREV: 1, Role.HISTORY: 0})
        pool.arm({Role.NEXT: [FakeRef("1:0:1:A:0:0:0:0:0:0:A")],
                  Role.PREV: [FakeRef("1:0:1:B:0:0:0:0:0:0:B")]})
        self.assertEqual(len(nav.allocations), 2)
        pool.release_for("test")
        self.assertEqual(len(nav.stopped), 2)
        for slots in pool._slots_by_role.values():
            for slot in slots:
                self.assertEqual(slot.state, SlotState.IDLE)
                self.assertIsNone(slot.recordable)

    def test_no_allocation_without_fbc_slot(self):
        nim = FakeNimManager([FakeNim(is_fbc=False)])
        nav = FakeNav()
        pool = FBCPreTunePool(
            nav_provider=lambda: nav,
            nim_manager_provider=lambda: nim,
        )
        pool.configure({Role.NEXT: 1, Role.PREV: 0, Role.HISTORY: 0})
        pool.arm({Role.NEXT: [FakeRef("1:0:1:A:0:0:0:0:0:0:A")]})
        self.assertEqual(len(nav.allocations), 0)
        self.assertEqual(pool._slots_by_role[Role.NEXT][0].state, SlotState.IDLE)

    def test_no_allocation_when_fbc_disabled(self):
        nim = FakeNimManager([FakeNim(is_fbc=True, enabled=False)])
        nav = FakeNav()
        pool = FBCPreTunePool(
            nav_provider=lambda: nav,
            nim_manager_provider=lambda: nim,
        )
        pool.configure({Role.NEXT: 1, Role.PREV: 0, Role.HISTORY: 0})
        pool.arm({Role.NEXT: [FakeRef("1:0:1:A:0:0:0:0:0:0:A")]})
        self.assertEqual(len(nav.allocations), 0)

    def test_suppress_role_releases_and_blocks_arm(self):
        pool, nav, _ = make_pool()
        pool.configure({Role.NEXT: 1, Role.PREV: 0, Role.HISTORY: 0})
        pool.arm({Role.NEXT: [FakeRef("1:0:1:A:0:0:0:0:0:0:A")]})
        self.assertEqual(len(nav.allocations), 1)
        pool.suppress([Role.NEXT])
        self.assertEqual(pool._slots_by_role[Role.NEXT][0].state, SlotState.IDLE)
        n_before = len(nav.allocations)
        pool.arm({Role.NEXT: [FakeRef("1:0:1:A:0:0:0:0:0:0:A")]})
        self.assertEqual(len(nav.allocations), n_before)
        pool.unsuppress([Role.NEXT])
        pool.arm({Role.NEXT: [FakeRef("1:0:1:A:0:0:0:0:0:0:A")]})
        self.assertEqual(len(nav.allocations), n_before + 1)

    def test_arm_skips_redundant_retune(self):
        pool, nav, _ = make_pool()
        pool.configure({Role.NEXT: 1, Role.PREV: 0, Role.HISTORY: 0})
        ref = FakeRef("1:0:1:A:0:0:0:0:0:0:A")
        pool.arm({Role.NEXT: [ref]})
        pool._mark_locked_optimistic(pool._slots_by_role[Role.NEXT][0])
        pool.arm({Role.NEXT: [FakeRef("1:0:1:A:0:0:0:0:0:0:Renamed")]})
        self.assertEqual(len(nav.allocations), 1)

    def test_use_real_pretune_off_skips_prepare(self):
        pool, nav, _ = make_pool()
        pool.configure({Role.NEXT: 1, Role.PREV: 0, Role.HISTORY: 0})
        _cfg.use_real_pretune.value = False
        try:
            pool.arm({Role.NEXT: [FakeRef("1:0:1:A:0:0:0:0:0:0:A")]})
            rec = nav.allocations[0][1]
            self.assertFalse(rec.prepared, "prepare must not be called when use_real_pretune is off")
            self.assertFalse(rec.started)
        finally:
            _cfg.use_real_pretune.value = False

    def test_use_real_pretune_on_calls_prepare_and_start(self):
        pool, nav, _ = make_pool()
        pool.configure({Role.NEXT: 1, Role.PREV: 0, Role.HISTORY: 0})
        _cfg.use_real_pretune.value = True
        try:
            pool.arm({Role.NEXT: [FakeRef("1:0:1:A:0:0:0:0:0:0:A")]})
            rec = nav.allocations[0][1]
            self.assertTrue(rec.prepared, "prepare must be called when use_real_pretune is on")
            self.assertTrue(rec.started, "start must be called after successful prepare")
            self.assertIsNotNone(rec.prepare_args)
            self.assertTrue(rec.prepare_args[0].startswith("/tmp/fbc_csc_pretune_"),
                            "prepare must receive a non-empty /tmp path")
        finally:
            _cfg.use_real_pretune.value = False

    def test_release_stops_started_recordable(self):
        pool, nav, _ = make_pool()
        pool.configure({Role.NEXT: 1, Role.PREV: 0, Role.HISTORY: 0})
        _cfg.use_real_pretune.value = True
        try:
            pool.arm({Role.NEXT: [FakeRef("1:0:1:A:0:0:0:0:0:0:A")]})
            rec = nav.allocations[0][1]
            self.assertTrue(rec.started)
            pool.release_for("test")
            self.assertTrue(rec.stop_called, "stop() should be called before stopRecordService")
            self.assertTrue(rec.stopped)
        finally:
            _cfg.use_real_pretune.value = False

    def test_master_switch_off_blocks_allocation(self):
        pool, nav, _ = make_pool()
        pool.configure({Role.NEXT: 1, Role.PREV: 0, Role.HISTORY: 0})
        _cfg.allow_pretune.value = False
        try:
            pool.arm({Role.NEXT: [FakeRef("1:0:1:A:0:0:0:0:0:0:A")]})
            self.assertEqual(len(nav.allocations), 0,
                             "allow_pretune=False must prevent recordService")
            self.assertEqual(pool._slots_by_role[Role.NEXT][0].state, SlotState.IDLE)
        finally:
            _cfg.allow_pretune.value = True

    def test_recordService_returning_none_leaves_slot_idle(self):
        nim = FakeNimManager([FakeNim(is_fbc=True)])
        class StingyNav(FakeNav):
            def recordService(self, ref):
                return None
        nav = StingyNav()
        pool = FBCPreTunePool(
            nav_provider=lambda: nav,
            nim_manager_provider=lambda: nim,
        )
        pool.configure({Role.NEXT: 1, Role.PREV: 0, Role.HISTORY: 0})
        pool.arm({Role.NEXT: [FakeRef("1:0:1:A:0:0:0:0:0:0:A")]})
        self.assertEqual(pool._slots_by_role[Role.NEXT][0].state, SlotState.IDLE)


if __name__ == "__main__":
    unittest.main()
