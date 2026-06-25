from Components.config import (
    config,
    ConfigSubsection,
    ConfigYesNo,
    ConfigInteger,
    ConfigSelection,
)
from . import _

PLUGIN_NAME = "FBCChannelSpeedChange"
LOG_PATH = "/tmp/fbc_csc.log"


def _indicator_choices_and_default():
    """Build (choices, default) for pretune_indicator_style.

    Filters the dropdown to entries the running enigma2 actually
    exposes via pNavigation. Missing constants vanish from the
    choices list rather than appearing as broken options; the
    setup.xml description mentions which build is required for the
    full set.

    Default: "pseudo" (light blue indicator) when available,
    otherwise "recorded" (the pre-v0.6.0 status quo - same red
    highlight as before this option existed).
    """
    try:
        from enigma import pNavigation
    except ImportError:
        return [("recorded", _("Red (treated as recording)"))], "recorded"

    has_pseudo = hasattr(pNavigation, "isPseudoRecording")
    has_fastzap = hasattr(pNavigation, "isFromSpecialJumpFastZap")
    choices = []
    if has_pseudo:
        choices.append(("pseudo", _("Light blue (pseudo recording)")))
    if has_fastzap:
        choices.append(("hidden", _("Hidden")))
    choices.append(("recorded", _("Red (treated as recording)")))
    return choices, ("pseudo" if has_pseudo else "recorded")


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
    # ReleaseSingleChannel); default True so a paired companion
    # plugin (FCC-Extender) just works out of the box. Without such
    # a companion installed no caller fires the API, so an "on"
    # default is a no-op for the typical user. The Pay-TV side
    # (prewarm_descrambler_external) still defaults False, so the
    # EXTERNAL slot locks the transponder only - no CA load.
    # external_slot_ttl_min is the safety-net TTL applied when the
    # caller forgets to send a release - 5 min is long enough that
    # legitimate EPG-read sessions never get torn down mid-read.
    # prewarm_descrambler_external carries the Pay-TV behaviour for
    # the EXTERNAL slot and follows the same per-direction default-
    # off pattern as the other three.
    cfg.accept_external_pretune = ConfigYesNo(default=True)
    # Defensive ceiling against a buggy or malicious caller that
    # would otherwise thrash the recordable allocation path with
    # rapid rotating service references. 10 distinct refs / second
    # comfortably covers normal channel-list scroll + NumberZap
    # typing patterns; anything above is dropped. Not exposed in
    # setup.xml - power-user editable via /etc/enigma2/settings
    # only.
    cfg.external_max_calls_per_sec = ConfigInteger(
        default=10, limits=(1, 100))
    # TTL is exposed in minutes so the Setup screen shows a human
    # number; the controller multiplies by 60000 before handing it
    # to eTimer. Limits: 1 minute (the lower bound the safety-net
    # still makes sense at) up to 30 minutes (anything longer
    # leaks demods for too long).
    cfg.external_slot_ttl_min = ConfigInteger(
        default=5, limits=(1, 30))
    cfg.prewarm_descrambler_external = ConfigYesNo(default=False)

    # Tiny on-screen overlay after every zap, showing the measured
    # latency in ms with a colour cue (green / yellow / orange / red).
    # Off by default to avoid extra widgets stacked on top of the
    # InfoBar.
    cfg.show_osd_timing = ConfigYesNo(default=False)

    # Pretune slots are technically iRecordableService instances, so
    # the channel-list painter colours them like a recording. This
    # toggle picks which RecordType flag the pool tags them with:
    #   - pseudo:   colorServicePseudoRecorded (light blue) - visible
    #               but distinct from a real recording (default)
    #   - hidden:   isFromSpecialJumpFastZap, no painter mask matches,
    #               no indicator at all (requires enigma2 >= 7.4)
    #   - recorded: isUnknownRecording, current red highlight - the
    #               pre-v0.6.0 behaviour, kept for users who want it
    # Choices missing from the running enigma2 are filtered out at
    # ConfigSelection construction; see _indicator_choices_and_default.
    _indicator_choices, _indicator_default = _indicator_choices_and_default()
    cfg.pretune_indicator_style = ConfigSelection(
        default=_indicator_default, choices=_indicator_choices)

    cfg.debug_log = ConfigYesNo(default=False)

    return cfg


cfg = _initialize()


def total_slots():
    return (int(bool(cfg.pretune_next.value))
            + int(bool(cfg.pretune_prev.value))
            + int(bool(cfg.pretune_history.value)))
