# Installation guide

Tested target: **GigaBlue UHD Quad 4K Pro on OpenATV 7.6.0**.
Should also work on any other FBC-equipped OpenATV box running
Python 3 (untested — feedback welcome).

## Standard install (OpenATV plugin browser)

Recommended path for most users. The plugin is in the official
OpenATV feed, so the receiver's built-in plugin manager picks it
up automatically once `opkg update` has run.

1. **Menu → Plugins** on the receiver.
2. Press the **green** button to open Download plugins (label
   wording differs slightly between skins — sometimes "Add new
   plugin").
3. Navigate to **Extensions**.
4. Pick `enigma2-plugin-extensions-fbc-channelspeedchange` and
   press OK.
5. Confirm the enigma2 restart when prompted.

After the restart the plugin runs with sane defaults:
`pretune_next`, `pretune_prev` and `pretune_history` all `yes`,
master switches `allow_pretune` and `use_real_pretune` both `yes`.
You should see the channel list mark the three pretuned services
in red within a couple of seconds of the first manual zap.

## Install the latest release directly (from GitHub)

The OpenATV feed mirror updates on a maintainer cadence and lags
the GitHub release by a few days. If a bug fix you need just
landed and is not yet in the feed, fetch the IPK directly.

The receiver needs internet access; everything else happens via SSH.

```sh
ssh root@<your-box-ip>

# 1. Grab the latest IPK from GitHub
wget https://github.com/empyfi/FBC-ChannelSpeedChange/releases/download/v0.6.2/enigma2-plugin-extensions-fbc-channelspeedchange_0.6.2_all.ipk -O /tmp/fbc.ipk

# 2. Install
opkg install /tmp/fbc.ipk

# 3. Restart enigma2 (does not reboot the box, ~10-20 s blackout)
init 4 && sleep 2 && init 3
```

This drops the plugin into the same `/usr/lib/enigma2/python/Plugins/Extensions/FBCChannelSpeedChange/`
location as the feed install. Subsequent `opkg update` runs will
not roll the plugin back to an older feed version (opkg sees the
locally-installed version is newer); once the feed catches up, the
two versions converge.

## Verification

```sh
# Confirm the IPK landed
opkg list-installed | grep fbc-channel

# Watch the plugin log
tail -f /tmp/fbc_csc.log
```

In a healthy install the log shows lines like:

```
[info] arbiter started
[info] controller started (next=True prev=True last=True; descramble next=False prev=False last=False)
[info] FBCChannelSpeedChange session_start ok
[info] interceptor started
[info] ZAP_TIMING attr=zapDown result=HIT delta_ms=122.4
```

The log is size-capped at 256 KB and rotates: once it fills, the
current file becomes `fbc_csc.log.1`, the previous `.1` becomes
`.2`, and so on up to `.3` (oldest dropped). When reporting a bug,
attach all of `fbc_csc.log` plus any `fbc_csc.log.1` … `.3` so the
minutes leading up to the problem are included.

After a few zaps you can also run:

```sh
# One-time fetch of the stats helper
wget https://raw.githubusercontent.com/empyfi/FBC-ChannelSpeedChange/main/tools/zap_stats.py -O /tmp/zap_stats.py

# Summarise /tmp/fbc_csc_timing.csv
python3 /tmp/zap_stats.py
```

to get a min / median / mean / max table per direction. The
script is intentionally not part of the IPK so that the package
stays minimal; it talks to `/tmp/fbc_csc_timing.csv` which the
plugin always writes regardless of the script being present.
The CSV rotates at 256 KB the same way `fbc_csc.log` does (three
backups kept: `.csv.1` / `.csv.2` / `.csv.3`); when reporting a
zap-perception bug, attach the live CSV plus the backups.

## Optional: enable the on-screen latency overlay

The plugin can flash the latency of each zap in the top-right
corner for 1.5 s, colour-coded green / yellow / orange / red
(plus cyan for external zaps). Off by default. To turn it on:

**Menu → Plugins → FBC ChannelSpeedChange → "Show zap latency OSD" → yes**

The overlay computes its position from the current
`getDesktop(0).size()` so it lands sensibly on any resolution.

## Build from source

Useful if you want to hack on the plugin. Requires Python 3 on
the dev host; no `make` or `ar` needed.

```sh
git clone https://github.com/empyfi/FBC-ChannelSpeedChange.git
cd FBC-ChannelSpeedChange
python build.py
# produces enigma2-plugin-extensions-fbc-channelspeedchange_<version>_all.ipk
```

The Makefile path (`make ipk`) works too on hosts that have GNU
`make` and `ar`. Both paths produce identical packages.

## Uninstall

```sh
opkg remove enigma2-plugin-extensions-fbc-channelspeedchange
init 4 && sleep 2 && init 3
```

The plugin restores all wrapped InfoBar methods on shutdown and
deletes its throwaway `/tmp/fbc_csc_pretune_*.ts` files. Zap
behaviour returns to stock immediately after the restart.

### One-time cleanup for installs from v0.3.3 or earlier

Releases up to and including v0.3.3 shipped without an opkg `postrm`
maintainer script, so after `opkg remove` the plugin's directory at
`/usr/lib/enigma2/python/Plugins/Extensions/FBCChannelSpeedChange/`
still contains Python bytecode that the runtime wrote at startup
(`__pycache__/*.pyc`). The leftover bytecode makes the enigma2
plugin browser keep listing the plugin (with no icon, since
`plugin.png` is gone). Wipe the directory once:

```sh
rm -rf /usr/lib/enigma2/python/Plugins/Extensions/FBCChannelSpeedChange
init 4 && sleep 2 && init 3
```

v0.3.4 and later carry a `postrm` script that does this automatically
on uninstall.

## Troubleshooting

### No HITs in the log, every zap is a MISS

Verify the box actually has FBC tuners:

```sh
cat /proc/bus/nim_sockets
```

Look for `Name: DVB-S2X NIM(45308X FBC)` (or similar). If your
NIMs are not flagged as FBC, the plugin will refuse to allocate
anything (by design — it never touches non-FBC tuners) and every
zap is a MISS. The fallback path through the standard enigma2
zap still works; you just get no speedup.

### Plugin self-disabled with a popup

Look at the bottom of `/tmp/fbc_csc.log` for the watchdog
message and the preceding errors. Three consecutive zap failures
in a row cause the controller to self-disable as a safety
measure. Restart enigma2 (`init 4 && init 3`) to re-enable.

### Popup: "missing required interfaces", plugin stays off

At startup the plugin runs a sanity check before hooking anything:
it confirms the enigma2 interfaces it depends on are present
(`InfoBar.zapUp` / `zapDown` / `servicelist`,
`NavigationInstance.recordService` / `playService`). If a critical
interface is missing it refuses to start, shows this popup, and
logs a line such as:

```
[error] sanity check failed; not starting. Missing: InfoBar.servicelist
```

This is deliberate — the plugin stays fully off rather than failing
half-way through the first zap. It almost always means the receiver
is running an enigma2 build the plugin has not been adapted to (a
non-OpenATV image, or an OpenATV release that renamed/removed an
interface). Nothing is wrapped and no tuner is touched, so the box
behaves exactly as without the plugin. Please open a bug with the
`sanity check failed` line and your image / OpenATV version.

Missing *optional* interfaces do not stop the plugin; they log a
`sanity (degraded)` warning naming the feature that is unavailable
(for example history-zap interception) while everything else keeps
working.

### `/tmp` filling up

Should never happen — the plugin punches holes in the pretune
files every two seconds, so logical sizes grow but the underlying
tmpfs pages are released. If you see `/tmp` use climbing past
~10 MB, please open a bug with the contents of
`/tmp/fbc_csc.log`.

### enigma2 crash loop after install

Use the master safety switch from SSH:

```sh
sed -i 's/^config\.plugins\.fbc_csc\.allow_pretune=.*/config.plugins.fbc_csc.allow_pretune=False/' /etc/enigma2/settings
init 4 && sleep 2 && init 3
```

This freezes the pool empty without uninstalling, so the plugin
loads cleanly but never allocates a tuner. Then please open a
bug.
