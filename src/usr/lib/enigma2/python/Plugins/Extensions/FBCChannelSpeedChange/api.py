"""Public API surface for external pretune callers.

Two entry points - the design rationale, integration scenarios
and idempotency rules live in ``notes/backlog.md`` under
"Candidate for v0.5.0".

Intended consumers:
  * Oberhesse's FCC-Extender (OpenATV port in progress)
  * Future native channel-list hover hook
  * Any third-party plugin that wants to feed a service
    reference into the FBC-CSC pre-tune pool

Public surface:

    from Plugins.Extensions.FBCChannelSpeedChange.api import (
        PreTuneSingleChannel, ReleaseSingleChannel,
    )

    PreTuneSingleChannel(service_ref)        # arm or refresh
    ReleaseSingleChannel(service_ref)        # release if slot holds ref
    ReleaseSingleChannel()                   # release whatever is in the slot

Both functions return ``None``. Failures are caught and logged
- callers do not need to wrap calls in try/except.

Internal note: every entry point goes through two gates before
touching the controller. The master switch ``cfg.allow_pretune``
is the same gate the internal NEXT / PREV / HISTORY allocations
respect; ``cfg.accept_external_pretune`` is the per-feature gate
specific to the public API. With either gate off, calls are
silent no-ops - matching the FCC system plugin's behaviour when
``FccInstance`` is disabled.

The convergence / idempotency rules (skip if the ref is already
armed in NEXT / PREV / HISTORY, no-op on a repeat with the same
ref, overwrite on a different ref) are enforced inside the
controller, not here. This module stays a thin pass-through so
the public contract is observable and testable in isolation.
"""

import re

from .logger import debug, error
from .config import cfg


# Whitelist for the canonical DVB broadcast serviceref shape, e.g.
# "1:0:1:6DCA:44D:1:C00000:0:0:0:" (HD+ Disney Channel HD). Format:
# <type>:<flags>:<stype>:<sid>:<tsid>:<onid>:<ns>:<parent_sid>:<parent_tsid>:<unused>:[<path>:[<name>]]
# Only "1:0:" (DVB broadcast service) is accepted. Bouquets ("1:7:"),
# IPTV ("4097:"), markers, file-backed playback refs and refs carrying a
# non-hex trailing path are rejected before any SWIG constructor sees
# them, so a malformed caller string cannot reach the C++ parser.
_SREF_SHAPE = re.compile(
    r"^1:0:[0-9a-fA-F]+:[0-9a-fA-F]+:[0-9a-fA-F]+:[0-9a-fA-F]+:[0-9a-fA-F]+:[0-9a-fA-F:]*$"
)
_SREF_MAX_LEN = 512


def _default_controller_provider():
    """Return the live Controller instance or None.

    Imported lazily so the api module itself can be imported off-box
    (the controller drags in the full enigma stack at module load).
    """
    try:
        from .controller import Controller
        return Controller.peek()
    except Exception:
        return None


# Module-level provider so tests can inject a fake without
# monkey-patching the Controller class. Production callers leave
# this alone.
_controller_provider = _default_controller_provider


def PreTuneSingleChannel(service_ref):
    """Arm or refresh the EXTERNAL pool slot with ``service_ref``.

    Accepts either an ``eServiceReference`` instance or a string
    matching the canonical DVB broadcast serviceref shape
    (``"1:0:<stype>:<sid>:<tsid>:<onid>:<ns>:..."``). Strings are
    converted via the SWIG constructor only after the shape
    whitelist matches, so a malformed caller string cannot reach
    the C++ parser.

    Silent no-op when:
      * the master switch ``cfg.allow_pretune`` is off
      * the external-pretune gate ``cfg.accept_external_pretune``
        is off
      * the controller has not yet started (early boot)
      * ``service_ref`` is ``None``, an unrecognised object, or a
        string that does not match the DVB broadcast shape

    The controller decides whether to allocate, refresh, throttle
    or skip based on the idempotency rules and the rate limiter -
    this entry point just routes. Returns ``None``.
    """
    coerced = _coerce_to_serviceref(service_ref)
    if coerced is None:
        return
    if not _gate_open():
        return
    c = _controller_provider()
    if c is None:
        return
    _log_caller("PreTuneSingleChannel", coerced)
    try:
        c.pretune_external(coerced)
    except Exception as exc:
        error("PreTuneSingleChannel crashed (caught): %r" % exc)


