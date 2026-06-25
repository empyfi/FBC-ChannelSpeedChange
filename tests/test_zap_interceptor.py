"""Tests for the zap-interceptor's OSD-honesty path.

The interceptor labels external zaps (history selector / Last-Channel
button, EPG OK, NumberZap OK, FCC-Extender-driven api zap) as HIT in
the OSD / CSV when the pool currently holds a matching slot - the
channel-share path is what delivers the speedup, and the user
deserves an honest "HIT" instead of a neutral "EXT". Previously every
bypass zap was labelled EXT regardless of pool wirkung, which
under-credited the pool for history recalls especially.
"""

import os
import sys
import types
import unittest

from _enigma_stubs import bootstrap
bootstrap()

from FBCChannelSpeedChange.config import cfg as _cfg
_cfg.allow_pretune.value = True
_cfg.use_real_pretune.value = False  # off-box; skip prepare/start

from FBCChannelSpeedChange.fbc_pretune_pool import FBCPreTunePool, Role
from FBCChannelSpeedChange import zap_interceptor as zi


class FakeRef:
    def __init__(self, s):
        self._s = s

    def toString(self):
        return self._s


class FakeNav:
    def __init__(self, current_ref=None):
        self._current_ref = current_ref
        self.played = []
        self.allocations = []

    def recordService(self, ref, simulate=False, type=None):
        rec = _FakeRecordable()
        self.allocations.append((ref, rec))
        return rec

    def stopRecordService(self, rec):
        rec.stopped = True

    def playService(self, ref):
        self.played.append(ref)
        self._current_ref = ref

    def getCurrentlyPlayingServiceReference(self):
        return self._current_ref


class _FakeRecordable:
    def __init__(self):
        self.stopped = False

    def getError(self):
        return 0

    def frontendInfo(self):
        return None


class FakeNim:
    def isFBCTuner(self): return True
    def isFBCRoot(self):  return False
    def isFBCLink(self):  return False
    def isEnabled(self):  return True


class FakeNimManager:
    def __init__(self):
        self.nim_slots = [FakeNim()]


def _install_nav(fake_nav):
    """Install a fake NavigationInstance module so the interceptor's
    `import NavigationInstance` lookup returns the fake. Idempotent
    across test methods.
    """
    ni = types.ModuleType("NavigationInstance")
    ni.instance = fake_nav
    sys.modules["NavigationInstance"] = ni


class _StubInterceptor:
    """Minimal stand-in for ZapInterceptor that carries just the fields
    `_record_zap_timing` reads. Avoids pulling InfoBar / Session /
    osd_timing into the unit-test surface so the (ext, hit, near) ->
    hit_str mapping can be exercised in isolation.
    """

    def __init__(self):
        self._zap_start_ns = 0
        self._zap_attr = None
        self._zap_hit = None
        self._zap_near = False
        self._on_zap = None
        self._infobar = None
        self.emitted_rows = []
        self._osd_calls = []

    def _maybe_show_osd(self, attr, hit_str, delta_ms):
        self._osd_calls.append((attr, hit_str, delta_ms))


