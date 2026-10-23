# Continuous capture — design

The target, in the owner's words:

> 1. Continuous local recording with 60-minute circular retention.
> 2. Golfer/tee-box activity detection as the primary way to identify
>    potential shots.
> 3. Swing, ball movement and audio as independent signals that
>    increase confidence.
> 4. Secondary analysis of recorded footage to identify missed shots.
> 5. Upload only confirmed or likely shots, retaining uncertain clips
>    for recovery.

(Ball movement is dropped by decision — motion burst and audio only.)

This replaces the trigger/session model rather than decorating it.
What is shipped today (`swing_select`) cuts a recorded SESSION into
swings; this removes the session.

---

## The economy of the thing: less changes than it looks

**Continuous recording already happens.** `copycap.SegmentRing` runs an
ffmpeg process writing 1-second segments from agent start, with no
reference to any trigger. The log line `copycap: ring started (1s
segments, 155s window)` appears at boot. The trigger has never started
the camera; it only decides which span gets extracted.

**Continuous motion tracking already happens.** `MotionTracker` is fed
from the capture thread whether or not a recording is in progress,
because a clip begins before its trigger.

**The backend already aligns the two cameras by wall clock.** Produce
reads `tee_recording_started_at` and `green_recording_started_at`,
two independently stamped times. Continuous inherits that mechanism;
it does not introduce it.

So three of the five steps are mechanisms that exist. What is missing
is the decision layer and the event model.

---

## 1. Retention

```yaml
copy_window_seconds: 3600        # was buffer+max_clip+30 = 155
```

At 4000 kbps that is **1.8 GB** on disk. The write rate does not
change — the ring already writes 500 KB/s continuously — so SD card
wear is identical. Only the delete is deferred.

Check free space before enabling; `copycap` evicts oldest-first, so a
short card degrades retention rather than failing.

---

## 2-3. Detection: a sweep, not a session end

A watermark walks forward over the motion signal.

```
every SWEEP_INTERVAL (30 s):
    window = [analysed_until - OVERLAP, now - SETTLE]
    impacts = find_swings(signal in window, person_times, confirm_times)
    for each impact not within MIN_SEPARATION of one already emitted:
        emit(impact)
    analysed_until = now - SETTLE
```

* **SETTLE ≈ 12 s** — `after_sec` (9) plus segment margin. A swing
  must have its whole post-window on disk before it is cut, or the
  clip ends mid-ball-flight.
* **OVERLAP ≈ 6 s** — `before_sec` plus margin, so a swing straddling
  the previous boundary is seen whole rather than clipped by an
  arbitrary sweep edge.
* **Dedupe against recently emitted impacts**, not just within one
  sweep: the overlap deliberately re-examines footage, so the same
  swing WILL be found twice and must be emitted once.

Signals, in the order they are consulted:

| signal | role | status |
|---|---|---|
| motion burst in the trigger zones | finds candidates | **built** |
| burst width | rejects locomotion (a group walking scored 9.68, higher than any swing) | **built** |
| person in the ROI | rejects cloud on a sky-aimed camera | **built** |
| audio transient | CONFIRMS; promotes to high. Never vetoes — a dead mic must not mute every swing | **plumbed, needs the mic** |

Trigger zones keep their job, but it changes: they no longer say WHEN
to record, they say WHERE to measure. That is already wired.

---

## 4. Secondary analysis

Between groups the Pi is idle: capture runs, nothing uploads, three
cores do nothing. A second pass over retained footage, at a LOWER
threshold than the live sweep, catches what the first pass missed.

This is what makes aggressive filtering safe. Without an hour of
retention, a missed swing is gone; with it, a miss is a 90-second
delay. The live sweep can therefore be tuned for precision and the
sweep-behind for recall, which is the right division.

---

## 5. The event model: one event per swing

**The central decision: reuse `/event-trigger`.** The Pi calls it when
it has DETECTED a swing rather than when a person has stood still for
two seconds. The backend's event, pairing, produce and Production
pipeline are unchanged — they never knew why the Pi called.

`/event-trigger` gains one optional field:

```
impact_at: float    # wall clock of the impact, not of the call
```

Everything downstream of the event row stays as it is.

### Green coordination

Today: tee triggers -> backend wakes green -> green records -> green
stops when `/event-stop` arrives.

Continuous: the green runs its own ring (it already imports
`copyengine`). The backend's wake message carries `impact_at`, and the
green cuts that window out of its own ring instead of recording live.
`/event-stop` is no longer needed for this path.

**THE CLOCK IS THE RISK.** Both sides now cut by absolute time, so
their clocks must agree. Today the green's recording is bracketed by a
signal, which bounds the error by signal latency; cutting by timestamp
exposes raw clock skew. Two mitigations, both cheap:

* **The green cuts wider** — `impact_at - before - PAD` to
  `impact_at + after + PAD`, PAD ≈ 3 s. Produce already trims against
  the two `recording_started_at` stamps.
* **Report clock health on the heartbeat** so skew is visible on the
  card rather than discovered in a misaligned composite.

A Pi with no internet cannot run NTP — which is exactly the rig we
have been fighting all week — so this must degrade visibly, not
silently.

---

## What this deletes

| gone | why |
|---|---|
| `record_delay_seconds` (hold 10 s) | nothing is "started" any more |
| `max_clip_seconds` cap | no session to cap |
| `no_person_timeout` | no session to end |
| `max_consecutive_splits` / `split_cooldown_seconds` | the runaway guard guarded against capped clips back to back |
| the green's long-poll record loop | it cuts from its ring instead |
| `trigger_dwell_seconds` | dwell no longer starts anything |

The person detector stays, demoted from trigger to confidence signal.

---

## Failure modes

**Backend unreachable at detection time.** Observed 11 Oct: two swings
lost at 17:41 and 17:42 to `[Errno 101]` — `/event-trigger` failed
four times and the agent logged `skipping`. Nothing was recorded,
nothing spooled, the footage is gone.

Under this design that swing is **on disk for an hour**. The impact is
queued locally with its timestamp and retried; when the link returns,
the event is created and the clip cut from the ring. **This is the
single biggest robustness gain in the design and it matters more than
the bandwidth saving.**

**Disk fills.** The ring evicts oldest-first; retention shortens.

**Clock skew.** Wide green cuts plus visible clock health.

**Detector regression.** The secondary pass is the net. And the whole
feature stays behind a flag with the session model intact beneath it,
so a bad build is one config line from the old behaviour.

**Audio, when the mic lands.** Today `arecord` is bracketed per
recording. Continuous needs audio in a ring too, or swing windows cut
from the past have no sound. That is a real change to the audio path
and it is not designed here.

---

## What I am not sure about

* **Does Production want one card per swing, or one per group?** This
  reshapes the operator's day, and it is a product question, not a
  technical one.
* **Hole assignment.** Events today inherit the camera's assigned
  hole; one event per swing does not change that, but four events
  where there was one changes what the Production queue looks like.
* **Whether `swing_count` and the segment editor assume a multi-swing
  clip.** There is an edit wizard for multi-swing uploads; one swing
  per event may make it redundant or may break an assumption in it.

---

## Order of work

1. **Retention to 3600 s** and remove the cap. One config line; makes
   every later step recoverable. Do this first and alone.
2. **The sweep**, emitting through the existing `/event-trigger` with
   `impact_at`. Tee only, green still on the old path.
3. **Local queue for unreachable-backend**, which is where the
   robustness win is. **DONE, 14 Oct, and taken out of order** — the
   tee's modem turned out to re-enumerate every three minutes, which
   made this the step that was actually losing swings.

   IT WAS NOT A NEW QUEUE. Almost all of it already existed: the
   spool holds a clip for 24 h, and `_recover_lost_event` re-registers
   a session the server has no row for at the moment the clip finally
   goes up. What was missing was a way IN. `/event-trigger` is the
   first call on the capture path and was the only one with no
   fallback — it failed, the loop logged `skipping`, and the swing was
   never cut. Nothing downstream ever got a chance.

   Three pieces, listed here because each one is a trap worth
   remembering:

   - **`BackendUnreachable`.** Silence told apart from refusal. A 4xx
     is the backend saying no and recording against it is pointless;
     anything else — dead modem, DNS, timeout, 5xx — is nobody
     answering, and a swing during silence still happened. It
     subclasses `RuntimeError` so every existing handler is unchanged.
   - **`TriggerGate`.** The one veto is the operator's pause, and that
     answer lives on the backend we cannot reach. So `watch-status`
     states `triggering_enabled` on every poll (~1 s) and the agent
     remembers the last answer, on disk, because the rig that needs
     this is the rig that reboots mid-outage. Never heard → record:
     the cost of guessing wrong is a bounded spool, the cost of the
     other default is the whole round.
   - **The recovery tri-state.** Found while wiring the above, and it
     would have eaten exactly the swings this step exists to save:
     `_recover_lost_event` returned a bool, so "the backend refused to
     make an event" and "we could not ask" shared an answer — and that
     answer DELETES the clip. On a link that dies every three minutes,
     a recovery landing in one of those gaps is routine. Now
     `refused` drops and `unreachable` keeps.
4. **Green cuts from its ring** on `impact_at`, plus clock health on
   the heartbeat.
5. **Delete the session machinery** once the above has run a real
   round.
6. **Audio ring** with the mic, and `confirm_times` wired live.

Each step is independently useful and independently revertible. Step 1
alone would have saved the two swings lost on 11 Oct; step 3 is what
saves the ones lost to the modem, and on the current tee rig that is
the larger number.
