"""Find the swings inside a recorded session, so only they go up.

WHY. A session runs until the tee box empties, which on a par-3 with a
group waiting is 36-126 s holding three or four swings and a lot of
people standing about. All of it is uploaded. At 4000 kbps a 120 s
clip is 60 MB, which on a modem that re-enumerates every couple of
minutes takes eleven minutes and usually does not finish. Three 13.5 s
cuts are 6.75 MB each, about seventy seconds, and they fit between the
drops. That is the win -- not the total bytes, but fitting in the gap.

WHAT THIS IS NOT. It is not a trigger. The ROI dwell still decides
when to record; this decides what to KEEP, after the fact, from
footage already on disk in the segment ring. Nothing here can lose a
swing that was recorded -- a session with no detections uploads whole,
exactly as today.

THE SIGNAL, measured on real tee footage (docs/swing-detection-eval.md):

    swing             5 - 7     <- ratio of smoothed motion to median
    people milling    2 - 4
    quiet             ~1

So the margin is about 2:1, NOT the 10:1 that "a static scene with a
sudden burst" would give. A foursome walking, pulling clubs and taking
practice swings is the noise floor, and it is high. Two consequences
run through every default below:

  * THE THRESHOLD IS LOW AND THE BIAS IS TO UPLOAD. A missed shot is a
    refund; a needless 6.75 MB is nothing. When in doubt, keep it.
  * VIDEO ALONE CANNOT TELL A SWING FROM A PRACTICE SWING. Audio can
    (a strike is acoustically unique) and `confirm_times` exists for
    when the mic is wired. Until then this over-collects on purpose.

WHY NOT REUSE detect_swings_combined. Measured against ground truth on
event-736 it reported the two WEAKEST candidates in the clip (people
milling, ratios 3.7) and missed all three real swings, including the
largest motion event in the first 90 s. The signal it computes is
sound; the peak-picking on top of it is not. This module keeps the
signal and replaces the selection with ordinary local-maximum finding.
"""

from __future__ import annotations

import logging
from typing import Iterable, Optional, Sequence

log = logging.getLogger("golfreelz_agent.swingfind")

# Ratio above the session's own median motion for a burst to count.
# Swings measured 5-7 and a milling foursome 2-4, so 4.0 sits in the
# gap while leaving the bias where it belongs -- on keeping footage.
DEFAULT_MIN_RATIO = 4.0

# Two peaks closer than this are the same swing seen twice (backswing
# and follow-through both spike). Matches detect_swings_from_audio so
# the two detectors agree about what "one swing" means.
DEFAULT_MIN_SEPARATION = 6.0

# The window kept around an impact. Same figures as the audio path,
# for the same reason: the address is worth having and the ball flight
# is the point.
DEFAULT_BEFORE = 4.5
DEFAULT_AFTER = 9.0

# Smoothing span. A swing's motion burst lasts ~0.8 s; 0.15 s removes
# single-frame noise without flattening the burst.
DEFAULT_SMOOTH_SECONDS = 0.15

# A SWING IS A SPIKE; WALKING IS A PLATEAU. Amplitude alone cannot
# tell them apart -- measured on event-736, the group walking off the
# tee scored 9.68, HIGHER than any real swing in the clip, because
# four bodies crossing the frame move more pixels than one person
# swinging. Burst width does separate them:
#
#     swing  ~1 s   peak 4.73   width 0.38 s
#     swing ~52 s   peak 6.39   width 0.46 s
#     swing ~60 s   peak 4.94   width 4.62 s
#     WALKING ~92 s peak 9.68   width 7.30 s
#
# Width is measured at half the peak, which is scale-free -- it does
# not care how bright or contrasty the scene is.
_WIDE_BURST = 3.0          # above this, treat as locomotion: low confidence
_NARROW_BURST = 1.5        # a swing's own burst is well under a second

