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

## ANSWERED: IT WAS WHERE THE DONGLE WAS SITTING (16 Oct)

The tee's dongle was moved a few feet, next to a window. Same rig,
same SIM, same MTU, same hour.

| | before, old spot | **after, by the window** |
|---|---|---|
| **upload** | 17,474 B/s | **1,696,346 B/s** (**97x**) |
| download | 581 KB/s | 1,120 KB/s |
| RTT | 70 ms | 58 ms (min 49) |
| loss | 0% | 0% |

**CONFIRMED ACROSS A REBOOT**, which this page has learned to insist
on: a second run after a full power cycle gave **1,627,211 B/s** up
and 1,429 KB/s down — within 4% of the first on upload. Two readings,
one power cycle, same answer. Against the worst reading of 15 Oct
(2,184 B/s) it is 745x.

**Both transfers COMPLETED.** Every earlier test in this
investigation timed out at 30 s having moved a fraction of its
payload; this one pushed 10 MB in five seconds. Upload is now faster
than download. A 23 MB clip goes from 22 minutes to about 14 seconds.

### Why it hid for a month

**Uplink is power-limited at the device.** The tower transmits with far
more power than a USB dongle can, so a marginal RF path cripples
upload while download stays healthy. That asymmetry is the whole
signature, and we kept measuring it and kept explaining it away as
something else:

| Theory | How it died |
|---|---|
| USB current cap | real, fixed 14 Oct, resets went to 0 — and uploads stayed broken |
| SIM expired | real, refilled 15 Oct — and uploads stayed broken |
| upload chunk ratchet | real bug, fixed in bed67c6 — and uploads stayed broken |
| weak RF (first pass) | killed by a run with 0% loss, 74 ms and 511 KB/s down... which measured the DOWNLINK |
| MTU black hole | real, fixed — upload went 3,440 -> 6,553 B/s, i.e. nothing |
| carrier uplink shaping | **killed by this**: no token bucket lifts 97x because a device moved a few feet |

**MTU is cleanly excluded as the cause**, which is what makes this
conclusive rather than suggestive: 1440 was set in the OLD position on
15 Oct and upload stayed at 3,440-6,553 B/s. Same MTU, new position,
1.7 MB/s. Position is the only variable.

### The lesson worth keeping

**A healthy downlink says nothing about the uplink, and every
convenient measurement we had was a downlink measurement.** Ping, the
heartbeat, SSH responsiveness, the live view, `speed.cloudflare.com`'s
default test — all of them look fine on a link that cannot push a clip.
When uploads are the symptom, measure uploads, and treat a good
download as no evidence at all.

Corollary for this deployment: **the dongle's position is a
first-class piece of hardware configuration.** Write down where it
goes, and re-measure upload after anyone touches the rig.

---

## STEP MINUS TWO — CAN THE LINK CARRY A FULL-SIZE PACKET? (15 Oct)

> **READ THIS FIRST: on the tee, this was REAL AND NOT THE CAUSE.**
> The black hole below is genuine -- 1472 lost every packet, 1412 lost
> none -- and the MTU should be set correctly regardless. But setting
> it to 1440 did NOT restore upload throughput: 6,553 and 3,440 B/s,
> indistinguishable from the 2,184-8,737 B/s before the change. It was
> a second fault found while hunting the first.
>
> Fix it when you find it. Do not stop looking because you did.

**Downloads fine and uploads dead is an MTU problem until proven
otherwise.** Three commands, no guessing:

```bash
ip link show eth1 | grep -o 'mtu [0-9]*'
ping -M do -s 1472 -c 3 8.8.8.8    # needs a full 1500 MTU
ping -M do -s 1400 -c 3 8.8.8.8    # needs 1428
```

`-M do` forbids fragmentation, so each packet arrives whole or not at
all. **1472 failing while 1400 succeeds is the diagnosis.** Measured on
the tee, 15 Oct: the interface was set to 1500, 1472 lost 100% of
packets, 1400 lost none.

### Why it is invisible without that test

The path drops oversize packets **silently** -- no ICMP
"fragmentation needed" comes back -- so TCP never learns and simply
stalls. Everything small keeps working, which is most of what anyone
looks at:

| What you see | Why |
|---|---|
| ping, SSH, heartbeats all fine | small packets |
| **download 511 KB/s, the best ever recorded here** | the server's path to the Pi is unaffected |
| upload sends a burst, then nothing | TCP scales up to full-size segments, which vanish |
| upload byte counts are exact powers of two (64/128/256 KB in 30 s) | the socket buffer absorbed that much and then the flow wedged |
| the uploader's smaller chunks limp while big ones time out | smaller writes keep more segments under the limit |

**That power-of-two byte count is the tell to remember.** A link that
is merely slow transfers arbitrary numbers of bytes. A clean 65536 or
131072 means a buffer filled and the wire stopped.

### What it is NOT, and how we wasted a day proving it

The same rig, in the same hour, read: **USB resets 0**, `POWER CLEAN`,
60.1 fps delivering, SIM paid up, and on one run **0% packet loss with
74 ms RTT and 511 KB/s down**. Upload was 8.7 KB/s on that run. A
healthy radio and a dead uplink at the same moment rules out signal,
congestion and power together -- so when every hardware reading is
good, stop testing hardware.

Three explanations were offered and knocked down by measurement before
this one stuck: the upload chunk ratchet (a real bug, fixed, but not
this), weak uplink RF (killed by the 0%-loss run), and a carrier data
cap (that was the SIM expiry earlier the same day, a separate fault).

### The fix

Find the largest payload that gets through, add 28 for the IP+ICMP
headers, and set that as the MTU:

```bash
for s in 1472 1452 1442 1432 1422 1412 1400; do
  printf "payload %5d (MTU %5d): " "$s" "$((s+28))"
  ping -M do -s $s -c 2 -W 2 8.8.8.8 >/dev/null 2>&1 && echo OK || echo FAIL
done
sudo ip link set eth1 mtu 1428       # whatever the ladder says
```

`ip link set` does NOT survive a reboot or a modem re-enumeration, and
this dongle re-enumerates. Make it permanent for whichever stack the
rig runs (`systemctl is-active NetworkManager dhcpcd`).

### Is it new?

Probably. This deployment has historical upload readings of 96-144
KB/s, which cannot happen through a black hole, so this is not a
months-old fault that was missed. The likeliest trigger is a plan or
APN change routing through a gateway with different tunnel overhead --
the tee's SIM was refilled hours before this appeared. **Re-run the
1472 test after any SIM or plan change.**

### BOTH RIGS, SIMULTANEOUSLY: IT IS NOT OUR HARDWARE (15 Oct, 17:43)

The control this page had been missing. Same block run on the tee and
the green within seconds of each other -- simultaneity matters, because
three earlier conclusions on this page were drawn from sequential
readings on a link that moves 10x by itself, and all three were wrong.

| | tee | green |
|---|---|---|
| dongle | one EIOTCLUB | **a different one** |
| SIM | `...856138` | **a different SIM** |
| Pi | one | **a different Pi** |
| camera | yes | **none attached** |
| usb_max_current_enable | 1 (fixed 14 Oct) | **not applied** |
| MTU 1472 / 1412 | FAIL / OK | **FAIL / OK** |
| download | 46-511 KB/s | 79 KB/s |
| **upload** | **2,184-8,737 B/s** | **2,184 B/s** |

The green moved **exactly 65,536 bytes in 30 seconds** -- the same
power-of-two burst-then-stall as the tee, to the byte.

**ELIMINATED, ALL AT ONCE:** both dongles, both SIMs, both plans, both
Pis, the camera load, the agent, and yesterday's USB current fix. Two
rigs sharing nothing but a location and a carrier fail identically.
**No hardware swap at the course can help, and the 11 Oct "it is the
radio unit" conclusion does not apply to THIS fault** -- that test
measured a rig against a healthy one, and here there is no healthy one.

It also reframes the MTU black hole above: **both rigs fail at 1500**,
so that is the carrier's tunnel overhead (the BICS APN's
encapsulation), not a per-rig fault. Set 1440 on every rig.

What is left needs no code and no parts:

1. **TIME OF DAY, and it is free.** Every reading in this section was
   taken between 1pm and 5:43pm. "Uploads crawl during the day and
   push through in the late evening" is the oldest observation on this
   page, and the 96-144 KB/s figures being compared against may all be
   evening ones. **Re-run both rigs at 9-10pm before anything else.**
