"""Top-level lifecycle owner.

The Controller is the only object that crosses module boundaries. It
instantiates the pool, predictor, arbiter, and interceptor; it owns the
watchdog timer; it is the single point that decides "plugin enabled vs
disabled" at runtime.

Created from plugin.py's WHERE_SESSIONSTART hook. There is one and only
one Controller per enigma2 session.
"""

from . import _
from .logger import info, debug, warn, error
from .config import cfg
from .fbc_pretune_pool import FBCPreTunePool, Role
from .predictor import Predictor
from .resource_arbiter import ResourceArbiter
from .zap_interceptor import ZapInterceptor, sanity_check_infobar


class Controller:
    _instance = None

    @classmethod
    def peek(cls):
        return cls._instance

    @classmethod
    def get(cls, session=None):
        if cls._instance is None and session is not None:
            cls._instance = Controller(session)
        return cls._instance

    def __init__(self, session):
        self._session = session
        self._enabled = False
        self._disabled_by_watchdog = False
        self._consecutive_failures = 0

        self._pool = FBCPreTunePool()
        self._predictor = Predictor()
        self._arbiter = ResourceArbiter(self._pool)
        self._interceptor = ZapInterceptor(self._pool, self._predictor,
                                            on_zap=self._on_post_zap)

        self._watchdog_timer = None
        self._rearm_timer = None
        self._pending_zap_start_time = None

        Controller._instance = self

    # --- lifecycle ------------------------------------------------------

    def start(self):
        # Idempotent: enigma2 may call session_start more than once.
        if self._enabled:
            debug("controller.start: already running, skipping")
            return
        try:
            if not cfg.enabled.value:
                info("plugin disabled in config; not starting")
                return
            # One-shot API discovery on first run of every fresh session.
            try:
                from . import diagnostic
                diagnostic.run_once()
            except Exception as exc:
                error("diagnostic.run_once: %r" % exc)
            # Sanity-check the enigma2 surface before committing. Pool
            # and arbiter are checked now; the interceptor is checked
            # once the InfoBar is available (direct or deferred path).
            pool_crit, pool_opt = self._pool.sanity_check()
            arb_crit, arb_opt = self._arbiter.sanity_check()
            for w in pool_opt + arb_opt:
                warn("sanity (degraded): %s" % w)
            if pool_crit + arb_crit:
                self._sanity_refuse(pool_crit + arb_crit)
                return

            self._apply_pool_capacity()
            self._arbiter.start()
            infobar = self._find_infobar()
            if infobar is None:
                warn("InfoBar not ready yet; deferring interceptor start")
                self._defer_interceptor_start()
            else:
                if not self._apply_interceptor(infobar):
                    return
            self._enabled = True
            self._schedule_rearm(delay_ms=500)
            info("controller started (next=%s prev=%s last=%s)" % (
                bool(cfg.pretune_next.value),
                bool(cfg.pretune_prev.value),
                bool(cfg.pretune_history.value)))
        except Exception as exc:
            error("controller.start failed: %r" % exc)
            self.stop()

    def stop(self):
        try:
            self._enabled = False
            if self._watchdog_timer is not None:
                self._watchdog_timer.stop()
                self._watchdog_timer = None
            if self._rearm_timer is not None:
                self._rearm_timer.stop()
                self._rearm_timer = None
            self._interceptor.stop()
            self._arbiter.stop()
            self._pool.shutdown()
            info("controller stopped")
        except Exception as exc:
            error("controller.stop failed: %r" % exc)

    def on_config_changed(self):
        """Called by Settings UI after Save."""
        try:
            if not cfg.enabled.value and self._enabled:
                info("config: plugin disabled by user")
                self.stop()
                return
            if cfg.enabled.value and not self._enabled and not self._disabled_by_watchdog:
                info("config: plugin re-enabled by user")
                self.start()
                return
            # Capacity change only.
            self._apply_pool_capacity()
            self._schedule_rearm(delay_ms=100)
        except Exception as exc:
            error("on_config_changed failed: %r" % exc)

    # --- pool driving ---------------------------------------------------

    def _apply_pool_capacity(self):
        self._pool.configure({
            Role.NEXT: 1 if cfg.pretune_next.value else 0,
            Role.PREV: 1 if cfg.pretune_prev.value else 0,
            Role.HISTORY: 1 if cfg.pretune_history.value else 0,
        })

    def _on_post_zap(self):
        """Fires after every zap (HIT, MISS, or external)."""
        try:
            self._pending_zap_start_time = None
            self._consecutive_failures = 0
            self._schedule_rearm(delay_ms=250)
        except Exception as exc:
            error("_on_post_zap: %r" % exc)

    def _schedule_rearm(self, delay_ms):
        try:
            from enigma import eTimer
            if self._rearm_timer is None:
                self._rearm_timer = eTimer()
                self._rearm_timer.callback.append(self._do_rearm)
            self._rearm_timer.stop()
            self._rearm_timer.start(delay_ms, True)
        except Exception as exc:
            error("_schedule_rearm: %r" % exc)

    def _do_rearm(self):
        try:
            if not self._enabled:
                return
            plan = {
                Role.NEXT: self._predictor.next_service(count=1) if cfg.pretune_next.value else [],
                Role.PREV: self._predictor.prev_service(count=1) if cfg.pretune_prev.value else [],
                # History uses count=1 so only the immediately previous
                # service is pre-tuned. Deeper history positions are not
                # pre-tuned; the speedup is only useful for the
                # one-step-back case.
                Role.HISTORY: self._predictor.history_service(count=1) if cfg.pretune_history.value else [],
            }
            self._pool.arm(plan)
        except Exception as exc:
            error("_do_rearm: %r" % exc)
            self._record_failure()

    # --- sanity ---------------------------------------------------------

    def _apply_interceptor(self, infobar):
        """Sanity-check the InfoBar surface, then start the interceptor.

        Returns True if the interceptor is now active; False means a
        critical interface is missing and the plugin has been stopped.
        """
        crit, opt = sanity_check_infobar(infobar)
        for w in opt:
            warn("sanity (degraded): %s" % w)
        if crit:
            self._sanity_refuse(crit)
            return False
        self._interceptor.start(infobar)
        return True

    def _sanity_refuse(self, missing):
        error("sanity check failed; not starting. Missing: %s"
              % ", ".join(missing))
        self.stop()
        try:
            from Tools.Notifications import AddPopup
            from Screens.MessageBox import MessageBox
            AddPopup(
                _("FBC-ChannelSpeedChange could not start: this enigma2 "
                  "build is missing required interfaces (%s). The plugin "
                  "stays off.") % ", ".join(missing),
                MessageBox.TYPE_WARNING, 10,
                id="FBC_CSC_SANITY_FAILED",
            )
        except Exception:
            pass

    # --- watchdog -------------------------------------------------------

    def _record_failure(self):
        self._consecutive_failures += 1
        warn("failure count -> %d" % self._consecutive_failures)
        if self._consecutive_failures >= 3:
            self._self_disable("watchdog: 3 consecutive failures")

    def _self_disable(self, reason):
        error("self-disabling: %s" % reason)
        self._disabled_by_watchdog = True
        self.stop()
        try:
            from Tools.Notifications import AddPopup
            from Screens.MessageBox import MessageBox
            AddPopup(
                _("FBC-ChannelSpeedChange disabled itself for safety.\n"
                  "Reason: %s\nRe-enable in Plugins after restart.") % reason,
                MessageBox.TYPE_WARNING, 10,
                id="FBC_CSC_SELF_DISABLED",
            )
        except Exception:
            pass

    # --- helpers --------------------------------------------------------

    def _find_infobar(self):
        try:
            from Screens.InfoBar import InfoBar
            return InfoBar.instance
        except Exception:
            return None

    def _defer_interceptor_start(self):
        try:
            from enigma import eTimer
            self._defer_timer = eTimer()

            def attempt():
                ib = self._find_infobar()
                if ib is None:
                    return  # try again on next tick
                self._defer_timer.stop()
                try:
                    self._apply_interceptor(ib)
                except Exception as exc:
                    error("deferred interceptor.start: %r" % exc)

            self._defer_timer.callback.append(attempt)
            self._defer_timer.start(1000, False)  # poll every 1 s
        except Exception as exc:
            error("_defer_interceptor_start: %r" % exc)