# Hard reject. Deliberately generous: 7.3 s of walking is out, a 5 s
# ambiguity is still uploaded, because the bias is to keep footage.
DEFAULT_MAX_BURST = 6.0

_CONF_HIGH = 6.0
_CONF_MEDIUM = 4.5


def _median(xs: Sequence[float]) -> float:
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return 0.0
    mid = n // 2
    return s[mid] if n % 2 else 0.5 * (s[mid - 1] + s[mid])


def smooth(values: Sequence[float], span: int) -> list:
    """Centred moving average, edges included rather than trimmed.

    Edge handling matters: a swing in the first second of a clip is
    common (the trigger fires on the player walking up and they hit
    almost at once), and a windowing scheme that drops the edges
    silently loses it.
    """
    n = len(values)
    if n == 0:
        return []
    span = max(1, int(span))
    if span <= 1:
        return list(values)
    half = span // 2
    out = []
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        out.append(sum(values[lo:hi]) / float(hi - lo))
    return out


def burst_width(ratio: Sequence[float], index: int, rate: float) -> float:
    """Seconds the signal stays above HALF the peak, around `index`.

    Half-peak rather than an absolute level so the measure does not
    change with scene brightness or contrast. This is the one number
    that separates a swing from a group walking across frame, and
    amplitude is not.
    """
    n = len(ratio)
    if n == 0 or rate <= 0 or not (0 <= index < n):
        return 0.0
    half = ratio[index] * 0.5
    lo = index
    while lo > 0 and ratio[lo] > half:
        lo -= 1
    hi = index
    while hi < n - 1 and ratio[hi] > half:
        hi += 1
    return (hi - lo) / float(rate)


def find_swings(
    times: Sequence[float],
    motion: Sequence[float],
    *,
    min_ratio: float = DEFAULT_MIN_RATIO,
    min_separation_sec: float = DEFAULT_MIN_SEPARATION,
    before_sec: float = DEFAULT_BEFORE,
    after_sec: float = DEFAULT_AFTER,
    smooth_seconds: float = DEFAULT_SMOOTH_SECONDS,
    max_burst_sec: float = DEFAULT_MAX_BURST,
    duration_sec: Optional[float] = None,
    confirm_times: Optional[Iterable[float]] = None,
    confirm_tolerance_sec: float = 1.0,
) -> list:
    """Swing windows from a motion signal sampled during recording.

    `times` and `motion` are parallel: the wall-clock offset of each
    sample and the mean absolute frame difference at it.

    `confirm_times` are impact times from another source -- the mic,
    once it is wired. A peak that matches one is promoted to "high"
    whatever its ratio, because audio knows what video cannot: that
    the club met the ball. Peaks with no match are still returned;
    audio CONFIRMS, it does not veto, since a missed microphone would
    otherwise throw away every swing.

    Returns a list shaped like detect_swings_from_audio's, so the rest
    of the pipeline cannot tell which detector produced it.
    """
    n = min(len(times), len(motion))
    if n < 3:
        return []
    times = list(times[:n])
    motion = [float(m) for m in motion[:n]]

    span_sec = times[-1] - times[0]
    if span_sec <= 0:
        return []
    rate = (n - 1) / span_sec                     # samples per second
    sm = smooth(motion, int(round(rate * smooth_seconds)))

    med = _median(sm)
    if med <= 0:
        # A perfectly still signal has no median to divide by. That is
        # not a swing-free session, it is a broken one (a frozen
        # camera), and inventing ratios from it would manufacture
        # detections. Say nothing and let the whole clip go up.
        return []
    ratio = [v / med for v in sm]

    # LOCAL MAXIMA, not "the first N above a line". The bug in the
    # detector this replaces was selection, not signal: a long shallow
    # rise from people walking crosses a threshold earlier and wider
    # than a swing's sharp spike, so threshold-and-group hands back the
    # milling and never reaches the swing.
    peaks = []
    for i in range(1, n - 1):
        if ratio[i] < min_ratio:
            continue
        if ratio[i] >= ratio[i - 1] and ratio[i] > ratio[i + 1]:
            peaks.append((ratio[i], times[i], i))

    # Strongest first, then suppress anything too close to one already
    # taken. Order matters: keeping the EARLIEST of a cluster would
    # keep the backswing and drop the impact.
    peaks.sort(reverse=True)
    kept: list = []
    for r, t, i in peaks:
        if any(abs(t - k[1]) < min_separation_sec for k in kept):
            continue
        w = burst_width(ratio, i, rate)
        if max_burst_sec > 0 and w > max_burst_sec:
            # Sustained motion over this long is people moving about,
            # not a club. Dropped rather than demoted: a 7 s "swing"
            # window is mostly footage of a group walking away.
            log.debug("swingfind: %.2fs burst at t=%.1f (ratio %.1f) "
                      "looks like locomotion, not a swing", w, t, r)
            continue
        kept.append((r, t, w))
    kept.sort(key=lambda k: k[1])

    confirms = sorted(float(c) for c in (confirm_times or []))
    t0 = times[0]
    end_cap = (t0 + duration_sec) if duration_sec else times[-1]

    out = []
    for r, t, w in kept:
        confirmed = any(abs(t - c) <= confirm_tolerance_sec for c in confirms)
        if confirmed:
            # Audio heard the strike. Nothing video measures outranks
            # that, so width and ratio stop mattering here.
            conf = "high"
        elif w >= _WIDE_BURST:
            # Broad and slow. Could be a swing buried in movement,
            # could be a group shuffling -- keep it, flag the doubt.
            conf = "low"
        elif w <= _NARROW_BURST and r >= _CONF_MEDIUM:
            conf = "high"
        elif r >= _CONF_HIGH:
            conf = "high"
        elif r >= _CONF_MEDIUM:
            conf = "medium"
        else:
            conf = "low"
        out.append({
            "peak_time_sec": round(t, 3),
            "start_sec": round(max(t0, t - before_sec), 3),
            "end_sec": round(min(end_cap, t + after_sec), 3),
            "ratio": round(r, 2),
            "burst_duration_sec": round(w, 3),
            "confidence": conf,
            "audio_confirmed": confirmed,
        })
    return out