2. **A phone speed test standing at the tee box.** Same location,
   different carrier. A phone doing megabits where both dongles manage
   2 KB/s means the EIOTCLUB service is the problem; a phone that also
   crawls means the tower is.
3. **One dongle and SIM taken elsewhere.** Same hardware, different
   location -- the clean separation of carrier from tower.

### What was still broken after fixing it

Upload stayed at 3-7 KB/s against a 511 KB/s download, still stalling
after a power-of-two burst (192 KB, then 64 KB and a "Connection reset
by peer"). A burst that runs then collapses to a trickle is the shape
of a **token bucket**, which points at carrier uplink shaping rather
than anything on the Pi.

Three candidates remained, and all three need a site visit to settle,
because each is a swap:

1. **The plan or APN.** `america.bics` is a BICS aggregator APN and has
   never been checked on this deployment. The spare RiverTowne SIM
   (15 GB, active, unused) swapped into the tee dongle is a clean A/B:
   same hardware, same spot, different account.
2. **The radio unit.** The 11 Oct section below concluded the
   THROUGHPUT fault followed the modem in a two-direction swap. This is
   a throughput fault. That conclusion was never retired, and it was
   the USB-reset fault -- a different one -- that turned out to live
   with the Pi.
3. **Time of day.** "Uploads crawl during the day and push through in
   the late evening" is the oldest and most reliable observation on
   this page, and every reading here was taken in the afternoon. The
   96-144 KB/s figures this is being compared against may all be
   evening ones. **Re-measure at night before swapping any hardware.**

That last one is free, so do it first.

---

## STEP MINUS ONE — IS THERE DATA ON THE SIM? (15 Oct)

**Do this before anything else on this page.** It takes thirty seconds,
it needs no SSH and no site visit, and on 15 Oct it was the answer to a
fault we had chased through USB resets, power rails, modem swaps, buck
converters and two Raspberry Pis.

Log in at **sim.eiotclub.com** and read the SIM's card:

| Reading | Meaning |
|---|---|
| `Activated`, GB remaining | the account is fine — carry on down this page |
| **`Suspended`** | **STOP. The plan is dead. No amount of triage fixes this.** |
| `0 / 0 MB` | there is no package on this SIM at all |
| Expire Date in the past, or today | the plan ran out by TIME, which it does whether or not the data was used |

### What it looks like from the outside, and why it fools you

The tee's plan expired on 15 Oct. The symptoms were, in order:

1. The first few uploads of the morning went through fine
2. Each successive upload was slower than the last
3. One stalled dead at 97.1% and never moved
4. The heartbeat stopped **at the same instant** as the upload
5. **USB resets: 0.** `POWER CLEAN`. 60.1 fps delivering. Every piece
   of hardware reporting perfect health

Points 2 and 3 are what make this worth a section. A plan running out
does not look like a switch being thrown; it looks like **progressive
degradation**, which is the exact signature of a weak radio, a failing
modem, or a starved USB port — all three of which this page spends
pages on, and all three of which we chased.

Point 5 is the tell, and it is the one to learn. **A rig whose every
hardware reading is healthy while its throughput dies is an ACCOUNT
problem, not a hardware problem.** The modem never re-enumerated. The
Pi never browned out. The camera never dropped a frame. Nothing was
broken, and that is information rather than a dead end.

### The ICCIDs collide — read them carefully

Two of the SIMs in this deployment differ by one digit in the
seventeenth position:

```
RiverTowne   8932042000021856238
tee          8932042000021856138
                           ^
```

A third (`...21458188`) is also suspended and expired months ago.
Checking the wrong card is easy and tells you the opposite of the
truth: on 15 Oct the first SIM read **15 of 15 GB unused**, which
briefly looked like proof that data was NOT the problem. It was a
different SIM.

### The dongle's LED, which needs no tools at all

Worth knowing because it is readable by anyone standing at the rig,
and because it is easy to over-read. Per EIOTCLUB's own guide for the
CLR907, **green blinks for three different states** and the DUTY CYCLE
is what separates them:

| Pattern | State |
|---|---|
| ~200 ms on, 1800 ms off (brief winks, mostly dark) | searching / no SIM / not registered |
| ~1800 ms on, 200 ms off (mostly lit, brief gaps) | registered, standby -- ready |
| ~200 ms on/off, fast and even | data transferring |
| blue + green steady | just powered on |
| **no light** | power problem -- check the USB supply |

So "it was flashing green" is NOT a diagnosis. On 15 Oct it was
reported alongside a suspended SIM and is consistent with the
searching state, but the same colour means "registered and fine" with
the timing inverted.

What it does settle: the dongle has power and is alive. No light would
have pointed back at the USB supply.

**Its real use is confirming a refill.** After buying a package, watch
the LED: mostly-lit with brief gaps, or fast even blinking, means
registered and carrying data. Still mostly dark means the refill did
not take. That is a check with no SSH, no browser and no laptop.

Model caveat: the table above is the CLR907's. A CLR903 or M47 may
differ -- a mismatch means read the printed manual, not that something
is broken.

### Prevention

- **The expiry is a date, not a balance.** A 30-day package dies on day
  30 with data left on it. Diary the expiry, or the rig goes dark
  mid-round with everything reporting healthy.
- **Swing select is a data decision, not just a bandwidth one.** It
  cuts a session to the ~20 s around each swing -- measured at 26%
  coverage on this deployment's own footage, with every swing kept. On
  a metered SIM that is roughly four times as much golf per gigabyte,
  at no cost in quality, because it drops the walking rather than
  compressing the swing.
- Sizing, at the clip sizes this deployment actually produces (11-28 MB
  for 36-40 s): a 15 GB month is about **830 clips**, or ~3,300 with
  swing select on. A 24 GB / 360-day package is less than two months of
  that spread over a year -- it is a plan for a spare SIM, not a
  working camera.

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

**Is the link busy, or is it broken?** Both look like a timeout from
your end, and the difference decides whether you wait or drive out. The
camera card answers it without an SSH session: a **`⇡ N clips owed`**
pill appears whenever a camera has triggered clips the server has not
received. The server derives it from the events table — a row is
written when the tee triggers, the filename is filled in when the clip
lands — so it works on any agent old enough to trigger at all,
including the ones too far behind to report their own queue depth.

Read the trend, not the number:

| The pill | What it means | What to do |
|---|---|---|
| absent | Nothing outstanding | A timeout here is not congestion. Look at the tailnet |
| present and **falling** | Uploads are landing; the uplink is just full | Wait. It will clear on its own, and `ssh` will start holding |
| present and **stuck** | Clips are not moving at all | The agent, the modem, or the power — not a busy link |
| **red** (oldest >4h) | Near `upload_spool_max_age_hours` (24h) | These may never arrive; the spool deletes rather than retries past that |

A stuck count does not always mean a clip is still in flight: a Pi that
lost power mid-recording, or whose spool hit `upload_spool_max_mb` and
evicted the file to make room, leaves exactly the same row behind. So a
number that will not come down is a reason to go and look, not a reason
to keep waiting.

**Many clips each part-uploaded is a different shape from one clip
stuck.** On 7 Oct the tee showed nine clips at 10–25%, each stalled for
over an hour, none finished — which looks like parallel uploads and is
not: the uploader is a single thread and the spool is strictly
oldest-first. Two ordering bugs produced it, both now fixed. The shrink
ladder re-encoded a clip, which rewrote the file and so its mtime, and
the spool ordered and expired by mtime — so the clip it had just made
sendable sorted to the back of the queue. And every fresh swing got an
attempt ahead of the whole backlog, because the backoff gate only
covered a link that had *failed*, not one that was merely slow. Clips
now carry a `spooled_at` in their sidecar JSON that a re-encode cannot
move, and a fresh clip joins the back of the spool rather than jumping
it. **If you see this shape again on an agent from 7 Oct or later, the
ordering is not the cause — read the agent log before assuming it is.**

**Shrinking the trigger zone is the brake that works on an old agent.**
Pausing a camera from the app stops the server writing event rows, but
only an agent carrying the `triggering_disabled` fix stops *recording
and uploading* for it — on anything older the Pi keeps filling the
uplink and you lose the swings for nothing. Triggering is gated
entirely by the zone (`_in_roi` → dwell → trigger), and the zone is
re-sent on the Pi's one-second status poll, so dragging the box into a
dead corner of the frame stops new clips within about a second on every
build. It costs the swings either way; the difference is that this one
actually frees the link so the backlog can drain. Drag it back when you
are done, and do not press **Capture** — that is exempt from the zone
by design.

**The 📶 pill answers the next question before you ask it.** If the
card says live, the uplink panel already tells you whether the link is
weak, capped, or re-enumerating — see *Reading the modem's signal*
below. That is the one part of this runbook that no longer needs a
shell.

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

**Which capture mode?** Read it off the card, do not assume: the STREAM
block's "Opened as" is the geometry OpenCV really got, and `camera.mode`
in `config.yaml` should name the preset that matches it — `1080p30` for
1920x1080@30, `720p30` for 1280x720@30. Getting this wrong is harmless
to the footage (the clip is stamped at the measured rate either way) and
wrong for everything sized from it.

Then let it drop. Reconnect whenever and read the result:

```bash
tail -40 /tmp/update.log
systemctl is-active golfreelz-agent
```

**Get a key on first.** A password prompt is the one part of this that
needs a human inside the 20-second window, and it is why a retry loop
does not work. Pushing a key is a single short command — exactly the
shape that does get through:

```powershell
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh pi@<ip> "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```

(no key yet: `ssh-keygen -t ed25519` once, then the above.)

After that the whole update is one unattended command, and a loop can
keep firing it until one lands:

```powershell
for ($i=1; $i -le 40; $i++) {
  ssh -o ConnectTimeout=8 -o BatchMode=yes pi@<ip> `
    "sudo bash -c 'setsid /opt/golfreelz-agent/update.sh >/tmp/update.log 2>&1 </dev/null & disown; echo launched'"
  if ($LASTEXITCODE -eq 0) { "landed on attempt $i"; break }
  Start-Sleep 5
}
```

`sudo` will still want a password unless the service account has a
NOPASSWD rule for that one script — worth adding on a rig whose link
cannot hold a session long enough to type one. One narrow rule, and
`visudo -c` before you close the session that made it, because a
malformed sudoers file locks sudo out entirely and that one is a drive:

```bash
echo 'pi ALL=(ALL) NOPASSWD: /opt/golfreelz-agent/update.sh' \
  | sudo tee /etc/sudoers.d/golfreelz-update > /dev/null
