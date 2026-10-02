# Camera triage — a trigger fires but no clip arrives

Field runbook for the case where a camera is **online and triggering but
its clip never reaches the backend**. Written to be followed by someone
who wasn't in the debugging session — read it top to bottom, stop when
you get an answer.

**Current open case (Baldwin Links, hole 3):** the TEE camera (#1)
triggers, stays online, and its live view works intermittently. The GREEN
camera (#2) works perfectly. Every event parks with **TEE ✗ never
arrived · GREEN ✓ arrived** and nothing reaches Production.

---

## First: read the panel, don't guess

**`/admin/cameras` → Camera events** (bottom of the page). Tick
**"Only stuck / failed"**.

Each event shows two chips. They are the whole diagnosis:

| Chips | Meaning |
|---|---|
| TEE ✗ · GREEN ✓ | Tee Pi is failing to upload. **This is the open case.** |
| TEE ✓ · GREEN ✗ | Green never uploaded; the tee-only fallback should have produced anyway after 3 min |
| Both ✗ | Neither Pi uploaded — suspect the backend or the network, not one camera |
| Both ✓, stuck at "Both clips in" | Upload is fine; the produce queue is stalled |

Two traps worth knowing:

- **A green-only event can never produce.** The tracer needs the tee
  view, and the tee-only fallback filters on `tee_clip_filename`, so
  nothing sweeps it. It sits forever. Delete those; Re-process cannot
  help, because the tee clip does not exist anywhere.
- **"Processed" does not mean a clip was made.** A capture with no
  detectable swing is deliberately marked processed. Check Production
  for the actual row.

---

## Step 0 — You cannot SSH in at all

Every step below starts "SSH into the affected Pi", so when that is the
thing that is broken the runbook has nothing to say. It does now.

**Do not start with the network. Start with the card**, because the Pi
answers a question SSH cannot: each camera's `last seen` on
`/admin/cameras` is bumped by any successful call, and the agent polls
`watch-status` every second. So it is current to within a second or two,
and it splits the problem in half for free:

| `last seen` says | What is true | Where the fault is |
|---|---|---|
| **live** (under 150s) | The Pi is running, its modem is up, it is reaching the backend right now | NOT the Pi and NOT the link. Something between you and it: the tailnet, sshd, the address you used |
| **late** (150s–15min) | It was there recently | Probably a flapping link — try again, and read the modem notes below |
| **down** / never | The agent is not reaching the internet | The Pi, its power, or its modem. Nothing can be fixed remotely; this is a drive |

Each Pi's only uplink is its own LTE USB modem, so when the modem is
gone, SSH and the heartbeat are gone together. **A camera that is still
heartbeating while SSH times out is therefore a tailnet problem, not a
camera problem** — which is also the most common one, because:

> The tee's uplink is a USB cellular modem that reboots itself every
> ~30 seconds under load: alive ~23s at ~125 KB/s, then gone ~8s while
> it re-enumerates.

That is from the comment on the resumable-upload code, which exists
*because* of it. Short bursts (a heartbeat, a status poll) cross it
fine. A held TCP connection does not, and `ssh` is a held TCP
connection. Expect to retry, and expect a session to die mid-command.

**If the card says live and SSH still times out:**

```bash
tailscale status | grep -i golfreelz   # from any machine on the tailnet
```

The address may simply have moved — a node that re-registers can come
back on a different 100.x, and `tailscale status` prints the current
one. The hostname only works if MagicDNS is enabled on the tailnet AND
the machine you are sitting at is using Tailscale's resolver, which on
Windows it often is not:

```bash
ssh pi@golfreelz-tee        # "Could not resolve hostname" => use the IP
ssh pi@100.81.62.127        # always works
```

If the node shows as offline in `tailscale status` while the camera is
heartbeating, Tailscale on the Pi has lost its connection while the
modem kept working. There is no remote fix — the agent's command
channel carries lens and exposure commands, not a shell — so it is a
site visit, or a reboot by whatever out-of-band means the mount has.

**Working over the modem once you are in.** Do not try to hold a session
open for the length of a job. What the link looks like in practice:

```
$ ssh pi@golfreelz-tee
pi@golfreelz-tee:~ $ client_loop: send disconnect: Connection reset
$ ssh pi@golfreelz-tee
ssh: connect to host ... port 22: Connection timed out      # re-enumerating
$ ssh pi@golfreelz-tee                                       # back
```

So **launch the work detached and let the session die.** `setsid` is in
coreutils and is on every Pi; `tmux` and `screen` are not installed, and
apt-getting one over this link is the problem you are trying to avoid.
`sudo -v` first so the password prompt happens while you are still
attached — a backgrounded sudo cannot ask.

```bash
sudo -v                                   # password once, ~2 seconds
sudo setsid bash -c '/opt/golfreelz-agent/update.sh' \
  >/tmp/update.log 2>&1 </dev/null &
```

Then let it drop. Reconnect whenever and read the result:

```bash
tail -40 /tmp/update.log
systemctl is-active golfreelz-agent
```

Two flags worth putting in front of every `ssh` to this host. The
default connect timeout is about two minutes, which makes retrying
hopeless; ten seconds makes it a reflex. The keepalives hold the flow
open in the modem's NAT table across a re-enumeration instead of
letting it be forgotten:

```bash
ssh -o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=24 \
    pi@golfreelz-tee
```

`update.sh` already fetches `pi-agent/` as a sparse, blob-filtered
checkout for the same reason: a plain clone pulls ~100 MB, which over
this link once took ten minutes and had to be abandoned.

---

## Step 1 — Is it power? (2 minutes)

SSH into the affected Pi and run:

```bash
vcgencmd get_throttled
```

| Result | Meaning | Next |
|---|---|---|
| `throttled=0x0` | **Power is clean.** | Skip to Step 2. The battery is not the problem — don't swap it. |
| bit 0 set (`0x1`) | Under-voltage **right now** | Step 3 |
| bit 16 set (`0x50000`, `0x10000`…) | Under-voltage **has happened** since boot | Step 3 |

Confirm with:

```bash
dmesg | grep -i -E "under-?voltage|throttl"
uptime          # a recent boot means it power-cycled
```

**Run this before doing anything else.** If it returns `0x0`, power is
ruled out and a bigger battery would waste a day.

---

## Step 2 — What does the agent actually say?

```bash
journalctl -u golfreelz-agent -n 200 --no-pager
journalctl -u golfreelz-agent | grep -iE "upload|timeout|error|traceback" | tail -30
```

Look for: upload timeouts, HTTP errors, tracebacks, ffmpeg failures,
`No space left on device`.

Also check the scratch space — **`work_dir` defaults to
`/tmp/golfreelz-agent`, which is tmpfs (RAM)**:

```bash
df -h /tmp
free -h
```

If tmpfs is full, recordings fail to write and there is nothing to
upload. Fix by pointing `work_dir` at an SD-card path in `config.yaml`.

---

## Step 3 — Voltage under load, not at idle

This is the measurement that matters, and the one usually skipped.

Multimeter on **DC volts**. Probe the **converter's input** (the 12V
side) **while the Pi is uploading** — trigger a swing, or at minimum
catch it during boot.

| Input under load | Meaning |
|---|---|
| Holds 12 V+ | Battery and wiring are fine. The converter is the weak link — swap in the spare. |
| Sags below ~10 V | Power delivery problem: battery, or a resistive joint |

Then measure the **converter output** the same way. It must stay
**5.0–5.2 V** under load. A supply that reads 5.1 V at idle and sags to
4.6 V during upload looks perfect on a bench test and fails in the field.

### Why idle readings lie

`V_drop = I × R`. A 0.5 Ω bad joint drops 0.2 V at idle — invisible — and
1 V at 2 A, which browns out a Pi. **Same joint, same battery, opposite
outcome.** Upload is the peak-current moment: H.264 encode plus radio
transmit together.

---

## Step 4 — Swap the harnesses

The single most informative test, and it needs no instruments.

**Move the tee camera onto the green camera's harness** (battery, fuse,
cable, converter — the whole chain).

