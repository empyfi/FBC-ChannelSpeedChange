from Components.config import (
    config,
    ConfigSubsection,
    ConfigYesNo,
    ConfigInteger,
)

PLUGIN_NAME = "FBCChannelSpeedChange"
LOG_PATH = "/tmp/fbc_csc.log"


def _initialize():
    config.plugins.fbc_csc = ConfigSubsection()
    cfg = config.plugins.fbc_csc

    cfg.enabled = ConfigYesNo(default=True)

    # Master kill-switch on the actual tuner allocation path. Default
    # True; flip off as a quick safety brake without uninstalling.
    cfg.allow_pretune = ConfigYesNo(default=True)

    # The real pretune path (prepare(tmpfile) + start()) is the only
    # thing that gives a measurable speedup. Default on.
    cfg.use_real_pretune = ConfigYesNo(default=True)

    # Yes/no toggle per direction. On a yes the pool reserves one
    # demodulator for that role and pre-tunes the predicted
    # neighbour service; on a no it reserves none.
    cfg.pretune_next = ConfigYesNo(default=True)
    cfg.pretune_prev = ConfigYesNo(default=True)
    # Pre-tune the most recently watched service so a History Zap
    # press (last-channel button or history-selector pick of the top
    # entry) hits the cached recordable. Default on.
    cfg.pretune_history = ConfigYesNo(default=True)

    cfg.release_for_recording = ConfigYesNo(default=True)
    cfg.release_for_pip = ConfigYesNo(default=True)

    # Per-direction "engage the descrambler during pre-tune" toggles.
    # Default off: the pool calls prepare() with descramble=False, so
    # the CA descrambler / softcam / cardsharing / CI+ CAM path never
    # engages while a scrambled neighbour is held in the pool. The
    # transponder is still locked, channel-sharing still works at
    # swap-in; what changes is that the descrambler initialises after
    # the swap rather than ahead of it, adding ~400 ms (one ECM
    # round-trip) as a visible black frame on pay-TV HIT zaps.
    #
    # Enable per direction to opt back into the pre-warmed path:
    #   - HISTORY: one extra continuous decoder session, slow rotation
    #     (only re-arms when the live channel changes). Lowest impact
    #     - typically the safe opt-in if any decoder budget exists.
    #   - NEXT / PREV: extra sessions that re-arm on every zap. High
    #     ECM-burst profile; recommended only for users with a
    #     verified multi-decode card AND no cardsharing concern.
    cfg.prewarm_descrambler_history = ConfigYesNo(default=False)
    cfg.prewarm_descrambler_next = ConfigYesNo(default=False)
    cfg.prewarm_descrambler_prev = ConfigYesNo(default=False)

    # v0.5.0 external pretune. accept_external_pretune is the master
    # gate for the public api module (PreTuneSingleChannel /
    # ReleaseSingleChannel); default False so a fresh install does
    # nothing unless the user opted in. external_slot_ttl_ms is the
    # safety-net TTL applied when the caller forgets to send a
    # release - 5 min is long enough that legitimate EPG-read
    # sessions never get torn down mid-read. prewarm_descrambler_external
    # carries the Pay-TV behaviour for the EXTERNAL slot and follows
    # the same per-direction default-off pattern as the other three.
    cfg.accept_external_pretune = ConfigYesNo(default=False)
    cfg.external_slot_ttl_ms = ConfigInteger(
        default=300000, limits=(10000, 1800000))
    cfg.prewarm_descrambler_external = ConfigYesNo(default=False)

    # Tiny on-screen overlay after every zap, showing the measured
    # latency in ms with a colour cue (green / yellow / orange / red).
    # Off by default to avoid extra widgets stacked on top of the
    # InfoBar.
    cfg.show_osd_timing = ConfigYesNo(default=False)

    cfg.debug_log = ConfigYesNo(default=False)

    return cfg


cfg = _initialize()


def total_slots():
    return (int(bool(cfg.pretune_next.value))
            + int(bool(cfg.pretune_prev.value))
            + int(bool(cfg.pretune_history.value)))