sudo chmod 440 /etc/sudoers.d/golfreelz-update
sudo visudo -c          # must say "parsed OK"; if not, rm the file NOW
```

Check it from the laptop without running anything: `-n` never prompts,
`-l` only asks whether it would be allowed.

```powershell
ssh -o BatchMode=yes pi@<ip> "sudo -n -l /opt/golfreelz-agent/update.sh"
```

It echoes the script path back when the rule is live, and says a
password is required when it is not.

> **Done on the tee (hole 8) on 3 Oct 2026** — key plus NOPASSWD rule.
> That rig no longer needs a session you can hold; see the one-shot
> check below.

**ONE SHOT BEATS A SESSION on a link like this.** Everything worth
knowing can be asked in a single non-interactive command, which either
crosses or does not — no window to keep alive, nothing half-finished:

```powershell
ssh -o BatchMode=yes pi@<ip> "free -h; echo ---; uptime; dmesg -T | grep -icE 'usb (disconnect|reset)'; echo ---; ls /var/lib/golfreelz-agent/spool/*.mp4 2>/dev/null | wc -l; df -h /var/lib/golfreelz-agent | tail -1"
```

That answers, in order: whether the Pi has the RAM for its configured
pre-roll (1080p60 x 5s wants ~1.9GB), how often the modem has fallen
off the USB bus against how long the Pi has been up, how many clips are
still waiting on the link, and whether the card has room for them.

**WATCH WHICH WINDOW YOU ARE IN.** Two black terminals side by side and
the commands look alike. Anything starting `ssh` or `Get-Content` is
the laptop's PowerShell; anything with `sudo` or `~/.ssh` is the Pi.
Running `ssh-keygen` in the Pi window makes a key on the WRONG MACHINE,
and testing the key from the Pi window has it log in to itself and
refuse — which looks exactly like the key having failed.

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

## Step 0b — You cannot reach the other rig from this one

Each rig is its own island. The Pi, its camera and a little router live
on one network, and **every kit ships that router defaulting to the same
private range** — so the green camera at `192.168.50.10` and the tee
camera at `192.168.50.11` are not neighbours. They are `.10` behind the
green rig's `192.168.50.1` and `.11` behind the tee rig's, with no route
between them. The Cameras card shows each Pi's view of its own camera,
which cannot distinguish that from one shared LAN.

Tested 3 Oct, from the green Pi:

```
$ ping -c2 192.168.50.11
From 192.168.50.1 icmp_seq=1 Destination Host Unreachable
```

That reply is the GREEN rig's own router saying nothing by that address
exists on its segment. Which settles it in one line — if you ever wonder
again, this is the test, and a reply from `.1` rather than from `.11` is
the answer.

**So a camera is only reachable through its own Pi.** Which also means
the tunnel below is worth knowing, because the camera's web UI is not
reachable any other way: it is HTTP on a private address while
GolfReelz is HTTPS, and browsers refuse to mix the two.

```powershell
ssh -L 8080:192.168.50.10:80 pi@<that rig's Pi>     # its own camera
```

→ `http://localhost:8080`. Use a different local port per rig if you
have two open. Reaching a Pi therefore unlocks both halves of the job
at once: `config.yaml` and the agent on the Pi itself, and the camera's
own settings through it.

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

## Step 4b — Change a CAMERA setting without the web UI (11 Oct)

**The Hanwha web UI is unusable over the LTE link, and that is not a
fault to fix.** It is a single-page app that fires dozens of requests
per screen and needs the tunnel held open throughout. The modem gives
~23 s alive then ~8 s gone while it re-enumerates, so the first page
loads and the next click lands in a dead window. Hours have been lost
to retrying this.

**Use SUNAPI instead.** Every setting the web UI exposes is one HTTP
request, which crosses the bad link without noticing it, and the agent
already speaks it — `agent/common.py::_sunapi`. Read first:

```bash
sudo -u golfreelz /opt/golfreelz-agent/venv/bin/python3 - <<'EOF'
import sys, json, yaml
sys.path.insert(0, "/opt/golfreelz-agent")
from agent.common import stream_profile_view
cfg = yaml.safe_load(open("/opt/golfreelz-agent/config.yaml"))
print(json.dumps(stream_profile_view(cfg.get("camera", {})), indent=2))
EOF
```

### Four things that will waste your time if you do not know them

**1. THE SENSOR MODE GATES EVERYTHING.** A profile cannot ask for a
frame rate the sensor is not producing. Set `FrameRate=60` on the
profile alone and the camera answers `OK` and **silently leaves it at
30**. Raise the sensor first:

```
media.cgi?msubmenu=videosource&action=set&Channel=0&SensorCaptureFrameRate=60
```

Legal values on the XNV-6080R are 25, 30, 50, 60. Then set the profile.

**2. THE VERB IS NOT THE SAME ACROSS SUBMENUS.** `videosource` takes
`action=set`; `videoprofile` takes `action=update`. The wrong one
returns `NG / Error Code: 601 / Action Not Found`, which reads like
the feature is missing when it is only the verb. Try both, or read the
answer off the capability XML (below). `image.cgi?msubmenu=camera`
also takes `set`.

**2b. CODEC-SPECIFIC FIELDS KEEP THEIR NAMESPACE IN THE UPDATE.**
`FrameRate` is written plain, but `BitrateControlType` is rejected
with `NG / 602 / Invalid Parameter(s)` and must be sent as
`H264.BitrateControlType`. The rule is what the `view` dump shows: a
key that reads back as `Channel.0.Profile.2.H264.X` is written as
`H264.X`, one that reads back as `Channel.0.Profile.2.X` is written
as `X`. Expect the same for the H.265 profile with an `H265.` prefix.
Send both spellings and take whichever returns OK.

**3. `OK` DOES NOT MEAN IT CHANGED.** This firmware returns `OK` for a
clamped write. **Always read the value back** — never trust the
response. The cheap test for "is this field writable at all" is to
write a value you know is legal but different (30 -> 25), read it
back, then put it straight back. If the small change sticks and the
big one does not, the field is fine and something is capping it.

