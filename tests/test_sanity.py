import unittest

from _enigma_stubs import bootstrap
bootstrap()

from FBCChannelSpeedChange.zap_interceptor import sanity_check_infobar
from FBCChannelSpeedChange.fbc_pretune_pool import FBCPreTunePool
from FBCChannelSpeedChange.resource_arbiter import ResourceArbiter


class FakeServicelist:
    def __init__(self, with_history=True, with_setcur=True, with_add=True):
        if with_history:
            self.history = []          # a list, so .append exists
        if with_setcur:
            self.setCurrentSelection = lambda *a: None
        if with_add:
            self.addToHistory = lambda *a: None


class FakeInfobar:
    def __init__(self, zapup=True, zapdown=True, servicelist=True,
                 histback=True, histnext=True, **sl_kw):
        if zapup:
            self.zapUp = lambda *a: None
        if zapdown:
            self.zapDown = lambda *a: None
        if servicelist:
            self.servicelist = FakeServicelist(**sl_kw)
        if histback:
            self.historyBack = lambda *a: None
        if histnext:
            self.historyNext = lambda *a: None


class InfobarSanityTests(unittest.TestCase):
    def test_complete_infobar_passes(self):
        crit, opt = sanity_check_infobar(FakeInfobar())
        self.assertEqual(crit, [])
        self.assertEqual(opt, [])

    def test_none_infobar_is_critical(self):
        crit, _ = sanity_check_infobar(None)
        self.assertTrue(crit)

    def test_missing_zapup_is_critical(self):
        crit, _ = sanity_check_infobar(FakeInfobar(zapup=False))
        self.assertTrue(any("zapUp" in c for c in crit))

    def test_missing_servicelist_is_critical(self):
        crit, _ = sanity_check_infobar(FakeInfobar(servicelist=False))
        self.assertTrue(any("servicelist" in c for c in crit))

    def test_missing_history_nav_is_optional_not_critical(self):
        crit, opt = sanity_check_infobar(
            FakeInfobar(histback=False, histnext=False))
        self.assertEqual(crit, [])
        self.assertTrue(any("historyBack" in o for o in opt))

    def test_missing_setcurrentselection_is_optional(self):
        crit, opt = sanity_check_infobar(FakeInfobar(with_setcur=False))
        self.assertEqual(crit, [])
        self.assertTrue(any("setCurrentSelection" in o for o in opt))


class FakeNav:
    def __init__(self, rec=True, play=True):
        if rec:
            self.recordService = lambda *a: None
        if play:
            self.playService = lambda *a: None


class PoolSanityTests(unittest.TestCase):
    def test_good_nav_passes(self):
        pool = FBCPreTunePool(nav_provider=lambda: FakeNav(),
                              nim_manager_provider=lambda: object())
        crit, _ = pool.sanity_check()
        self.assertEqual(crit, [])

    def test_nav_without_recordservice_is_critical(self):
        pool = FBCPreTunePool(nav_provider=lambda: FakeNav(rec=False),
                              nim_manager_provider=lambda: object())
        crit, _ = pool.sanity_check()
        self.assertTrue(any("recordService" in c for c in crit))

    def test_nav_none_is_optional_not_critical(self):
        pool = FBCPreTunePool(nav_provider=lambda: None,
                              nim_manager_provider=lambda: object())
        crit, opt = pool.sanity_check()
        self.assertEqual(crit, [])
        self.assertTrue(opt)


class ArbiterSanityTests(unittest.TestCase):
    def test_no_critical_ever(self):
        arb = ResourceArbiter(object(),
                              record_timer_provider=lambda: None,
                              session_provider=lambda: None)
        crit, opt = arb.sanity_check()
        self.assertEqual(crit, [])
        self.assertTrue(opt)


if __name__ == "__main__":
    unittest.main()
