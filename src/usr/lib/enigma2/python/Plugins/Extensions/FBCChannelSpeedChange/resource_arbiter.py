"""Watch PiP and Recording state, release demods on demand.

PiP and Recording have absolute priority. The arbiter listens for
early state transitions (STATE_PREPARED, not STATE_RUNNING) so the
pool gives up demodulators *before* enigma2 needs them - eliminating
the race where a recording start would fail because a tuner was
still held.

Graceful degradation: when PiP/Recording grab demods the pool stays
configured but with idle slots. As soon as the priority consumer
ends, re-arm restores full capacity.
"""

from .logger import info, warn, error
from .config import cfg


# enigma2 RecordTimer states. Values copied from RecordTimer.py for
# the OpenATV 7.x line; revisit here if upstream changes them.
STATE_PREPARED = 1
STATE_RUNNING = 2
STATE_ENDED = 3


class ResourceArbiter:
    """Wires NavigationInstance/RecordTimer + PiP events to the pool."""

    def __init__(self, pool,
                 record_timer_provider=None,
                 session_provider=None):
        self._pool = pool
        self._record_timer_provider = record_timer_provider or _default_record_timer_provider
        self._session_provider = session_provider or _default_session_provider
        self._record_active_count = 0
        self._pip_active = False
        self._record_conn = None
        self._pip_conn = None

    def start(self):
        self._wire_record_timer()
        self._wire_pip()
        info("arbiter started")

    def stop(self):
        self._record_conn = None
        self._pip_conn = None
        info("arbiter stopped")

    # --- recording ------------------------------------------------------

    def _wire_record_timer(self):
        rt = self._record_timer_provider()
        if rt is None:
            warn("record timer unavailable; recording arbitration disabled")
            return
        # OpenATV exposes a CList of stateChange callbacks via
        # NavigationInstance.RecordTimer.on_state_change that fires
        # for any timer's state transition; hook that signal
        # directly.
        try:
            on_state_change = getattr(rt, "on_state_change", None)
            if on_state_change is not None:
                on_state_change.append(self._on_record_state_change)
                self._record_conn = on_state_change
                return
            # Fallback: poll periodically. Not ideal but keeps the
            # arbiter functional on builds that lack the signal.
            warn("record timer has no on_state_change; periodic poll fallback")
            from enigma import eTimer
            self._poll_timer = eTimer()
            self._poll_timer.callback.append(self._poll_record_timer)
            self._poll_timer.start(2000, False)
            self._record_conn = self._poll_timer
        except Exception as exc:
            error("wire_record_timer failed: %r" % exc)

    def _on_record_state_change(self, timer):
        if not cfg.release_for_recording.value:
            return
        try:
            state = getattr(timer, "state", None)
            if state == STATE_PREPARED:
                self._record_active_count += 1
                info("recording PREPARED (active=%d) -> releasing pool" % self._record_active_count)
                self._pool.release_for("recording_prepared")
            elif state == STATE_ENDED:
                if self._record_active_count > 0:
                    self._record_active_count -= 1
                info("recording ENDED (active=%d)" % self._record_active_count)
                # arm() is the controller's job; the active counter
                # is the signal availability source, polled by the
                # controller.
        except Exception as exc:
            error("on_record_state_change: %r" % exc)

    def _poll_record_timer(self):
        rt = self._record_timer_provider()
        if rt is None:
            return
        try:
            running = 0
            for timer in getattr(rt, "timer_list", []):
                if getattr(timer, "state", 0) in (STATE_PREPARED, STATE_RUNNING):
                    running += 1
            prev = self._record_active_count
            self._record_active_count = running
            if running > prev and cfg.release_for_recording.value:
                info("poll: recording started -> releasing pool")
                self._pool.release_for("recording_poll")
        except Exception as exc:
            error("poll_record_timer: %r" % exc)

    # --- PiP ------------------------------------------------------------

    def _wire_pip(self):
        # PiP events are fired via session.pipshown / Session
        # callbacks. OpenATV varies; the robust approach is to poll
        # session.pip on a short timer when armed - cost is
        # negligible and avoids hooks into volatile internals.
        try:
            from enigma import eTimer
            self._pip_timer = eTimer()
            self._pip_timer.callback.append(self._poll_pip)
            self._pip_timer.start(1500, False)
        except Exception as exc:
            error("wire_pip failed: %r" % exc)

    def _poll_pip(self):
        if not cfg.release_for_pip.value:
            return
        session = self._session_provider()
        if session is None:
            return
        try:
            shown = bool(getattr(session, "pipshown", False))
            if shown and not self._pip_active:
                self._pip_active = True
                info("PiP started -> releasing pool")
                self._pool.release_for("pip_start")
            elif not shown and self._pip_active:
                self._pip_active = False
                info("PiP stopped")
        except Exception as exc:
            error("poll_pip: %r" % exc)

    # --- queries used by Controller ------------------------------------

    def priority_active(self):
        """True if PiP or any recording is currently consuming resources."""
        return self._record_active_count > 0 or self._pip_active


# --- default providers -------------------------------------------------

def _default_record_timer_provider():
    try:
        import NavigationInstance
        nav = NavigationInstance.instance
        if nav is not None:
            return nav.RecordTimer
    except Exception:
        pass
    return None


def _default_session_provider():
    try:
        from Screens.InfoBar import InfoBar
        if InfoBar.instance is not None:
            return InfoBar.instance.session
    except Exception:
        pass
    return None