**3b. SET THE MODE BEFORE THE VALUE IT GOVERNS.** Changing
`BitrateControlType` re-clamps `Bitrate` to something legal for the new
mode, silently and after the fact. On the green, 19 Oct: `Bitrate=2560`
then `CBR` both answered `OK` and read back **5120**. The same write of
2560, sent again once the profile was already CBR, stuck first time.
Nothing was wrong with the field or the value — only with the order.
Mode first, then the numbers it governs, then read back.

**4. THE CAMERA DOCUMENTS ITSELF.** `attributes.cgi` is the authority
on field names, legal values and which verb each action takes — far
better than guessing:

```bash
curl -s --digest -u admin:PASSWORD http://<cam>/stw-cgi/attributes.cgi/media
```

It takes a path, NOT a `msubmenu` query, so `_sunapi` cannot fetch it
— use `requests` directly. ~21 KB for media, ~209 KB for everything.
Save it to `/tmp` and grep locally; it does not change, so fetch it
once per camera and never again over a bad link.

### The AUDIO input, read off the camera (16 Oct)

`media.cgi?msubmenu=audioinput` — verb is **`set`**, not `update`.

| Field | Legal values | Tee, as found |
|---|---|---|
| `Source` | `ExternalMIC`, `LineIn` | **`LineIn`** |
| `PowerOnExternalMIC` | bool | `True` |
| `Gain` | int **1-10** | **5** |
| `EncodingType` | G711, G726, AAC | `AAC` |
| `Bitrate` | 16000..64000 | `48000` |
| `Mode` | Mono only | `Mono` |
| `NoiseReduction` | bool | `False` |
| `NoiseReductionSensitivity` | 1-3 | 1 |
| `SampleRate` | **NOT SETTABLE** — view only | 16000 |

**`Source=LineIn` with `PowerOnExternalMIC=True` is worth a second
look.** The camera is powering an external mic while reading the input
as line level, and the two differ by roughly 20-40 dB. A microphone
feeding a line-level input is far too quiet, and `Gain` (1-10) cannot
make that up. Which is correct depends on the mic: one with its own
preamp and a separate 12 V feed may genuinely be line level. It is a
one-command A/B and fully reversible — try it before touching Gain.

**SAMPLE RATE IS FIXED AT 16 kHz on this model.** It appears only as a
`view` response and is absent from `set`. So there is nothing above
8 kHz in the audio, ever, and raising `Bitrate` past 48000 buys nothing
— there is no bandwidth there to encode. Any plan that depends on
wideband audio needs a different mic path, not a camera setting.

**LEAVE NoiseReduction OFF.** It works by smearing transients, and a
club-on-ball strike IS a transient — it would attack the exact signal
the server's `detect_swings_from_audio` reads. There is no AGC
parameter in this submenu, which is a relief for the same reason.

**Gain is not reach.** It lifts the noise floor by as much as the
signal, and the failure mode at the top of the range is clipping on
near strikes — losing the impact you need to catch the ambience you
were curious about. If `ExternalMIC` turns out to be right, the useful
move may be gain BELOW 5, not above.

### What the tee camera was set to, and what it is now

| field | was | now | why |
|---|---|---|---|
| `SensorCaptureFrameRate` | 30 | **60** | the gate on everything below |
| `Profile.2.FrameRate` | 30 | **60** | ~2x tracer points, less motion blur |
| `Profile.2.H264.GOVLength` | 60 | 60 | unchanged, but see below |
| `Profile.2.Resolution` | 1920x1080 | same | already right |
| `Profile.2.Bitrate` | 2560 | **4000** | read back 19 Oct; the table said 2560 and was wrong — read, don't trust this row |
| `Profile.2.H264.BitrateControlType` | VBR | **CBR** | predictable clip size |

**GOV 60 only became correct when the frame rate did.** The copy
engine cuts the ring into 1-second segments
(`copyengine.py`, `copy_segment_seconds: 1.0`) and ffmpeg's segment
muxer can only break on a keyframe. At GOV 60 / 30 fps a keyframe
arrives every TWO seconds, so every "1 second" segment was really two
and clip starts quantised to 2 s. At 60 fps it is one keyframe per
second and the segment length means what it says. Nobody set out to
fix this; it fell out of the frame-rate change. If the frame rate is
ever lowered again, lower GOV with it.

**A profile the agent is streaming will drop when you change it**, and
the sensor change restarts the whole pipeline. The agent reopens after
`stall_reopen_seconds` (15 s). Restart it afterwards anyway so it
opens cleanly against the new profile rather than whatever it
reconnected to mid-change.

**Then read the CARD, not the API.** The heartbeat reports the size
and rate the agent OPENED at AND the rate frames are really arriving
at, side by side. That pair exists precisely because a camera that
claims a setting and delivers another is the failure mode here — it
has now happened twice in one day.

**60 fps costs a stop of light.** Half the exposure window, made up
with gain, and `AGCMode` on this camera is already `High`. Fine in
daylight; look at an actual dusk clip before assuming.

---

## Step 5 — Swap the MODEMS (when the symptom is load-correlated)

Step 4 swaps the power chain. This swaps the radio, and the two
together separate every remaining candidate.

**Reach for this when the fault tracks LOAD rather than time:** short
bursts cross fine (heartbeats, status polls, the live view) and
anything sustained dies within half a minute. That is the tee's
signature, in SSH and in clip upload alike — the same fault wearing two
hats, not two problems.

### Do the 2x2, and measure THROUGHPUT, not ping (10 Oct)

Both rigs went on a bench indoors and were measured against each
other. The measurement method below is sound and worth keeping. The
conclusion originally drawn from it was not — see the warning under
the table.

**Measure the same way in all four configurations, agents stopped.**

**STOPPING THE AGENT STOPS THE HEARTBEAT — AND NOTHING RESTARTS IT.**
This reads as a fault on the card and is not one. Every round below
begins `sudo systemctl stop golfreelz-agent`; the heartbeat *is* the
agent, so `last seen` freezes at the beat immediately before the stop
and then ages — seen, late, offline — while the rig is perfectly
healthy. The tell is that `last seen` is pinned to the minute the test
started, every time. Rounds that end in `sudo reboot` hide it, because
systemd brings the unit back on boot; rounds that just end leave the
agent down until someone notices.

Do not rely on remembering the restart. Wrap it once per Pi:

```bash
cat > ~/lt.sh <<'EOF'
#!/bin/bash
sudo systemctl stop golfreelz-agent
trap 'sudo systemctl start golfreelz-agent; echo "agent restarted"' EXIT
~/linktest.sh "$@"
EOF
chmod +x ~/lt.sh
```

and run `~/lt.sh LABEL` thereafter. The trap fires on Ctrl-C and on
failure too, which is exactly when the restart is forgotten.

Separately: a measurement run saturates the uplink for ~3.5 minutes
(five runs x 12 s down + 12 s up). Heartbeats have a 5 s timeout, so
even with the agent running a few minutes of lateness during a test is
expected and clears itself. A `last seen` that keeps climbing past
that is a stopped service, not congestion. Check which it is before
chasing the radio:

```bash
systemctl is-active golfreelz-agent
```

```bash
echo "=== $(hostname) $(date) ==="
ping -c 10 8.8.8.8 | tail -2
curl -o /dev/null -w "down: %{speed_download} B/s\n" --max-time 30 \
  "https://speed.cloudflare.com/__down?bytes=25000000"
head -c 10000000 /dev/zero | curl -o /dev/null -w "up:   %{speed_upload} B/s\n" \
  --max-time 30 -X POST --data-binary @- "https://speed.cloudflare.com/__up"
```

| Configuration | down B/s | up B/s | loss |
|---|---|---|---|
| before any swap | 3,770 | 2,384 | 40% |
| the other rig, before any swap | 138,238 | 43,689 | 0% |
| after the swap — one rig | — unreachable, could not hold SSH — | | |
| after the swap — other rig | 475,781 | 144,171 | 0% |
| after a further modem change | 310,232 | 96,111 | 0% |

### ANSWERED: it is the RADIO UNIT (modem + SIM), 11 Oct

Done the way the rest of this page had been begging for: **both rigs
measured within seconds of each other, and the suspect part swapped in
BOTH directions.** Four readings inside six minutes, on a weekday
morning when the fault actually shows.

| time | card | board | modem | down KB/s | up KB/s |
|---|---|---|---|---|---|
| 10:35 | green | 5355… | **green** | 673 | 277 |
| 10:36 | tee | 96b4… | **tee** | 172 | 59 |
| 10:41 | green | 5355… | **tee** | **48** | **0** |
| 10:41 | tee | 96b4… | **green** | **678** | **404** |

