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

from .logger import error
from .config import cfg


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

    Silent no-op when:
      * the master switch ``cfg.allow_pretune`` is off
      * the external-pretune gate ``cfg.accept_external_pretune``
        is off (default)
      * the controller has not yet started (early boot)

    The controller decides whether to allocate, refresh or skip
    based on the idempotency rules - this entry point just routes.
    Returns ``None``.
    """
    if not _gate_open():
        return
    c = _controller_provider()
    if c is None:
        return
    try:
        c.pretune_external(service_ref)
    except Exception as exc:
        error("PreTuneSingleChannel crashed (caught): %r" % exc)


def ReleaseSingleChannel(service_ref=None):
    """Release the EXTERNAL pool slot.

    With ``service_ref``: release only if the slot currently
    holds that exact reference. Race-safe against a late close
    event landing after a newer ``PreTuneSingleChannel`` already
    overwrote the slot - the late release does not accidentally
    drop the newer ref.

    Without ``service_ref``: release whatever is in the slot
    unconditionally. Use this when the caller does not track
    which ref it last sent.

    Silent no-op when the gates are off or the controller has
    not started. Returns ``None``.
    """
    if not _gate_open():
        return
    c = _controller_provider()
    if c is None:
        return
    try:
        c.release_external(service_ref)
    except Exception as exc:
        error("ReleaseSingleChannel crashed (caught): %r" % exc)


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
