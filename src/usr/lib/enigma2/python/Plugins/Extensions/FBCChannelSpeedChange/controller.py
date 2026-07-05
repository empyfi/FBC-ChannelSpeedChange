"""Top-level lifecycle owner.

The Controller is the only object that crosses module boundaries. It
instantiates the pool, predictor, arbiter, and interceptor; it owns the
watchdog timer; it is the single point that decides "plugin enabled vs
disabled" at runtime.

Created from plugin.py's WHERE_SESSIONSTART hook. There is one and only
one Controller per enigma2 session.
"""

import time
import traceback

from . import _
from .logger import info, debug, warn, error
from .config import cfg
from .fbc_pretune_pool import FBCPreTunePool, Role
from .predictor import Predictor, _key as _ref_key
from .resource_arbiter import ResourceArbiter
from .zap_interceptor import ZapInterceptor, sanity_check_infobar


class _ExternalRateLimiter:
    """Sliding-window rate limiter for the public api module.

    Two checks run in order:
      * **Same-ref debounce.** A repeat call with the same ref
        within 100 ms is reported as ``idempotent`` and the caller
        path silently drops it - the pool's own idempotency would
        cover this too, but short-circuiting here avoids touching
        the pool / lookup at all on a chatty caller.
      * **Distinct-ref burst cap.** When the number of distinct
        refs seen in the trailing 1-second window already equals
        ``cfg.external_max_calls_per_sec`` (default 10) and the
        new ref is not one of them, the call is reported as
        ``throttled`` and dropped. Real-world callers stay well
        under this ceiling; the cap fires on rotating-ref bursts
        from buggy or hostile sources.

    The limiter also tracks drops since the last warn emission so
    the caller can rate-limit its own warn output to once per
    second.
    """

    BURST_WINDOW_NS = 1_000_000_000          # 1 s
    SAME_REF_MIN_INTERVAL_NS = 100_000_000   # 100 ms

    def __init__(self):
        self._last_seen = {}        # ref_key -> last ts ns
        self._window = []           # list of (ts ns, ref_key)
        self._drops_since_warn = 0
        self._last_warn_ns = 0

    def classify(self, ref_key, now_ns, max_distinct_per_sec):
        """Returns one of ``'allow'``, ``'idempotent'`` or
        ``'throttled'``. The caller acts on the verdict; this
        helper only tracks state.
        """
        last = self._last_seen.get(ref_key)
        if last is not None and now_ns - last < self.SAME_REF_MIN_INTERVAL_NS:
            self._last_seen[ref_key] = now_ns
            return "idempotent"
        cutoff = now_ns - self.BURST_WINDOW_NS
        self._window = [(ts, k) for ts, k in self._window if ts > cutoff]
        distinct = {k for _, k in self._window}
        if len(distinct) >= max_distinct_per_sec and ref_key not in distinct:
            return "throttled"
        self._window.append((now_ns, ref_key))
        self._last_seen[ref_key] = now_ns
        return "allow"

    def record_drop(self):
        self._drops_since_warn += 1

    def take_warn_count(self, now_ns):
        """Returns the number of drops since the last warn, but at
        most once per second. ``None`` outside the window so the
        caller can short-circuit emitting a warn line.
        """
        if now_ns - self._last_warn_ns <= self.BURST_WINDOW_NS:
            return None
        count = self._drops_since_warn
        self._drops_since_warn = 0
        self._last_warn_ns = now_ns
        return count