| Result | Conclusion |
|---|---|
| Tee camera now uploads fine | **It's the harness**, not the Pi and not the battery. Rebuild the tee harness. |
| Tee camera still fails | It's the Pi, its SD card, or its config — not power |

The green camera working perfectly on identical hardware is already
strong evidence that the *design* is sound and something specific to the
tee build is wrong.

---

## Step 5 — Swap the MODEMS (when the symptom is load-correlated)

Step 4 swaps the power chain. This swaps the radio, and the two
together separate every remaining candidate.

**Reach for this when the fault tracks LOAD rather than time:** short
bursts cross fine (heartbeats, status polls, the live view) and
anything sustained dies within half a minute. That is the tee's
signature, in SSH and in clip upload alike — the same fault wearing two
hats, not two problems.

Why those three candidates are not independent:

```
weak signal (LOCATION)
      ↓  modem transmits at higher power
higher current draw (MODEM)
      ↓  5V rail sags under sustained TX
USB bus resets, modem re-enumerates (PI)
      ↓
"Connection reset", then ~8s of nothing, then fine again
```

A location problem *presents* as a power problem. So test the two ends,
not the middle:

| Swap | Fault follows the hardware | Fault stays at the tee |
|---|---|---|
| Modem (tee ↔ green) | That modem unit is bad | The spot, or the harness |
| Harness (Step 4) | The tee harness is bad | Not power |

