# FBC-ChannelSpeedChange

An OpenATV / Enigma2 plugin that accelerates channel zapping on receivers
with FBC (Full Band Capture) tuners. Designed and field-tested on the
**GigaBlue UHD Quad 4K Pro**. Measured medians on this hardware:
**Channel ↑/↓ 117–124 ms**, **History/Recall 59 ms** — down from
0.7–1.5 s for the same cross-transponder zaps without the plugin. PiP
and Recording always retain priority on the FBC demodulator pool.

## Why another zap accelerator?

The two existing options on OpenATV both have gaps for the
GigaBlue UHD Quad 4K Pro use case:

**Built-in FCC (`eFCCServiceManager`)** — kernel-level fast
channel change infrastructure. Theoretically the fastest possible
path, but on OpenATV 7.6.0 the singleton is never constructed
(`getInstance()` returns `None`) and the user-facing config
option is not exposed. This build offers no way to bootstrap it
from Python. On builds that DO expose it, FCC only accelerates
the next channel and only within the same band.

**SpecialJump (`openatv/SpecialJump`)** — a Swiss-army-knife
plugin whose primary purpose is ad-skipping in recorded videos
(binary-search jump algorithm). Its "Fast Zap Mode" is one
feature among many. The zap part pre-tunes the **next** channel
on a second tuner — no previous, no history — and does not
distinguish FBC from non-FBC tuners.

This plugin is the opposite trade-off: a **focused zap
accelerator** that does nothing else. It pre-tunes all three
zap directions, refuses to allocate on non-FBC tuners, gives
PiP / Recording absolute priority, and instruments itself
heavily so you can measure what you actually get on your own
hardware.

### Feature list

- Pre-tune NEXT channel in the bouquet
- Pre-tune PREVIOUS channel in the bouquet
- Pre-tune LAST-WATCHED channel (History / Recall zap)
- Per-direction yes/no toggle in the settings UI
- FBC-only allocation — refuses to touch USB or non-FBC slots
- Auto-release of the pre-tune pool when a recording enters
  STATE_PREPARED (before the recorder needs the demod)
- Auto-release of the pre-tune pool when PiP becomes visible
- Two-tier safety opt-in: `allow_pretune` master switch plus
  `use_real_pretune` for the prepare()+start() path
- Crash watchdog: self-disable after three consecutive failures
  with a one-shot user notification
- Preserves `servicelist.history` and the channel-list cursor
  on every HIT (so the standard history navigation, the
  history selector dialog, and the channel-list cursor all
  behave like without the plugin)
- Optional on-screen latency overlay (colour-coded, off by
  default)
- Per-zap timing CSV (`/tmp/fbc_csc_timing.csv`) with
  `tools/zap_stats.py` summariser for objective A/B testing
- tmpfs reclaim every 2 seconds via
  `fallocate(PUNCH_HOLE | KEEP_SIZE)` so the throwaway pre-tune
  `.ts` files do not balloon RAM
- Dependency-injected enigma2 APIs so the codebase can be
  unit-tested off-box (29 tests at the time of writing)

## Measured performance

Field measurement on the GigaBlue UHD Quad 4K Pro running
OpenATV 7.6.0 (`gbquad4kpro`, Astra 19.2°E + 28.2°E + mixed
HD/SD bouquet, v0.3.0 build, all three pretune toggles on):

| Zap path | n | min | **median** | mean | max |
|---|---|---|---|---|---|
| Channel ↑ — plugin HIT via fast bypass | 11 | 85 ms | **117 ms** | 138 ms | 229 ms |
| Channel ↓ — plugin HIT via fast bypass | 17 | 99 ms | **124 ms** | 203 ms | 910 ms |
| History / Recall zap — pretune cache hit | 12 | 42 ms | **59 ms** | 63 ms | 128 ms |
| External zap, no pretune target | 11 | 718 ms | 841 ms | 1198 ms | 4362 ms |
| Wrapper MISS (rare; pool empty during a recording) | 2 | 1646 ms | 1661 ms | 1661 ms | 1676 ms |

