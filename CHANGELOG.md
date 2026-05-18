# Changelog

All notable changes to this project are documented here.
The format loosely follows [Keep a Changelog](https://keepachangelog.com/).

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
