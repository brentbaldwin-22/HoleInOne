# Bringing the GREEN rig up to the TEE's configuration

The tee was rebuilt piece by piece over two weeks — a USB current cap,
an MTU black hole, a dongle that had to move, an upload ratchet, a
sensor frame rate, a capture engine. Each fix was made on the tee
alone, because the tee was the one being debugged. **The green has had
none of them.**

This is the list, in the order it has to happen, with the check that
says each step took. It is written to be followed on the green rig with
nothing but an SSH session and `curl`.

> **Read Step 0 before changing anything.** Three numbers decide which
> of the later steps is actually your problem, and they cost one glance
> at a page you already have open.

---

## Step 0 — What is the green REALLY sending?

Open the green camera's card on `/admin/cameras` and read the stream
panel. It prints three rates side by side, and they are three different
claims:

| reading | what it is |
|---|---|
| the size/rate the stream **opened** at | what the camera announced |
| the rate frames are **arriving** at | what it is actually delivering |
| the rate the **config** believes | `camera.fps` in `config.yaml` |

That panel exists because a camera that claims one setting and delivers
another is the failure mode on this hardware — it has happened twice in
one day. **Clips are stamped with the CONFIG's rate**, so:

- config **above** delivered → the clip is stamped faster than it was
  shot and plays **fast and short** (a 35 s capture played in 21).
- config **below** delivered → stamped slower than shot: **slow motion**.
- arriving well under opened, with the config matching the config →
  the camera is announcing a rate it cannot sustain, or frames are
  being dropped on the way in. That is **choppy**, and it is a
  different fault from either of the above.

A green reading `29.96 fps` on the production card is the *file's* rate,
which is the config's belief, not evidence about the camera. Get the
three numbers before deciding which problem you have.

---

## Step 1 — The Pi (do these first; they gate everything else)

### 1a. USB current cap

A Pi 5 that does not detect a 5 V / 5 A supply caps **all** downstream
USB at 600 mA. An LTE dongle on transmit crosses that on its own.

```bash
vcgencmd get_config usb_max_current_enable
grep -i usb_max_current /boot/firmware/config.txt
vcgencmd get_throttled
```

If it is not `1` **and the supply is genuinely a 5 V/5 A unit**, add
`usb_max_current_enable=1` to `/boot/firmware/config.txt` and reboot.
On a phone charger or a 15 W brick, **do not set it** — raising the cap
on a supply that cannot deliver moves the brownout from the port to the
whole board.

*Check:* `vcgencmd get_config usb_max_current_enable` reads 1 after the
reboot, and `get_throttled` is `0x0`.

### 1b. MTU

Downloads fine and uploads dead is an MTU problem until proven
otherwise. Find the largest payload that survives and add 28:

```bash
ip link show eth1 | grep -o 'mtu [0-9]*'
for s in 1472 1452 1442 1432 1422 1412 1400; do
  printf "payload %5d (MTU %5d): " "$s" "$((s+28))"
  ping -M do -s $s -c 2 -W 2 8.8.8.8 >/dev/null 2>&1 && echo OK || echo FAIL
done
sudo ip link set eth1 mtu 1440        # whatever the ladder says
```

`ip link set` does **not** survive a reboot or a modem re-enumeration,
and this dongle re-enumerates. Make it permanent for whichever stack
the rig runs — check with
`systemctl is-active NetworkManager dhcpcd`.

*Check:* `ping -M do -s 1412` passes and `-s 1472` fails, and the MTU
survives `sudo reboot`.

### 1c. Where the dongle sits

This is the one that mattered. On the tee, moving the dongle near a
window took upload from **17,474 to 1,696,346 B/s — 97×**, confirmed
across a reboot. Nothing in software came close.

**The dongle's position is a first-class piece of hardware
configuration.** Put the green's where the tee's is, and measure upload
— not ping, not download. A healthy downlink says nothing about the
uplink.

### 1d. Agent code

```bash
sudo /opt/golfreelz-agent/update.sh
```

*Check:* the Cameras card shows the same `fw green-0.1.0+<hash>` build
id as the tee's. That hash is a sha256 of `agent/*.py` and is the only
remote drift detector there is — if the two rigs differ, they are not
running the same code, whatever `update.sh` reported.

### 1e. Clear the learned upload rate

The uploader learns a rate and persists it. A green that spent weeks on
a broken link is still carrying the floor it was beaten down to, and
**that matters beyond upload speed**: a stream-copied clip is sent
first-generation only while the learned rate is at its ceiling. Below
it, every clip gets re-encoded — to 1500 kbps and 720p on a green's
defaults. Soft, slow clips, forever, from a file that was already fine.