**HIT rate for wrapper-bracketed zaps: 28 / 30 = 93 %.** The two
MISSes happened during an active recording when the
ResourceArbiter had released the pool; the plugin transparently
fell back to the standard enigma2 zap.

A few notes on what the numbers say:

* **History zap is the fastest path of all** at 59 ms median —
  faster even than Channel ↑/↓ — because the recall button
  triggers `nav.playService` externally on a transponder where
  the pretune recordable is already locked, so the resource
  manager's channel-sharing kicks in with zero wrapper overhead.
  The pretune for the last-watched service has to be enabled
  (it is, by default) for this to work.
* **Channel ↑/↓ medians around 120 ms** reflect the fast-bypass
  path: predictor → pool.swap_in → playService → manual history
  bookkeeping, all while the pretune recordable holds the target
  transponder. The decoder init dominates the residual latency
  and cannot be shortened from Python on this build.
* **External zaps without a pretune target** (e.g. picking a
  channel in the EPG that is on neither the next nor the
  previous nor the last-watched transponder) measure ~720–
  1200 ms. The plugin contributes nothing here; this is the
  stock OpenATV zap baseline for cross-transponder switches.
* **The handful of outliers inside the HIT bucket** (910 ms for
  Channel ↓, 229 ms for Channel ↑) correspond to HD↔SD or LNB-
  band switches; the pretuned demod was on the right MUX but
  the decoder still had to reinitialise.

To reproduce on your own hardware, see
[`tools/zap_stats.py`](tools/zap_stats.py). Fetch it once on the
box and run after a few zaps:

```sh
wget https://raw.githubusercontent.com/empyfi/FBC-ChannelSpeedChange/main/tools/zap_stats.py -O /tmp/zap_stats.py
python3 /tmp/zap_stats.py
```

## Hardware requirements

- Receiver with at least one FBC tuner (GigaBlue UHD Quad 4K Pro
  recommended; should work on any modern FBC-equipped OpenATV box)
- OpenATV 7.x or newer (Python 3)
- ~50 MB free on `/tmp` (the plugin holds throwaway `.ts` files there;
  a background timer punches holes via `fallocate(PUNCH_HOLE)` every
  two seconds so the underlying tmpfs stays well under 5 MB)

## Install

See [`docs/install.md`](docs/install.md) for the SSH-based install
procedure.

Quick version:

```sh
ssh root@<your-box>
wget https://github.com/empyfi/FBC-ChannelSpeedChange/releases/download/v0.3.4/enigma2-plugin-extensions-fbc-channelspeedchange_0.3.4_all.ipk -O /tmp/fbc.ipk
opkg install /tmp/fbc.ipk
init 4 && sleep 2 && init 3
```

After enigma2 restarts the plugin is on with sane defaults — all three
pretune toggles (next / previous / last-watched) are enabled. Open
**Menu → Plugins → FBC ChannelSpeedChange** to fine-tune.

### Alternative: OpenEmbedded / autotools build

The repository also ships an autotools skeleton
(`configure.ac`, `Makefile.am`, `po/Makefile.am`, `autogen.sh`) so
the plugin can be picked up by an OpenEmbedded recipe and built
into the OpenATV feed directly. From a source checkout:

```sh
./autogen.sh
./configure --prefix=/usr --libdir=/usr/lib
make
make DESTDIR=/path/to/staging install
```

This installs the Python sources under
`$(libdir)/enigma2/python/Plugins/Extensions/FBCChannelSpeedChange`
and the compiled translation catalog at
`.../FBCChannelSpeedChange/locale/<lang>/LC_MESSAGES/FBCChannelSpeedChange.mo`.
The quick-install IPK path above (`build.py` → GitHub release →
`opkg install`) remains the supported way to install on a running
box; autotools is for distribution maintainers.

## Settings