class ExtZapLabelMapping(unittest.TestCase):
    """`_record_zap_timing` maps (attr=ext, hit=True/False) to HIT/EXT
    so the OSD bucket-colours pool-delivered bypass zaps by latency
    and only neutral-cyan-labels the genuine bypass case.
    """

    def setUp(self):
        # Redirect CSV writes to a per-test temp file so the real
        # /tmp/fbc_csc_timing.csv is never touched.
        import tempfile
        fd, self._csv = tempfile.mkstemp(prefix="fbc_csc_int_test_",
                                         suffix=".csv")
        os.close(fd)
        self._orig_csv = zi._TIMING_CSV
        zi._TIMING_CSV = self._csv

    def tearDown(self):
        zi._TIMING_CSV = self._orig_csv
        try:
            os.unlink(self._csv)
        except OSError:
            pass

    def _call_record(self, attr, hit, near=False):
        stub = _StubInterceptor()
        stub._zap_start_ns = 1  # any non-None monotonic value
        stub._zap_attr = attr
        stub._zap_hit = hit
        stub._zap_near = near
        zi.ZapInterceptor._record_zap_timing(stub)
        return stub

    def test_ext_with_pool_hit_labels_as_HIT(self):
        stub = self._call_record(attr="ext", hit=True)
        self.assertEqual(len(stub._osd_calls), 1)
        _attr, hit_str, _delta = stub._osd_calls[0]
        self.assertEqual(hit_str, "HIT",
                         "ext zap that the pool delivered must "
                         "label as HIT, not EXT")

    def test_ext_without_pool_hit_keeps_EXT(self):
        stub = self._call_record(attr="ext", hit=False, near=False)
        self.assertEqual(len(stub._osd_calls), 1)
        _attr, hit_str, _delta = stub._osd_calls[0]
        self.assertEqual(hit_str, "EXT",
                         "genuine bypass with no pool match keeps "
                         "the neutral EXT label")

    def test_ext_with_tp_match_labels_as_NEAR(self):
        """Bypass zap: pool.lookup miss but pool.tp_match hit ->
        NEAR (intra-TP channel-share via sibling slot).
        """
        stub = self._call_record(attr="ext", hit=False, near=True)
        self.assertEqual(stub._osd_calls[0][1], "NEAR")

    def test_wrapper_miss_with_tp_match_labels_as_NEAR(self):
        """Wrapper zapDown predictor returned None or pool empty
        -> hit=False. If a sibling slot is on the same transponder
        as the live ref, the cold-tune fallback path still benefits
        from channel-share. Label NEAR, not MISS.
        """
        stub = self._call_record(attr="zapDown", hit=False, near=True)
        self.assertEqual(stub._osd_calls[0][1], "NEAR")

    def test_wrapper_miss_without_tp_match_labels_as_MISS(self):
        stub = self._call_record(attr="zapDown", hit=False, near=False)
        self.assertEqual(stub._osd_calls[0][1], "MISS")

    def test_hit_takes_priority_over_near(self):
        """If both hit and near are True (would not happen in
        practice - hit precludes near probing - but worth pinning),
        HIT must win.
        """
        stub = self._call_record(attr="ext", hit=True, near=True)
        self.assertEqual(stub._osd_calls[0][1], "HIT")

    def test_wrapped_zap_unaffected(self):
        """Sanity guard: zapUp/zapDown HIT path is untouched."""
        stub = self._call_record(attr="zapDown", hit=True)
        self.assertEqual(stub._osd_calls[0][1], "HIT")


class PoolHitDetection(unittest.TestCase):
    """`_pool_hit_for_current_service` returns True iff the pool's
    role-agnostic lookup matches the currently-playing service ref.
    Drives the evStart-side HIT classification.
    """

    def _make_interceptor(self, pool, nav):
        _install_nav(nav)
        return zi.ZapInterceptor(pool=pool, predictor=None)

    def _make_pool(self, armed_ref=None):
        pool = FBCPreTunePool(
            nav_provider=lambda: FakeNav(),
            nim_manager_provider=lambda: FakeNimManager(),
        )
        pool.configure({Role.EXTERNAL: 1})
        if armed_ref is not None:
            pool.arm({Role.EXTERNAL: [armed_ref]})
            pool._mark_locked_optimistic(
                pool._slots_by_role[Role.EXTERNAL][0])
        return pool

    def test_hit_when_pool_holds_current_ref(self):
        ref = FakeRef("1:0:1:X:0:0:0:0:0:0:X")
        nav = FakeNav(current_ref=ref)
        pool = self._make_pool(armed_ref=ref)
        ic = self._make_interceptor(pool, nav)
        self.assertTrue(ic._pool_hit_for_current_service())

    def test_miss_when_pool_does_not_hold_current_ref(self):
        nav = FakeNav(current_ref=FakeRef("1:0:1:Z:0:0:0:0:0:0:Z"))
        pool = self._make_pool(armed_ref=FakeRef("1:0:1:Y:0:0:0:0:0:0:Y"))
        ic = self._make_interceptor(pool, nav)
        self.assertFalse(ic._pool_hit_for_current_service())

    def test_miss_when_pool_empty(self):
        nav = FakeNav(current_ref=FakeRef("1:0:1:X:0:0:0:0:0:0:X"))
        pool = self._make_pool(armed_ref=None)
        ic = self._make_interceptor(pool, nav)
        self.assertFalse(ic._pool_hit_for_current_service())

    def test_miss_when_nav_returns_none(self):
        nav = FakeNav(current_ref=None)
        pool = self._make_pool(
            armed_ref=FakeRef("1:0:1:X:0:0:0:0:0:0:X"))
        ic = self._make_interceptor(pool, nav)
        self.assertFalse(ic._pool_hit_for_current_service())

    def test_evstart_sets_hit_true_on_pool_match(self):
        """End-to-end: evStart -> _on_nav_event sees the bypass
        path (no wrapper bracket), probes the pool, and labels the
        zap as HIT when the slot holds the live ref.
        """
        ref = FakeRef("1:0:1:X:0:0:0:0:0:0:X")
        nav = FakeNav(current_ref=ref)
        pool = self._make_pool(armed_ref=ref)
        ic = self._make_interceptor(pool, nav)
        ic._evStart = 1
        ic._evTunedIn = 6
        ic._zap_start_ns = None  # bypass path: no wrapper bracket
        ic._on_nav_event(1)
        self.assertEqual(ic._zap_attr, "ext")
        self.assertTrue(ic._zap_hit,
                        "evStart with a pool-armed live ref must "
                        "classify the zap as HIT")

    def test_evstart_sets_hit_false_when_pool_empty(self):
        ref = FakeRef("1:0:1:X:0:0:0:0:0:0:X")
        nav = FakeNav(current_ref=ref)
        pool = self._make_pool(armed_ref=None)
        ic = self._make_interceptor(pool, nav)
        ic._evStart = 1
        ic._evTunedIn = 6
        ic._zap_start_ns = None
        ic._on_nav_event(1)
        self.assertEqual(ic._zap_attr, "ext")
        self.assertFalse(ic._zap_hit,
                         "evStart on a genuine bypass (no pool "
                         "match) must stay non-HIT")


