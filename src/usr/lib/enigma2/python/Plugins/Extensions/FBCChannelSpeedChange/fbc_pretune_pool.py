"""Pool of pre-tuned services backed by NavigationInstance.recordService.

On this build only `allocateRawChannel` is exposed on
`eDVBResourceManager` (slot-index level, wrong abstraction for
service-aware pre-tuning) and `eDVBChannel` is not importable from
the Python `enigma` module. `NavigationInstance.recordService(ref)`
returns an `iRecordableServicePtr` and is the documented entry point
for background tuning.

The pre-tune mechanism: call `recordService(ref)` to allocate a
demodulator and tune the transponder; hold the returned object. On
swap-in stop the recordable and immediately `playService(ref)`. The
resource manager's transponder-sharing logic keeps the tuner locked
across the transition; the PAT/PMT cache stays warm, which is where
the latency win lives.

Slot lifecycle: IDLE -> TUNING -> LOCKED -> IDLE (after swap_in or
release). RELEASED is only used during pool shutdown.
"""

import threading
from enum import Enum

from .logger import info, debug, warn, error
from .config import cfg as _cfg


_DUMPED_RECORDABLE_API = False


class Role(Enum):
    NEXT = "next"
    PREV = "prev"
    HISTORY = "history"
    # Driven by the public api module (Phase 2 onwards). External
    # callers - currently Oberhesse's FCC-Extender, future native
    # channel-list hover hook - feed a service reference through
    # PreTuneSingleChannel and the controller routes it into this
    # bucket. EXTERNAL never competes with the internal predictor;
    # NEXT / PREV / HISTORY keep their own capacity.
    EXTERNAL = "external"


class SlotState(Enum):
    IDLE = "idle"
    TUNING = "tuning"
    LOCKED = "locked"
    RELEASED = "released"


class PreTuneSlot:
    __slots__ = ("role", "service_ref", "recordable", "state", "_lock_timer",
                 "_tmp_file", "_started", "_reclaim_timer")

    def __init__(self, role):
        self.role = role
        self.service_ref = None
        self.recordable = None  # iRecordableServicePtr or None
        self.state = SlotState.IDLE
        self._lock_timer = None
        self._tmp_file = None
        self._started = False
        self._reclaim_timer = None

    def __repr__(self):
        sr = self.service_ref.toString() if self.service_ref else "-"
        return "<PreTuneSlot %s state=%s ref=%s>" % (self.role.value, self.state.value, sr)


