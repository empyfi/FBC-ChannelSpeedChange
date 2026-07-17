# Changelog

All notable changes to this project are documented here.
The format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## [0.6.4] - 2026-07-16

### Fixed
- Non-DVB service references (IPTV, HTTP-stream and file-backed
  services) are no longer routed into the pre-tune path. Before
  v0.6.4 a zap into an IPTV bouquet (Pluto TV etc.) crash-looped
  enigma2: the predictor picked up the neighbouring IPTV services
  as pre-tune candidates, the pool handed them to
  `NavigationInstance.recordService(...)`, and the C++ recordable
  layer (`eDVBServiceRecord`) faulted because it only supports DVB
  frontends. Every subsequent zap tried the same allocation and
  the box entered a restart loop until the user disabled the
  plugin or moved out of the IPTV bouquet.

  Predictor now filters bouquet neighbours and history entries by
  `eServiceReference.type == idDVB` before returning them as
  candidates. The pool applies the same check as a second-line
  defence for any path that feeds a ref straight into `arm()`
  (e.g. the external-slot arm from the public API). The public
  `PreTuneSingleChannel` / `ReleaseSingleChannel` entry points
  reject non-DVB object refs with a debug-log line. Zap into the
  IPTV bouquet still works; only the pre-tune acceleration is
  skipped for services that are not tunable via a DVB frontend.

- Service-scan dialogs no longer fail with "Fehler beim Start der
  Suche" when the pre-tune pool happens to hold the frontend the
  scan requested. Before v0.6.4 the workaround was to disable the
  plugin manually before every scan; forum-reported on 2026-07-12
  and reproduced on the test bench on 2026-07-16 with the pool
  holding Tuner A while the user opened Netzwerksuchlauf.

  The controller now wraps the three scan-related screens
  (`Screens.ScanSetup.ScanSetup`, `ScanSimple`, and
  `Screens.ServiceScan.ServiceScan`) with a class-level patch
  analogous to the v0.6.3 standby wrapper. On the first scan
  screen opening every pool slot is released and the re-arm cycle
  is blocked; overlapping scan screens (the openatv stack pushes
  ServiceScan on top of ScanSetup) share a counter so the block
  stays in place across the whole scan session. Once the last
  scan screen closes the block clears and a fresh re-arm is
  scheduled after 500 ms.

  The wrapper is idempotent per screen; screens the running
  enigma2 build does not expose are skipped silently and reported
  via `sanity_check_scan_hook` as optional degradation. The public
  `PreTuneSingleChannel` API also short-circuits while a scan is
  active.

### Changed
- No config surface, no C-binding surface. 39 new tests added
  (`tests/test_scan.py` with 21 cases, plus IPTV-filter cases in
  `test_predictor.py`, `test_pool_state.py`, `test_external_api.py`);
  178 → 217 green.

## [0.6.3] - 2026-07-05

### Fixed
- Pre-tune slots are released the moment the box enters standby and
  arming stays blocked until standby ends. Before v0.6.3 the pool
  kept its slots armed across the transition, so the FBC frontends
  stayed busy and the box could not reach a proper standby state.
  Two visible consequences went away with this release:

  - On shared Unicable installations where a second receiver reuses
    the same SCR bands while the first box is in standby, the bands
    the pool held on to were no longer available. Reported by a
    forum user running two boxes on one Unicable feed.
  - For users who keep the box permanently in standby the LNB
    stayed powered and the frontends kept a tuning lock for no
    playback reason, with a small but real idle power cost.

  On the leave-standby edge the controller schedules a fresh re-arm
  so the next zap after wake-up hits a warm pretune slot as usual.
  The public API (`PreTuneSingleChannel`) also short-circuits to a
  no-op while the box is in standby; the caller stays free to fire
  and forget without holding state.

  Implementation uses `session.onEnterStandby` /
  `session.onLeaveStandby` — standard enigma2 hooks. On a build
  that does not expose either attribute, the plugin falls back to
  pre-v0.6.3 behaviour with a `sanity (degraded)` warn line so the
  degradation is visible in the log.

### Changed
- No config surface, no C-binding surface. 18 new tests
  (`tests/test_standby.py`), 163 → 181 green.

## [0.6.2] - 2026-07-01