```bash
sudo systemctl stop golfreelz-agent
sudo rm -f /var/lib/golfreelz-agent/pending/learned_kbps.json
sudo systemctl start golfreelz-agent
```

*Check:* the card's "Link forced down to" row is absent, or reads the
same as the configured ceiling.

---

## Step 2 — The camera (SUNAPI, not the web UI)

The Hanwha web UI needs the tunnel held open across dozens of requests
and the modem does not oblige. Every setting is one HTTP request
instead. Read first:

```bash
sudo -u golfreelz /opt/golfreelz-agent/venv/bin/python3 - <<'EOF'
import sys, json, yaml
sys.path.insert(0, "/opt/golfreelz-agent")
from agent.common import stream_profile_view
cfg = yaml.safe_load(open("/opt/golfreelz-agent/config.yaml"))
print(json.dumps(stream_profile_view(cfg.get("camera", {})), indent=2))
EOF
```

Then set, in this order — **the sensor gates everything**. A profile
cannot ask for a rate the sensor is not producing; set `FrameRate=60`
on the profile alone and the camera answers `OK` and silently leaves it
at 30.

| field | target | submenu / verb |
|---|---|---|
| `SensorCaptureFrameRate` | **60** | `videosource` / `action=set` |
| `Profile.N.FrameRate` | **60** | `videoprofile` / `action=update` |
| `Profile.N.H264.GOVLength` | **60** | `videoprofile` / `action=update` |
| `Profile.N.H264.BitrateControlType` | **CBR** | `videoprofile` / `action=update` |
| `Profile.N.Resolution` | 1920x1080 | `videoprofile` / `action=update` |
| `Profile.N.Bitrate` | **read the tee's — do not assume** | `videoprofile` / `action=update` |

**Set `BitrateControlType` BEFORE `Bitrate`.** Changing the mode
re-clamps the bitrate to something legal for the new mode, silently and
after the fact. On the green, 19 Oct: `Bitrate=2560` then `CBR` both
answered `OK` and read back **5120**; the identical write of 2560 sent
again, once the profile was already CBR, stuck first time.

**And read the TEE rather than trusting a table — including the one in
camera-triage.md.** That table recorded the tee's bitrate as 2560 and a
read on 19 Oct returned **4000**. A number written down once is a
number about a camera as it was that day.

Four things that waste an afternoon if you do not know them:

1. **The verb differs per submenu.** `videosource` takes `action=set`;
   `videoprofile` takes `action=update`. The wrong one returns
   `NG / 601 / Action Not Found`, which reads like a missing feature.
2. **Codec fields keep their namespace.** `FrameRate` is written plain;
   `BitrateControlType` must be sent as `H264.BitrateControlType` or it
   comes back `NG / 602 / Invalid Parameter(s)`. The rule is what the
   view dump shows: a key reading back as `…Profile.2.H264.X` is
   written `H264.X`.
3. **`OK` does not mean it changed.** This firmware returns `OK` for a
   clamped write. Always read the value back.
4. **`attributes.cgi` is the authority** on names, legal values and
   verbs — `curl -s --digest -u admin:PASSWORD
   http://<cam>/stw-cgi/attributes.cgi/media`. It takes a path, not an
   `msubmenu` query. Fetch it once, save it, grep it locally.

**GOV must move with the frame rate, in the same breath.** The copy
engine cuts the ring into 1-second segments and ffmpeg's segment muxer
can only break on a keyframe. At GOV 60 / 30 fps a keyframe arrives
every TWO seconds, so every "1 second" segment is really two and clip
starts quantise to 2 s. At 60 fps it is one per second. If the rate is
ever lowered again, lower GOV with it.

**60 fps costs a stop of light**, made up with gain, and `AGCMode` is
already `High`. Fine in daylight — look at a real dusk clip before
assuming.

**Restart the agent afterwards.** Changing a profile the agent is
streaming drops it, and the sensor change restarts the whole pipeline.
It reopens on its own after `stall_reopen_seconds` (15 s), but restart
it anyway so it opens cleanly against the new profile rather than
whatever it reconnected to mid-change.

*Check:* read the values back, then read the **card**, not the API. The
three numbers from Step 0 should now all say 60.

---

## Step 3 — `config.yaml`

The role is **not** in this file — the backend decides it from the
`auth_token` at the first heartbeat — so the two rigs' configs differ
in fewer places than you would think. The reliable way to get parity is
to start from the tee's file rather than editing the green's:

```bash
# on the tee
sudo cat /opt/golfreelz-agent/config.yaml > /tmp/tee-config.yaml
```