class FBCPreTunePool:
    def __init__(self, nav_provider=None, nim_manager_provider=None):
        self._nav_provider = nav_provider or _default_nav_provider
        self._nim_provider = nim_manager_provider or _default_nim_provider

        self._lock = threading.RLock()
        self._slots_by_role = {}        # Role -> list[PreTuneSlot]
        self._suppressed_roles = set()

    # --- public API -----------------------------------------------------

    def sanity_check(self):
        """Inspect the NavigationInstance surface the pool depends on.

        Returns (critical, optional). A NavigationInstance that exists
        but lacks recordService is a genuine API incompatibility
        (critical). A NavigationInstance that is None is treated as
        optional - it is usually just not ready yet at session start
        and the allocate path already tolerates it. A missing NIM
        manager only costs FBC enumeration, so it is optional too.
        """
        critical = []
        optional = []
        nav = self._nav_provider()
        if nav is None:
            optional.append("NavigationInstance (not ready yet)")
        else:
            if not hasattr(nav, "recordService"):
                critical.append("NavigationInstance.recordService")
            if not hasattr(nav, "playService"):
                critical.append("NavigationInstance.playService")
        if self._nim_provider() is None:
            optional.append("NimManager (FBC enumeration unavailable)")
        return (critical, optional)

    def configure(self, capacity_by_role):
        with self._lock:
            for role, want in capacity_by_role.items():
                cur = self._slots_by_role.setdefault(role, [])
                while len(cur) > want:
                    self._release_slot(cur.pop())
                while len(cur) < want:
                    cur.append(PreTuneSlot(role))
            debug("pool.configure %s -> %s" % (capacity_by_role, self._summary()))

    def arm(self, plan):
        with self._lock:
            for role, targets in plan.items():
                slots = self._slots_by_role.get(role, [])
                for i, slot in enumerate(slots):
                    if role in self._suppressed_roles:
                        if slot.state != SlotState.IDLE:
                            self._release_slot(slot, keep_object=True)
                        continue
                    target = targets[i] if i < len(targets) else None
                    if target is None:
                        if slot.state != SlotState.IDLE:
                            self._release_slot(slot, keep_object=True)
                        continue
                    self._ensure_tuned(slot, target)

    def lookup(self, service_ref):
        if service_ref is None:
            return None
        key = _ref_key(service_ref)
        debug("LOOKUP target=%s" % key)
        match = None
        with self._lock:
            for slots in self._slots_by_role.values():
                for slot in slots:
                    if slot.service_ref is None:
                        debug("  slot role=%s state=%s ref=<empty>" % (
                            slot.role.value, slot.state.value))
                        continue
                    slot_key = _ref_key(slot.service_ref)
                    matched = slot_key == key and slot.state in (SlotState.LOCKED, SlotState.TUNING)
                    debug("  slot role=%s state=%s key=%s match=%s" % (
                        slot.role.value, slot.state.value, slot_key, matched))
                    if matched and match is None:
                        match = slot
        return match

    def confirm_hit(self, service_ref):
        """Look up a matching pre-tuned slot. Returns the slot on a hit,
        None on a miss. Does NOT touch the navigation stack.

        The caller is expected to invoke the standard enigma2 zap path
        (zapUp/zapDown/historyBack/...) while the pre-tuned recordable
        is still alive, then call release_after_swap(slot) once the
        zap has completed. eDVBResourceManager's channel-sharing logic
        finds the recordable on the target transponder and reuses its
        channel, so the speedup is preserved AND the normal bookkeeping
        (servicelist.history, channel-list cursor, evNewProgramInfo
        signals) runs unmodified.
        """
        return self.lookup(service_ref)

    def release_after_swap(self, slot):
        """Called by the interceptor after the original zap method has
        completed. Tears down the recordable and resets the slot so the
        next arm() cycle can refill it.
        """
        if slot is None:
            return
        with self._lock:
            debug("release_after_swap role=%s" % slot.role.value)
            self._release_slot(slot, keep_object=True)

    # Convenience wrapper kept for unit tests and any caller that
    # still wants the all-in-one shape. Production interceptor code
    # uses confirm_hit + original.zap + release_after_swap so the
    # channel-list state stays consistent.
    def swap_in(self, service_ref):
        slot = self.confirm_hit(service_ref)
        if slot is None:
            return False
        nav = self._nav_provider()
        if nav is None:
            warn("swap_in: NavigationInstance unavailable")
            return False
        try:
            ref = slot.service_ref
            debug("swap_in HIT role=%s ref=%s (legacy path)" %
                  (slot.role.value, ref.toString()))
            nav.playService(ref)
            self.release_after_swap(slot)
            return True
        except Exception as exc:
            error("swap_in failed: %r" % exc)
            return False

    def release_for(self, reason):
        with self._lock:
            info("release_for(%s)" % reason)
            for slots in self._slots_by_role.values():
                for slot in slots:
                    self._release_slot(slot, keep_object=True)

    def suppress(self, roles):
        with self._lock:
            self._suppressed_roles |= set(roles)
            for role in roles:
                for slot in self._slots_by_role.get(role, []):
                    self._release_slot(slot, keep_object=True)

    def unsuppress(self, roles):
        with self._lock:
            self._suppressed_roles -= set(roles)

    def shutdown(self):
        with self._lock:
            for slots in self._slots_by_role.values():
                for slot in slots:
                    self._release_slot(slot)
            self._slots_by_role.clear()
            info("pool shutdown")

    # --- internals ------------------------------------------------------

    def _ensure_tuned(self, slot, target_ref):
        if slot.service_ref is not None and _ref_key(slot.service_ref) == _ref_key(target_ref):
            return  # already aimed at this service
        if slot.state != SlotState.IDLE:
            self._release_slot(slot, keep_object=True)
        self._allocate_pretune(slot, target_ref)

    def _allocate_pretune(self, slot, target_ref):
        # Master safety gate: even if slot counts > 0 are persisted
        # from an earlier install, refuse to touch the tuner subsystem
        # unless the master switch is on. Guards against a regression
        # in the allocation path self-replicating across boots.
        try:
            if not _cfg.allow_pretune.value:
                debug("allow_pretune is False; skipping allocation for %s"
                      % target_ref.toString())
                return
        except Exception:
            return

        nav = self._nav_provider()
        if nav is None:
            warn("allocate: NavigationInstance unavailable")
            return
        if not self._any_fbc_capable():
            debug("no FBC-capable slot configured; skipping pre-tune")
            return
        try:
            rec = nav.recordService(target_ref)
            if rec is None:
                info("recordService returned None for %s" % target_ref.toString())
                return
            slot.recordable = rec
            slot.service_ref = target_ref
            slot.state = SlotState.TUNING
            self._dump_recordable_api_once(rec)

            err_after_record = self._safe_get_error(rec)
            debug("pretune START role=%s ref=%s err_after_record=%s" % (
                slot.role.value, target_ref.toString(), err_after_record))

            self._probe_frontend_once(rec)

            # Optional second-tier path: call prepare(tmpfile) +
            # start() so the demod is actually tuned. The filename
            # must be non-empty (empty filenames trip an internal C++
            # assertion in eDVBServiceRecord::prepare). Gated behind
            # use_real_pretune so a regression in the allocation path
            # cannot self-replicate across boots without an explicit
            # opt-in.
            if _cfg.use_real_pretune.value:
                self._kick_real_tune(slot, rec)

            self._schedule_optimistic_lock(slot)
        except Exception as exc:
            error("recordService raised: %r" % exc)
            slot.recordable = None
            slot.service_ref = None
            slot.state = SlotState.IDLE

    def _direction_descramble(self, role):
        """Per-direction descramble flag for the pre-tune prepare() call.

        Reads cfg.prewarm_descrambler_{history,next,prev,external}
        (all default False). Returns False on any lookup error so a
        corrupted config never accidentally engages the descrambler.
        """
        try:
            if role is Role.HISTORY:
                return bool(_cfg.prewarm_descrambler_history.value)
            if role is Role.NEXT:
                return bool(_cfg.prewarm_descrambler_next.value)
            if role is Role.PREV:
                return bool(_cfg.prewarm_descrambler_prev.value)
            if role is Role.EXTERNAL:
                return bool(_cfg.prewarm_descrambler_external.value)
        except Exception:
            pass
        return False

    def _kick_real_tune(self, slot, rec):
        """Call prepare(tmpfile, ...) + start() so the demod is actually
        tuned to the transponder. Gated on cfg.use_real_pretune; required
        for the pre-tune to give a measurable speedup vs plain playService.

        Uses the canonical 9-arg iRecordableService.prepare signature
        verified against openatv/enigma2 branch 7.6
        lib/python/RecordTimer.py:
            prepare(filename, begin, end, eit_event_id,
                    name, description, tags,
                    descramble, record_ecm)

        The descramble flag is taken per-direction from
        cfg.prewarm_descrambler_{history,next,prev}, all default off.
        With descramble=False the recordable locks the transponder
        without engaging the CA path - no parallel-decode load on the
        card / softcam / CAM. Channel-sharing at swap-in still re-uses
        the locked tuner; the descrambler initialises on the live
        consumer. Trades ~400 ms first-clear-frame on scrambled HIT
        zaps (one ECM round-trip) for universal safety regardless of
        the user's decryption stack.
        """
        import time
        import os
        # Per-allocation unique filename - the same role can be re-armed
        # multiple times in quick succession; the previous file may not
        # be unlinked yet when prepare() opens the new one.
        tmp = "/tmp/fbc_csc_pretune_%s_%d.ts" % (slot.role.value, int(time.time() * 1000))
        slot._tmp_file = tmp
        descramble = self._direction_descramble(slot.role)
        try:
            err = rec.prepare(tmp, 0, 0, 0, "", "", "", descramble, False)
            debug("pretune prepare role=%s descramble=%s file=%s err=%r" % (
                slot.role.value, descramble, tmp, err))
            if err:
                # Non-zero means prepare refused; release the slot rather
                # than leaving a half-allocated recordable around.
                warn("prepare returned non-zero err=%r, releasing slot" % err)
                self._release_slot(slot, keep_object=True)
                return
        except Exception as exc:
            error("prepare raised: %r" % exc)
            self._release_slot(slot, keep_object=True)
            return

        try:
            start_err = rec.start()
            slot._started = True
            debug("pretune start role=%s err=%r" % (slot.role.value, start_err))
            if start_err:
                warn("start returned non-zero err=%r" % start_err)
                return
        except Exception as exc:
            error("start raised: %r" % exc)
            self._release_slot(slot, keep_object=True)
            return

        # eDVBServiceRecord now writes to slot._tmp_file at ~7 MB/s.
        # On this box /tmp is tmpfs (RAM-backed, ~1.4 GB). Two active
        # slots at 14 MB/s would fill /tmp in under two minutes
        # without zapping. Punching holes in the file every couple of
        # seconds releases the tmpfs pages while keeping the writer's
        # append offset valid (the file stays "logically" the same
        # size, just sparse before the writer's current position).
        self._schedule_reclaim(slot)

    # ---- /tmp reclaim via PUNCH_HOLE ----------------------------------

    _RECLAIM_INTERVAL_MS = 2000

    def _schedule_reclaim(self, slot):
        try:
            from enigma import eTimer
        except Exception:
            return
        try:
            timer = eTimer()
            slot._reclaim_timer = timer
            timer.callback.append(lambda: self._reclaim_tmpfs(slot))
            timer.start(self._RECLAIM_INTERVAL_MS, False)  # repeating
        except Exception as exc:
            error("_schedule_reclaim: %r" % exc)

    def _reclaim_tmpfs(self, slot):
        if slot is None or not slot._tmp_file:
            return
        try:
            import os
            for path in self._slot_paths(slot._tmp_file):
                if not os.path.exists(path):
                    continue
                try:
                    size = os.path.getsize(path)
                except OSError:
                    continue
                if size <= 0:
                    continue
                # Punch a hole over the whole current size. The writer's
                # next append still goes to (old_size + chunk), with the
                # space before it now sparse and not occupying tmpfs RAM.
                if not _punch_hole(path, 0, size):
                    # Fall back to truncate if PUNCH_HOLE is unsupported
                    # (tmpfs supports it since kernel 3.5; this is just
                    # belt and suspenders).
                    try:
                        with open(path, "rb+") as fh:
                            fh.truncate(0)
                    except OSError:
                        pass
        except Exception as exc:
            debug("_reclaim_tmpfs: %r" % exc)

    @staticmethod
    def _slot_paths(ts_path):
        yield ts_path
        for suf in (".ap", ".sc", ".cuts", ".meta", ".eit"):
            yield ts_path + suf

    _PROBED = False

    def _probe_frontend_once(self, rec):
        if FBCPreTunePool._PROBED:
            return
        FBCPreTunePool._PROBED = True
        try:
            fi = rec.frontendInfo()
            info("DIAG: frontendInfo() returned %r (type=%s)" % (fi, type(fi).__name__))
        except Exception as exc:
            info("DIAG: frontendInfo() raised %r" % exc)

    def _safe_get_error(self, rec):
        try:
            return rec.getError()
        except Exception as exc:
            return "<getError raised: %r>" % exc

    def _schedule_optimistic_lock(self, slot):
        try:
            from enigma import eTimer
        except Exception:
            return
        try:
            timer = eTimer()
            # eTimer cannot be GC'd while pending; pin to the slot.
            slot._lock_timer = timer
            timer.callback.append(lambda: self._mark_locked_optimistic(slot))
            timer.start(1500, True)
        except Exception as exc:
            error("_schedule_optimistic_lock: %r" % exc)

    def _mark_locked_optimistic(self, slot):
        with self._lock:
            if slot.state == SlotState.TUNING:
                slot.state = SlotState.LOCKED
                debug("pretune LOCKED (optimistic) role=%s" % slot.role.value)

    def _any_fbc_capable(self):
        """At least one FBC tuner exists and is enabled.

        Sanity check only: no specific slot is pinned, since
        recordService lets enigma2's resource manager pick.
        Allocation is refused if no FBC tuner is available so a
        non-FBC tuner is never used.
        """
        nim_manager = self._nim_provider()
        if nim_manager is None:
            return False
        try:
            for slot in getattr(nim_manager, "nim_slots", []):
                is_fbc = (
                    (hasattr(slot, "isFBCTuner") and slot.isFBCTuner()) or
                    (hasattr(slot, "isFBCRoot") and slot.isFBCRoot()) or
                    (hasattr(slot, "isFBCLink") and slot.isFBCLink())
                )
                if not is_fbc:
                    continue
                enabled = True
                if hasattr(slot, "isEnabled"):
                    try:
                        enabled = bool(slot.isEnabled())
                    except Exception:
                        pass
                if enabled:
                    return True
        except Exception as exc:
            error("FBC enumeration failed: %r" % exc)
        return False

    def _release_slot(self, slot, keep_object=False):
        if slot is None:
            return
        try:
            # Stop the tmpfs reclaim timer first; it might fire
            # mid-cleanup and try to punch a hole in a file about to
            # be deleted.
            if slot._reclaim_timer is not None:
                try:
                    slot._reclaim_timer.stop()
                except Exception:
                    pass
                slot._reclaim_timer = None
            if slot.recordable is not None:
                # If the recording was started (use_real_pretune
                # path), call stop() before stopRecordService so the
                # demod is cleanly released and the file handle
                # closes.
                if slot._started:
                    try:
                        slot.recordable.stop()
                    except Exception as exc:
                        debug("recordable.stop on release: %r" % exc)
                    slot._started = False
                nav = self._nav_provider()
                if nav is not None:
                    try:
                        nav.stopRecordService(slot.recordable)
                    except Exception as exc:
                        debug("stopRecordService on release: %r" % exc)
                slot.recordable = None
            # Clean up the throwaway pretune file (if any).
            if slot._tmp_file:
                try:
                    import os
                    if os.path.exists(slot._tmp_file):
                        os.remove(slot._tmp_file)
                    # also clean up the .ap / .sc / .meta sidecars that
                    # eDVBServiceRecord may write next to the .ts file
                    for suf in (".ap", ".sc", ".cuts", ".meta", ".eit"):
                        side = slot._tmp_file + suf
                        if os.path.exists(side):
                            os.remove(side)
                except OSError as exc:
                    debug("tmp file cleanup: %r" % exc)
                slot._tmp_file = None
            slot.service_ref = None
            slot.state = SlotState.IDLE if keep_object else SlotState.RELEASED
        except Exception as exc:
            error("release_slot failed: %r" % exc)

    def _dump_recordable_api_once(self, rec):
        global _DUMPED_RECORDABLE_API
        if _DUMPED_RECORDABLE_API:
            return
        _DUMPED_RECORDABLE_API = True
        try:
            info("==== DIAG: iRecordableService methods (first allocation) ====")
            for n in sorted(dir(rec)):
                if n.startswith("_"):
                    continue
                try:
                    v = getattr(rec, n)
                    info("  %s [%s]" % (n, type(v).__name__))
                except Exception as exc:
                    info("  %s <getattr error: %r>" % (n, exc))
            info("==== DIAG: end iRecordableService ====")
        except Exception as exc:
            error("_dump_recordable_api_once: %r" % exc)

    def _summary(self):
        return {role.value: [s.state.value for s in slots]
                for role, slots in self._slots_by_role.items()}