class TpMatchDetection(unittest.TestCase):
    """v0.5.3 NEAR detection: pool.tp_match returns True when any
    slot is locked on the same transponder, even when no slot holds
    the exact service ref. eDVBResourceManager channel-shares at
    the transponder level, so the actual latency is intra-TP not
    cold cross-TP. Label as NEAR instead of EXT/MISS.
    """

    def _make_pool(self, armed_ref):
        pool = FBCPreTunePool(
            nav_provider=lambda: FakeNav(),
            nim_manager_provider=lambda: FakeNimManager(),
        )
        pool.configure({Role.NEXT: 1})
        if armed_ref is not None:
            pool.arm({Role.NEXT: [armed_ref]})
            pool._mark_locked_optimistic(
                pool._slots_by_role[Role.NEXT][0])
        return pool

    def test_tp_match_same_transponder_different_service(self):
        """ZDF (3F3) and ZDF Neo (3F3) share transponder 3F3 even
        though their service ids differ (2B66 vs 2B7A). tp_match
        must catch this case.
        """
        pool = self._make_pool(
            armed_ref=FakeRef("1:0:19:2B66:3F3:1:C00000:0:0:0:"))  # ZDF
        target = FakeRef("1:0:19:2B7A:3F3:1:C00000:0:0:0:")        # ZDF Neo
        self.assertTrue(pool.tp_match(target))

    def test_tp_match_returns_false_on_different_transponder(self):
        pool = self._make_pool(
            armed_ref=FakeRef("1:0:19:2B66:3F3:1:C00000:0:0:0:"))
        target = FakeRef("1:0:1:445F:453:1:C00000:0:0:0:")  # WELT, tp 453
        self.assertFalse(pool.tp_match(target))

    def test_tp_match_returns_false_on_empty_pool(self):
        pool = self._make_pool(armed_ref=None)
        target = FakeRef("1:0:19:2B66:3F3:1:C00000:0:0:0:")
        self.assertFalse(pool.tp_match(target))

    def test_tp_match_returns_false_on_none(self):
        pool = self._make_pool(
            armed_ref=FakeRef("1:0:19:2B66:3F3:1:C00000:0:0:0:"))
        self.assertFalse(pool.tp_match(None))