Copy it to the green, then change **only** these:

| key | green's value |
|---|---|
| `auth_token` | the GREEN camera's token from `/admin/cameras` |
| `camera.device` | the green camera's RTSP URL (its own IP) |
| `camera.substream_device` | same, if the tee sets one |
| `tee_box_roi` | **delete** — tee-only |
| `audio` | **delete** — the mic is on the tee |
| `camera.fps` | 60, to match what Step 2 just set |

And one judgement call that is not a straight copy:

**`upload_scale_height`.** The green defaults to **720 when the key is
absent** — unlike the tee, where absent means off. So omitting the line
does not turn downscaling off on a green, it turns it on. The only
form that means "off" on both roles is an explicit `upload_scale_height: 0`.

That 720 was deliberate: the dual-camera composite downscales to 720
anyway, so uploading 1080 was wasted bandwidth. **Two things have
changed since.** The bandwidth argument is gone — the dongle move made
upload 97× faster. And the green's footage is no longer only cosmetic:
the descent search reads it to find where the ball landed, and a ball
forty yards out is about three pixels across at 1080 and under two at
720. Pixels it does not have are pixels the detector cannot use.

So for parity **and** for detection, set it explicitly:

```yaml
upload_scale_height: 0
```

If green clips later turn out to be too slow to send, that is the line
to put back — not the frame rate.

**What 1080p60 costs on the green**, before you commit to it:

- The pre-roll ring holds `buffer_seconds * fps` **frames** — ~1.9 GB
  at 1080p60 with `buffer_seconds: 5`. Fine on an 8 GB Pi 5, not on a
  4 GB one. Under `capture_engine: copy` the segment ring on disk is
  the real pre-roll, so the RAM one is cut to
  `copy_fallback_preroll_seconds` (2.0 s).

  **That cut used to apply to the TEE ONLY** — the rule lived in
  `tee.py` and `green.py` never called it, so a green on the copy
  engine held the full 1.9 GB for a ring nothing but its decode
  fallback reads, while also holding the segments on disk. Fixed on
  19 Oct, in time for this switch; it needs `update.sh` on the green to
  take.
- Under `copy` there is no decode and no re-encode, so the software
  encoder's ~31 fps ceiling at 1080p does not apply. **Under `decode`
  it does**, and a camera set to 60 gives you about half of it.

  **CONFIRMED 19 Oct: the tee runs `copy`, the green ran `decode`.**
  Every rate on the green's card read 60 — opened, delivering, stamped,
  config — and the clips were still wrong, because not one of those
  four is about the Pi. A decode-path agent fed 60 fps writes ~31 fps
  of frames into a file stamped 60, and that plays at half speed. The
  card now has a **"Recorded by"** row for exactly this; the agent had
  been reporting the field on every heartbeat and the page never read
  it.

      capture_engine: "copy"
      copy_segment_seconds: 1.0        # must match the camera's GOV

  Raising the green 30 -> 60 while it is still on `decode` makes this
  WORSE, not better: at 30 the encoder could keep up.
- `copy_window_seconds` is an age, not a size, and the segments are at
  whatever bitrate the camera sends. An hour at 4000 kbps is 1.8 GB,
  and the 2 GB upload spool shares the filesystem.

*Check:* `sudo systemctl restart golfreelz-agent`, then the journal's
first lines name the engine, the rate and the ring size.

---

## Step 4 — Prove it, on a real capture

Config read back is not proof; the camera has lied twice. Trigger a
capture and check the produced row:

1. **Frame rate** on the production card reads **60 fps**, matching the
   tee's row.
2. **Frames ÷ length ≈ 60.** If they agree with each other but not with
   reality — the clip plays fast or slow against a stopwatch — the
   config's rate and the delivered rate still disagree. Back to Step 0.
3. **Playback is smooth.** Choppy with a correct frame count means
   frames are arriving unevenly, not that the rate is wrong.
4. **Upload time is sane.** A 23 MB clip went up in ~14 s on the fixed
   tee. Minutes means Step 1b or 1c did not take.

---

## What is deliberately NOT copied

- **`audio`** — one mic, on the tee. The green has no audio input
  configured and `extra_lead_seconds` is meaningless without one.
- **`tee_box_roi` / trigger zones** — the green does not detect people
  or fire triggers; it long-polls for the tee's.
- **`audio.in_segments`** — leave `false` on both. It produces SILENT
  clips on this camera; the regression is documented and unfixed.

---

## Related

- [`camera-triage.md`](./camera-triage.md) — why each of these exists,
  and the measurements behind them
- [`field-deployment.md`](./field-deployment.md) — placement and BOM