# --- helpers ------------------------------------------------------------

def _ref_key(service_ref):
    try:
        s = service_ref.toString()
    except AttributeError:
        s = str(service_ref)
    parts = s.split(":")
    if len(parts) >= 11:
        parts = parts[:10]
    return ":".join(parts)


def _punch_hole(path, offset, length):
    """fallocate(fd, FALLOC_FL_PUNCH_HOLE | FALLOC_FL_KEEP_SIZE, offset, len)

    Returns True on success, False on any failure (caller falls back to
    truncate). The .ts file stays "logically" the same size from the
    writer's perspective; the underlying tmpfs pages are released.
    """
    try:
        import os, ctypes, ctypes.util
        FALLOC_FL_KEEP_SIZE = 0x01
        FALLOC_FL_PUNCH_HOLE = 0x02
        libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
        fd = os.open(path, os.O_RDWR)
        try:
            # ARM 32-bit: fallocate takes (int fd, int mode, off_t offset, off_t len)
            # off_t is 64-bit on Linux even on 32-bit ARM since glibc 2.0+
            libc.fallocate.argtypes = [
                ctypes.c_int, ctypes.c_int, ctypes.c_int64, ctypes.c_int64,
            ]
            libc.fallocate.restype = ctypes.c_int
            rc = libc.fallocate(
                fd,
                FALLOC_FL_PUNCH_HOLE | FALLOC_FL_KEEP_SIZE,
                ctypes.c_int64(offset),
                ctypes.c_int64(length),
            )
            return rc == 0
        finally:
            os.close(fd)
    except Exception:
        return False


def _default_nav_provider():
    try:
        import NavigationInstance
        return NavigationInstance.instance
    except Exception:
        return None


def _default_nim_provider():
    try:
        from Components.NimManager import nimmanager
        return nimmanager
    except Exception:
        return None
