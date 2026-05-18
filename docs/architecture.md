# Architecture

This document captures the design at a level intended for readers
modifying the plugin. For end-user behaviour see
[`../README.md`](../README.md) and [`install.md`](install.md).

## Goals (recap)

1. Sub-200 ms zap latency for next / previous / last-watched
   channels — achieved (median 114 – 122 ms in field test).
2. PiP and Recording always win — implemented via the
   ResourceArbiter calling `pool.release_for()` on
   `STATE_PREPARED` and PiP-shown transitions.
3. Never destabilise enigma2 — every hooked method is in a
   try/except envelope, every allocation path is gated by a
   two-tier opt-in (`cfg.enabled` + `cfg.allow_pretune`), and a
   watchdog auto-disables the plugin after three consecutive
   failures.

## Module map

```
plugin.py            entry point (WHERE_SESSIONSTART + WHERE_PLUGINMENU)
  -> controller.py   singleton lifecycle, watchdog, re-arm timer
       +- fbc_pretune_pool.py   FBC demod reservations & state machine
       +- predictor.py          next / prev / history lookups
       +- resource_arbiter.py   PiP / Recording state listener
       +- zap_interceptor.py    InfoBar method wrappers + OSD trigger
       +- osd_timing.py         heads-up latency overlay (opt-in)
  config.py / settings_ui.py    settings + UI
  logger.py                      size-capped /tmp/fbc_csc.log
  diagnostic.py                  one-shot API dump on debug_log=on
```

## Why a pool, not per-zap pre-tune

A naive accelerator would, on every Channel ↑ press, fire off a
pre-tune of the next channel. That doubles outgoing tune events
without helping the current press — the user is already zapping.

A pool keeps demodulators **continuously locked** on the next
predicted targets. When the user finally presses the key the
destination is already locked, so `playService()` hits
`eDVBResourceManager`'s channel-sharing path and reuses the
existing channel rather than re-tuning. That is where the
ms-range win actually lives.

## Slot lifecycle

```
              configure()         arm(target)              channel locks
IDLE  ----------------------->  TUNING  -------------------->  LOCKED
   ^                              |                                |
   |                              | tune fails / lost lock         | swap_in()
   |                              v                                | / release_for()
   +-------- release(keep_object=True) <-----------------------+
```

`SlotState.RELEASED` is only set when a slot is dropped entirely
(pool shrink or shutdown). Re-armable slots cycle IDLE -> TUNING
-> LOCKED -> IDLE.

The TUNING -> LOCKED transition is driven by an optimistic
1500 ms `eTimer`: `iRecordableService` on this build exposes no
event-list attribute, so the real lock signal cannot be
subscribed to. After 1500 ms the slot is assumed locked and HITs
start serving from it. Field testing shows the actual lock
completes well within that window.

## Why recordService (and not eFCCServiceManager)

OpenATV 7.6.0 ships `eFCCServiceManager` in `libenigma` but
never constructs the singleton — `eFCCServiceManager.getInstance()`
returns `None`, and the `setFCCEnable` method is bound as an
instance method, so the singleton cannot be bootstrapped from
Python. The kernel-level FCC infrastructure is reachable in C++
only on this build.

That leaves `NavigationInstance.recordService(ref)` plus
`iRecordableService.prepare(filename)` plus `start()` as the
only working "background tune" primitive from Python. The
throwaway `.ts` file in `/tmp` is the price; periodic
`fallocate(PUNCH_HOLE | KEEP_SIZE)` mitigates it so the
underlying tmpfs stays well under 5 MB regardless of how long
the user idles.

`prepare()` with an empty filename trips an internal C++
assertion inside `eDVBServiceRecord::prepare` that Python's
try/except cannot catch. A real `/tmp/fbc_csc_pretune_<role>_
<timestamp>.ts` path avoids it; the `allow_pretune` master
switch additionally ensures any future crash-class regression
cannot self-replicate across reboots via persisted config.

## Two-tier safety opt-in

`cfg.enabled` (default True) — gates the whole controller. Off
means full hands-off, no method wrapping.

`cfg.allow_pretune` (default True) — gates only the tuner
allocation path. Off means the pool stays empty, no
`recordService` ever fires, but wrappers stay installed and the
OSD/timing infrastructure still works. Useful when diagnosing
whether the plugin is the cause of an issue without
uninstalling.

`cfg.use_real_pretune` (default True) — gates the
`prepare(filename) + start()` calls inside the allocation path.
Off means the pool holds idle recordable handles only (a debug
mode that produces no measurable speedup).

`cfg.pretune_{next,prev,history}` (default yes/yes/yes) — yes/no
toggles for each role.

## Fast-path vs pass-through

The interceptor splits behaviour by hooked method:

* `zapUp` / `zapDown` take the **fast bypass**:
  pool.swap_in -> nav.playService(target) directly, skipping the
  original method. After the play, the interceptor manually
  replays `servicelist.setCurrentSelection` and
  `servicelist.addToHistory` so the channel-list cursor and
  history list stay consistent. The original method's listbox
  refresh is skipped to keep zap perception snappy.

