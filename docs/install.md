# Installation guide

Tested target: **GigaBlue UHD Quad 4K Pro on OpenATV 7.6.0**.
Should also work on any other FBC-equipped OpenATV box running
Python 3 (untested — feedback welcome).

## Standard install (from GitHub release)

The receiver needs internet access; everything else happens via SSH.
Recommended approach for end users.

```sh
ssh root@<your-box-ip>

# 1. Grab the latest IPK from GitHub
wget https://github.com/empyfi/FBC-ChannelSpeedChange/releases/download/v0.3.3/enigma2-plugin-extensions-fbc-channelspeedchange_0.3.3_all.ipk -O /tmp/fbc.ipk

# 2. Install
opkg install /tmp/fbc.ipk

# 3. Restart enigma2 (does not reboot the box, ~10-20 s blackout)
init 4 && sleep 2 && init 3
```

After the restart the plugin runs with sane defaults:
`pretune_next`, `pretune_prev` and `pretune_history` all `yes`,
master switches `allow_pretune` and `use_real_pretune` both `yes`.
You should see the channel list mark the three pretuned services
in red within a couple of seconds of the first manual zap.

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
[info] controller started (next=True prev=True last=True)
[info] FBCChannelSpeedChange session_start ok
[info] interceptor started
[info] ZAP_TIMING attr=zapDown result=HIT delta_ms=122.4
```

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