### Fixed
- Public API string-form input now accepts the full canonical
  `eServiceReference.toString()` shape including the trailing
  `::<display name>` suffix. Before v0.6.2, callers that read the
  channel-list cursor via `getCurrentSelection().toString()` and
  passed the raw string to `PreTuneSingleChannel` /
  `ReleaseSingleChannel` had their calls silently rejected by the
  input whitelist because the display-name characters (`Z`, `D`,
  `F` in `ZDF` etc.) fall outside the hex character class the old
  regex required for every position after the initial `1:0:`.
  Companion plugins had to strip the suffix before every call to
  work around this.

  New regex accepts the ten hex fields followed by the optional
  standard `:` or `::<name>` tail. Non-empty path fields
  (`:file.ts:name`, `:/etc/shadow:name`) remain rejected — those
  never appear on a DVB broadcast ref and are the shape a
  path-injection attempt would take. The 512-byte length cap still
  applies to the whole string.

  When `Debug-Log` is on, a rejected string ref now emits a debug
  line naming the reason (shape mismatch or oversize). Silent
  rejection is preserved on the normal info-level surface so
  malicious spam callers cannot fill the log.

### Changed
- No config surface, no C-binding surface, no behavioural change
  for callers already passing `eServiceReference` objects or
  suffix-free strings. Purely a widened input contract.

## [0.6.1] - 2026-06-29

### Fixed
- Channel-list indicator no longer goes stale during cursor
  navigation. Before v0.6.1, when an external pretune source
  (FCC-Extender hover, NumberZap typing) rotated through pretune
  refs in tight succession, each visited service kept its pretune
  indicator colour (blue with the v0.6.0 default, or red on the
  pre-v0.6.0 baseline) until the user re-opened the bouquet.
  Root cause: the pool calls `NavigationInstance.recordService`
  directly, bypassing RecordTimer, so no event fires to invalidate
  the channel-list listbox when a slot is armed or released. The
  C++ painter (`eListboxServiceContent`) would paint the recording
  colour on the next paint, but the listbox engine never marked
  the affected rows dirty.

  The pool now calls `eListbox.invalidate()` on the live
  ChannelSelection widget after each slot arm and release, which
  marks the visible region dirty without changing the cursor.
  Implementation note: `eListboxServiceContent.lookupService(ref)`
  is unreliable upstream — only the shortcut for `ref == cursor`
  returns a correct index; the fall-through for-loop has an empty
  body and returns `m_list.size()` as a sentinel — so a targeted
  `redrawItemByIndex(idx)` would fire on a phantom past-the-end
  index. Full `invalidate()` works around this without depending
  on the broken upstream helper.

  Pre-existing bug since v0.3.x — verified to reproduce on v0.5.3
  with the red highlight too. v0.6.0's blue default just made it
  more noticeable.

  Touches only `fbc_pretune_pool.py` (+76 LOC: helper +
  arm/release call sites). No config changes, no new C-binding
  surface — `eListbox.invalidate()` is exposed by enigma2's
  standard GUI SWIG layer. Cosmetic refresh is best-effort and
  silently no-ops when running off-box or when the channel-list
  widget has not been built yet.

## [0.6.0] - 2026-06-25

### Added
- New ConfigSelection `pretune_indicator_style` in the Diagnostics
  group of the Settings screen, with three choices for how
  pre-tuned services appear in the channel list:
  - `pseudo` (default): the entry is rendered with
    `colorServicePseudoRecorded` - light blue on stock skins,
    visually distinct from a real recording but still clearly
    "this slot is holding a tuner".
  - `hidden`: tagged as `isFromSpecialJumpFastZap` so no indicator
    mask matches at all - the entry looks like any normal idle
    service. Requires enigma2 7.4+ (the constant is filtered out
    of the dropdown on older builds).
  - `recorded`: keeps the pre-v0.6.0 behaviour - tagged as
    `isUnknownRecording` so the painter renders it the same red
    as a real recording. Kept for users who liked the old visual
    signal that "a tuner is busy on this channel".
  Choice changes take effect on the next re-arm cycle (i.e. after
  one zap). The setup-screen description spells out the enigma2
  version requirement for the "Hidden" choice.

### Changed
- The pool's `recordService` call now uses the canonical 3-arg
  Python signature `recordService(ref, simulate=False,
  type=<RecordType>)` instead of the implicit 1-arg form. Default
  type was previously `isUnknownRecording`, which the channel-list
  painter (`eListboxServiceContent`) treats as a real recording
  and paints red. With v0.6.0's `pseudo` default the pool passes
  `isPseudoRecording`, so external recording counters
  (`getRealRecordingsCount`, OpenWebif "Recording list",
  `/api/statusinfo isRecording`) no longer report pre-tune slots
  as recordings - confirmed end-to-end on the test bench.