def ReleaseSingleChannel(service_ref=None):
    """Release the EXTERNAL pool slot.

    With ``service_ref``: release only if the slot currently
    holds that exact reference. Race-safe against a late close
    event landing after a newer ``PreTuneSingleChannel`` already
    overwrote the slot - the late release does not accidentally
    drop the newer ref. Accepts the same input forms as
    ``PreTuneSingleChannel``.

    Without ``service_ref``: release whatever is in the slot
    unconditionally. Use this when the caller does not track
    which ref it last sent.

    Silent no-op when the gates are off, the controller has not
    started, or ``service_ref`` is non-``None`` but neither an
    ``eServiceReference``-shaped object nor a whitelist-matching
    string. Returns ``None``.
    """
    if service_ref is not None:
        coerced = _coerce_to_serviceref(service_ref)
        if coerced is None:
            return
        service_ref = coerced
    if not _gate_open():
        return
    c = _controller_provider()
    if c is None:
        return
    _log_caller("ReleaseSingleChannel", service_ref)
    try:
        c.release_external(service_ref)
    except Exception as exc:
        error("ReleaseSingleChannel crashed (caught): %r" % exc)


def _coerce_to_serviceref(obj):
    """Return an ``eServiceReference``-shaped object, or ``None``.

    Pass-through for objects already exposing ``toString()`` (the
    established binding contract). Strings are accepted only when
    they match the DVB broadcast whitelist and stay under the
    length cap, and are then handed to the SWIG constructor. Any
    constructor failure collapses to ``None`` so the caller's
    misuse cannot escape into the controller path.
    """
    if obj is None:
        return None
    if hasattr(obj, "toString"):
        return obj
    if not isinstance(obj, str):
        return None
    if len(obj) > _SREF_MAX_LEN:
        return None
    if not _SREF_SHAPE.match(obj):
        return None
    try:
        from enigma import eServiceReference
        return eServiceReference(obj)
    except Exception:
        return None


def _log_caller(entry_point, service_ref):
    """Emit one debug line per public-API call identifying the
    caller's filename and line. Gated on ``cfg.debug_log`` because
    ``inspect.stack`` is expensive enough to want to keep out of
    the hot path on normal runs; the line lets a forum reporter
    answer "which plugin called us" once the debug toggle is on.
    """
    try:
        if not cfg.debug_log.value:
            return
    except Exception:
        return
    try:
        import inspect
        # [0] this fn, [1] PreTune/ReleaseSingleChannel, [2] actual caller.
        frame = inspect.stack()[2]
        ref_str = service_ref.toString() if service_ref is not None else "<None>"
        debug("%s(%s) called from %s:%d" % (
            entry_point, ref_str, frame.filename, frame.lineno))
    except Exception:
        # Caller inspection is best-effort; never propagate.
        pass


def _gate_open():
    """Both gates must be on for the API to do anything.

    ``allow_pretune`` is the master kill-switch the whole plugin
    respects. ``accept_external_pretune`` is the external-API gate
    introduced in v0.5.0, default False so a fresh install does
    nothing unless the user opted in.

    Lookups are wrapped in try/except so the missing-attribute case
    (config key not yet present pre-Phase-4) collapses to "off"
    rather than crashing the caller.
    """
    try:
        if not cfg.allow_pretune.value:
            return False
        if not cfg.accept_external_pretune.value:
            return False
    except Exception:
        return False
    return True
