# FBC-ChannelSpeedChange — Public API

Reference for companion plugins that want to feed a service
reference into the FBC-CSC pre-tune pool. Designed for and
verified against the FCC-Extender (OpenATV port in progress);
the surface is generic enough that any plugin can use it.

The module path is stable across the v0.5.x line:

```python
from Plugins.Extensions.FBCChannelSpeedChange.api import (
    PreTuneSingleChannel,
    ReleaseSingleChannel,
)
```

Both entry points return `None`. Failures are caught and
logged inside the api module so callers do not need to wrap
calls in their own `try`/`except`.

---

## Entry points

### `PreTuneSingleChannel(service_ref)`

Arm or refresh the EXTERNAL pool slot with `service_ref`.

**Behaviour:**
- The slot is allocated against the supplied service reference
  via `iRecordableService.recordService` → `prepare` → `start`.
- If the same reference is already armed in NEXT / PREV /
  HISTORY (the internal predictor's slots) the call is a
  no-op; the eventual zap is satisfied by
  eDVBResourceManager's channel-share on the existing slot.
- If the same reference is already armed in the EXTERNAL slot
  the call refreshes the slot's TTL but does not re-allocate.
- A different reference overwrites the EXTERNAL slot — the
  previous recordable is torn down before the new one is
  allocated.

**Argument:**
- `service_ref` — an `eServiceReference`-shaped object. The
  check is duck-typed: any object exposing `.toString()` is
  accepted. `None`, primitives, and objects without
  `.toString()` are silently dropped.

**Returns:** `None`.

### `ReleaseSingleChannel(service_ref=None)`

Release the EXTERNAL pool slot.

**Behaviour:**
- **With `service_ref`:** the slot is released only if it
  currently holds that exact reference. Race-safe against a
  late close event that lands after a newer
  `PreTuneSingleChannel` already overwrote the slot — the late
  release does not accidentally drop the newer ref.
- **Without `service_ref` (`None`):** the slot is released
  unconditionally. Use this when the caller does not track
  which ref it last sent.

**Argument:**
- `service_ref` (optional) — `eServiceReference`-shaped or
  `None`. Other types are silently dropped.

**Returns:** `None`.

---

## Gates

Calls are silent no-ops when any of the following is false:

| Gate | Source | Default | Note |
|---|---|---|---|
| Master safety | `cfg.allow_pretune` | True | Off blocks every tuner reservation in the plugin, not just the EXTERNAL slot. |
| External feature | `cfg.accept_external_pretune` | True | The api-module-specific gate. Off silences the public api without disturbing the internal NEXT/PREV/HISTORY paths. |
| Controller alive | `Controller.peek()` | — | During early boot or after a watchdog self-disable the controller is `None` and calls fall through. |
| Input validation | `_is_serviceref` | — | Non-`eServiceReference` input never reaches the controller layer. |

A companion plugin does NOT need to check these gates itself.
The api module short-circuits and the call returns immediately
when a gate is closed.

---

## Idempotency and convergence

Three rules collapse via a single `pool.lookup` call inside
the controller:

- Ref already armed in NEXT, PREV or HISTORY → no-op
- Ref already armed in EXTERNAL → no-op
- Any other ref → overwrite the EXTERNAL slot

A companion plugin can therefore call `PreTuneSingleChannel`
liberally — for instance on every cursor-move event — without
worrying about churn. The hot-path cost of a repeated call
with the same ref is constant: one rate-limiter lookup plus
one debug-log line (gated on `cfg.debug_log`).

The TTL is refreshed on every call regardless of the verdict.
An idle companion plugin can keep the slot alive by
re-asserting periodically without triggering a re-allocation
cycle.

---

## Rate limiting

A defensive ceiling protects against a buggy or hostile caller
that would otherwise thrash the recordable allocation path.

| Rule | Value | Configurable |
|---|---|---|
| Same-ref debounce | 100 ms | hardcoded |
| Distinct-ref burst cap | 10 / sec | `cfg.external_max_calls_per_sec` (1..100, not in setup.xml) |
| Throttle warn rate | 1 / sec | hardcoded |

**Verdicts:**

| Verdict | Trigger | Log level | Pool effect |
|---|---|---|---|
| `allow` | Normal case | info on first arm | `pool.arm` called |
| `idempotent` | Same ref within 100 ms of previous | debug | none |
| `throttled` | Burst cap exceeded, ref not yet in window | warn (1/sec) | none |

The release path is intentionally unrestricted. Throttling a
release call would risk leaking a slot.

---

## Lifecycle

Three release paths run side by side, in descending priority:

1. **Explicit release from the companion plugin.**
   `ReleaseSingleChannel(ref)` on every close-without-OK is
   the primary path. With `ref` it is race-safe.
2. **`evNewProgramInfo` listener.** When the live service
   changes to the EXTERNAL slot's ref, the slot is released
   automatically. Covers the case where the eventual zap
   bypasses FBC-CSC's `ZapInterceptor` (e.g.
   `session.nav.playService` from outside `ChannelSelection`).
