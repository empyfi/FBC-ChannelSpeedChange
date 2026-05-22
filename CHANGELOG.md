# Changelog

All notable changes to this project are documented here.
The format loosely follows [Keep a Changelog](https://keepachangelog.com/).

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
