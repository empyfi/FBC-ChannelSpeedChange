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
4. Co-exist safely with any decryption stack — pre-tune locks
   the transponder but the CA descrambler stays disengaged by
   default, so cardsharing accounts, single-decode CAMs and
   CI+ modules see no parallel load above the live consumer's
   baseline. Per-direction opt-in flags let users with extra
   decoder capacity reclaim the full HD+ HIT speedup.

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

The pool calls the canonical 9-argument signature

```
prepare(filename, begin, end, eit_event_id,
        name, description, tags, descramble, record_ecm)
```

verified against `openatv/enigma2` branch `7.6`
`lib/python/RecordTimer.py` line 1547 (`RecordTimerEntry.prepare`).
`begin`, `end`, `eit_event_id` are zero; `name`, `description`,
`tags` are empty strings; `record_ecm` is `False`. The `descramble`
argument is the per-direction flag described in the next section.

## Descrambler behaviour and pay-TV channels

For free-to-air services the descrambler is irrelevant: there is
no scrambled stream to decode and `descramble` is a no-op. For
scrambled services (HD+, Sky, ORF, BISS-coded feeds, …) the
default `descramble=False` keeps the CA path completely quiet
while the slot is in TUNING / LOCKED. Empirical consequence on an
OSCam-dvbapi setup with three pretune slots armed against
scrambled neighbours: no extra OSCam clients, no extra ECM
traffic, no card load above the live consumer's baseline.

At swap-in (`pool.swap_in` -> `nav.playService(ref)` for
zapUp/zapDown, or the standard zap path for historyBack/
historyNext/EXT) the live consumer attaches to the recordable's
locked channel via `eDVBResourceManager` channel-sharing. The
descrambler initialises **on the live consumer** at that moment,
exactly as it would for a cold zap to a scrambled service. The
tuner-lock saving is preserved; the ~400 ms one-ECM-round-trip
descrambler-init cost is not. User-visible effect: a brief black
frame on a scrambled HIT between the moment the tuner lock
completes and the moment the first descrambled frame reaches the
decoder.

```
   phase         A) descramble=False  (default)        B) descramble=True  (opt-in)
   --------      ------------------------------        ----------------------------

   ARM           prepare(descramble=False)             prepare(descramble=True)
                 tuner locks transponder               tuner locks transponder
                 no ECM, no card load                  ECM RTT starts immediately;
                                                       first CW after ~400 ms
                 slot idle (no CA traffic)             steady CW stream while armed

   ---- user zaps -------------------------------------------------------------------

   SWAP-IN       nav.playService(target)               nav.playService(target)
                 live attaches via eDVBRM share        live attaches via eDVBRM share

   DESCRAMBLE    initialises NOW on live attach;       already running on the slot;
                 one ECM RTT (~400 ms)                 live picks up active context

   FIRST FRAME   ~400 ms after tune lock               immediately after tune lock
                 ===> brief black frame                ===> no black frame
```

### Per-direction opt-in to v0.3.7-style pre-warm behaviour

Three independent config flags reverse the default for the
matching role:

* `cfg.prewarm_descrambler_history` (default off)
* `cfg.prewarm_descrambler_next`    (default off)
* `cfg.prewarm_descrambler_prev`    (default off)

`_kick_real_tune` reads the flag for `slot.role` via
`_direction_descramble()` and passes it as the 8th positional to
`prepare()`. When True for a given role, that role's pretune
engages the descrambler immediately: an OSCam dvbapi client
appears for the service, ECMs flow, and the swap-in shows no
black frame because the descrambler is already streaming
control words.

The three slots are mechanically symmetric. Each slot:

* is re-armed by the controller after every successful zap
  (the predictor is re-queried with the new live service, the
  pool tears down any stale recordable, and `_kick_real_tune`
  starts a fresh recordable for the new target);
* when its toggle is on, keeps one extra descrambler session
  running in addition to the live one, for as long as the slot
  stays on its target — even when the user is sitting on a
  channel and not zapping;
* when re-armed to a new target, produces at most one fresh
  descrambler-init ECM round-trip for that new target.

So with all three toggles on, parallel-decode count peaks at
3 + 1 (live) and the ECM rate is the sum of up to four continuous
sessions; during a steady linear bouquet walk the convergence
skip drops HISTORY, so the walking case settles at 2 + 1 / three
continuous sessions. With one toggle on, it is 1 + 1 regardless
of zap pattern. There is no per-zap penalty that distinguishes
HISTORY from NEXT or PREV; the only asymmetry is *which service*
each slot ends up holding.