The fault followed the modem into the green rig and left the tee rig in
the same swap. Fourteenfold one way, four to sevenfold the other, with
the cards and boards untouched.

The bad side also shows the real symptom rather than just a low number:
two of its five runs could not complete a ping at all (`loss=?`, 0/0)
between runs that managed 213-217 KB/s. Total intermittent dropout --
which is what the tee has been doing for months, now reproduced on the
green rig because it is holding the tee's radio.

**ELIMINATED, ALL AT ONCE:** the SD card, the Pi board, the kernel
version, the USB port, the mount, the location, and the time of day.
Each had been blamed at some point; none of them moved.

**NOT YET SEPARATED:** the SIM travels with its modem, so this isolates
the radio as a UNIT. Swapping only the SIMs splits dongle from line --
and since both are EIOTCLUB prepaid, entering each ICCID at
sim.eiotclub.com answers the data-cap question without touching the
hardware at all.

WHY THIS ONE COUNTS AND THE EARLIER ONES DID NOT. Everything before it
was sequential: measure, change a part, measure again, on a link that
moves more than 10x by itself. Three conclusions were drawn that way
and all three were wrong. Simultaneity is what makes the difference --
drift can flatter one rig, it cannot flatter one and cripple the other
in the same thirty seconds.

---

### Background: the link is also slow by DAY and fast by NIGHT

The oldest and most reliable observation about this deployment, and
the one that explains almost every "fault" chased in these pages:

> **Uploads crawl during the day and push through in the late
> afternoon and evening.** Observed across months, on the ORIGINAL tee
> rig with its original modem and its original SIM, and again on
> entirely new hardware.

Because it predates every component now installed, it cannot be the
Pi, the card, the modem, the SIM or the mount. It is congestion on a
consumer LTE line, and it is the environment rather than a fault.

Measured on 10 Oct, one rig, unchanged, over ninety minutes:

| time | up median | down median |
|---|---|---|
| 20:56 | 21 | 109 |
| 21:29 | 21 | 226 |
| 21:43 | 107 | 404 |
| 22:23 | **244** | **736** |

Twelvefold on upload, with nothing changed that mattered.

WHAT THIS MEANS FOR TRIAGE. A slow reading in the afternoon is not
evidence of anything. Before suspecting hardware, ask what time it is,
and compare against a reading from the SAME hour on a rig known to be
healthy. Every diagnosis below that rests on a single measurement is
worthless without that control.

WHAT IT MEANS FOR THE SYSTEM. Size clips for the DAYTIME link, which
is when golfers actually play, not for the evening one that makes the
numbers look good. The uploader's own ladder already walks the encode
down when the link cannot carry it and back up after clean sends;
that adaptation is not a workaround here, it is the design responding
to the real conditions.

---

### NOT the SD card either — and why A-B-A still fooled us (10 Oct)

Run properly the second time: one board (`96b4bdce178f1479`), one
dongle, nothing else touched, five runs per round, a clean shutdown and
two reboots between the legs.

| round | card | kernel | down median | **up median** | up range |
|---|---|---|---|---|---|
| A1 | tee | 6.18.34 | 109 | **21** | 11-48 |
| B1 | green | 6.12.75 | ~400 | **133** | 112-149 |
| A2 | tee | 6.18.34 | 226 | **21** | 11-64 |

**RETRACTED AN HOUR LATER. IT IS NOT THE CARD EITHER.** Kept here
because the way this went wrong is the most useful thing on the page.

The rounds above looked decisive: upload returned to the same median on
the return leg and the three groups did not overlap. Then the same card,
same board, same dongle, kept being measured:

| time | config | up median | down median |
|---|---|---|---|
| 20:56 | A1 | 21 | 109 |
| 21:29 | A2 | 21 | 226 |
| 21:43 | A3, wmem_max 212k | 107 | 404 |
| 22:23 | A4, wmem_max 4M | **244** | **736** |

Upload climbed 21 -> 244 across ninety minutes while a sysctl was
toggled back and forth, and the best figure of the night came from
restoring the value that was supposed to be the fault. It tracks the
clock, not the configuration: late evening, tower load falling.

Near-simultaneous pairs flip sign across the same evening -- 20:38 green
13 / tee 509; 20:56 tee 109 / green 385; 22:24 tee 244 / green 96. There
is no stable difference between the rigs.

WHY A-B-A WAS NOT ENOUGH. It controls for LINEAR drift. This trend
accelerated after A2, so A1 and A2 both sat in an early slow patch while
B1 fell partway up the rise. Five runs per round made each round precise
and did nothing about the thing that actually moved.

WHAT WOULD ACTUALLY SETTLE IT: measure both rigs SIMULTANEOUSLY, in
pairs, repeatedly over hours. Time is then controlled by construction
rather than by argument. A single pair proves nothing -- the three pairs
above disagree with each other.

THE STANDING LESSON, now paid for three times in one day (the SIM, the
card, the send buffer): on this deployment the link varies by more than
10x over tens of minutes, independently per modem. Any hypothesis tested
by measuring one thing, changing it, and measuring again will confirm
itself. If a difference cannot be shown with both rigs measured at the
same instant, repeatedly, it has not been shown.

WHY THE EARLIER ROUNDS SAID NOTHING. Single measurements, taken minutes
apart on different boards, ranged 13-509 KB/s. Against that spread one
sample per configuration cannot see a 6x effect, and two confident
conclusions were drawn from exactly that. `~/linktest.sh` (five runs,
median, board serial and hostname in the header) is what made the
difference -- not a better hypothesis.

The hard difference between the cards is the kernel: **6.12.75 is fast,
6.18.34 is slow.** A transmit-path regression is the leading suspect,
and TX offload defaults (TSO/GSO) on a cheap USB-ethernet dongle are the
usual cause. Check with `ethtool -i eth1` and `ethtool -k eth1` on both.

---

**AN EARLIER VERSION OF THIS SECTION SAID "IT WAS THE SIM."**
It read "IT WAS THE SIM" in confident bold. That was wrong, and it is
worth leaving the scar: the numbers above are real and the fault is
real, but the labels on the rows were an assumption about which parts
had been exchanged, not a record of it. What was actually swapped
between the two rigs was the **SD cards** — the SIMs never left their
modems. So every row attributing the result to a radio was describing
an experiment nobody ran.

A 100x throughput difference is a fact. "Therefore the SIM" was a
story laid over it, and it survived three paragraphs of reasoning
because the reasoning was never checked against what the hands did.

WHAT THIS RUNBOOK NOW ASKS OF YOU, before anything else:

1. **Write down the physical configuration before each measurement** —
   which board, which SD card, which modem, which SIM. All four. The
   hostname does NOT do this for you: it lives on the SD card, so the
   prompt follows the card and not the board, which is exactly how the
   labels above went wrong.
2. **Change one thing at a time**, and re-measure after each.
3. Only then write a conclusion.

SWAPPING SD CARDS SWAPS THE CAMERAS' IDENTITIES. The card carries
config.yaml, and with it the auth token, the assigned role and the
camera's RTSP address. Two rigs with exchanged cards have exchanged
identities in the backend: each reports as the other, triggers as the
other, and uploads against the other's events. Check `/admin/cameras`
after any card swap, and treat clips recorded in that window with
suspicion.

One counter to read with care: `dmesg` USB resets went 0 -> 2 across
this session, entirely from unplugging modems by hand. A physical
disconnect is a USB disconnect and the counter cannot tell the
difference, so it only means something while nobody is touching the
hardware.

**SLOW LINK vs MODEM GONE — the log says which, exactly.** Both end
with a clip that never arrives, and they need different fixes, so do
not guess:

| in the agent log | what it means |
|---|---|
| `Read timed out. (read timeout=45)` | the link is UP and slow. Wait, or lower the bitrate. |
| `[Errno 101] Network is unreachable` | the INTERFACE IS GONE. The modem is re-enumerating mid-transfer. No bitrate change helps. |

Confirm it with the card: `USB resets` climbing **during an upload**
is the modem dropping off. Flat while a clip crawls is a slow link.
Observed 11 Oct: 41 -> 48 inside one upload, with errno 101 in the
log and `chunk_bytes` already at the 65536 floor — three independent
readings of the same fault.

