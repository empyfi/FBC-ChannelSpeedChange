"""Hook InfoBar zap actions and route them through the pre-tune pool.

Bound-method wrapping on the live InfoBar instance; no global class
patching, so disable can cleanly restore the originals. This is the
single most safety-critical module in the plugin.

Every wrapper:
  1. Looks up the predicted destination service.
  2. Asks the pool for a swap_in.
  3. On HIT - skip the original method (it would re-do the work).
  4. On MISS - call the original method unchanged.
  5. On ANY exception - fall back to the original method.

The wrapper must never raise into enigma2, hence the broad try/except.
"""

import time

from .logger import info, debug, error
from .config import cfg


_TIMING_CSV = "/tmp/fbc_csc_timing.csv"


def _emit_csv(row):
    try:
        with open(_TIMING_CSV, "a") as fh:
            fh.write(",".join(str(c) for c in row) + "\n")
    except OSError:
        pass


def _ensure_csv_header():
    """Create the CSV with a header row if it does not exist yet.
    Existing rows are left in place so multiple sessions of timing
    data can be collected and averaged.
    """
    import os
    if os.path.exists(_TIMING_CSV):
        return
    try:
        with open(_TIMING_CSV, "w") as fh:
            fh.write("epoch,attr,result,delta_ms\n")
    except OSError:
        pass


_WRAPPED_ATTR = "_fbc_csc_wrapped"


