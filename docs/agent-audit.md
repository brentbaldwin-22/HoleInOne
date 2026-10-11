# Pi agent audit — what it costs, what is dead, what to cut

Written 11 Oct, in answer to a fair question: after two camera
generations and a mic about to be added, has the agent accumulated
enough bolted-on functionality to be worth wiping and rebuilding —
and could any of it be causing the connectivity trouble?

**Short answers: no, and no.** But two real savings came out of it,
both config rather than code, and one of them is large.

---

## 1. There is no dead code

Every module is imported and reached:

| module | lines | reached by |
|---|---|---|
| `common.py` | 3802 | everything |
| `tee.py` | 1380 | entry point |
| `green.py` | 593 | entry point |
| `copycap.py` | 495 | copyengine |
| `copyengine.py` | 356 | tee, green, common |
| `livestream.py` | 300 | tee, green |
| `curfew.py` | 246 | entry point |
| `tee_roi.py` | 165 | tee |
| `focus_meter.py` | 126 | tee, green |
| `battery.py` | 114 | tee, green |

7,772 lines total, half of it `common.py`. Nothing orphaned.

**The decode engine is NOT cruft.** It is retained on purpose, and
`copyengine.py:5` says why: a rig that misbehaves must be revertible
by editing one line of YAML rather than by rolling back code over a
cellular link that may not hold an SSH session. On this deployment
that is worth its weight.

**The ML stack is already lean.** The production detector is YOLOv8n
through OpenCV's own DNN module — a 13 MB ONNX file, no torch, no
ncnn. MediaPipe is an OPTIONAL secondary, used only if the YOLO model
is missing, and `requirements.txt` gates it to Python < 3.13. On
Trixie it is almost certainly not installed at all, which makes the
systemd unit's "MediaPipe can use ~500 MB" note moot in practice.

---

## 2. The agent is not causing the connectivity trouble

Everything it sends when idle:

| call | cadence | notes |
|---|---|---|
| `GET /watch-status` | **every 1 s** | `livestream.py:59` — the only continuous traffic |
| `POST /heartbeat` | every 60 s | carries telemetry, link_info capped at 900 chars |
| `POST /still` | only when the SERVER asks | gated on `still_wanted`, floor 15 s |
| `POST /live-frame` | only while someone is watching | |
| `poll-trigger` | green only, long-poll | |
| uploads | event-driven | |

At roughly 1 KB round trip, the 1 Hz poll is about **1 KB/s
sustained** — 1% of a healthy 93 KB/s link, ~9% of an 11 KB/s bad
patch. Annoying at the bottom, never causal.

**And no amount of userspace traffic can cause what was actually
observed.** USB bus re-enumeration is a kernel event, and
`[Errno 101] Network is unreachable` is the interface disappearing.
The fault followed the modem through a simultaneous two-direction
swap, and USB resets went 41 -> 0 when the modem was changed. A
rewrite would not have touched any of it.

Worth relaxing the idle poll to 2-3 s eventually. It is not a fix for
anything; it is tidiness. Note the cost side: 1 Hz is what makes
`last seen` accurate to the second, which is the single diagnostic
that repeatedly split "the Pi is dead" from "the tailnet is dead".

---

## 3. REAL FINDING: 60 fps doubled the RAM pre-roll, silently

`preroll_seconds()` keeps `copy_fallback_preroll_seconds` (default
**2.0 s**) of DECODED frames in RAM under the copy engine — the
fallback depth for when the segment ring is unhealthy.

A 1920x1080x3 frame is 5.93 MB. So:

| sensor rate | frames held | RAM |
|---|---|---|
| 30 fps (this morning) | 60 | **356 MB** |
| 60 fps (now) | 120 | **712 MB** |

Raising the sensor to 60 fps doubled it, and nothing said so. Still
fine on an 8 GB Pi 5, but the comment that justified the 2.0 default
was written when it cost half as much. If RAM ever gets tight, this
is the first dial.

---

## 4. REAL FINDING: detection still decodes the full 1080p60 stream

From `copyengine.py:19`:

> WHAT DOES NOT CHANGE. Detection. **Without a substream configured**
> there is still only one stream worth decoding.

So the agent decodes 1920x1080 at 60 fps, continuously, forever — to
feed a detector that runs at `detection.fps: 5` and that YOLO
downscales to 320 px wide regardless. Almost all of that work is
thrown away.

**The camera is already emitting exactly the right stream and nobody
is using it.** From its own SUNAPI dump:

```
Channel.0.Profile.10.Name        = MOBILE
Channel.0.Profile.10.Resolution  = 320x240
Channel.0.Profile.10.FrameRate   = 10
```

320x240 at 10 fps, for a detector that wants 5 fps at 320 px. Point
`camera.substream_device` at `/profile10/media.smp` and:

- the 1080p60 stream is **never decoded at all** — it is copied to
  disk by the segment ring and copied out again;
- the pre-roll ring holds 320x240 frames: **712 MB -> ~4 MB**;
- CPU and therefore heat drop sharply (the card has been reading
  64-66 degC).

**This is the largest available saving in the whole system, and it is
one line of YAML.**

**THE TRADE-OFF, STATED PLAINLY.** The decode fallback records from
that RAM ring. With a substream configured, a clip that falls back
because the segment ring was unhealthy would be **320x240**, not
1080p. Today that path produces a degraded-but-usable clip; after
this change it produces a thumbnail. Measure how often the fallback
actually fires before taking the saving — on a rig where it never
fires, this is free.

---

## 5. What to do

1. **Do not wipe and rebuild.** The value in this tree is not the
   code, it is the scar tissue: the USB-reset counter that proved the
   modem, the opened-vs-delivering pair that caught the camera
   claiming 60 and sending 30, `agent_build_id()` that answers "did
   the fix land", the `expire_on_commit` comment that explained a
   Postgres failure in seconds. A rewrite rediscovers each of those
   at a golf course, in weather, with golfers waiting.
2. **Try the substream** (section 4), after checking how often the
   decode fallback fires.
3. **Know what 60 fps costs in RAM** (section 3). No action needed
   yet.
4. **Relax the idle poll** to 2-3 s if you want it, knowing what it
   buys.

Nothing here is a connectivity fix, because connectivity was never
software. That was settled with a modem swap and a reset counter.