class _ExternalStats:
    """Rolling 60-second activity counters for the external slot.

    Bumped by the controller on every external-call verdict, every
    TTL refresh, every release path. A heartbeat eTimer fires
    ``flush_if_active()`` at info level once per minute, providing
    a forensic summary that a forum reporter can paste alongside a
    bug report. Quiet minutes (no activity) emit nothing.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self.calls_armed = 0
        self.calls_idempotent = 0
        self.calls_convergence_skip = 0
        self.calls_throttled = 0
        self.calls_errors = 0
        self.ttl_refreshes = 0
        self.releases_explicit = 0
        self.releases_via_evnewproginfo = 0
        self.releases_via_ttl = 0

    @property
    def calls_total(self):
        return (self.calls_armed + self.calls_idempotent
                + self.calls_convergence_skip + self.calls_throttled
                + self.calls_errors)

    @property
    def releases_total(self):
        return (self.releases_explicit + self.releases_via_evnewproginfo
                + self.releases_via_ttl)

    def has_activity(self):
        return self.calls_total > 0 or self.releases_total > 0

    def format(self):
        return (
            "external stats (60s): %d calls (%d armed, %d idempotent, "
            "%d convergence-skip, %d throttled, %d errors), TTL "
            "refreshes %d, releases %d (%d explicit, %d evNewProgramInfo, "
            "%d TTL)" % (
                self.calls_total, self.calls_armed, self.calls_idempotent,
                self.calls_convergence_skip, self.calls_throttled,
                self.calls_errors, self.ttl_refreshes,
                self.releases_total, self.releases_explicit,
                self.releases_via_evnewproginfo, self.releases_via_ttl,
            )
        )


def sanity_check_standby_hook():
    """Inspect the standby-transition surface the pool release relies on.

    Returns (critical, optional). Missing hooks are always optional:
    without them the pretune slots stay armed across a standby cycle
    which is the pre-v0.6.3 behaviour - degraded rather than broken.
    The main reason to expose it at all is to surface the degradation
    on non-mainstream builds so a forum reporter knows why standby
    still keeps the frontends busy.

    openatv (and every other enigma2 fork inspected) does NOT expose
    ``onEnterStandby`` / ``onLeaveStandby`` on the Session object. The
    canonical detection path is the ``Screens.Standby.Standby`` class:
    an instance is created when the box enters standby, and closed on
    resume. Every fork keeps this contract for legacy plugin
    compatibility, so patching the base class covers Standby, Standby2
    (subclass on openatv) and every downstream variant in one shot.
    """
    optional = []
    try:
        from Screens.Standby import Standby  # noqa: F401
    except Exception as exc:
        optional.append("Screens.Standby.Standby import: %r" % exc)
    return [], optional


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
        self._external_rate_limiter = _ExternalRateLimiter()
        self._external_stats = _ExternalStats()
        self._external_stats_timer = None

        # v0.6.3 standby handling. When True, the re-arm cycle and the
        # external-api arm path both short-circuit to no-op so no new
        # pretune allocation happens while the box is in standby. Live
        # slots are released on the enter-standby edge; the leave edge
        # schedules a fresh re-arm. Dispatch runs through module-level
        # helpers that resolve Controller.peek() so the class-level
        # Standby patch (installed once per process) stays independent
        # of the controller lifecycle.
        self._in_standby = False

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
            sb_crit, sb_opt = sanity_check_standby_hook()
            for w in pool_opt + arb_opt + ext_opt + sb_opt:
                warn("sanity (degraded): %s" % w)
            if pool_crit + arb_crit + ext_crit + sb_crit:
                self._sanity_refuse(pool_crit + arb_crit + ext_crit + sb_crit)
                return

            self._apply_pool_capacity()
            self._arbiter.start()
            self._wire_evnewproginfo()
            self._wire_standby_hooks()
            self._start_external_stats_heartbeat()
            infobar = self._find_infobar()
            if infobar is None:
                warn("InfoBar not ready yet; deferring interceptor start")
                self._defer_interceptor_start()
            else:
                if not self._apply_interceptor(infobar):
                    return
            self._enabled = True
            self._schedule_rearm(delay_ms=500)
            info("controller started (next=%s prev=%s last=%s; "
                 "descramble next=%s prev=%s last=%s)" % (
                bool(cfg.pretune_next.value),
                bool(cfg.pretune_prev.value),
                bool(cfg.pretune_history.value),
                bool(cfg.prewarm_descrambler_next.value),
                bool(cfg.prewarm_descrambler_prev.value),
                bool(cfg.prewarm_descrambler_history.value)))
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
            self._stop_external_stats_heartbeat()
            self._unwire_evnewproginfo()
            self._unwire_standby_hooks()
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
            if self._in_standby:
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

        Defended against caller abuse by ``_ExternalRateLimiter``:
        same-ref calls within 100 ms collapse, and the distinct-ref
        burst is capped at ``cfg.external_max_calls_per_sec`` per
        second. Failures inside this method do NOT increment the
        plugin watchdog counter - the 3-failure self-disable
        protects against internal bugs, not against external-caller
        misuse.
        """
        try:
            if not self._enabled:
                return
            if self._in_standby:
                debug("pretune_external: box in standby, dropping")
                return
            # Refresh the TTL on every call, even on drop / idempotent
            # paths - the caller still wants the slot to stay alive
            # even on the short-circuit arm path.
            self._refresh_external_ttl()
            try:
                key = _ref_key(ref)
            except Exception:
                debug("pretune_external: unprintable ref, dropping")
                return
            try:
                max_per_sec = int(cfg.external_max_calls_per_sec.value)
            except Exception:
                max_per_sec = 10
            verdict = self._external_rate_limiter.classify(
                key, time.monotonic_ns(), max_per_sec)
            if verdict == "idempotent":
                self._external_stats.calls_idempotent += 1
                debug("pretune_external idempotent (same ref within 100 ms): %s"
                      % key)
                return
            if verdict == "throttled":
                self._external_stats.calls_throttled += 1
                self._external_rate_limiter.record_drop()
                drops = self._external_rate_limiter.take_warn_count(
                    time.monotonic_ns())
                if drops is not None:
                    warn("pretune_external throttled: max %d distinct refs/sec "
                         "exceeded; dropped %d call(s) since last warn"
                         % (max_per_sec, drops))
                return
            existing = self._pool.lookup(ref)
            if existing is not None:
                self._external_stats.calls_convergence_skip += 1
                debug("pretune_external: ref already armed "
                      "(role=%s), skipping arm" % existing.role.value)
                return
            self._pool.arm({Role.EXTERNAL: [ref]})
            self._external_stats.calls_armed += 1
            info("pretune_external armed ref=%s" % key)
        except Exception as exc:
            # External-caller induced failures do NOT increment the
            # watchdog counter - those are someone else's bugs and
            # must not let a misbehaving companion plugin take down
            # the controller.
            # Always include the full traceback - errors are rare
            # enough that log volume is not a concern, and the
            # traceback is what a forum reporter needs to attribute
            # the fault.
            self._external_stats.calls_errors += 1
            error("pretune_external: %r\n%s" % (exc, traceback.format_exc()))

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
                    self._external_stats.releases_explicit += 1
                    info("release_external (unconditional)")
                    self._stop_external_ttl()
                return
            target_key = _ref_key(ref)
            for slot in slots:
                if slot.service_ref is None:
                    continue
                if _ref_key(slot.service_ref) == target_key:
                    self._pool.release_after_swap(slot)
                    self._external_stats.releases_explicit += 1
                    info("release_external matched ref=%s" % target_key)
                    self._stop_external_ttl()
                    return
            debug("release_external: no EXTERNAL slot holds %s "
                  "(possibly already torn down)" % target_key)
        except Exception as exc:
            error("release_external: %r\n%s" % (exc, traceback.format_exc()))

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
                ttl_ms = int(cfg.external_slot_ttl_min.value) * 60 * 1000
            except AttributeError:
                ttl_ms = 300000  # config key not yet present
            except Exception:
                ttl_ms = 300000
            self._external_ttl_timer.stop()
            self._external_ttl_timer.start(ttl_ms, True)
            self._external_stats.ttl_refreshes += 1
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
        # Bookkeeping for the stats heartbeat - release_external
        # itself credits as releases_explicit because it cannot tell
        # the caller apart; bump the TTL counter here and decrement
        # explicit so the totals stay correct.
        self.release_external(None)
        if self._external_stats.releases_explicit > 0:
            self._external_stats.releases_explicit -= 1
        self._external_stats.releases_via_ttl += 1

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

    # --- standby handling (v0.6.3) -------------------------------------

    def _wire_standby_hooks(self):
        """Install a monkey-patch on ``Screens.Standby.Standby.__init__``
        so every standby-screen instantiation notifies the controller.

        openatv (and inspected forks) do not expose an ``onEnterStandby``
        signal on the Session object; the canonical detection surface
        is the standby Screen instance itself. Patching the base class
        covers both ``Standby`` (called when the user is in the menu at
        standby time) and ``Standby2`` (subclass, called when TV is
        live at standby time), because both funnel through the same
        ``__init__``. Downstream forks that add their own subclasses
        pick up the hook the same way.

        The patch is intentionally idempotent (marker attribute on the
        class) so a Controller restart or a spurious re-wire does not
        stack multiple wrappers. Dispatch goes through
        ``Controller.peek()`` so an inactive plugin cleanly no-ops.
        """
        try:
            from Screens.Standby import Standby as _Standby
        except Exception as exc:
            warn("_wire_standby_hooks: Standby class unavailable (%r)" % exc)
            return
        if getattr(_Standby, "_fbc_csc_wrapped", False):
            debug("_wire_standby_hooks: Standby.__init__ already wrapped")
            return
        _orig_init = _Standby.__init__

        def _wrapped_init(self, session, *args, **kwargs):
            _orig_init(self, session, *args, **kwargs)
            try:
                # Screen.__init__ populates self.onClose - safe to
                # append here without a hasattr check because we are
                # running after the base init.
                self.onClose.append(_standby_leave_dispatch)
                _standby_enter_dispatch()
            except Exception as exc:
                # Never let a plugin bug take down the standby screen.
                error("standby wrapper failed: %r" % exc)

        _Standby.__init__ = _wrapped_init
        _Standby._fbc_csc_wrapped = True
        debug("_wire_standby_hooks: Standby.__init__ wrapped")

    def _unwire_standby_hooks(self):
        """No-op by design.

        The Standby class-level patch is idempotent and dispatches via
        ``Controller.peek()``, so an inactive controller already sees
        the dispatcher return early. Trying to restore the original
        ``__init__`` would race against any other plugin that patched
        Standby after us and revert their patch too. Leaving the
        wrapper installed for the process lifetime is the safer
        contract.
        """
        return

    def _on_enter_standby(self):
        """Release every pool slot and block further arming.

        Live viewing is stopped by enigma2 itself on standby entry; the
        pool's pretune slots are the only remaining tuner consumers
        this plugin owns. Freeing them lets the FBC frontends idle so
        the box can reach a proper standby state (relevant on shared
        Unicable installations where a second receiver needs the SCR
        bands, and for users who keep the box permanently in standby).
        """
        try:
            info("entering standby: releasing pool")
            self._in_standby = True
            if self._rearm_timer is not None:
                try:
                    self._rearm_timer.stop()
                except Exception:
                    pass
            self._pool.release_for("standby")
            self._stop_external_ttl()
        except Exception as exc:
            error("_on_enter_standby: %r" % exc)

    def _on_leave_standby(self):
        try:
            info("leaving standby: scheduling re-arm")
            self._in_standby = False
            self._schedule_rearm(delay_ms=500)
        except Exception as exc:
            error("_on_leave_standby: %r" % exc)

    # --- external stats heartbeat (60s, info-level) --------------------

    def _start_external_stats_heartbeat(self):
        """Fires once per minute; emits a one-line summary of the
        external slot activity at info level when there has been
        any. Quiet minutes emit nothing - the log stays clean while
        an idle box is sitting around.
        """
        try:
            from enigma import eTimer
            if self._external_stats_timer is None:
                self._external_stats_timer = eTimer()
                self._external_stats_timer.callback.append(
                    self._flush_external_stats)
            self._external_stats_timer.stop()
            self._external_stats_timer.start(60_000, False)  # repeating
        except Exception as exc:
            error("_start_external_stats_heartbeat: %r" % exc)

    def _stop_external_stats_heartbeat(self):
        if self._external_stats_timer is not None:
            try:
                self._external_stats_timer.stop()
            except Exception:
                pass

    def _flush_external_stats(self):
        try:
            if self._external_stats.has_activity():
                info(self._external_stats.format())
            self._external_stats.reset()
        except Exception as exc:
            error("_flush_external_stats: %r" % exc)

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
                    self._external_stats.releases_via_evnewproginfo += 1
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


# --- module-level helpers ----------------------------------------------

def _standby_enter_dispatch():
    """Class-level Standby wrapper landing pad for enter events.

    Runs at Standby-screen instantiation time. Resolves the live
    controller through ``Controller.peek()`` so an uninstalled or
    stopped plugin cleanly no-ops without ever touching the standby
    path.
    """
    c = Controller.peek()
    if c is None or not c._enabled:
        return
    c._on_enter_standby()


def _standby_leave_dispatch():
    """Class-level Standby wrapper landing pad for close events.

    Registered on the Standby screen's ``onClose`` list, so it fires
    when the box wakes up. Same peek-and-drop pattern as the enter
    dispatcher.
    """
    c = Controller.peek()
    if c is None or not c._enabled:
        return
    c._on_leave_standby()


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
