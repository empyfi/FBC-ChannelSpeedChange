"""Tiny on-screen overlay that shows the most recent zap latency.

Opt-in via cfg.show_osd_timing. Auto-hides 1500 ms after the last
update so it never lingers on the screen. Colour-coded so a glance
is enough:

    green   < 200 ms    HIT - pretune fully effective
    yellow  < 500 ms    HIT - decoder or descrambling cost dominated
    orange  < 800 ms    HIT but heavy, or fast MISS
    red    >= 800 ms    MISS - fell back to the standard zap path
    teal               NEAR - intra-TP channel-share (different service,
                              same transponder as an armed slot)
    cyan               EXT  - bypass cold tune, no pool involvement

Implementation: a single Screen instance via
session.instantiateDialog(); each zap calls show()/hide() on the
same instance and resets a 1500 ms eTimer. This avoids stacking
dialogs on rapid Channel+ presses and never interferes with input
routing because the screen has no ActionMap.

Position is computed at runtime from getDesktop(0).size() so the
overlay lands in a sensible corner regardless of skin resolution.
"""

from .logger import info, debug, error


_OSD = None  # singleton instance, created lazily on first show()


def show(session, attr, result, delta_ms):
    """Called from the interceptor right after timing data is recorded."""
    if session is None:
        debug("osd_timing.show: session is None")
        return
    try:
        global _OSD
        if _OSD is None or _OSD.session is not session:
            _OSD = _make_osd(session)
            if _OSD is None:
                return
            info("OSD overlay created at runtime position %s" % (_OSD._position,))
        _OSD.flash(attr, result, delta_ms)
    except Exception as exc:
        error("osd_timing.show: %r" % exc)


def cleanup():
    global _OSD
    try:
        if _OSD is not None:
            _OSD.hard_close()
    except Exception as exc:
        debug("osd_timing.cleanup: %r" % exc)
    _OSD = None


def _compute_position(desktop_w, desktop_h):
    """Top-right corner with a 20 px margin from each edge."""
    width = 320
    height = 64
    margin = 20
    x = max(0, desktop_w - width - margin)
    y = margin + 60   # leave room above for the InfoBar's clock area
    return x, y, width, height


def _make_osd(session):
    try:
        from Screens.Screen import Screen
        from Components.Label import Label
        from enigma import eTimer, gRGB, getDesktop, ePoint, eSize
    except Exception as exc:
        error("osd_timing: enigma imports failed: %r" % exc)
        return None

    try:
        ds = getDesktop(0).size()
        dw, dh = ds.width(), ds.height()
    except Exception:
        dw, dh = 1920, 1080
    x, y, w, h = _compute_position(dw, dh)

    class _ZapTimingOSD(Screen):
        # Skin position is a placeholder; instance.move() overrides
        # it after construction to honour the real desktop size
        # instead of a hard-coded one.
        skin = (
            '<screen name="FBCChannelSpeedChangeOSD" '
            'position="100,100" size="%d,%d" '
            'title=" " flags="wfNoBorder" zPosition="20" '
            'backgroundColor="#a0000000">'
            '<widget name="text" position="10,8" size="%d,%d" '
            'font="Regular;32" halign="center" valign="center" '
            'foregroundColor="white" backgroundColor="#a0000000" '
            'transparent="1"/>'
            '</screen>'
        ) % (w, h, w - 20, h - 16)

        def __init__(self, session):
            Screen.__init__(self, session)
            self["text"] = Label("")
            self._timer = eTimer()
            self._timer.callback.append(self._auto_hide)
            self._visible = False
            self._position = (x, y, w, h)
            self.onLayoutFinish.append(self._reposition)

        def _reposition(self):
            try:
                self.instance.move(ePoint(x, y))
                self.instance.resize(eSize(w, h))
                debug("OSD repositioned to (%d,%d) size %dx%d on desktop %dx%d"
                      % (x, y, w, h, dw, dh))
            except Exception as exc:
                error("OSD reposition: %r" % exc)

        def flash(self, attr, result, delta_ms):
            arrow = "+" if attr == "zapUp" else ("-" if attr == "zapDown" else "*")
            self["text"].setText("%s %d ms  %s" % (arrow, int(round(delta_ms)), result))
            self._apply_colour(_bucket_colour(result, delta_ms))
            try:
                if not self._visible:
                    self.show()
                    self._visible = True
                    info("OSD shown (%d ms %s)" % (int(round(delta_ms)), result))
            except Exception as exc:
                error("OSD show(): %r" % exc)
            self._timer.stop()
            self._timer.start(1500, True)

        def _apply_colour(self, hex_str):
            try:
                h = hex_str.lstrip("#")
                col = gRGB(int(h, 16))
                self["text"].instance.setForegroundColor(col)
            except Exception as exc:
                debug("set colour: %r" % exc)

        def _auto_hide(self):
            if self._visible:
                try:
                    self.hide()
                except Exception as exc:
                    debug("OSD hide: %r" % exc)
                self._visible = False

        def hard_close(self):
            self._timer.stop()
            self._auto_hide()
            try:
                self.session.deleteDialog(self)
            except Exception:
                pass

    try:
        osd = session.instantiateDialog(_ZapTimingOSD)
        return osd
    except Exception as exc:
        error("instantiateDialog failed: %r" % exc)
        return None


def _bucket_colour(result, delta_ms):
    if result == "HIT":
        if delta_ms < 200:
            return "#00ff00"   # green
        if delta_ms < 500:
            return "#ffff00"   # yellow
        return "#ff8000"       # orange
    if result == "NEAR":
        # Teal: intra-TP channel-share. Different service than any
        # armed slot but the demod is already locked on the right
        # transponder via a sibling slot. Latency profile sits
        # between HIT and EXT (~100-300 ms typical). One colour
        # regardless of delta_ms - NEAR is by construction always
        # the fast intra-TP regime.
        return "#80ffc0"
    if result == "MISS":
        if delta_ms < 800:
            return "#ff8000"   # orange
        return "#ff4040"       # red
    if result == "EXT":
        # Neutral cyan: bypass zap (history selector, EPG select,
        # NumberZap, FCC-Extender api path) where the pool did NOT
        # hold the target ref AND no slot is locked on the right
        # transponder - so channel-share could not deliver the
        # speedup. The timing is still surfaced for comparison.
        # Bypass zaps that DID hit a pool slot are labelled HIT
        # above; intra-TP-shared bypass zaps are labelled NEAR.
        return "#80c0ff"
    return "#cccccc"