**CLIP SIZE IS NOT THE BUG WHEN THE RADIO IS.** 2367 kbps of video is
296 KB/s, so a 36 s swing is 10.6 MB: 26 seconds on a healthy modem
(404 KB/s, measured), eight minutes at 21 KB/s. Before cutting
`max_clip_seconds`, read its comment in `config.example.yaml` — it
was 30 s once and cut real swings in half, and chunked resumable
upload is what removed the need for a small cap.

**PING IS NOT A LINK TEST.** The first ping on the broken rig came back
72 ms with 0% loss and sent this investigation sideways for an hour. A
link can be perfect for 64-byte packets and carry 3 KB/s. Throughput is
the measurement; latency is a hint. Run the ping again too — the second
run on the same rig lost 40%, so one clean ping proves nothing either.

**A hotspot dongle is not an industrial modem.** Many consumer plans
meter or deprioritise tethered data separately, and a throttle to
~2G speeds (32-128 kbps) looks exactly like 3,770 B/s with intermittent
loss. Before buying hardware, read the line's usage in the carrier
portal.

**Both rigs look identical from the Pi.** Each modem NATs to
`192.168.1.100` via `192.168.1.1`, so after a swap nothing on the Pi
changes and nothing in the config or the agent needs touching — the
agent knows nothing about SIMs, carriers or modems. Label the hardware,
because the software cannot tell you what is plugged in.

### First, rule out the boring one: the link is just full

**Observed 2 Oct: the tee became reachable the moment its uploads
finished.** That is the cheapest explanation and it needs no fault at
all — a 6.5 MB clip up a ~125 KB/s uplink is ~52 seconds of saturated
wire, and a saturated cellular uplink buffers deeply. SSH packets queue
behind megabytes of video, round-trip time goes from 50 ms to tens of
seconds, and the session dies with exactly the "Connection reset" and
"Connection timed out" you would get from a broken modem.

An earlier version of this section went straight to a power→USB-reset
chain. That may still be right, but **saturation explains the same
symptoms with no hardware fault**, so test it first:

```bash
dmesg -T | grep -icE "usb (disconnect|reset)"
```

| Result | Meaning |
|---|---|
| **0**, or a count that does not grow across an upload | The modem never re-enumerated. It is bandwidth, not hardware — stop here and do not swap anything |
| A count that climbs each upload | The modem really is resetting. Continue below |

If it is bandwidth: the hardware is fine, the window for SSH is
**between uploads**, and `/admin/cameras` already shows uploads in
flight. Pausing triggering on the camera stops new clips at the source
and drains the queue — on an agent new enough to honour it (the signal
is `triggering_disabled`; agents before 3 Oct ignored it and kept
uploading while "paused").

### If the modem really is resetting

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

The cheapest evidence needs no SSH at all. **`/admin/cameras` shows a
⚡ pill per camera** carrying `vcgencmd get_throttled` off the
heartbeat: *power clean*, *browned out since boot*, or *under-voltage
right now*. The middle one is the find — a brownout that already
happened and left no other trace — and it is Step 1 of this runbook
answered from a laptop.

A 🔋 pill appears beside it **only on a rig with an INA226 fitted** on
the 12 V feed; where there is one, watch the voltage while a clip
uploads, because a rail that reads fine at idle and sags under load is
the brownout caught in the act. Where there is not, there is no pill —
which is exactly why the ⚡ one exists, since a missing sensor and a
healthy camera used to look identical from here.

One SSH command settles whether the modem is really re-enumerating
rather than the carrier dropping the bearer:

```bash
dmesg -T | grep -iE "usb (disconnect|reset)|new (high|super)-speed" | tail -30
vcgencmd get_throttled     # 0x0 here while dmesg shows USB resets
                           # => NOT the Pi's power. Look at the modem.
```

### Test 1 and Test 2 — autosuspend, then port power (14 Oct)

Two theories survived the review of Google's advice. Both are cheap,
both are read-only to start, and **the first command below may settle
both of them in thirty seconds**, so run it first.

Everything here runs on the tee Pi over SSH. Nothing in Test 1 or the
first two steps of Test 2 changes anything — they only read.

#### Step A — read the kernel's own words (run this first)

The reset *count* on the card says a disconnect happened. The kernel
log says **why**. We have never read it.

```bash
sudo dmesg -T | grep -iE "usb|eth1|over-?current|error -(71|110)|enumerate" | tail -60
```

| What the log says | What it means |
|---|---|
| `over-current condition` / `over-current change` | the port's current limit tripped. **Power, definitively.** Go to Test 2. |
| `device descriptor read/64, error -71` | stalled during enumeration — power or a marginal cable |
| `error -110` (timeout) | the device never answered — power, or a dongle that is dying |
| `unable to enumerate USB device` | the dongle stopped responding altogether |
| bare `usb 1-1: USB disconnect` with **no error line before it** | the device took itself off the bus. **Not power.** Modem firmware, or autosuspend — go to Test 1. |

If `dmesg` has rolled over and shows nothing from the bad window, make
it persistent so the next one is captured:

```bash
sudo mkdir -p /var/log/journal && sudo systemd-tmpfiles --create --prefix /var/log/journal
sudo systemctl restart systemd-journald
# afterwards: sudo journalctl -k --since "-2 hours" | grep -i usb
```

#### Test 1 — is the kernel suspending the modem?

**Step 1. Find the modem's USB device.** The interface is probably
`eth1`, but do not assume it — this prints whichever network interfaces
are USB-attached:

```bash
for i in /sys/class/net/*; do
  p=$(readlink -f "$i")
  case "$p" in *usb*) echo "$(basename "$i") -> $p";; esac
done
```

**Step 2. Walk up to the USB device directory and read its power
settings.** Substitute the interface name from Step 1 if it is not
`eth1`:

```bash
d=$(readlink -f /sys/class/net/eth1/device)
while [ -n "$d" ] && [ "$d" != "/" ] && [ ! -f "$d/idVendor" ]; do d=$(dirname "$d"); done
echo "USB device dir: $d"
cat "$d/idVendor" "$d/idProduct" 2>/dev/null; cat "$d/product" 2>/dev/null
echo "--- power ---"
for f in control autosuspend_delay_ms runtime_status runtime_suspended_time; do
  printf '%-26s %s\n' "$f" "$(cat "$d/power/$f" 2>/dev/null)"
done
cat /sys/module/usbcore/parameters/autosuspend   # -1 = off globally
```

How to read it:

| Reading | Verdict |
|---|---|
| `control` = `on` | autosuspend is already disabled for this device. **Test 1 is dead** — go to Test 2. |
| `control` = `auto` | the kernel is *allowed* to suspend it. Necessary, not sufficient — do Step 3. |
| `runtime_status` = `suspended` while the card shows the rig online | caught in the act. Theory confirmed. |

**Step 3. Prove it actually suspends, don't just prove it's allowed
to.** `runtime_suspended_time` is cumulative milliseconds spent
suspended. Read it, wait, read it again — with the rig idle, no
uploads:

```bash
cat "$d/power/runtime_suspended_time"; sleep 600; cat "$d/power/runtime_suspended_time"
```

Number grew → the modem **is** being suspended, and this is the fault.
Number identical → it has never been suspended, and autosuspend is not
our problem no matter what `control` says.

Expect the second outcome. Most USB network drivers hold the device
awake while the interface is up, so this test most often comes back
negative — but it costs ten minutes of waiting and the fix below is
free, so apply the fix either way.

**Step 4. The fix (free, harmless, idempotent).** Immediately:

```bash
echo on | sudo tee "$d/power/control"
```

That is lost on replug and reboot. To make it permanent, use the
vendor and product IDs printed in Step 2 in place of `XXXX`/`YYYY`:

```bash
sudo tee /etc/udev/rules.d/50-modem-no-autosuspend.rules >/dev/null <<'EOF'
ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="XXXX", ATTR{idProduct}=="YYYY", TEST=="power/control", ATTR{power/control}="on"
EOF
sudo udevadm control --reload-rules
```

Verify the rule actually takes, rather than trusting that it did —
reboot, then re-run Step 2 and confirm `control` reads `on`.

#### Test 2 — is the Pi starving the USB port?

This is the theory that fits our best evidence: **resets climb during
uploads, not while idle.** An LTE modem's draw spikes on transmit.

**The ⚡ pill on the card does not answer this.** It reports
`vcgencmd get_throttled`, which watches the Pi's own 5 V input rail. A
USB port can brown out its peripheral while that rail reads perfectly
clean — `POWER CLEAN` and a starved dongle are consistent readings.