class ZapInterceptor:
    def __init__(self, pool, predictor, on_zap=None):
        self._pool = pool
        self._predictor = predictor
        self._on_zap = on_zap  # Controller callback, fires after every zap
        self._infobar = None
        self._wrapped = []  # list of (obj, attr, original)
        self._nav_event_conn = None
        # In-flight zap timing: set when a WRAP wrapper fires,
        # cleared when the matching evTunedIn arrives. Allows
        # end-to-end zap latency computation without depending on
        # infobar event hooks.
        self._zap_start_ns = None
        self._zap_attr = None
        self._zap_hit = None

    def start(self, infobar):
        self._infobar = infobar
        self._wrap_zap_methods()
        self._wire_external_zap_listener()
        _ensure_csv_header()
        self._dump_servicelist_history_once()
        info("interceptor started")

    def _dump_servicelist_history_once(self):
        """One-shot diagnostic dump describing the shape of
        InfoBar.servicelist.history. History list layout varies
        across builds; this surfaces the actual shape at info level.
        """
        try:
            sl = getattr(self._infobar, "servicelist", None)
            if sl is None:
                info("HIST DIAG: no servicelist on infobar")
                return
            history = getattr(sl, "history", None)
            info("HIST DIAG: history type=%s len=%s" % (
                type(history).__name__,
                len(history) if hasattr(history, "__len__") else "?"))
            if history and hasattr(history, "__getitem__"):
                try:
                    sample = history[-1]
                    info("HIST DIAG: last entry type=%s repr=%r" % (
                        type(sample).__name__, sample))
                except Exception as exc:
                    info("HIST DIAG: cannot read last entry: %r" % exc)
            # Surface other history-related servicelist attributes
            # so an alternate source can be picked if .history is
            # the wrong place.
            related = [n for n in dir(sl)
                       if "istor" in n.lower() and not n.startswith("_")]
            info("HIST DIAG: history-related attrs: %s" % related)
        except Exception as exc:
            error("_dump_servicelist_history_once: %r" % exc)

    def stop(self):
        self._unwire_external_zap_listener()
        self._unwrap_all()
        self._infobar = None
        try:
            from . import osd_timing
            osd_timing.cleanup()
        except Exception as exc:
            debug("osd cleanup: %r" % exc)
        info("interceptor stopped")

    # --- method wrapping -----------------------------------------------

    def _wrap_zap_methods(self):
        if self._infobar is None:
            return
        # zapUp / zapDown are wrapped on the InfoBar instance.
        # zapToService is the lower-level entry used by EPG, numeric
        # input, history-back; wrapping it covers all those paths in
        # one place.
        for name in ("zapUp", "zapDown"):
            self._wrap_directional(self._infobar, name)
        # historyBack lives on InfoBarChannelSelection too. If missing on
        # this build, skip without error.
        if hasattr(self._infobar, "historyBack"):
            self._wrap_directional(self._infobar, "historyBack")
        if hasattr(self._infobar, "historyNext"):
            self._wrap_directional(self._infobar, "historyNext")

    def _wrap_directional(self, obj, attr):
        if not hasattr(obj, attr):
            return
        original = getattr(obj, attr)
        if getattr(original, _WRAPPED_ATTR, False):
            return  # already wrapped (defensive against double-start)

        interceptor = self

        # zapUp / zapDown take a fast bypass: nav.playService
        # directly + manual servicelist.history update.
        # historyBack / historyNext go through the original method
        # because they need the history_pos navigation logic.
        fast_path_attrs = ("zapUp", "zapDown")

        def wrapper(*args, **kwargs):
            interceptor._zap_start_ns = time.monotonic_ns()
            interceptor._zap_attr = attr
            interceptor._zap_hit = None
            debug("WRAP %s fired (args=%d kwargs=%d)" % (attr, len(args), len(kwargs)))

            slot_to_release = None
            try:
                if cfg.enabled.value:
                    target = interceptor._predict_for(attr)
                    if target is None:
                        debug("WRAP %s: predictor returned None" % attr)
                        interceptor._zap_hit = False
                    else:
                        debug("WRAP %s: predictor -> %s" % (attr, target.toString()))
                        if attr in fast_path_attrs:
                            # Fast path: direct play + manual history
                            # update afterwards. Skips the original
                            # method's listbox UI work to keep zap
                            # perception snappy.
                            interceptor._zap_hit = True
                            if interceptor._pool.swap_in(target):
                                info("zap HIT via %s (fast path)" % attr)
                                interceptor._update_bookkeeping(target)
                                interceptor._notify_zap()
                                return None
                            interceptor._zap_hit = False
                        else:
                            # Pass-through path: confirm the slot, let
                            # original do its full work. Used for
                            # historyBack/historyNext where the navigation
                            # logic must run unmodified.
                            slot_to_release = interceptor._pool.confirm_hit(target)
                            interceptor._zap_hit = (slot_to_release is not None)
                            if interceptor._zap_hit:
                                info("zap HIT via %s (pass-through)" % attr)
                            else:
                                debug("zap MISS via %s" % attr)
            except Exception as exc:
                error("interceptor wrapper %s: %r" % (attr, exc))

            try:
                return original(*args, **kwargs)
            finally:
                if slot_to_release is not None:
                    try:
                        interceptor._pool.release_after_swap(slot_to_release)
                    except Exception as exc:
                        error("release_after_swap: %r" % exc)
                interceptor._notify_zap()

        setattr(wrapper, _WRAPPED_ATTR, True)
        try:
            setattr(obj, attr, wrapper)
            self._wrapped.append((obj, attr, original))
        except Exception as exc:
            error("could not wrap %s: %r" % (attr, exc))

    def _unwrap_all(self):
        for obj, attr, original in self._wrapped:
            try:
                setattr(obj, attr, original)
            except Exception as exc:
                error("unwrap %s failed: %r" % (attr, exc))
        self._wrapped = []

    def _predict_for(self, attr):
        # enigma2 maps zapUp -> servicelist.moveUp() (smaller index =
        # earlier in bouquet = prev_service in this predictor) and
        # zapDown -> moveDown() (larger index = next_service). The
        # mapping below mirrors what enigma2 does without the plugin.
        try:
            if attr == "zapUp":
                results = self._predictor.prev_service(count=1)
            elif attr == "zapDown":
                results = self._predictor.next_service(count=1)
            elif attr in ("historyBack", "historyNext"):
                results = self._predictor.history_service(count=1)
            else:
                return None
            return results[0] if results else None
        except Exception as exc:
            error("_predict_for(%s) failed: %r" % (attr, exc))
            return None

    # --- external zap detection ----------------------------------------

    def _wire_external_zap_listener(self):
        """Catch zaps that bypass the wrappers (EPG, channel-list
        select, numeric input, history-selector dialog). Listens to
        NavigationInstance.event for evStart and evTunedIn so the
        pool can re-arm against the new live service AND the OSD
        overlay shows latency for any kind of zap.
        """
        try:
            import NavigationInstance
            from enigma import iPlayableService
            nav = NavigationInstance.instance
            if nav is None:
                return
            self._evTunedIn = iPlayableService.evTunedIn
            # evStart fires the moment playService picks up a new
            # ref; used as a timing-start fallback when no WRAP has
            # bracketed the zap (e.g. history selector picks the
            # channel via nav.playService directly, bypassing the
            # wrappers).
            self._evStart = iPlayableService.evStart
            nav.event.append(self._on_nav_event)
            self._nav_event_conn = nav
        except Exception as exc:
            error("wire_external_zap_listener: %r" % exc)

    def _unwire_external_zap_listener(self):
        if self._nav_event_conn is None:
            return
        try:
            self._nav_event_conn.event.remove(self._on_nav_event)
        except Exception as exc:
            error("unwire_external_zap_listener: %r" % exc)
        finally:
            self._nav_event_conn = None

    def _on_nav_event(self, reason):
        try:
            if reason == self._evStart:
                # If no WRAP has set the start timestamp yet, this is an
                # external zap (history-selector dialog, EPG, numeric
                # input). Use evStart as the timing anchor instead so
                # the OSD overlay still shows a number.
                if self._zap_start_ns is None:
                    self._zap_start_ns = time.monotonic_ns()
                    self._zap_attr = "ext"
                    self._zap_hit = None
                    debug("NAV evStart - timing anchor set for external zap")
            elif reason == self._evTunedIn:
                self._record_zap_timing()
                debug("NAV evTunedIn (external or post-zap)")
                self._notify_zap()
        except Exception as exc:
            error("_on_nav_event: %r" % exc)

    def _record_zap_timing(self):
        start = self._zap_start_ns
        if start is None:
            # No timing anchor at all - shouldn't happen now that evStart
            # is wired as a fallback, but keep the guard for safety.
            info("ZAP_TIMING no anchor (skipped)")
            return
        try:
            delta_ms = (time.monotonic_ns() - start) / 1_000_000.0
            attr = self._zap_attr or "?"
            hit = self._zap_hit
            # External zaps (history selector, EPG, numeric input)
            # come through evStart with attr='ext' and no hit/miss
            # label; surfaced as EXT in the timing log and OSD.
            if attr == "ext":
                hit_str = "EXT"
            else:
                hit_str = "HIT" if hit else ("MISS" if hit is False else "?")
            # ZAP_TIMING stays at info level - this is the headline
            # data in the log, available without enabling verbose
            # debug.
            info("ZAP_TIMING attr=%s result=%s delta_ms=%.1f" % (attr, hit_str, delta_ms))
            _emit_csv([int(time.time()), attr, hit_str, "%.1f" % delta_ms])
            self._maybe_show_osd(attr, hit_str, delta_ms)
        except Exception as exc:
            error("_record_zap_timing: %r" % exc)
        finally:
            self._zap_start_ns = None
            self._zap_attr = None
            self._zap_hit = None

    def _update_bookkeeping(self, ref):
        """Replicate the parts of servicelist.zap() bypassed by the
        fast path. Without this the InfoBar history list and channel-
        list cursor go stale after every HIT.

        Defensive: each API access is wrapped because servicelist
        attribute names vary slightly across openatv versions.
        Bookkeeping failures log at debug level and are non-fatal -
        better stale state than a crashed wrapper.
        """
        if self._infobar is None:
            return
        sl = getattr(self._infobar, "servicelist", None)
        if sl is None:
            return
        try:
            # 1. Update the channel-list cursor so it points to the new
            #    live service. Without this, the next zapUp would move
            #    the cursor relative to the OLD position and skip a
            #    channel.
            if hasattr(sl, "setCurrentSelection"):
                try:
                    sl.setCurrentSelection(ref)
                except Exception as exc:
                    debug("setCurrentSelection: %r" % exc)

            # 2. Append to the history list. Prefer the official
            #    addToHistory method when available so the build's
            #    preferred deduplication / truncation logic applies;
            #    fall back to a manual append matching the canonical
            #    enigma2 semantics if it is absent.
            added = False
            if hasattr(sl, "addToHistory"):
                try:
                    sl.addToHistory(ref)
                    added = True
                except Exception as exc:
                    debug("addToHistory: %r" % exc)
            if not added:
                hist = getattr(sl, "history", None)
                if isinstance(hist, list):
                    # Skip if the last entry already matches - matches
                    # the openatv canonical behaviour and avoids
                    # duplicate consecutive entries when arm() re-tunes
                    # to the same target after an external zap.
                    if not hist or hist[-1] != ref:
                        hist.append(ref)
                        if hasattr(sl, "history_pos"):
                            try:
                                sl.history_pos = len(hist) - 1
                            except Exception:
                                pass
        except Exception as exc:
            error("_update_bookkeeping: %r" % exc)

    def _maybe_show_osd(self, attr, hit_str, delta_ms):
        if not cfg.show_osd_timing.value:
            return
        session = self._session_from_infobar()
        if session is None:
            return
        try:
            from . import osd_timing
            osd_timing.show(session, attr, hit_str, delta_ms)
        except Exception as exc:
            error("_maybe_show_osd: %r" % exc)

    def _session_from_infobar(self):
        ib = self._infobar
        return getattr(ib, "session", None) if ib is not None else None

    def _notify_zap(self):
        if self._on_zap is not None:
            try:
                self._on_zap()
            except Exception as exc:
                error("_on_zap callback: %r" % exc)
