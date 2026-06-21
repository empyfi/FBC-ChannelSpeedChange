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
from .predictor import Predictor, _key as _ref_key
from .resource_arbiter import ResourceArbiter
from .zap_interceptor import ZapInterceptor, sanity_check_infobar


def sanity_check_external_hook():
    """Inspect the evNewProgramInfo subscription surface the v0.5.0
    EXTERNAL slot lifecycle relies on.

    Returns (critical, optional). A missing surface is critical when
    ``cfg.accept_external_pretune`` is on - the EXTERNAL slot would
    otherwise leak past the TTL only path on shortcut-zaps that bypass
    the ZapInterceptor. When the gate is off the same missing surface
    is purely informational.
    """
    critical = []
    optional = []
    accept_on = False
    try:
        accept_on = bool(cfg.accept_external_pretune.value)
    except Exception:
        accept_on = False

    def _flag(msg):
        (critical if accept_on else optional).append(msg)

    try:
        from enigma import iPlayableService
        if not hasattr(iPlayableService, "evNewProgramInfo"):
            _flag("iPlayableService.evNewProgramInfo enum")
    except Exception as exc:
        _flag("enigma.iPlayableService import: %r" % exc)

    try:
        import NavigationInstance
        nav = NavigationInstance.instance
        if nav is None:
            _flag("NavigationInstance.instance (not ready)")
        elif not hasattr(nav, "event"):
            _flag("NavigationInstance.instance.event")
    except Exception as exc:
        _flag("NavigationInstance import: %r" % exc)

    return critical, optional


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

        # v0.5.0 external-slot plumbing. eTimer instantiation is
        # deferred to the first use so the import-time module load
        # stays cheap.
        self._external_ttl_timer = None
        self._nav_event_conn = None
        self._evNewProgramInfo = None

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
            ext_crit, ext_opt = sanity_check_external_hook()
            for w in pool_opt + arb_opt + ext_opt:
                warn("sanity (degraded): %s" % w)
            if pool_crit + arb_crit + ext_crit:
                self._sanity_refuse(pool_crit + arb_crit + ext_crit)
                return

            self._apply_pool_capacity()
            self._arbiter.start()
            self._wire_evnewproginfo()
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
            self._stop_external_ttl()
            self._unwire_evnewproginfo()
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
        # cfg.accept_external_pretune lands in Phase 4; treat the
        # missing attribute as off so this code path is safe between
        # phases.
        try:
            external_cap = 1 if cfg.accept_external_pretune.value else 0
        except AttributeError:
            external_cap = 0
        self._pool.configure({
            Role.NEXT: 1 if cfg.pretune_next.value else 0,
            Role.PREV: 1 if cfg.pretune_prev.value else 0,
            Role.HISTORY: 1 if cfg.pretune_history.value else 0,
            Role.EXTERNAL: external_cap,
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
            next_refs = self._predictor.next_service(count=1) if cfg.pretune_next.value else []
            prev_refs = self._predictor.prev_service(count=1) if cfg.pretune_prev.value else []
            # History uses count=1 so only the immediately previous
            # service is pre-tuned. Deeper history positions are not
            # pre-tuned; the speedup is only useful for the
            # one-step-back case.
            hist_refs = self._predictor.history_service(count=1) if cfg.pretune_history.value else []
            next_refs, prev_refs, hist_refs = _collapse_history_on_convergence(
                next_refs, prev_refs, hist_refs)
            plan = {
                Role.NEXT: next_refs,
                Role.PREV: prev_refs,
                Role.HISTORY: hist_refs,
            }
            self._pool.arm(plan)
        except Exception as exc:
            error("_do_rearm: %r" % exc)
            self._record_failure()

    # --- external pretune (v0.5.0 public API) --------------------------

    def pretune_external(self, ref):
        """Arm or refresh the EXTERNAL pool slot with ``ref``.

        Convergence check via ``pool.lookup`` covers both idempotency
        rules in one shot:
          * ref already armed in NEXT / PREV / HISTORY → no-op (the
            eventual zap is satisfied by channel-share on the
            existing slot)
          * ref already armed in EXTERNAL → no-op (refresh the TTL
            anyway so the slot keeps living)
        A different ref simply overwrites the previous EXTERNAL
        target on the next arm() cycle.
        """
        try:
            if not self._enabled:
                return
            # Always refresh the TTL on a call, even when the ref is
            # already armed - the call itself proves the caller still
            # wants the slot alive.
            self._refresh_external_ttl()
            existing = self._pool.lookup(ref)
            if existing is not None:
                debug("pretune_external: ref already armed "
                      "(role=%s), skipping arm" % existing.role.value)
                return
            self._pool.arm({Role.EXTERNAL: [ref]})
            try:
                key = _ref_key(ref)
            except Exception:
                key = "<unprintable>"
            info("pretune_external armed ref=%s" % key)
        except Exception as exc:
            error("pretune_external: %r" % exc)
            self._record_failure()

    def release_external(self, ref):
        """Release the EXTERNAL pool slot.

        With ``ref``: release only if the slot currently holds that
        exact reference (race-safe against a late close-event landing
        after a newer ``pretune_external`` overwrote the slot).
        Without ``ref`` (``None``): release every armed EXTERNAL slot.
        """
        try:
            if not self._enabled:
                return
            slots = self._pool._slots_by_role.get(Role.EXTERNAL, [])
            if ref is None:
                released = False
                for slot in slots:
                    if slot.service_ref is not None:
                        self._pool.release_after_swap(slot)
                        released = True
                if released:
                    info("release_external (unconditional)")
                    self._stop_external_ttl()
                return
            target_key = _ref_key(ref)
            for slot in slots:
                if slot.service_ref is None:
                    continue
                if _ref_key(slot.service_ref) == target_key:
                    self._pool.release_after_swap(slot)
                    info("release_external matched ref=%s" % target_key)
                    self._stop_external_ttl()
                    return
            debug("release_external: no EXTERNAL slot holds %s "
                  "(possibly already torn down)" % target_key)
        except Exception as exc:
            error("release_external: %r" % exc)

    def _refresh_external_ttl(self):
        """Start or restart the TTL safety net. Default 5 min - long
        enough that legitimate EPG-read sessions never get torn down
        mid-read, short enough that a leaked slot does not hold a
        tuner indefinitely.

        Lifecycle ownership belongs to the external caller (FCC-
        Extender etc.) via the explicit ``release_external`` path;
        the TTL only catches the cases where the caller never sends
        a release (crashed, plugin disabled mid-flight, future
        Extender bug).
        """
        try:
            from enigma import eTimer
            if self._external_ttl_timer is None:
                self._external_ttl_timer = eTimer()
                self._external_ttl_timer.callback.append(
                    self._handle_external_ttl)
            try:
                ttl_ms = int(cfg.external_slot_ttl_ms.value)
            except AttributeError:
                ttl_ms = 300000  # Phase 4 lands the config key
            except Exception:
                ttl_ms = 300000
            self._external_ttl_timer.stop()
            self._external_ttl_timer.start(ttl_ms, True)
        except Exception as exc:
            error("_refresh_external_ttl: %r" % exc)

    def _stop_external_ttl(self):
        if self._external_ttl_timer is not None:
            try:
                self._external_ttl_timer.stop()
            except Exception:
                pass

    def _handle_external_ttl(self):
        info("external slot TTL expired - releasing")
        self.release_external(None)

    def _wire_evnewproginfo(self):
        """Subscribe to ``evNewProgramInfo`` so a zap that bypasses
        the ZapInterceptor (e.g. ``session.nav.playService`` from
        outside ChannelSelection) still triggers EXTERNAL-slot
        cleanup when the live service matches the armed ref.

        On builds where the event constant is unavailable, the
        subscription is skipped silently. Phase 5 hardens this into
        a sanity check that warns the user explicitly.
        """
        try:
            import NavigationInstance
            from enigma import iPlayableService
            nav = NavigationInstance.instance
            if nav is None:
                warn("evNewProgramInfo: NavigationInstance not ready")
                return
            self._evNewProgramInfo = getattr(
                iPlayableService, "evNewProgramInfo", None)
            if self._evNewProgramInfo is None:
                warn("evNewProgramInfo: enum value missing on this build")
                return
            nav.event.append(self._on_nav_event)
            self._nav_event_conn = nav
        except Exception as exc:
            error("_wire_evnewproginfo: %r" % exc)

    def _unwire_evnewproginfo(self):
        if self._nav_event_conn is None:
            return
        try:
            self._nav_event_conn.event.remove(self._on_nav_event)
        except Exception as exc:
            debug("evNewProgramInfo unwire: %r" % exc)
        finally:
            self._nav_event_conn = None

    def _on_nav_event(self, reason):
        try:
            if reason != self._evNewProgramInfo:
                return
            self._release_external_if_live_matches()
        except Exception as exc:
            error("_on_nav_event: %r" % exc)

    def _release_external_if_live_matches(self):
        """Compare the live service ref against the EXTERNAL slot;
        release the slot on a match. Covers the case where the zap
        bypasses the ZapInterceptor and would otherwise leave the
        EXTERNAL slot armed against the now-live service - wasting a
        demodulator until the next rearm or TTL.
        """
        try:
            import NavigationInstance
            nav = NavigationInstance.instance
            if nav is None:
                return
            live = nav.getCurrentlyPlayingServiceReference()
            if live is None:
                return
            live_key = _ref_key(live)
            slots = self._pool._slots_by_role.get(Role.EXTERNAL, [])
            for slot in slots:
                if slot.service_ref is None:
                    continue
                if _ref_key(slot.service_ref) == live_key:
                    debug("evNewProgramInfo: live matches EXTERNAL "
                          "slot, releasing")
                    self._pool.release_after_swap(slot)
                    self._stop_external_ttl()
                    return
        except Exception as exc:
            error("_release_external_if_live_matches: %r" % exc)

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


# --- helpers ------------------------------------------------------------

def _collapse_history_on_convergence(next_refs, prev_refs, hist_refs):
    """During linear bouquet walking the HISTORY target (the
    just-departed channel) converges on PREV (walking Channel up) or
    NEXT (walking Channel down). The pool would then hold two
    recordables on the same service: eDVBResourceManager channel-shares
    the demod, but the dvbapi side still sees two demuxer subscriptions,
    and with both directions' prewarm_descrambler flag on the card pays
    a redundant continuous ECM stream.

    On convergence the HISTORY slot is dropped. A recall still HITs the
    surviving slot because the pool's lookup is role-independent: it
    walks every armed slot and returns the first key-matching one.
    """
    if not hist_refs:
        return next_refs, prev_refs, hist_refs
    hist_key = _ref_key(hist_refs[0])
    if next_refs and _ref_key(next_refs[0]) == hist_key:
        return next_refs, prev_refs, []
    if prev_refs and _ref_key(prev_refs[0]) == hist_key:
        return next_refs, prev_refs, []
    return next_refs, prev_refs, hist_refs
