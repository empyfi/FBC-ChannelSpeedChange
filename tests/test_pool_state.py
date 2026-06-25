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
        self.record_args = []  # list of (ref, simulate, type) tuples
        self.stopped = []

    def recordService(self, ref, simulate=False, type=None):
        rec = FakeRecordable()
        self.allocations.append((ref, rec))
        self.record_args.append((ref, simulate, type))
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
            def recordService(self, ref, simulate=False, type=None):
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
            # Canonical 9-arg iRecordableService.prepare signature, verified
            # against openatv/enigma2 7.6 lib/python/RecordTimer.py:
            #   (filename, begin, end, eit_event_id,
            #    name, description, tags, descramble, record_ecm)
            # Locked here as a regression guard against accidentally
            # falling back to the 4-arg form (which leaks descramble=True
            # via the C++ default).
            self.assertEqual(len(rec.prepare_args), 9,
                             "prepare must be called with the 9-arg form")
            self.assertTrue(rec.prepare_args[0].startswith("/tmp/fbc_csc_pretune_"),
                            "prepare must receive a non-empty /tmp path")
            # Per-role identifying strings - lets OpenWebif /
            # active-recordings consumers attribute the recordable.
            self.assertEqual(rec.prepare_args[4], "FBC-CSC NEXT pretune")
            self.assertEqual(rec.prepare_args[5],
                             "Plugin background tune; not a user recording.")
            self.assertEqual(rec.prepare_args[6], "fbc-csc-pretune")
            self.assertFalse(rec.prepare_args[7],
                             "descramble defaults False so the CA path stays disengaged")
            self.assertFalse(rec.prepare_args[8],
                             "record_ecm must stay False")
        finally:
            _cfg.use_real_pretune.value = False

    def test_prewarm_descrambler_history_only(self):
        """HISTORY-only opt-in is the cardsharing-safe configuration:
        only the HISTORY slot pre-engages the descrambler; NEXT and
        PREV stay at descramble=False so the high-burst per-zap
        rotation never adds ECM heat.
        """
        pool, nav, _ = make_pool(fbc_count=4)
        pool.configure({Role.NEXT: 1, Role.PREV: 1, Role.HISTORY: 1})
        _cfg.use_real_pretune.value = True
        _cfg.prewarm_descrambler_history.value = True
        try:
            pool.arm({
                Role.NEXT: [FakeRef("1:0:1:N:0:0:0:0:0:0:")],
                Role.PREV: [FakeRef("1:0:1:P:0:0:0:0:0:0:")],
                Role.HISTORY: [FakeRef("1:0:1:H:0:0:0:0:0:0:")],
            })
            by_ref = {ref.toString(): rec for ref, rec in nav.allocations}
            self.assertTrue(by_ref["1:0:1:H:0:0:0:0:0:0:"].prepare_args[7],
                            "HISTORY opt-in -> descramble=True")
            self.assertFalse(by_ref["1:0:1:N:0:0:0:0:0:0:"].prepare_args[7],
                             "NEXT not opted in -> descramble=False")
            self.assertFalse(by_ref["1:0:1:P:0:0:0:0:0:0:"].prepare_args[7],
                             "PREV not opted in -> descramble=False")
        finally:
            _cfg.prewarm_descrambler_history.value = False
            _cfg.use_real_pretune.value = False

    def test_prewarm_descrambler_all_on(self):
        """All three opt-ins on - every role pre-engages the descrambler.
        This is the v0.3.7-equivalent behaviour for users with a verified
        multi-decode capable card and no cardsharing concern.
        """
        pool, nav, _ = make_pool(fbc_count=4)
        pool.configure({Role.NEXT: 1, Role.PREV: 1, Role.HISTORY: 1})
        _cfg.use_real_pretune.value = True
        _cfg.prewarm_descrambler_history.value = True
        _cfg.prewarm_descrambler_next.value = True
        _cfg.prewarm_descrambler_prev.value = True
        try:
            pool.arm({
                Role.NEXT: [FakeRef("1:0:1:N:0:0:0:0:0:0:")],
                Role.PREV: [FakeRef("1:0:1:P:0:0:0:0:0:0:")],
                Role.HISTORY: [FakeRef("1:0:1:H:0:0:0:0:0:0:")],
            })
            for ref, rec in nav.allocations:
                self.assertTrue(rec.prepare_args[7],
                                "all opt-ins on -> descramble=True for every role")
        finally:
            _cfg.prewarm_descrambler_history.value = False
            _cfg.prewarm_descrambler_next.value = False
            _cfg.prewarm_descrambler_prev.value = False
            _cfg.use_real_pretune.value = False

    def test_indicator_style_default_passes_pseudo_record_type(self):
        """Default pretune_indicator_style is 'pseudo' so the painter
        renders the entry with colorServicePseudoRecorded (light blue)
        instead of the red colorServiceRecorded. The pool must pass
        pNavigation.isPseudoRecording (=4) as the type argument.
        """
        from enigma import pNavigation
        pool, nav, _ = make_pool()
        pool.configure({Role.NEXT: 1, Role.PREV: 0, Role.HISTORY: 0})
        # default came in from config init; assert it explicitly
        self.assertEqual(_cfg.pretune_indicator_style.value, "pseudo")
        pool.arm({Role.NEXT: [FakeRef("1:0:1:A:0:0:0:0:0:0:A")]})
        self.assertEqual(len(nav.record_args), 1)
        ref, simulate, type_flag = nav.record_args[0]
        self.assertFalse(simulate)
        self.assertEqual(type_flag, pNavigation.isPseudoRecording)

    def test_indicator_style_hidden_passes_fastzap_record_type(self):
        from enigma import pNavigation
        pool, nav, _ = make_pool()
        pool.configure({Role.NEXT: 1, Role.PREV: 0, Role.HISTORY: 0})
        _cfg.pretune_indicator_style.value = "hidden"
        try:
            pool.arm({Role.NEXT: [FakeRef("1:0:1:A:0:0:0:0:0:0:A")]})
            _, _, type_flag = nav.record_args[0]
            self.assertEqual(type_flag, pNavigation.isFromSpecialJumpFastZap)
        finally:
            _cfg.pretune_indicator_style.value = "pseudo"

    def test_indicator_style_recorded_passes_unknown_record_type(self):
        """The 'recorded' choice restores the pre-v0.6.0 behaviour by
        passing isUnknownRecording (=8), which the painter sees as a
        real recording and paints red.
        """
        from enigma import pNavigation
        pool, nav, _ = make_pool()
        pool.configure({Role.NEXT: 1, Role.PREV: 0, Role.HISTORY: 0})
        _cfg.pretune_indicator_style.value = "recorded"
        try:
            pool.arm({Role.NEXT: [FakeRef("1:0:1:A:0:0:0:0:0:0:A")]})
            _, _, type_flag = nav.record_args[0]
            self.assertEqual(type_flag, pNavigation.isUnknownRecording)
        finally:
            _cfg.pretune_indicator_style.value = "pseudo"

    def test_indicator_type_falls_back_when_constant_missing(self):
        """If the user-saved value points to a pNavigation constant
        that the running enigma2 build does not expose (rare - only on
        a build downgrade after the value was saved), _indicator_type()
        must fall back to isUnknownRecording rather than throw or pass
        a meaningless flag.
        """
        from enigma import pNavigation
        from FBCChannelSpeedChange.fbc_pretune_pool import _indicator_type
        saved_fastzap = pNavigation.isFromSpecialJumpFastZap
        _cfg.pretune_indicator_style.value = "hidden"
        try:
            del pNavigation.isFromSpecialJumpFastZap
            self.assertEqual(_indicator_type(), pNavigation.isUnknownRecording,
                             "missing constant -> fall back to unknown (red)")
        finally:
            pNavigation.isFromSpecialJumpFastZap = saved_fastzap
            _cfg.pretune_indicator_style.value = "pseudo"

    def test_indicator_choices_filtered_when_fastzap_missing(self):
        """The ConfigSelection helper hides 'hidden' from the dropdown
        if pNavigation does not expose isFromSpecialJumpFastZap, so the
        user never picks a value that would silently fall back.
        """
        from enigma import pNavigation
        from FBCChannelSpeedChange.config import _indicator_choices_and_default
        saved = pNavigation.isFromSpecialJumpFastZap
        try:
            del pNavigation.isFromSpecialJumpFastZap
            choices, default = _indicator_choices_and_default()
            keys = [k for k, _label in choices]
            self.assertNotIn("hidden", keys)
            self.assertIn("pseudo", keys)
            self.assertIn("recorded", keys)
            self.assertEqual(default, "pseudo")
        finally:
            pNavigation.isFromSpecialJumpFastZap = saved

    def test_indicator_choices_default_recorded_when_pseudo_missing(self):
        """Extreme degradation: neither pseudo nor fastzap available.
        Only 'recorded' remains and becomes the forced default.
        """
        from enigma import pNavigation
        from FBCChannelSpeedChange.config import _indicator_choices_and_default
        saved_pseudo = pNavigation.isPseudoRecording
        saved_fastzap = pNavigation.isFromSpecialJumpFastZap
        try:
            del pNavigation.isPseudoRecording
            del pNavigation.isFromSpecialJumpFastZap
            choices, default = _indicator_choices_and_default()
            self.assertEqual(choices, [("recorded", "Red (treated as recording)")])
            self.assertEqual(default, "recorded")
        finally:
            pNavigation.isPseudoRecording = saved_pseudo
            pNavigation.isFromSpecialJumpFastZap = saved_fastzap

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
            def recordService(self, ref, simulate=False, type=None):
                return None
        nav = StingyNav()
        pool = FBCPreTunePool(
            nav_provider=lambda: nav,
            nim_manager_provider=lambda: nim,
        )
        pool.configure({Role.NEXT: 1, Role.PREV: 0, Role.HISTORY: 0})
        pool.arm({Role.NEXT: [FakeRef("1:0:1:A:0:0:0:0:0:0:A")]})
        self.assertEqual(pool._slots_by_role[Role.NEXT][0].state, SlotState.IDLE)

    def test_external_role_configurable_and_armed(self):
        """Role.EXTERNAL is wired the same way as NEXT/PREV/HISTORY:
        configure() reserves the bucket, arm() with an EXTERNAL ref
        allocates a recordable and transitions to TUNING. No new
        plumbing inside the pool - the role is just another key in
        the by-role dict.
        """
        pool, nav, _ = make_pool()
        pool.configure({Role.EXTERNAL: 1})
        self.assertEqual(len(pool._slots_by_role[Role.EXTERNAL]), 1)
        ref = FakeRef("1:0:1:X:0:0:0:0:0:0:X")
        pool.arm({Role.EXTERNAL: [ref]})
        slot = pool._slots_by_role[Role.EXTERNAL][0]
        self.assertEqual(slot.state, SlotState.TUNING)
        self.assertEqual(len(nav.allocations), 1)
        pool._mark_locked_optimistic(slot)
        self.assertEqual(slot.state, SlotState.LOCKED)

    def test_external_role_lookup(self):
        """Lookup is role-agnostic - an EXTERNAL slot answers a hit
        the same way a NEXT slot would. The interceptor / controller
        never has to know the slot's role to find a HIT.
        """
        pool, _, _ = make_pool()
        pool.configure({Role.EXTERNAL: 1})
        ref = FakeRef("1:0:1:X:0:0:0:0:0:0:X")
        pool.arm({Role.EXTERNAL: [ref]})
        pool._mark_locked_optimistic(pool._slots_by_role[Role.EXTERNAL][0])
        slot = pool.lookup(FakeRef("1:0:1:X:0:0:0:0:0:0:Renamed"))
        self.assertIsNotNone(slot)
        self.assertEqual(slot.role, Role.EXTERNAL)
        self.assertEqual(slot.state, SlotState.LOCKED)

    def test_release_slot_unlinks_file_even_if_recordable_stop_raises(self):
        """Belt-and-suspenders: a faulty recordable.stop() must not
        prevent the .ts file unlink. Per-stage isolation in
        _release_slot keeps each cleanup step independent.
        """
        import os
        import tempfile
        pool, nav, _ = make_pool()
        pool.configure({Role.NEXT: 1})
        pool.arm({Role.NEXT: [FakeRef("1:0:1:A:0:0:0:0:0:0:A")]})
        slot = pool._slots_by_role[Role.NEXT][0]
        fd, path = tempfile.mkstemp(prefix="fbc_csc_pretune_test_",
                                    suffix=".ts")
        os.close(fd)
        try:
            slot._tmp_file = path
            slot._started = True

            class BoomRecordable(FakeRecordable):
                def stop(self):
                    raise RuntimeError("simulated stop failure")

            slot.recordable = BoomRecordable()
            pool._release_slot(slot, keep_object=True)
            self.assertFalse(os.path.exists(path),
                             "tmp file must be unlinked even if "
                             "recordable.stop raises")
            self.assertEqual(slot.state, SlotState.IDLE)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_sweep_stale_pretune_files_removes_leftovers(self):
        """Startup sweep removes leftover pretune files matching the
        pool's filename pattern, leaves unrelated files alone.
        """
        import os
        import shutil
        import tempfile
        from FBCChannelSpeedChange import fbc_pretune_pool as pool_mod
        d = tempfile.mkdtemp()
        try:
            a = os.path.join(d, "fbc_csc_pretune_next_1234567890.ts")
            b = os.path.join(d, "fbc_csc_pretune_prev_0987654321.ts.ap")
            c = os.path.join(d, "unrelated_file.ts")
            for p in (a, b, c):
                with open(p, "w") as fh:
                    fh.write("x")
            removed = pool_mod._sweep_stale_pretune_files(directory=d)
            self.assertIn(a, removed)
            self.assertIn(b, removed)
            self.assertNotIn(c, removed)
            self.assertFalse(os.path.exists(a))
            self.assertFalse(os.path.exists(b))
            self.assertTrue(os.path.exists(c),
                            "unrelated file must be left alone")
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_external_role_release_after_swap(self):
        """After a HIT on an EXTERNAL slot, release_after_swap brings
        it back to IDLE and stops the recordable, exactly as for the
        internal roles. The controller calls this on evNewProgramInfo
        for the shortcut-zap path; the api's ReleaseSingleChannel
        triggers the same teardown.
        """
        pool, nav, _ = make_pool()
        pool.configure({Role.EXTERNAL: 1})
        ref = FakeRef("1:0:1:X:0:0:0:0:0:0:X")
        pool.arm({Role.EXTERNAL: [ref]})
        rec = nav.allocations[0][1]
        pool._mark_locked_optimistic(pool._slots_by_role[Role.EXTERNAL][0])
        slot = pool.confirm_hit(FakeRef("1:0:1:X:0:0:0:0:0:0:X"))
        self.assertIsNotNone(slot)
        pool.release_after_swap(slot)
        self.assertEqual(pool._slots_by_role[Role.EXTERNAL][0].state,
                         SlotState.IDLE)
        self.assertTrue(rec.stopped)


if __name__ == "__main__":
    unittest.main()
