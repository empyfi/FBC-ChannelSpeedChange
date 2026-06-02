# Changelog

All notable changes to this project are documented here.
The format loosely follows [Keep a Changelog](https://keepachangelog.com/).

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