- `_kick_real_tune` now passes a per-role identifier as the
  `name` / `description` / `tags` arguments of `prepare()`
  ("FBC-CSC NEXT pretune" etc.) instead of empty strings. The
  recordable surfaces through any external UI that lists active
  recordings (OpenWebif, `/timer` REST endpoint, any
  `getRecordings(isAnyRecording)` consumer) with a clear,
  attributable label. No change to descrambler / channel-share /
  swap-in semantics; same 9-arg `prepare()` signature, only the
  three previously-empty string slots are populated.

### Notes
- Field-tested on the GigaBlue UHD Quad 4K Pro (openatv 7.6.0,
  `git35118+befedea0`): rc1 IPK deployed via `opkg install
  --force-reinstall`, restart cycle clean, both pre-tune slots
  transitioned to LOCKED state, no new sidecar suffixes (only the
  five known `.ts`/`.meta`/`.sc`/`.ap`/`.cuts`/`.eit`),
  `/api/statusinfo isRecording=false` with two armed slots,
  zero warn/error regressions. Channel-list visual confirmed
  blue under default settings; toggle round-trip through
  `hidden` and `recorded` confirmed against the live channel
  list. Zap latency unchanged: 4 sample zaps median 123.6 ms vs
  v0.5.3 baseline 128 ms (ext HIT) - well within measurement
  noise.
- pNavigation RecordType enum verified live via diagnostic.py
  dump on the test build: all nine constants exposed,
  `isFromSpecialJumpFastZap = 128` present, matching openatv's
  `lib/nav/core.h` on the 7.6 branch.
- 6 new tests in `tests/test_pool_state.py` covering each
  indicator-style value (`pseudo`/`hidden`/`recorded`) and the
  defensive fallback paths for missing pNavigation constants.
  Tests 156 -> 162 green.

## [0.5.3] - 2026-06-23

### Added
- New `NEAR` result label for the intra-TP channel-share case.
  When a bypass zap targets a service whose transponder is
  already locked by a sibling pool slot (different service id
  on the same MUX), `pool.lookup` correctly misses at the
  service level but `eDVBResourceManager` channel-shares at the
  transponder level - the demod is already there, so the zap
  lands in the ~100-300 ms PMT-switch regime instead of the
  ~700-900 ms cold-tune regime. Previously these zaps were
  labelled `EXT` and dragged the EXT-bucket median artificially
  low; they now report as `NEAR` with their own OSD bucket-
  colour (teal). Wrapper-MISS paths get the same upgrade when
  a TP-share is available. Adds `pool.tp_match(ref)` as the
  transponder-level partner of the existing `pool.lookup`.

### Changed
- Wrapper-path zap timing now anchors on `evStart` (same as the
  bypass path) instead of at the top of the wrapper closure.
  The wrapper sets `_zap_attr` / `_zap_hit` up front so the
  metadata is known by the time `evStart` fires; the timing
  anchor itself is set by `_on_nav_event(evStart)` for every
  path. Net effect: wrapper-HIT and bypass-HIT measurements
  now cover the same `evStart -> evTunedIn` span, eliminating
  the ~50 ms measurement gap where `zapUp/zapDown HIT` looked
  artificially slower than `ext HIT` in v0.5.2.

## [0.5.2] - 2026-06-23

### Fixed
- OSD overlay and timing CSV now label pool-delivered external
  zaps honestly. Previously every bypass zap (history selector /
  Last-Channel button, EPG OK, NumberZap OK, FCC-Extender-driven
  api zap) was tagged as a neutral cyan `EXT` regardless of
  whether the pool's channel-share path had actually delivered
  the speedup. On `evStart` the interceptor now probes the pool
  for the live ref and, on a match, classifies the zap as `HIT` -
  so the overlay bucket-colours the same 50-60 ms recall green
  instead of cyan, and `zap_stats.py` aggregates the `(ext, HIT)`
  combo as a genuine hit. Bypass zaps without a pool match still
  surface as `EXT` so the two cases can be told apart.

### Changed
- `controller started` log line now also reports the three
  `prewarm_descrambler_*` flags alongside the three `pretune_*`
  flags, so a fresh boot's descrambler-prewarm state is visible
  in `/tmp/fbc_csc.log` without grepping `/etc/enigma2/settings`
  or inspecting the pool's `.ts` files.
- Single canonical implementation of the serviceref-key
  normaliser. `fbc_pretune_pool` now imports `_key` from
  `predictor` instead of carrying its own duplicate `_ref_key`;
  a future change to the normalisation rule can no longer drift
  between the two modules.