Each direction tracks a specific user action:

* **HISTORY** = `predictor.history_service()` — the most recent
  non-live entry of `InfoBar.servicelist.history` (i.e. the
  channel the user just zapped away from). HITs the last-channel
  button and the top entry of the history selector.
* **NEXT** = `predictor.next_service()` →
  `bouquet[live_idx + 1]`. HITs Channel ↑.
* **PREV** = `predictor.prev_service()` →
  `bouquet[live_idx - 1]`. HITs Channel ↓.

In a pure linear bouquet walk (mostly Channel ↑ or ↓) the
matching directional slot HITs every step. In that pattern the
HISTORY target converges on the same service as the opposite-
direction slot (both end up on the just-departed channel). The
controller detects the convergence at re-arm time and drops the
redundant HISTORY arm — the surviving slot answers any later
recall via the pool's role-independent lookup, so no recall MISS
is introduced and the dvbapi side carries only one demuxer
subscription for that service instead of two. Demods would be
shared at `eDVBResourceManager` level either way; the saving is on
the CA side, where it matters for cardsharing setups and
single-decode CAMs.

In a recall-heavy pattern (last-channel button between a small
set of favourites) HISTORY HITs every recall; NEXT/PREV hold
bouquet neighbours of whichever channel happens to be live and
rarely HIT.

Anti-share heuristics that track service diversity over a long
window therefore see HISTORY's target set as small (constrained
by the user's recall pattern) and NEXT/PREV's target set as
wider (whatever the bouquet positions around live happen to be
as live moves). Anti-share heuristics that only track ECM rate
or concurrent session count see no difference between the three
directions.

### Provider coverage

All empirical numbers in this section (ECM rates, ~400 ms
black-frame duration, the three-parallel-decode observation)
were measured against a single test bench: an HD+ subscription
smartcard (CAID 1843, Nagravision Aladin) in the GigaBlue UHD
Quad 4K Pro's internal sci0 reader, descrambled by OSCam-smod
through enigma2's dvbapi link.

The `descramble=False` mechanic itself lives in
`eDVBServiceRecord` ahead of any CA system and is therefore
provider-agnostic. Sky DE / UK / IT (Videoguard, NagraMA), ORF
(Cryptoworks, Irdeto), M7 Group, Vodafone GigaTV, freenet TV /
Diveo via CI+ CAM and similar should exercise the same code path
identically. The specific numbers will not all transfer:

* Black-frame duration is the first-ECM round-trip for the
  specific CA system plus any reader pairing check. Roughly
  200–700 ms across common consumer pay-TV stacks.
* Parallel-decode capacity (relevant when any
  `prewarm_descrambler_*` flag is on) is a hardware property of
  the card / CAM / sharing server. Most consumer cards and CI+
  CAMs handle one or two sessions; cardsharing anti-share
  thresholds add provider-specific service-rate / diversity
  limits on top.

NCam (OSCam-derived), CCcam, mgcamd and mainline OSCam may also
handle the enigma2-restart dvbapi-desync described below
differently from OSCam-smod.

### OSCam dvbapi handshake after enigma2 restart

Observed on OSCam-smod rsvn11726 with a minimal `oscam.conf`
`[dvbapi]` section (pmt_mode=6, request_mode=1): after enigma2
restarts (e.g. from `init 4 && init 3`, or an opkg install of a
new plugin version), the dvbapi socket between enigma2 and OSCam
can desynchronise. The OSCam log fills with `Error: network
packet malformed! (no start)` and `Unknown socket command
received: 0x...`. ECMs stop flowing, the live pay-TV picture goes
black, and the OSCam status page shows the reader as CARDOK and
the dvbapi client as OK while `total_ecm_min` stays at zero.

Restarting the softcam manager once
(`/etc/init.d/softcam stop && /etc/init.d/softcam start`)
clears the state and dvbapi reconnects cleanly. The plugin does
not touch the softcam directly; this is a known OSCam-side
state-confusion mode that is independent of the plugin and would
reproduce after any enigma2 restart on the affected configs.
Worth documenting alongside the v0.4.0 release notes so users
who only see the black picture do not blame the new plugin
version.

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

`cfg.prewarm_descrambler_{history,next,prev}` (default off/off/off) —
per-direction opt-in to the v0.3.7-style pre-warmed descrambler
path. See "Descrambler behaviour and pay-TV channels" for the
trade-off and the recommended HISTORY-only configuration.