Both staying at the tee, with the green flawless on identical kit,
means **the location** — and the fix is an antenna or a mount position,
not another part.

### Measuring it without a site visit

The cheapest evidence needs no SSH at all. **`/admin/cameras` shows the
tee's battery voltage and current**, reported on every heartbeat from
the INA226 on its 12 V feed. Watch it while a clip uploads: a rail that
reads fine at idle and sags under load is the brownout, seen from a
laptop.

One SSH command settles whether the modem is really re-enumerating
rather than the carrier dropping the bearer:

```bash
dmesg -T | grep -iE "usb (disconnect|reset)|new (high|super)-speed" | tail -30
vcgencmd get_throttled     # 0x0 here while dmesg shows USB resets
                           # => NOT the Pi's power. Look at the modem.
```

And on site, the LM1200's own admin page (usually `192.168.5.1`) reports
RSRP / RSRQ / SINR. Compare the tee mount against the green mount: that
is the location question answered in two minutes with a phone.

---

## Decision tree

```
get_throttled == 0x0 ?
├── YES → not power. Go to journalctl (Step 2).
│         Likely: upload timeout, tmpfs full, agent crash.
└── NO  → it has browned out.
          Measure converter INPUT under load (Step 3).
          ├── holds 12V  → converter is weak. Swap the spare.
          └── sags <10V  → swap harnesses (Step 4).
                           ├── fixed  → tee harness is bad. Rebuild.
                           └── still  → battery genuinely undersized/flat.
```

---

## Known-good reference

- Converter output: **5.0–5.2 V**, at idle *and* under load
- Fuse: **5 A**, standard ATC (tan), within ~6" of the battery
- Pi 5 LED: red = powered but not booting (usually SD card); green
  activity = booting normally
- `vcgencmd get_throttled` on a healthy rig: `throttled=0x0`

## Report back

- `vcgencmd get_throttled` output
- Last 30 lines of `journalctl -u golfreelz-agent`
- Converter input and output voltage **under load**
- Whether the harness swap changed anything

That set is enough to name the cause without another site visit.

---

## Related

- [`battery-power-wiring.md`](./battery-power-wiring.md) — the full power
  build, terminal sizes, and the wiring order
- [`field-deployment.md`](./field-deployment.md) — hardware BOM and
  placement
