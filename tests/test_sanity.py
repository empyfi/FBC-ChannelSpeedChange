import sys
import types
import unittest

from _enigma_stubs import bootstrap
bootstrap()

from FBCChannelSpeedChange.zap_interceptor import sanity_check_infobar
from FBCChannelSpeedChange.fbc_pretune_pool import FBCPreTunePool
from FBCChannelSpeedChange.resource_arbiter import ResourceArbiter
from FBCChannelSpeedChange.controller import sanity_check_external_hook
from FBCChannelSpeedChange.config import cfg as _cfg
from Components.config import ConfigYesNo

if not hasattr(_cfg, "accept_external_pretune"):
    _cfg.accept_external_pretune = ConfigYesNo(default=False)


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


class ExternalHookSanityTests(unittest.TestCase):
    """Phase 5 sanity check: the evNewProgramInfo subscription surface
    that the EXTERNAL slot lifecycle relies on. Missing-surface is
    critical only when the user has opted into accept_external_pretune;
    otherwise it is informational because the api module silently
    no-ops when the gate is off.
    """

    def setUp(self):
        # Some upstream test modules (test_controller_external) inject
        # a fake NavigationInstance into sys.modules. Pop it so each
        # case here can choose its own surface state explicitly.
        self._saved_nav = sys.modules.pop("NavigationInstance", None)
        self._original_gate = _cfg.accept_external_pretune.value

    def tearDown(self):
        if self._saved_nav is not None:
            sys.modules["NavigationInstance"] = self._saved_nav
        else:
            sys.modules.pop("NavigationInstance", None)
        _cfg.accept_external_pretune.value = self._original_gate

    def _inject_full_surface(self):
        """Provide a complete NavigationInstance + event-list shape."""
        nav_mod = types.ModuleType("NavigationInstance")
        nav_mod.instance = type("StubNav", (), {"event": []})()
        sys.modules["NavigationInstance"] = nav_mod

    def test_no_failures_when_surface_complete(self):
        self._inject_full_surface()
        _cfg.accept_external_pretune.value = True
        crit, opt = sanity_check_external_hook()
        self.assertEqual(crit, [], "complete surface must not flag critical")
        self.assertEqual(opt, [], "complete surface must not flag optional")

    def test_missing_nav_is_optional_when_gate_off(self):
        # NavigationInstance not in sys.modules and gate off.
        _cfg.accept_external_pretune.value = False
        crit, opt = sanity_check_external_hook()
        self.assertEqual(crit, [],
                         "missing surface with gate off must not be critical")
        self.assertTrue(opt,
                        "missing surface with gate off should land as "
                        "informational optional warning")

    def test_missing_nav_is_critical_when_gate_on(self):
        # NavigationInstance not in sys.modules and gate on.
        _cfg.accept_external_pretune.value = True
        crit, opt = sanity_check_external_hook()
        self.assertTrue(crit,
                        "missing surface with gate on must be critical "
                        "so the start path refuses rather than silently "
                        "leaking EXTERNAL slots past the TTL only path")


if __name__ == "__main__":
    unittest.main()