def coverage_fraction(windows: Sequence[dict], duration_sec: float) -> float:
    """How much of the session the cut windows actually cover, 0..1.

    THE DECISION TO CUT IS NOT FREE. Four swings in two minutes means
    the windows nearly tile the session, and uploading them separately
    costs more than the whole clip once overlaps and per-upload
    overhead are counted. The caller uses this to decide whether
    cutting is worth it at all; `merge_windows` first, or this
    double-counts the overlaps.
    """
    if duration_sec <= 0 or not windows:
        return 0.0
    total = sum(max(0.0, w["end_sec"] - w["start_sec"]) for w in windows)
    return min(1.0, total / duration_sec)


def merge_windows(windows: Sequence[dict]) -> list:
    """Fuse overlapping windows so a swing is never uploaded twice.

    Two swings 8 s apart produce windows that overlap by 5.5 s. Sent
    separately that is 5.5 s of duplicated video on a link that cannot
    spare it, and two clips showing the same shot.
    """
    if not windows:
        return []
    ws = sorted(windows, key=lambda w: w["start_sec"])
    out = [dict(ws[0])]
    for w in ws[1:]:
        last = out[-1]
        if w["start_sec"] <= last["end_sec"]:
            last["end_sec"] = max(last["end_sec"], w["end_sec"])
            if w["ratio"] > last["ratio"]:
                last["ratio"] = w["ratio"]
                last["peak_time_sec"] = w["peak_time_sec"]
                last["confidence"] = w["confidence"]
            last["audio_confirmed"] = (
                last.get("audio_confirmed") or w.get("audio_confirmed"))
        else:
            out.append(dict(w))
    return out