* `historyBack` / `historyNext` take the **pass-through** path:
  pool.confirm_hit() returns the slot reference, the original
  method runs normally (it needs `history_pos` walking, not a
  raw playService), and the slot is released after the original
  returns. The pretuned recordable stays alive during
  original's playService, so `eDVBResourceManager` still
  channel-shares and the speedup is preserved.

* **External zaps** (history-selector dialog, EPG select,
  numeric input) bypass the wrappers entirely. The interceptor
  subscribes to `iPlayableService.evStart` as a fallback timing
  anchor so the OSD overlay still shows a number for those
  zaps; the result is labelled `EXT` in the timing CSV and the
  OSD card is tinted cyan.

## ServiceReference identity normalisation

Both predictor and pool compare service references via `_key()`
which drops the trailing channel name from the reference string.
The name is volatile (the user can rename channels at any time);
everything before it identifies the actual service. Without
normalisation, a renamed channel would create a permanent cache
miss.

## History extraction

`InfoBar.servicelist.history` on this OpenATV build is a list of
nested lists: each entry looks like
`[bouquet_path_ref_1, bouquet_path_ref_2, ..., target_ref]`.
`predictor._extract_ref` walks the structure recursively, in
**reverse order**, so the trailing element (the actual target)
wins over any earlier element (the bouquet path prefix). Plain
`eServiceReference` entries also work because the recursion's
base case returns the ref directly when it has `.toString()`.

## tmpfs RAM reclaim

Each active pretune slot owns a 2 s `eTimer` that calls
`fallocate(fd, PUNCH_HOLE | KEEP_SIZE, 0, current_size)` on the
slot's `.ts` file. tmpfs supports sparse storage on this kernel,
so the underlying RAM pages are released even though the writer
(eDVBServiceRecord) keeps appending. The file's logical size
keeps growing — `ls -la` shows hundreds of MB after a few
minutes — but `df /tmp` reports only the few MB of "live" data
between reclaim cycles.

The `.ap` / `.sc` / `.cuts` / `.meta` / `.eit` sidecar files
that eDVBServiceRecord can emit get the same treatment via the
slot-paths iterator.

## Re-arm strategy

Controller re-arms on a 250 ms one-shot eTimer after every zap
(HIT, MISS, or external evTunedIn). The 250 ms delay is
intentional: it lets the live tune complete first to avoid
competing for resources during the most contended window. After
that the next/prev/last targets line up for the new current
position.

## Why dependency injection everywhere

The off-box dev host (e.g. Windows + plain Python 3) cannot
import enigma2. Every module that touches
`eDVBResourceManager`, `NavigationInstance`, `eServiceCenter`,
`InfoBar`, etc. takes those references through provider
callables that default to the live enigma2 singletons but
accept fakes in tests. As a result `tests/_enigma_stubs.py`
stays tiny — the only import-time stubs needed are
`Components.config` plus a minimal `enigma` module with
`eTimer` and `iPlayableService.{evStart,evTunedIn}`.

## On the red channel-list highlight

Pretuned services show up in red in the channel list because
they are technically active recordings — the only working
pretune primitive on this OpenATV build. Distinguishing them
from real recordings in a different colour would require either:

1. Editing the user's skin (per-user, not portable, breaks on
   skin updates).
2. Monkey-patching `NavigationInstance.getRecordingsServicesOnly`
   from Python — the channel list rendering, however, is in C++
   and likely bypasses the Python wrapper, so the patch would
   have no visible effect (unverified).

In practice the red highlight is informative: it always
reflects a busy demodulator. When a real recording starts, the
ResourceArbiter releases the matching pretune immediately, so
red never simultaneously means both "pretune" and "recording"
for the same channel.

## OSD overlay

`osd_timing.py` is opt-in (`cfg.show_osd_timing`, default off).
One `Screen` instance is created via
`session.instantiateDialog()` so the overlay lives outside the
modal dialog stack and never catches input. The position is
computed at runtime from `getDesktop(0).size()` and pinned to
the top-right corner with a 20 px margin. Successive zaps reuse
the same instance and reset a 1500 ms auto-hide timer — no
window stacking on rapid presses.

Colour buckets: green < 200 ms HIT, yellow < 500 ms HIT, orange
slow HIT or fast MISS, red >= 800 ms MISS, cyan EXT external
zap.

## Timing CSV

Every zap appends one row to `/tmp/fbc_csc_timing.csv` with
columns `epoch,attr,result,delta_ms`. The CSV survives plugin
restarts (header only written if missing) so multiple sessions
of timing data can be collected and averaged. `tools/zap_stats.py`
summarises the CSV with min / median / mean / max per
(attr, result).

## Safety envelope

Every public method called from enigma2 (plugin entry, wrapped
InfoBar methods, signal callbacks, eTimer callbacks) is wrapped
in try/except. On exception:

1. The error is written to `/tmp/fbc_csc.log`.
2. The original behaviour proceeds (e.g. the user's zap still
   happens via the original method or via plain playService).
3. The controller's failure counter increments.

After 3 consecutive failures the controller calls
`_self_disable()` which stops the arbiter, interceptor, and
pool, unwraps every InfoBar method, shows a one-shot
`AddPopup` notification, and refuses to re-enable until enigma2
is restarted (to avoid auto-recovery loops on broken state).