- `_release_slot` restructured so each cleanup stage (reclaim
  timer, recordable stop, file unlink, slot state reset) runs
  in its own try/except. A fault in any one stage - for example
  a faulty `recordable.stop()` - no longer skips the others. In
  particular, the throwaway pre-tune file is now unlinked
  unconditionally; previously a leaked `.ts` could hold tmpfs
  RAM until reboot.
- `/tmp/fbc_csc_timing.csv` is now size-capped at 256 KB with
  three rotated backups (`.1`/`.2`/`.3`), mirroring the
  existing `logger.py` rotation pattern. Each rotated segment
  carries the canonical CSV header so off-box analysis tooling
  does not have to special-case post-rotation segments.

### Added
- Startup sweep at `FBCPreTunePool` init removes leftover
  `/tmp/fbc_csc_pretune_*.ts*` files from a prior controller
  that died (e.g. `init 4` SIGKILL'd the Python process)
  before completing `_release_slot`. The pool is singleton with
  exclusive ownership of this filename pattern, so any matching
  file at init time is unreachable garbage by definition.

## [0.5.1] - 2026-06-22

### Added
- Yellow-button shortcut from the Settings screen to the
  FCC-Extender's own settings, gated on the same opkg-status
  detection as the existing FCC-Extender presence indicator.
  The button appears only when the companion plugin is
  detected; on every other box it stays out of the action map
  entirely so host-skin yellow bindings are untouched. Replaces
  the runtime monkey-patch the FCC-Extender used to inject into
  `FBCChannelSpeedChangeSetup.__init__` in earlier releases —
  each plugin now owns its own settings surface natively.
- `_parse_fccextender_status` helper exposing
  `(state, version)` so the boolean detector and the label
  formatter share one parser. No behaviour change to the
  presence-indicator label; six new tests pin the parser and
  detector contracts.

## [0.5.0] - 2026-06-21

### Added
- Public Python API at
  `Plugins.Extensions.FBCChannelSpeedChange.api` for companion
  plugins (FCC-Extender etc.) that want to feed a service
  reference into the pre-tune pool. Two entry points,
  `PreTuneSingleChannel(service_ref)` and
  `ReleaseSingleChannel(service_ref=None)`, both returning
  `None`. Calls are silent no-ops when the master switch or the
  new `accept_external_pretune` toggle is off, or when the
  controller has not started yet.
- The api accepts either an `eServiceReference` instance or a
  canonical DVB broadcast serviceref string
  (`1:0:<stype>:<sid>:<tsid>:<onid>:<ns>:...`). Strings are
  validated against a strict shape whitelist before the SWIG
  constructor sees them, so malformed input cannot reach the
  C++ parser. Callers holding the cursor as a string
  (`ChannelSelection.getCurrentSelection().toString()`) no
  longer need to wrap it in `eServiceReference(...)` themselves.
- New `Role.EXTERNAL` pool slot driven exclusively by the api
  module. Capacity 1 by default, never competes with the
  internal NEXT / PREV / HISTORY predictor. Convergence with
  any internal slot (the ref is already armed there) short-
  circuits the EXTERNAL allocation so a subsequent zap is
  satisfied through channel-share without a duplicate
  recordable.
- TTL safety net for the EXTERNAL slot
  (`cfg.external_slot_ttl_min`, default 5 minutes). Auto-
  releases when the companion plugin's explicit release never
  lands. Long enough that legitimate EPG-read sessions never
  get torn down mid-read. The UI exposes the value in minutes;
  the controller multiplies by 60000 before handing it to
  ``eTimer``.
- `evNewProgramInfo` listener that releases the EXTERNAL slot
  when the live service changes to the armed ref. Covers the
  shortcut-zap path where `session.nav.playService` is called
  from outside `ChannelSelection`.
- Three new ConfigYesNo / ConfigInteger toggles:
  `accept_external_pretune` (default True, master gate - a
  paired companion plugin just works; without one installed
  no caller fires the API anyway, so the "on" default is a
  no-op for the typical user),
  `external_slot_ttl_min` (default 5, limits 1..30),
  `prewarm_descrambler_external` (default False, Pay-TV opt-in
  for the EXTERNAL slot using the same semantics as the
  existing three direction toggles).
- FCC-Extender presence indicator injected into the External
  pretune Setup group. A small helper reads `/var/lib/opkg/status`
  at Setup-screen open time and inserts a "FCC-Extender:
  installed / not detected / status unknown" header-style row
  directly under the group header. Substring match on
  ``fccextender`` / ``fcc-extender`` catches the VTi build, the
  expected OpenATV port and either hyphenation Oberhesse settles
  on.
- New `External pretune (FCC-Extender)` group in `setup.xml`
  and matching DE translations in `po/de.po`. The new Pay-TV
  descrambler row for the EXTERNAL slot sits at the end of the
  existing Pay-TV group.
- `sanity_check_external_hook` runs at controller start
  alongside the pool and arbiter checks. Missing
  `evNewProgramInfo` enum or `NavigationInstance.event` is
  critical when `accept_external_pretune` is on (the start
  path refuses with a popup) and informational otherwise.

### Notes
- No new C-binding surface. The EXTERNAL slot reuses the
  existing `recordService → prepare(9-arg) → start()` path
  proven in v0.4.0 onwards.
- Public API contract designed for and verified against
  Oberhesse's FCC-Extender (OpenATV port in progress); the
  signature is generic enough that any plugin can use it.
- On VU+ boxes the OpenATV FCC system plugin remains the
  native fast-zap path. The FCC-Extender routes to FCC there
  without going through this API; FBC-CSC is typically not
  needed alongside FCC on the same box. README note added.

## [0.4.4] - 2026-06-12

### Changed
- Plugin Browser description now carries the version as a `[vX.Y.Z]`
  prefix so the users can identify  the installed build at a glance
  without opening the Settings UI.

### Added
- `__version__` constant exposed by the plugin package
  (`Plugins.Extensions.FBCChannelSpeedChange.__version__`) as the
  single runtime source of truth for the version string.
  `tools/bump_release_urls.py --check` now enforces that
  `CONTROL/control`, `Makefile` and the package's `__version__` all
  agree before a release goes out.

### Notes
- Pure presentation-layer change. Controller, pool, predictor,
  arbiter, interceptor and config behaviour are untouched.

## [0.4.3] - 2026-06-09

### Fixed
- Settings screen: the green button did not close the screen
  after a save. The v0.4.2 `keySave` override misread the return
  value of `ConfigListScreen.saveAll()` — `saveAll` returns an
  empty tuple `()` on a normal successful save (not a boolean),
  so `if not self.saveAll(): return` short-circuited and the
  follow-up close path never ran. Visible to the user as "green
  does nothing"; values were still written to
  `/etc/enigma2/settings` (saveAll completes its writes before
  returning) but the screen stayed open and the live controller
  was not notified.

  `keySave` now fires the `controller.on_config_changed()` hook
  and delegates the actual save+close to the parent's
  `Setup.keySave()`. As a side benefit the inherited path also
  honours `restart="gui"` / `restart="system"` attributes on
  `<item>` rows (no current `setup.xml` row uses either, but it
  is the correct shape if a future toggle ever needs it).

## [0.4.2] - 2026-06-09

### Changed
- Settings screen rebuilt on top of `Screens.Setup.Setup`. The
  previous version pinned an inline 600x400 skin block onto the
  plugin browser entry; on FHD and 4K image skins (Metrix HD,
  Gradient FHD, …) that produced a tiny floating window with
  truncated labels and skin-incompatible button graphics. The new
  setup screen inherits whatever Setup skin the active image
  provides: title bar, scrollable config list on the left, blue
  description panel on the right, button row at the bottom.
  Layout-correct from default skin up to 4K skin builds.
- The setup descriptor now lives in `setup.xml` alongside the
  plugin's Python sources. Each entry carries a `description`
  attribute the host renders in the help panel.

### Added
- Five visual group headers in the settings list (Plugin,
  Resource release, Zap acceleration, Pay-TV, Diagnostics)
  rendered via the official Setup separator pattern - an
  `<item text="── … ──"></item>` row with no inner config
  binding. Setup interprets the empty inner-text as a non-
  selectable header label and skips the help-panel render for
  that row.
- Three-line plugin intro at the top of the settings list,
  summarising what the plugin does and pointing at the GitHub
  repository for details.

### Notes
- No behaviour changes to the controller, pool, predictor,
  arbiter or interceptor. `config.py` is unchanged; the 13
  `ConfigYesNo` toggles persist identically to v0.4.1. The
  release is a pure presentation-layer rebuild.
- Full functional verification of every toggle on the test bench
  (GigaBlue UHD Quad 4K Pro, OpenATV 7.6.0). Phase 1 (auto):
  14 single-toggle smoke runs with `init 4 / init 3` between
  each, verified via `/tmp/fbc_csc.log`, pool slot inventory,
  and persisted-settings check. Phase 2 (webif-driven): live
  triggers for `release_for_recording`, `show_osd_timing`, and
  an OSCam-`ecmhistory` snapshot for the three
  `prewarm_descrambler_*` flags. Phase 3 (manual remote):
  `release_for_pip` confirmed via two PiP open/close cycles.
  13 / 13 PASS, no regressions, no Pay-TV side effects.

## [0.4.1] - 2026-06-06

### Changed
- Pool re-arm collapses the HISTORY slot when its target converges
  with NEXT or PREV. During linear bouquet walking the just-departed
  channel ends up on the HISTORY slot AND on the opposite-direction
  neighbour slot (PREV when walking Channel ↑, NEXT when walking
  Channel ↓), producing a redundant dvbapi subscription on the same
  service. The pool now detects the collision at arm time and skips
  arming HISTORY in that case. A recall after a skipped HISTORY still
  HITs because the pool's lookup is role-independent: it walks every
  armed slot and returns the first key-matching one, so the surviving
  PREV / NEXT slot answers the recall via channel-sharing.

  Measured on the test bench (HD+ Nagra Aladin via OSCam, all three
  prewarm_descrambler toggles on, mixed FTA/HD+ bouquet): card ECM
  rate −7 %, p95 ECM round-trip −22 % (722 ms vs 923 ms), card-stress
  events (RTT > 500 ms) −27 %. Walk and recall HIT rate unchanged
  (12/12 walk, 6/6 recall in the validation run). Median RTT stays at
  the hardware-bound 376 ms; the gain is concentrated in the tail.

### Notes
- The optimisation is silent for users without descrambler-prewarm
  enabled (HISTORY with `descramble=False` was harmless duplicate
  bookkeeping; nothing on the wire to save). It pays off specifically
  for cardsharing setups and single-decode CAMs with one or more
  `prewarm_descrambler_*` toggles on, where the redundant ECM stream
  was real card load.
- No new C-binding surface: the change is a Python-side predicate
  before `_kick_real_tune`, comparing service-reference keys via the
  predictor's existing `_key()` normalisation (rename-safe).

## [0.4.0] - 2026-05-31

### Changed
- Pre-tune of scrambled channels no longer engages the CA descrambler
  by default. The pool now calls `iRecordableService.prepare()` with
  the canonical 9-argument signature (verified against
  `openatv/enigma2` branch 7.6 `lib/python/RecordTimer.py`) and passes
  `descramble=False`. The FBC tuner still locks the target transponder,
  channel-sharing at swap-in is unaffected, but no parallel ECM /
  decoder load is added to the user's softcam / OSCam dvbapi /
  cardsharing / CI+ CAM path during pre-tune arm cycles. Safe for
  cardsharing setups (no anti-share ECM heat), single-decode CAMs
  (no contention with live) and CI+ modules.

  User-visible effect: scrambled HIT zaps show a brief black frame
  (~400 ms, one ECM round-trip) between tuner lock and clear picture.
  Free-to-air channels are unaffected.

### Added
- Three per-direction toggles to opt back into the v0.3.7-style
  behaviour where the descrambler engages during pre-tune (UI
  labels "Activate descrambler in NEXT / PREVIOUS / LAST pay-TV
  pre-tune"). Internal config keys
  `prewarm_descrambler_{next,prev,history}` are kept under the
  old name for settings-file compatibility, all default off.
  Each engages the descrambler on the matching
  pre-tune slot. The three slots are mechanically symmetric: all
  three re-arm on every successful zap, and each enabled toggle
  holds exactly one continuous descrambler session above the live
  consumer regardless of zap activity. Per-zap ECM bursts are
  therefore identical across the three directions; load scales
  with how many toggles are on, not with which one. The only
  asymmetry is which user action each slot HITs: HISTORY tracks
  the last non-live channel and HITs the last-channel button;
  NEXT and PREV track bouquet neighbours and HIT Channel ↑
  resp. Channel ↓. For cardsharing setups whose anti-share
  heuristic looks at long-window service diversity (rather than
  raw ECM rate), HISTORY's target set stays small for
  recall-heavy viewing while NEXT/PREV move with the live
  channel through the bouquet.
- `target_ref` column in `/tmp/fbc_csc_timing.csv` and the
  `ZAP_TIMING` log line. The currently-playing service reference is
  captured at `evTunedIn` so off-box analysis can classify FTA vs
  scrambled per zap (cross-reference against `lamedb5`).
- CSV header migrate-on-first-write. A pre-0.4.0 timing CSV with the
  legacy 4-column header is rewritten in place on first launch:
  header replaced with the new 5-column shape, legacy rows padded
  with an empty trailing `target_ref` so the file stays
  CSV-clean for off-box analysis. Idempotent; runs once per
  upgrade and is a no-op thereafter.

### Documentation
- New "Descrambler behaviour and pay-TV channels" section in
  `docs/architecture.md` describing the `descramble=False` default,
  the canonical 9-arg `iRecordableService.prepare()` signature with
  provenance, the per-direction toggles, the swap-in descrambler-
  initialisation mechanic, and the OSCam dvbapi handshake
  operational note.
- "Provider coverage" subsection in both `docs/architecture.md`
  and the README pay-TV section. All measurements (ECM rates,
  ~400 ms black-frame, parallel-decode capacity) come from a
  single test bench using HD+ Nagravision (CAID 1843) on
  OSCam-smod. The `descramble=False` mechanic is provider-
  agnostic; the numbers themselves will vary on Sky / ORF /
  CI+ CAM / other softcam configurations.

### Notes
- On some softcam configurations (observed with OSCam-smod) the
  dvbapi socket can desynchronise after an enigma2 restart, leaving
  pay-TV channels black. The fix is to restart the softcam manager
  (`/etc/init.d/softcam stop && /etc/init.d/softcam start`). The
  plugin itself never touches the softcam directly; this is an
  enigma2 ↔ softcam handshake issue.

## [0.3.7] - 2026-05-24

### Added
- Startup sanity check across the interceptor, pool and arbiter.
  Before wrapping anything, the plugin verifies the critical enigma2
  surface it depends on (`InfoBar.zapUp` / `zapDown` / `servicelist`,
  `NavigationInstance.recordService` / `playService`). A missing
  critical interface now makes the plugin refuse to start with a
  clear log line and a one-shot popup, instead of failing piecemeal
  at the first zap. Missing optional interfaces (`historyBack` /
  `historyNext`, `servicelist.setCurrentSelection`, the
  `RecordTimer.on_state_change` signal) log a degraded-mode warning
  and the plugin keeps running.

### Changed
- `logger.py` rotates the log instead of deleting it. When
  `fbc_csc.log` passes 256 KB it shifts to `fbc_csc.log.1` …
  `fbc_csc.log.3` (oldest dropped) so the minutes leading up to a
  recent crash stay readable for post-mortems. Previously the log
  was wiped wholesale on overflow.

### Fixed
- The IPK no longer ships `__pycache__/*.pyc` bytecode. Both build
  paths (`build.py` and the `Makefile`) now prune `__pycache__`
  and `*.py[co]` before packaging, so host-specific compiled
  bytecode (built for the dev host's Python version) cannot leak
  into the package; enigma2 compiles its own on first import. This
  also shrinks the IPK noticeably.

## [0.3.6] - 2026-05-24

### Fixed
- `build.py` now terminates ar member names with the GNU-style
  trailing slash (`debian-binary/`, `control.tar.gz/`,
  `data.tar.gz/`) inside the 16-byte name field. Both opkg and
  dpkg accept the slash either way, but 7zip uses the slash to
  distinguish a plain ar archive from a Debian-style payload-only
  view; without the slash 7zip's `deb` subtype handler showed only
  `data.tar.gz` and silently hid `debian-binary` and `control.tar.gz`,
  giving the impression that the IPK had no control file. With the
  slash 7zip reports `SubType = ar` and lists all three members.

  Functional behaviour on the receiver is unchanged - opkg parses
  both forms identically. The `Makefile` build path (system `ar -r`,
  GNU tooling) already wrote the slashed form, so this was only a
  defect in the pure-Python builder.

## [0.3.5] - 2026-05-22

### Changed
- Settings-screen skin XML in `settings_ui.py` gains
  `alphatest="blend"` on the red/green button pixmaps and
  `zPosition="1"` on the `key_red` / `key_green` label widgets.
  The pixmap edges now compose properly against any skin
  background, and the labels are guaranteed to render above the
  button graphic regardless of the skin's layer-sort behaviour.

## [0.3.4] - 2026-05-21

### Added
- `CONTROL/postrm` opkg maintainer script. Wipes the plugin
  directory after `opkg remove` so leftover Python bytecode
  (`__pycache__/*.pyc`) does not keep the enigma2 plugin browser
  listing the plugin as a ghost entry without an icon. Earlier
  releases required a one-time manual `rm -rf` after uninstall;
  this happens automatically now.
- `tools/bump_release_urls.py` for keeping the install URLs in
  `README.md` and `docs/install.md` in sync with the released
  version. Derives the "from" version from the docs themselves,
  so the script can run at any point in the release flow.
- `docs/install.md` notes the one-time manual cleanup users coming
  from v0.3.3 or earlier need to run once.

### Changed
- `build.py` installs opkg maintainer scripts (`preinst`,
  `postinst`, `prerm`, `postrm`) with mode `0755` in the control
  archive; the `Makefile` build path copies the whole `CONTROL/`
  tree and `chmod`s `postrm` so both build flows produce identical
  IPKs.

### Fixed
- `README.md` and `docs/install.md` install snippets now point at
  the actual current release tag and filename. The previous text
  was pinned at `v0.3.0` (and `docs/install.md` even referenced a
  stale `0.2.7` filename).

## [0.3.3] - 2026-05-21

### Added
- Autotools skeleton for the OpenEmbedded build path:
  `configure.ac`, top-level `Makefile.am`, `po/Makefile.am`
  and `autogen.sh`. After `./autogen.sh && ./configure &&
  make && make install` the plugin lands under
  `$(libdir)/enigma2/python/Plugins/Extensions/FBCChannelSpeedChange`
  and the compiled translation catalog under
  `.../FBCChannelSpeedChange/locale/<lang>/LC_MESSAGES/`.
- README section pointing distribution maintainers at the
  autotools path; the existing `wget` + `opkg install` IPK
  flow stays the primary supported install method for users
  on a running box.

### Changed
- `.gitignore` covers autotools-generated artefacts
  (`configure`, `Makefile.in`, `aclocal.m4`,
  `autom4te.cache/`, `build-aux/`, `po/Makefile.in`,
  `po/*.mo`).

## [0.3.2] - 2026-05-21

### Added
- gettext-based i18n. All user-facing strings (plugin
  description, all settings labels, the Cancel/Save buttons
  and the watchdog popup) now go through `_()` and pick up the
  enigma2 UI language at runtime.
- German translation under `po/de.po`. Wraps the 14 visible
  strings into proper German labels.
- `tools/compile_po.py` — pure-Python `.po` to `.mo` compiler
  used by the build (no external `msgfmt` required).
- `build.py` compiles every `po/*.po` into
  `src/.../locale/<lang>/LC_MESSAGES/FBCChannelSpeedChange.mo`
  before packaging, so the IPK ships ready-to-use catalogs.

### Changed
- Package `__init__.py` wires `gettext.bindtextdomain` against
  the plugin's own locale directory and falls back to an
  identity `_()` when enigma2 modules are unavailable
  (off-box tests).

## [0.3.1] - 2026-05-20

### Added
- Plugin icon (`plugin.png`, 100×40 px) shown next to the
  entry in the OpenATV plugin browser.

### Changed
- Plugin browser description rewritten to one sentence
  explaining what the plugin actually does
  ("Accelerates channel zapping by pre-tuning the next,
  previous and last-watched channel on free FBC tuners").
  The same text is now used for both `PluginDescriptor`
  entries.

## [0.3.0] - 2026-05-18

Initial public release.

### Features
- Pre-tune NEXT / PREVIOUS / LAST-WATCHED channel (each toggleable)
- FBC-only allocation; never touches USB or non-FBC tuners
- Auto-release of the pre-tune pool on `STATE_PREPARED` for
  recordings and on PiP visibility
- Two-tier safety opt-in (`allow_pretune`, `use_real_pretune`)
- Crash watchdog with self-disable after three consecutive
  failures
- Optional on-screen latency overlay (colour-coded)
- Per-zap timing CSV at `/tmp/fbc_csc_timing.csv` plus
  summariser tool
- tmpfs reclaim every 2 s via
  `fallocate(PUNCH_HOLE | KEEP_SIZE)` so the throwaway pre-tune
  `.ts` files do not balloon RAM
- 29 unit tests against mocked enigma2 APIs

### Measured on the GigaBlue UHD Quad 4K Pro (OpenATV 7.6.0)

| Zap path | n | median | mean |
|---|---|---|---|
| Channel ↑ HIT | 11 | 117 ms | 138 ms |
| Channel ↓ HIT | 17 | 124 ms | 203 ms |
| History / Recall HIT | 12 | 59 ms | 63 ms |
| External zap (no pretune target) | 11 | 841 ms | 1198 ms |

HIT rate for wrapper-bracketed zaps: 93 %.