| Key | Default | Notes |
|---|---|---|
| Enable plugin | yes | Master enable. Off means full hands-off, no wrapping. |
| Allow tuner allocation | yes | Belt-and-suspenders kill-switch. Off freezes the pool empty; nothing touches `recordService`. Useful if you suspect the plugin and want to disprove it without uninstalling. |
| Use real pre-tune | yes | When yes, runs the full prepare()+start() path that gives the actual speedup. When no, the plugin holds idle recordable handles only (no real benefit). |
| Pre-tune NEXT channel | yes | Reserves one demodulator that stays locked to the next channel in the bouquet. |
| Pre-tune PREVIOUS channel | yes | Same for the previous bouquet entry. |
| Pre-tune LAST channel (history) | yes | Reserves one demodulator for the most recently watched service, so History Zap (or the top entry of the history selector) becomes instant. |
| Release demods on recording | yes | Pool gives up demodulators the moment a recording enters STATE_PREPARED, ahead of the recorder needing them. |
| Release demods on PiP | yes | Same idea for PiP. |
| Show zap latency OSD | no | When yes, flashes the per-zap latency in milliseconds (colour-coded) in the top-right corner for 1.5 s. Off by default so it never surprises anyone on upgrade. |
| Verbose debug logging | no | Pipes diagnostic dumps and per-zap wrapper noise into `/tmp/fbc_csc.log`. Off in production. |

## How it works

1. The Controller starts on `WHERE_SESSIONSTART`, wires up the four
   submodules (pool, predictor, arbiter, interceptor), and re-arms the
   pool 250 ms after every zap.
2. The pool holds one `iRecordableService` per active role (NEXT,
   PREV, LAST). `recordService` allocates a demodulator;
   `prepare(/tmp/...)` plus `start()` actually tune it. The throwaway
   `.ts` files in `/tmp` are punched out every two seconds via
   `fallocate(PUNCH_HOLE | KEEP_SIZE)` so the underlying tmpfs stays
   well under 5 MB even though the files grow logically.
3. When the user presses Channel ↑ / ↓ the interceptor takes the fast
   bypass: it calls `nav.playService(ref)` directly while the
   pre-tuned recordable is still alive, so `eDVBResourceManager`
   reuses the locked channel instead of re-tuning. Immediately after
   the play it replays `servicelist.setCurrentSelection` and
   `servicelist.addToHistory` so the channel-list cursor and history
   list stay consistent with the regular zap path.
4. For `historyBack` / `historyNext` (and any external zap path like
   the history selector dialog, EPG, or numeric input) the interceptor
   passes through to the original method. `eDVBResourceManager` still
   sees the pretuned recordable on the target transponder so
   channel-sharing kicks in there too.
5. The resource arbiter listens for `RecordTimer.on_state_change` and
   PiP visibility. The moment a recording hits STATE_PREPARED, the
   pool releases all demodulators; once the recording ends, the next
   zap triggers a fresh re-arm using whichever demodulators are now
   free.

Detailed architecture and rationale: [`docs/architecture.md`](docs/architecture.md).

## Behaviour with recordings and PiP

Pre-tune yields to recordings and PiP. Both `STATE_PREPARED`
(recording about to start) and PiP becoming visible empty the
pool immediately, before the new consumer needs its demod.
Example walk-through with one active recording plus PiP plus a
zap (8-demod box):

| Event | Demods used | Pool | Zap result |
|---|---|---|---|
| Steady state, pool armed | 1 live + 3 pretune = 4 | full | — |
| Recording → `STATE_PREPARED` | 1 live + 1 rec = 2 | emptied | — |
| PiP shown | + 1 PiP = 3 | still empty | — |
| **First zap after both started** | live demod re-tunes on the spot, still 3 used | empty → MISS | stock latency (~800 ms) |
| 250 ms after that zap | 3 + 3 new pretune = 6 used, 2 free | refilled | — |
| **Second zap** | channel-shared with pretune | HIT | ~120 ms (~59 ms History) |

The recording and PiP run uninterrupted throughout. Only the
single zap that follows a recording or PiP start pays stock
OpenATV latency; from the next zap onwards the speedup is back,
filling the pool with whichever demodulators remain free.