**Step 1. Read the Pi 5's USB current budget.** A Pi 5 that does not
detect a 5 V / 5 A supply caps *all* downstream USB at 600 mA; with
one, it allows 1.6 A. An LTE dongle on transmit can cross 600 mA on
its own.

```bash
vcgencmd get_config usb_max_current_enable
grep -i usb_max_current /boot/firmware/config.txt
sudo dmesg | grep -iE "usb_max_current|power budget|insufficient|5A|pd "
vcgencmd get_throttled
```

**Step 2. Decide from what the PSU actually is, not from the reading
alone.**

- Official 27 W Raspberry Pi 5 PSU (or any supply genuinely rated
  5 V / 5 A): add `usb_max_current_enable=1` to
  `/boot/firmware/config.txt`, reboot, re-read. This raises the cap to
  1.6 A and is the one-line version of this fix.
- A phone charger, a car adapter, a 15 W brick: **do not set that
  flag.** Raising the cap on a supply that cannot deliver it moves the
  brownout from the port to the whole board. Replace the PSU first.

**Step 3. Catch a reset in the act, correlated with an upload.** Two
SSH sessions. In the first, watch the bus live:

```bash
sudo dmesg -TW | grep -iE "usb (disconnect|reset)|over-current|error -(71|110)" \
  | tee -a /tmp/usb-resets.log
```

In the second, saturate the uplink the way an upload does — this is the
upload half of the throughput test above, looped:

```bash
for n in 1 2 3 4 5; do
  head -c 10000000 /dev/zero | curl -o /dev/null -w "up $n: %{speed_upload} B/s\n" \
    --max-time 60 -X POST --data-binary @- "https://speed.cloudflare.com/__up"
done
```

Lines appearing in the first window *while the second is pushing*, and
not between runs, is the load correlation made visible. Note that this
leaves the agent running, so a minute or two of heartbeat lateness
during the push is expected.

**Step 4. The controlled test: one change, one reading.** Only if
Step 3 shows resets under load.

1. Baseline: run Step 3 exactly as written. Record the reset count.
2. Change **one** thing — put the dongle on a **powered** USB hub, so
   it draws from the hub's own supply instead of the Pi's port.
   Nothing else: same SIM, same position, same cable to the hub.
3. Re-run Step 3 identically. Record the count.

Zero resets on the hub → it is power, and the fix is the hub or a
proper PSU. The same count on the hub → it is not the Pi's power
budget, and Step A's kernel text is pointing at the modem itself.

Do not swap the hub in *and* reseat the antenna *and* change the APN in
the same visit. This page already has one wrong conclusion in it from a
test that moved two things at once.

### ANSWERED: THE PI WAS CURRENT-LIMITING ITS OWN USB PORT (14 Oct)

**The Raspberry Pi 5 caps all downstream USB at 600 mA unless it
detects a 5 V / 5 A supply. The tee rig is fed from a buck converter,
which cannot negotiate PD, so the cap was permanent.** An LTE modem on
transmit crosses 600 mA, browned out past its own undervoltage
lockout, reset, and rebooted — once every three minutes, all day, for
weeks.

The fix is one line in `/boot/firmware/config.txt`:

```
usb_max_current_enable=1
```

which lifts the cap to 1.6 A. Confirm with `vcgencmd get_config
usb_max_current_enable` (wants `1`) and reboot for it to take.

**ONLY DO THIS ON A SUPPLY THAT CAN DELIVER IT.** Raising the cap on a
converter that cannot moves the brownout from the port to the board,
and you will be chasing SD-card corruption instead. Watch
`vcgencmd get_throttled` afterwards; non-zero means back it out.

The measurement, with the dongle confirmed still at the same bus
address (`readlink -f /sys/class/net/eth1/device`) so the count is of
the same thing:

| | |
|---|---|
| before | 98 disconnects in 155 min = **0.63/min** |
| expected in the 42 min after | **~27** |
| observed | **0** |

Zero against twenty-seven is not a quiet spell. The link log agrees
independently: the hour after the reboot ran **0 dead minutes out of
39**, against 7.8% for the three hours before it.

**WHY THIS TOOK WEEKS, and the lesson for the next one.** Six days of
per-minute `get_throttled` readings are all `0x0` — the Pi's 5 V INPUT
rail never sagged, not once. Every power check this page documents
reads that rail, including the ⚡ pill on the Cameras card. The
shortage was at the PORT's own limiter, downstream of everything we
were measuring. **A clean `get_throttled` does not mean a peripheral
is being fed.**

### WHAT THOSE TESTS ANSWERED, 14 Oct

**The dongle reboots itself, every three minutes, all day.** Read it
off the kernel log in thirty seconds after weeks of guessing:

```
17:22:47  disconnect       device 92   1286:4e3c ASRMicro  <- working modem
17:22:48  reappears        device 93   2ecc:3001 MARVELL "WUKONG"
17:22:51  disconnect       device 93
17:22:56  reappears        device 94   1286:4e3c ASRMicro, eth1 registers
```

**A device that comes back under a DIFFERENT vendor/product ID has
returned to its power-on identity and booted again.** That is the whole
diagnosis, and it does not depend on knowing what "WUKONG" is.
Autosuspend cannot do that — a suspended device resumes as the same
device, often without re-enumerating at all. **Test 1 is dead; do not
bother with the udev rule.**

Rate, off the same Pi: **98 disconnects in 2 h 35 m**, with 99
`cdc_ether` registrations confirming each one re-registered. Two
disconnects per reboot cycle, so **~19 reboots an hour, one every three
minutes, for the whole uptime.** A baseline, not a load spike.

| Reading | 14 Oct | What it says |
|---|---|---|
| `usb_max_current_enable` | `0`, and **not** in config.txt | the firmware's own detected value: all downstream USB capped at **600 mA** |
| `EXT5V_V` | 5.089 V **at idle** | the input rail is fine when nothing is transmitting. Says nothing about load |
| `get_throttled` | `0x0` | correct and irrelevant — see below |
| ModemManager / uhubctl / usb_modeswitch | none running, nothing in the journal | **nothing on the Pi is resetting the port** |

**WHY THE 600 mA MATTERS ON THIS RIG SPECIFICALLY.** The tee Pi is fed
from a buck converter off the 12 V battery, not a USB-C PD brick. A
buck converter cannot negotiate PD, so **the Pi will never detect a 5 A
supply and the 600 mA cap is permanent** until the flag is forced. An
LTE modem on transmit crosses 600 mA without trying.

**THE ⚡ PILL CANNOT SEE THIS, and that is worth knowing before you
trust it again.** It reports `get_throttled`, which watches the Pi's
5 V *input* rail. The shortage here is *downstream* of that, at the
port's own limiter. `POWER CLEAN` and a starved dongle are consistent
readings, and this page previously treated the pill as settling the
question.

### TWO FAULTS, NOT ONE — and 11 Oct only ever answered the first

The "ANSWERED: it is the RADIO UNIT" section above measured **down/up
KB/s**. It never counted a single USB disconnect. So:

| Fault | Signature | Where it lives |
|---|---|---|
| slow / zero throughput | KB/s, ping loss | **followed the radio unit**, 11 Oct. That conclusion stands |
| USB re-enumeration | disconnect count in `dmesg` | **stays with the tee Pi** across dongle swaps. Never tested on 11 Oct |

Which makes that section's "**ELIMINATED, ALL AT ONCE:** … the USB
port" an over-reach. It eliminated the port as a cause of *slow
throughput*. It said nothing about the port as a cause of *resets*.
Treat the two lists of eliminations separately from here on.

Both dongles having been swapped and the resets having stayed on the
tee Pi is what points at the Pi. **One confound, stated so nobody
treats the green rig as a clean control:** the green has no camera on
it at the moment, so it uploads far less. A current-correlated fault
shows less on a quieter rig with identical hardware.

### STILL OPEN after those tests

