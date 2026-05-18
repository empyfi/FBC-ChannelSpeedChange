from Components.config import (
    config,
    ConfigSubsection,
    ConfigYesNo,
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