3. **TTL safety net.** `cfg.external_slot_ttl_min`, default
   5 min. Catches the case where the explicit release never
   lands (companion plugin crashed, plugin disabled
   mid-flight, future caller bug). Long enough that legitimate
   EPG-reading sessions never get torn down mid-read.

A double release (explicit call landing after the
`evNewProgramInfo` listener already cleaned up) is a no-op —
the companion does not have to track whether OK was pressed.

---

## Pay-TV behaviour

The EXTERNAL slot follows the same per-direction opt-in
pattern as the internal NEXT/PREV/HISTORY:
`cfg.prewarm_descrambler_external` is off by default, so the
EXTERNAL slot's `prepare()` call passes `descramble=False`.
The CA path stays quiet; channel-share at swap-in still
works. Opt-in adds one continuous ECM stream while the slot
is armed.

For the typical companion plugin (NumberZap-driven, cursor-
driven, EPG-driven), leave this off. The user-visible cost is
a ~400 ms black frame on the first ECM round-trip after the
swap; far less than the ECM-rate load of a sustained pre-warm
stream during the entire pretune window.

---

## Error handling

| Failure | Caller-visible | Plugin-side |
|---|---|---|
| Bad input (`None`, non-`eServiceReference`) | silent no-op | nothing logged |
| Controller not yet started | silent no-op | nothing logged |
| Gates off | silent no-op | nothing logged |
| Controller raises | silent no-op | error line + full traceback in log |
| Pool allocation fails | silent no-op | error line via the pool's existing path |

Crucially, **external-caller-induced failures do NOT
increment the FBC-CSC watchdog counter**. The 3-failure
self-disable mechanism protects FBC-CSC against its own
internal bugs; a misbehaving companion plugin cannot kill
FBC-CSC through its public API.

---

## Diagnostics

With `cfg.debug_log = True`, every public-api call emits a
caller-frame debug line:

```
[debug] PreTuneSingleChannel(1:0:19:283D:3FB:1:C00000:0:0:0:)
        called from /usr/lib/enigma2/python/Plugins/Extensions/FCCExtender/plugin.py:234
```

This identifies which plugin made the call — useful for
forensic triage when multiple plugins coexist.

A 60-second heartbeat emits a summary of EXTERNAL slot
activity at info level, but only when there has been any
activity in the last minute:

```
[info] external stats (60s): 47 calls (40 armed, 5 idempotent,
       0 convergence-skip, 2 throttled, 0 errors), TTL refreshes
       47, releases 3 (1 explicit, 2 evNewProgramInfo, 0 TTL)
```

Forum bug-reports that include the full log give a forensic
trail end-to-end: which plugin called, with which ref, what
the controller decided, what the pool did.

---

## Quick start

```python
from enigma import eServiceReference
from Plugins.Extensions.FBCChannelSpeedChange.api import (
    PreTuneSingleChannel,
    ReleaseSingleChannel,
)


def on_channel_list_cursor_move(new_ref_string):
    """Called by your plugin on every cursor movement in the
    channel list, EPG, NumberZap UI, etc.
    """
    ref = eServiceReference(new_ref_string)
    PreTuneSingleChannel(ref)


def on_channel_list_close_without_ok(last_ref_string):
    """Called when the user dismisses the channel-list / EPG
    overlay without committing the zap.
    """
    ref = eServiceReference(last_ref_string)
    ReleaseSingleChannel(ref)


def on_plugin_shutdown():
    """Belt-and-suspenders cleanup on plugin teardown."""
    ReleaseSingleChannel()  # release whatever is held
```

That is the entire surface. No initialisation, no singleton
to fetch, no callback to register.

---

## Compatibility note

On VU+ boxes the OpenATV `FastChannelChange` system plugin is
the native fast-zap path. The FCC-Extender routes to FCC there
without going through this API; FBC-CSC is typically not
needed alongside FCC on the same box. On every other FBC box
(GigaBlue, Octagon, etc.) FBC-CSC is the only available
backend.

The FCC system plugin auto-detects
`max_fcc = len(glob('/dev/fcc?'))` at startup and registers no
hooks when zero. A companion plugin can probe the same path
to decide which backend to route to.