## Fast-path vs pass-through

The interceptor splits behaviour by hooked method:

* `zapUp` / `zapDown` take the **fast bypass**:
  pool.swap_in -> nav.playService(target) directly, skipping the
  original method. After the play, the interceptor manually
  replays `servicelist.setCurrentSelection` and
  `servicelist.addToHistory` so the channel-list cursor and
  history list stay consistent. The original method's listbox
  refresh is skipped to keep zap perception snappy.

  ```
  user presses Channel ↑
       |
       v
  +-- InfoBar.zapDown  (wrapped by Interceptor;
  |   original body NOT executed: no listbox refresh)
  |
  +-- Interceptor runs the bypass:
  |     1. ref = predictor.next_service()
  |     2. pool.swap_in(ref)
  |          -> slot already LOCKED; eDVBRM reuses the tuner
  |     3. nav.playService(ref)
  |          -> live consumer attaches to the shared channel
  |     4. servicelist.setCurrentSelection(ref)
  |     5. servicelist.addToHistory(ref)
  |
  v
  zap complete
  ```

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
  OSD card is tinted cyan. Unlike `historyBack` / `historyNext`
  — which reliably HIT the HISTORY pool slot because that slot
  is always armed against the last-watched channel — an
  external zap only gets the channel-share speedup if the
  user-picked target happens to coincide with one of the three
  armed pool slots. For arbitrary numeric input that match is
  rare, so most numeric zaps land as cold tunes.

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

```
              zap completes (HIT / MISS / EXT)
                         |
                         v
           Controller's evTunedIn handler fires
                         |
                         v
              schedule 250 ms re-arm eTimer
                         |
                         v   (250 ms later; live tune has settled)
              re-arm sweep — three independent slots:

  +-------------------+   +-------------------+   +-------------------+
  |    NEXT slot      |   |    PREV slot      |   |   HISTORY slot    |
  +-------------------+   +-------------------+   +-------------------+
  | predictor.        |   | predictor.        |   | predictor.        |
  |  next_service()   |   |  prev_service()   |   | history_service() |
  |                   |   |                   |   |                   |
  | pool teardown     |   | pool teardown     |   | pool teardown     |
  | if target moved   |   | if target moved   |   | if target moved   |
  |                   |   |                   |   |                   |
  | _kick_real_tune   |   | _kick_real_tune   |   | _kick_real_tune   |
  +---------+---------+   +---------+---------+   +---------+---------+
            |                       |                       |
            +-----------------------+-----------------------+
                                    |
                                    v
                each slot runs the same inner state machine
                (descramble flag = cfg.prewarm_descrambler_<role>):

                       prepare(9-arg) -> start()
                                |
                                v
                        SlotState.TUNING
                                |
                                v   (1500 ms eTimer; no real lock event)
                        SlotState.LOCKED
```

The HISTORY arm is dropped before the per-slot inner state machine
runs whenever its target matches the NEXT or PREV target — a state
that occurs every step of a linear bouquet walk (the just-departed
channel is what HISTORY would have armed, and it is also what PREV
or NEXT now points at). See the convergence note in "Descrambler
behaviour and pay-TV channels" above for the trade-off this avoids.

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
columns `epoch,attr,result,delta_ms,target_ref`. `target_ref`
holds the currently-playing service reference at `evTunedIn` so
off-box analysis can classify FTA vs scrambled by cross-
referencing `/etc/enigma2/lamedb5` without needing a live debug
session. The CSV survives plugin restarts so multiple sessions
can be collected and averaged.

`_ensure_csv_header` is idempotent and includes a one-shot
migration for installs upgrading from a pre-0.4.0 CSV (legacy
4-column header `epoch,attr,result,delta_ms`): on first run the
header is rewritten in place to the new 5-column shape and
legacy rows are padded with an empty trailing field so column
counts stay consistent across the whole file. `tools/zap_stats.py`
summarises the CSV with min / median / mean / max per
(attr, result).

The `evTunedIn` anchor that drives the CSV measures up to demux
lock, **not** up to the first descrambled frame. On scrambled
HIT zaps with `prewarm_descrambler_*` off the CSV's `delta_ms`
therefore understates the wall-clock zap by one ECM round-trip
(~400 ms). The understatement does not affect the FTA columns
or the cross-config comparison; it is a property of the anchor
itself.

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