class NearOnEvStart(unittest.TestCase):
    """Wire-up test for the NEAR classification path inside
    _on_nav_event.
    """

    def _make_pool(self, armed_ref):
        pool = FBCPreTunePool(
            nav_provider=lambda: FakeNav(),
            nim_manager_provider=lambda: FakeNimManager(),
        )
        pool.configure({Role.NEXT: 1})
        if armed_ref is not None:
            pool.arm({Role.NEXT: [armed_ref]})
            pool._mark_locked_optimistic(
                pool._slots_by_role[Role.NEXT][0])
        return pool

    def _make_interceptor(self, pool, nav):
        _install_nav(nav)
        ic = zi.ZapInterceptor(pool=pool, predictor=None)
        ic._evStart = 1
        ic._evTunedIn = 6
        return ic

    def test_bypass_near_when_pool_tp_matches(self):
        """No wrapper bracketed, pool.lookup miss, but pool.tp_match
        hit -> _zap_near True, _zap_hit False, attr=ext.
        """
        armed = FakeRef("1:0:19:2B66:3F3:1:C00000:0:0:0:")  # ZDF (tp 3F3)
        target = FakeRef("1:0:19:2B7A:3F3:1:C00000:0:0:0:")  # ZDF Neo (3F3)
        nav = FakeNav(current_ref=target)
        pool = self._make_pool(armed_ref=armed)
        ic = self._make_interceptor(pool, nav)
        ic._zap_start_ns = None
        ic._zap_attr = None
        ic._on_nav_event(1)  # evStart
        self.assertEqual(ic._zap_attr, "ext")
        self.assertFalse(ic._zap_hit,
                         "service-level lookup must miss (different service id)")
        self.assertTrue(ic._zap_near,
                        "transponder-level fallback must match - "
                        "ZDF and ZDF Neo share tp 3F3")

    def test_bypass_ext_when_no_tp_match(self):
        """No wrapper bracketed, pool.lookup miss, pool.tp_match
        miss -> hit and near both False; result will be EXT.
        """
        armed = FakeRef("1:0:19:2B66:3F3:1:C00000:0:0:0:")
        target = FakeRef("1:0:1:445F:453:1:C00000:0:0:0:")  # WELT, different tp
        nav = FakeNav(current_ref=target)
        pool = self._make_pool(armed_ref=armed)
        ic = self._make_interceptor(pool, nav)
        ic._zap_start_ns = None
        ic._zap_attr = None
        ic._on_nav_event(1)
        self.assertEqual(ic._zap_attr, "ext")
        self.assertFalse(ic._zap_hit)
        self.assertFalse(ic._zap_near)

    def test_wrapper_miss_with_tp_match_upgrades_to_near(self):
        """Wrapper-tracked path with hit=False, tp_match True ->
        evStart upgrades _zap_near to True. attr stays as
        wrapper-set (zapDown), not overwritten to ext.
        """
        armed = FakeRef("1:0:19:2B66:3F3:1:C00000:0:0:0:")
        target = FakeRef("1:0:19:2B7A:3F3:1:C00000:0:0:0:")
        nav = FakeNav(current_ref=target)
        pool = self._make_pool(armed_ref=armed)
        ic = self._make_interceptor(pool, nav)
        ic._zap_start_ns = None
        ic._zap_attr = "zapDown"  # wrapper bracketed
        ic._zap_hit = False
        ic._on_nav_event(1)
        self.assertEqual(ic._zap_attr, "zapDown",
                         "wrapper-set attr must NOT be overwritten "
                         "to ext when wrapper bracketed first")
        self.assertFalse(ic._zap_hit)
        self.assertTrue(ic._zap_near,
                        "wrapper-MISS with TP-share must upgrade to NEAR")

    def test_wrapper_hit_skips_near_probe(self):
        """Wrapper already set hit=True; evStart should not probe
        tp_match (would be wasted work and could mislabel).
        """
        armed = FakeRef("1:0:19:2B66:3F3:1:C00000:0:0:0:")
        target = FakeRef("1:0:19:2B66:3F3:1:C00000:0:0:0:")
        nav = FakeNav(current_ref=target)
        pool = self._make_pool(armed_ref=armed)
        ic = self._make_interceptor(pool, nav)
        ic._zap_start_ns = None
        ic._zap_attr = "zapDown"
        ic._zap_hit = True
        ic._on_nav_event(1)
        self.assertTrue(ic._zap_hit)
        self.assertFalse(ic._zap_near,
                         "near flag must stay False when hit is True")


if __name__ == "__main__":
    unittest.main()