### When pre-tune demodulators are exhausted

`recordService` returns `None` when no FBC demodulator is
available; the affected slot stays IDLE without error and the
pool fills as many slots as remain free. The fill order is
fixed: **NEXT → PREV → HISTORY**, so under pressure the History
slot is the first to be skipped, then the Previous slot, with
the Next slot kept longest.

Example walk-through with heavy load — 1 live + 4 parallel
recordings + PiP = 6 of 8 demodulators already in active use:

| Event | Demods used | Pool |
|---|---|---|
| Steady state, all priority consumers running | 6 | empty (released on each start) |
| First zap → 250 ms later re-arm | 6 + 2 pretune = 8 used, 0 free | NEXT and PREV filled, HISTORY stays IDLE |
| Channel ↑ / ↓ on neighbour transponder | channel-shared with pretune | HIT, ~120 ms |
| History Zap | live demod re-tunes from scratch | MISS, stock latency |

Free demods vs. slots filled:

| Free demods at re-arm | NEXT | PREV | HISTORY |
|---|---|---|---|
| 3 or more | filled | filled | filled |
| 2 | filled | filled | IDLE |
| 1 | filled | IDLE | IDLE |
| 0 | IDLE | IDLE | IDLE |

At full saturation (every demodulator busy) the pool contributes
nothing — every zap runs at stock OpenATV speed, recordings and
PiP keep running without disruption.

### Behaviour with Timeshift

Timeshift comes in two trigger modes: **manual** (yellow / pause
button starts buffering the live channel on demand) and
**automatic** (`config.timeshift.permanent_timeshift`, where
enigma2 starts buffering on every channel tune). Both go through
the same `iPlayableService.startTimeshift()` entry point and reuse
the live demodulator via demuxer-side channel-sharing — no extra
demod is allocated for the buffer.

From the pool's perspective both modes are identical and
invisible: timeshift does not fire `RecordTimer.on_state_change`,
does not appear in `NavigationInstance.getRecordings()`, and never
triggers the ResourceArbiter. Pre-tune slots stay armed while
timeshift is active, the demod count stays the same as without
timeshift, and channel-share allocation prevents any contention on
the live transponder.

A zap during timeshift follows the standard fast-bypass path; the
pre-tune speedup applies as usual. `evInfoChanged` still fires
from `nav.playService`, so OpenATV's own SaveTimeshift hook and
permanent-timeshift re-arm continue to work without modification.

## What this plugin will NOT do for you

- Make a single fresh tune across satellites instant. The first lock,
  the LNB switch and the decoder init together cost ~600–1500 ms on
  Broadcom BCM7252S; that floor is hardware, not software.
- Replace the built-in FCC infrastructure. OpenATV 7.6 ships
  `eFCCServiceManager` in `libenigma` but never constructs the
  singleton and never exposes the config option; no Python path
  bootstraps it on this build. The recordService-based pretune
  here gets within a factor of two of what a kernel-side FCC
  would manage.
- Beat baseline when the user only zaps between services on the same
  transponder. enigma2 already handles that path in ~100 ms; the
  plugin matches that and does not regress it.

## Project status

v0.3.4 is the current build for long-term testing on the GigaBlue
UHD Quad 4K Pro under OpenATV 7.6.0. Everything in the feature
table works on this hardware. The pool has survived multiple
parallel recordings + PiP + rapid-fire zapping for hours without a
crash, the watchdog never had to self-disable, and the timing data
is reproducible across reboots.

A note on the channel-list highlight: pretuned services appear in
red in the channel list because they are technically active
recordings (the only working pretune path on this build).
Distinguishing them from real recordings would require a skin-level
change; see `docs/architecture.md` for the rationale. When a real
recording starts, the arbiter releases the matching demodulator
immediately, so the red highlight never lies — it always reflects
a busy demodulator.

## License

GPL-2.0-or-later. See [LICENSE](LICENSE).