- ~~`gr-linklog.sh` runs from cron every minute~~ **CLEARED, and then
  promoted.** It was briefly a suspect for the best possible reason: an
  unexplained script running sixty times an hour on the one rig with
  the fault. It is read-only — two `ping -c 5`, `measure_temp`,
  `get_throttled`, one CSV line, trim at 45000 lines. Ten pings a
  minute causes nothing.

  **It is also the only long timeline this project has.**
  `~/golfreelz-link.csv` carries one row a minute for up to thirty days
  with `uptime_s`, `temp_c`, `throttled` and gateway loss, which
  answers three things no live reading can:

  | Column | Question it answers |
  |---|---|
  | `gw_loss_pct` = 100 | when was the modem actually between lives |
  | `uptime_s` dropping | every Pi reboot, timestamped |
  | `throttled` | has this rig EVER browned out — we had only read it live, on a rig that was fine at that moment |

  So a change can be judged against the week before it instead of
  against a hopeful ten minutes after it. Read it with
  `tools/linklog_report.py`; the script itself is now in the repo at
  `pi-agent/tools/gr-linklog.sh` rather than on one SD card.

  **WHAT THE PERCENTAGES ARE NOT.** It pings for about three seconds
  out of every sixty, so a ten-second outage every three minutes lands
  inside a sample only a fraction of the time. The absolute numbers
  understate outage time, by a lot. They are a relative measure, and
  identical sampling on both sides of a change is what makes them
  worth having.
- **`EXT5V_V` under load** has not been read. Idle is 5.089 V; the
  number that matters is the one taken while an upload is pushing.
- **`usb_max_current_enable=1` was appended to config.txt on 14 Oct**
  without the converter's rating having been confirmed first, and it
  went in **twice** (two reboots, same command). The duplicate is
  harmless — the firmware takes the last line — but the file should be
  tidied, and `get_throttled` wants watching now: raising the USB cap
  on a converter that cannot supply it moves the brownout from the port
  to the board.

### Reading the modem's signal

The modem's own admin page reports RSRP / RSRQ / SINR, and comparing one
mount against the other is the location question answered directly.

**KNOW WHICH MODEM YOU HAVE — the address is not the same.** This has
already sent one evening chasing an empty `curl`:

| Modem | Admin page | API | Has its own WiFi? |
|---|---|---|---|
| NETGEAR LM1200 (original) | `http://192.168.5.1` | `/api/model.json` | **No** — USB/Ethernet only, nothing for a phone to join |
| EIOTCLUB 4G USB dongle (from 10 Oct) | `http://192.168.1.1` | NOT `model.json` — generic MiFi firmware, 404s | **Yes** — join its hotspot and browse to it from a phone |

Both present to the Pi as a USB network gadget on `eth1`, and both NAT
their clients to `192.168.1.100`, so **`ip route` cannot tell you which
modem is plugged in and neither can the Pi's address.** Label the
hardware. `lsusb` is the only honest answer, and note that a grep for
"netgear|sierra|huawei" misses the EIOTCLUB entirely.

**Start on the card. Since 7 Oct the agent reads this itself** and sends
it on the heartbeat, as a 📶 pill and an **Uplink** panel on
`/admin/cameras`. The modem hangs off the Pi's USB bus and the counters
come out of `/proc`, so none of it crosses the cellular link — which
means it arrives from a rig too starved to hold an SSH session, the only
time anyone wants it. The panel carries RSRP, RSRQ, SINR, RSSI, band,
carrier, the modem's data figure, the USB reset count and uptime, and
one sentence saying which of the three faults this is:

| The pill | What it means | What to do |
|---|---|---|
| 📶 **signal good** | RSRP above −90 and SINR above 13 | The mount is fine. Slow uploads here are a cap, a carrier throttle, or congestion |
| 📶 **signal marginal** | RSRP −90…−105, or SINR 0…13 | Enough for heartbeats, not for sustained upload |
| 📶 **signal poor** | RSRP below −105, or SINR below 0 | An antenna or a different mount position — **not** another modem |
| 📶 amber with resets | Signal fine but the USB bus has re-enumerated | The power chain, per the test above. Watch whether the count climbs during an upload |
| no pill | The rig found no modem | Nothing is fitted, or nothing is routed. Not a fault report |

The reset count is the runbook's bandwidth-or-hardware test, carried
home without an SSH session: **zero is a finding, not a blank.** It
means the modem never re-enumerated, so a link that is merely full is
the whole story and nothing should be swapped.

The reading is refreshed every 5 minutes rather than every heartbeat —
the modem can be wedged, and a diagnostic that blocks the beat becomes
the outage. So a number up to five minutes old is expected.

**The two routes below are still here**, for a rig running an agent
older than 7 Oct, or when the panel says *modem found but unreadable* —
which means the firmware on this unit does not serve the JSON shape the
agent knows, and someone has to look at what it does serve.

**Over SSH, which needs no site visit.** The modem hangs off the Pi's
USB bus, so reading it never touches the cellular link — only your SSH
session does, which is why this works even while the uplink is too slow
to carry a clip. Find the modem, then ask it:

```bash
ip route | grep -E "usb|wwan|enx|eth1"   # the gateway IS the modem
GW=$(ip route | awk '/^default/{print $3; exit}')
curl -s --max-time 10 "http://$GW/api/model.json" \
  | tr ',' '\n' | grep -iE "rsrp|rsrq|sinr|bars|band|rssi|usage|billing"
```

Take the gateway from the routing table rather than typing an address:
the two modems live on different subnets and hardcoding either one is
how this check silently returns nothing.

NETGEAR's LB/LM series serve that JSON. The EIOTCLUB does not — it runs
generic MiFi firmware (`g_platformName = "mifi"`) and 404s. Its real
endpoints are named in its own JavaScript, which is the fastest way to
find any unknown modem's API:

```bash
curl -s --max-time 10 "http://$GW/" | head -40            # what is it?
curl -s --max-time 10 "http://$GW/js/base/ajax_calls.js" | head -60
```

**Use curl, not a browser, while the link is bad.** An SSH port-forward
(`ssh -L 8080:192.168.5.1:80 pi@<rig>`) does let you open the real UI on
your laptop, but a full web UI with its assets crosses the starved link
and will crawl. The JSON is a few KB.

**On site**, a laptop in the modem's Ethernet port reaches the same page
with nothing in between.

WHAT TO READ. Bad RSRP/SINR means weak signal — an antenna or a mount
position, not another modem. Good signal with low throughput means a cap
or a carrier throttle, which the usage fields in that same blob will
show. Those are different problems with the same symptom, and this is
the one check that separates them.

---

## Decision tree

```
get_throttled == 0x0 ?
├── YES → not the Pi's INPUT rail. Go to journalctl (Step 2).
│         Likely: upload timeout, tmpfs full, agent crash.
│         It does NOT clear the USB PORT's current budget —
│         0x0 here and a starved modem are consistent. See
│         "Test 1 and Test 2".
└── NO  → it has browned out.
          Measure converter INPUT under load (Step 3).
          ├── holds 12V  → converter is weak. Swap the spare.
          └── sags <10V  → swap harnesses (Step 4).
                           ├── fixed  → tee harness is bad. Rebuild.
                           └── still  → battery genuinely undersized/flat.
```

---

## Known-good reference

- Uplink throughput, measured as above with the agent stopped: a
  healthy rig did **138–476 KB/s down, 26–144 KB/s up, 0% loss,
  70–145 ms**. A broken one did **3.8 KB/s down, 2.4 KB/s up, 40%
  loss**. There is no grey area between those two; if a reading is
  closer to the second, stop looking at the Pi.
- Upload is the number that matters, and even a healthy rig here is
  only ~35–144 KB/s. A 35s clip at 4000 kbps is ~17 MB, which is 2
  minutes at the top of that range and 8 at the bottom. Size the
  camera's own encoder against the measured uplink, not against what
  the link is supposed to do.
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

- **The skyline.** The ascent sweep searches a strip of sky and keeps
  ten blobs a frame, largest first. Draw the line just above the
  treeline (**☁ Skyline** on the camera card) — below it are the trees,
  the houses and the flag, every one of them a bigger blob than a ball
  at 180 yards. Until one is drawn the floor is guessed as "a golfer's
  height above the hitting area", which on Rivertowne 8 lands 150px
  inside the trees: a flight a scan found on nearly every frame came
  through the sweep as four points.

## Related

- [`green-parity.md`](./green-parity.md) — every fix on this page was
  made on the TEE while the tee was the one being debugged. That is the
  ordered list for bringing the green up to the same configuration,
  with the check that says each step took.
- [`battery-power-wiring.md`](./battery-power-wiring.md) — the full power
  build, terminal sizes, and the wiring order
- [`field-deployment.md`](./field-deployment.md) — hardware BOM and
  placement
