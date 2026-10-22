"""Run the Pi's own swing detector against a stored tee video.

WHY THIS EXISTS. The tee rig decides what to upload with
`pi-agent/agent/swingfind.py`, and the only way to see what it decided
was to read the agent's log after the fact — one session at a time, on
a Pi that sleeps. Tuning a threshold that way costs a day per guess.
The detector's input is a motion signal that can be recomputed exactly
from footage we already have, so it can be dialled in here instead and
the settled numbers moved to the rig afterwards.

THE PI'S MODULE IS IMPORTED, NOT COPIED. A second implementation would
be tuned here and then disagree with the rig in the field, which is
the one outcome that makes this worse than useless.

WHAT IS NOT IDENTICAL, and matters when reading the output:

  * THE PERSON GATE IS OFF. On the rig, a peak with nobody in the ROI
    near it is dropped (moving cloud scored 4.72 on a camera aimed
    down the hole). That gate needs MediaPipe/YOLO, which the backend
    does not carry, so every peak is shown here. The tester is
    therefore OPTIMISTIC: the rig will find the same peaks or fewer,
    never more. For choosing `min_ratio` and `max_burst_sec` that is
    the right way round — a peak you cannot see is one you cannot
    judge.

  * THE SIGNAL STARTS AT THE CLIP. On the rig the tracker runs
    continuously, so the pre-roll is already measured when a session
    begins; here the first sample is the clip's first frame. The
    median the ratio is taken against is computed over whatever is
    present, so a clip that opens mid-swing has a slightly higher
    median and a slightly lower ratio. Clips that start quiet — which
    is nearly all of them — are unaffected.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# The rig's module, loaded from the repo by path. `pi-agent` is not an
# importable package name (the hyphen), and adding it to sys.path would
# put `agent`, `common` and friends on the backend's import namespace,
# where they would shadow real modules. Loading this one file by spec
# keeps the blast radius to the file we actually want.
_SWINGFIND_PATH = (
    Path(__file__).resolve().parents[3] / "pi-agent" / "agent" / "swingfind.py"
)

_mod = None


def _swingfind():
    """The Pi's swingfind module, loaded once."""
    global _mod
    if _mod is not None:
        return _mod
    if not _SWINGFIND_PATH.exists():
        raise RuntimeError(
            f"the Pi's swingfind module is not at {_SWINGFIND_PATH} — the "
            "tester runs the rig's own code and will not fall back to a "
            "copy, because a copy is what it exists to prevent"
        )
    spec = importlib.util.spec_from_file_location(
        "_pi_swingfind", _SWINGFIND_PATH)
    mod = importlib.util.module_from_spec(spec)
    # Registered under a private name so a second load is a no-op and
    # nothing else can import it as `swingfind`.
    sys.modules["_pi_swingfind"] = mod
    spec.loader.exec_module(mod)
    _mod = mod
    return mod


def pi_defaults() -> dict:
    """The rig's own default thresholds, for seeding the form."""
    sf = _swingfind()
    return {
        "min_ratio": float(sf.DEFAULT_MIN_RATIO),
        "min_separation_sec": float(sf.DEFAULT_MIN_SEPARATION),
        "before_sec": float(sf.DEFAULT_BEFORE),
        "after_sec": float(sf.DEFAULT_AFTER),
        "smooth_seconds": float(sf.DEFAULT_SMOOTH_SECONDS),
        "max_burst_sec": float(sf.DEFAULT_MAX_BURST),
    }


def motion_trace(video_path, *, sample_hz: float = 20.0,
                 zones: Optional[list] = None,
                 zones_fractional: bool = False,
                 max_seconds: Optional[float] = None) -> dict:
    """The motion signal the rig would have measured for this clip.

    Decode is the whole cost here, so only the frames that will be
    sampled are decoded: `grab()` advances without decoding and
    `retrieve()` is called on the ones the tracker wants. On a 120 s
    clip at 50 fps that is ~2400 decodes instead of 6000.
    """
    import cv2

    sf = _swingfind()
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"could not open {video_path}")
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        if not (fps > 0):
            fps = 30.0
        n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)

        tracker = sf.MotionTracker(
            sample_hz=float(sample_hz),
            # keep_seconds must cover the whole clip or the tracker
            # trims the start as it goes and the median is taken over
            # a window instead of the session.
            keep_seconds=1e9,
        )
        if zones and width and height:
            # TEST ZONES ARRIVE AS FRACTIONS, camera zones as pixels.
            # The operator draws on a scaled <img>, so a pixel there is
            # not a pixel in the video; fractions are the only form that
            # survives the trip. The camera's own boxes are already in
            # native pixels and must NOT be scaled again.
            _z = zones
            if zones_fractional:
                _z = [{
                    "x": float(b["x"]) * width,
                    "y": float(b["y"]) * height,
                    "w": float(b["w"]) * width,
                    "h": float(b["h"]) * height,
                } for b in zones]
            tracker.set_zones(_z, width, height)

        # Decode only at sample points. +0.5 so rounding never lands
        # the step on 0 and spins forever on a high-fps clip.
        step = max(1, int(fps / max(1.0, float(sample_hz)) + 0.5))
        i = 0
        decoded = 0
        while True:
            if max_seconds is not None and (i / fps) > max_seconds:
                break
            if not cap.grab():
                break
            if i % step == 0:
                ok, frame = cap.retrieve()
                if not ok:
                    break
                tracker.feed(i / fps, frame)
                decoded += 1
            i += 1

        times, motion = tracker.span(0.0, 1e9)
        return {
            "times": times,
            "motion": motion,
            "fps": fps,
            "frames": n_frames,
            "decoded": decoded,
            "duration_sec": (i / fps) if fps else 0.0,
            "width": width,
            "height": height,
            "zones_used": len(getattr(tracker, "_boxes", []) or []),
        }
    finally:
        cap.release()


def _ratio_curve(sf, times, motion, *, smooth_seconds: float) -> dict:
    """The curve the detector actually thresholds, for plotting.

    NOT RAW MOTION. find_swings smooths the signal and divides by the
    session's own median, then compares THAT to min_ratio -- so a graph
    of raw frame-difference cannot be read against the threshold, and
    the peak an operator sees would not be the number the detector
    used. This recomputes the same curve with the rig's own `smooth`
    and `_median`, mirroring find_swings' rate arithmetic exactly, so
    the line on screen and the decision are the same quantity.

    Reusing the rig's functions rather than reimplementing them is the
    same rule as the rest of this module: a second smoother would drift.
    """
    n = min(len(times), len(motion))
    if n < 3:
        return {"times": [], "ratio": [], "median": 0.0}
    times = list(times[:n])
    motion = [float(m) for m in motion[:n]]
    span = times[-1] - times[0]
    if span <= 0:
        return {"times": [], "ratio": [], "median": 0.0}
    rate = (n - 1) / span
    sm = sf.smooth(motion, int(round(rate * smooth_seconds)))
    med = sf._median(sm)
    if med <= 0:
        # Matches find_swings: a dead-flat signal has no median to
        # divide by, and inventing ratios from it would draw peaks
        # that do not exist.
        return {"times": [round(t, 2) for t in times],
                "ratio": [], "median": 0.0}
    return {
        "times": [round(t, 2) for t in times],
        "ratio": [round(v / med, 3) for v in sm],
        "median": round(med, 4),
    }


# Thresholds worth trying, and burst widths worth pairing them with.
# The ratio list reaches well below the rig's 4.0 on purpose: on a
# camera where a swing is small and distant, the swing itself can sit
# inside the "people milling" band, and the question becomes whether
# WIDTH can separate them once the threshold is low enough to see them
# at all.
_SWEEP_RATIOS = (1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0, 3.5, 4.0)
_SWEEP_BURSTS = (0.8, 1.2, 1.6, 2.5, 4.0, 6.0)


def tune_against_known(video_path, known_sec, *, sample_hz: float = 20.0,
                       zones=None, zones_fractional: bool = False,
                       tolerance_sec: float = 4.0, **params) -> dict:
    """Which settings would have caught the swings you already know about.

    HUNTING A THRESHOLD BY HAND IS THE SLOW WAY and it cannot see the
    cost: lowering min_ratio until the swing appears also admits
    everything else, and the panel gave no way to count what else came
    with it. Given the times of real swings, every combination can be
    scored at once -- caught, and how much rubbish came too.

    find_swings IS CALLED, not reimplemented, once per combination. The
    video is decoded once and the signal reused, so the sweep costs
    arithmetic rather than another minute of ffmpeg.
    """
    sf = _swingfind()
    trace = motion_trace(video_path, sample_hz=sample_hz, zones=zones,
                         zones_fractional=zones_fractional)
    times, motion = trace["times"], trace["motion"]
    known = sorted(float(k) for k in known_sec)
    base = {k: v for k, v in params.items()
            if k in ("min_separation_sec", "before_sec", "after_sec",
                     "smooth_seconds")}

    # What the signal holds AT each swing, independent of any setting.
    # This is the question under the question: if there is no peak at
    # 60s, no threshold will ever find the swing at 60s, and the fix is
    # the zone or the camera rather than a number.
    curve = _ratio_curve(sf, times, motion,
                         smooth_seconds=float(base.get(
                             "smooth_seconds", sf.DEFAULT_SMOOTH_SECONDS)))
    at_swing = []
    if curve["ratio"]:
        n = len(curve["ratio"])
        span = curve["times"][-1] - curve["times"][0]
        rate = (n - 1) / span if span > 0 else 1.0
        for k in known:
            lo = max(0, int((k - tolerance_sec - curve["times"][0]) * rate))
            hi = min(n - 1, int((k + tolerance_sec - curve["times"][0]) * rate))
            if hi <= lo:
                at_swing.append({"at": k, "peak": None})
                continue
            seg = curve["ratio"][lo:hi + 1]
            best_i = lo + max(range(len(seg)), key=lambda j: seg[j])
            at_swing.append({
                "at": k,
                "peak": round(curve["ratio"][best_i], 2),
                "peak_at": round(curve["times"][best_i], 1),
                "burst_sec": round(
                    sf.burst_width(curve["ratio"], best_i, rate), 2),
            })

    rows = []
    for mr in _SWEEP_RATIOS:
        for mb in _SWEEP_BURSTS:
            dbg: dict = {}
            wins = sf.merge_windows(sf.find_swings(
                times, motion, duration_sec=trace["duration_sec"],
                min_ratio=mr, max_burst_sec=mb, person_times=None,
                debug=dbg, **base))
            hit = [w for w in wins
                   if any(abs(w["peak_time_sec"] - k) <= tolerance_sec
                          for k in known)]
            # A window can straddle two known swings; count SWINGS
            # caught, not windows that touched one, or a single greedy
            # window would score as a clean sweep.
            caught = sum(
                1 for k in known
                if any(abs(w["peak_time_sec"] - k) <= tolerance_sec
                       for w in wins))
            rows.append({
                "min_ratio": mr,
                "max_burst_sec": mb,
                "caught": caught,
                "of": len(known),
                "false_positives": len(wins) - len(hit),
                "windows": len(wins),
            })

    # Best first: every swing matters more than any amount of rubbish,
    # because a missed shot is a refund and a spare clip is 6 MB.
    # WHAT THE OPERATOR IS CURRENTLY ASKING FOR, always, even when the
    # sweep ranks it nowhere. "Here are twelve better settings" without
    # the one in the boxes leaves the size of the improvement unstated,
    # and that is the number being decided on.
    _cur_mr = float(params.get("min_ratio", sf.DEFAULT_MIN_RATIO))
    _cur_mb = float(params.get("max_burst_sec", sf.DEFAULT_MAX_BURST))
    _cur_dbg: dict = {}
    _cur_wins = sf.merge_windows(sf.find_swings(
        times, motion, duration_sec=trace["duration_sec"],
        min_ratio=_cur_mr, max_burst_sec=_cur_mb, person_times=None,
        debug=_cur_dbg, **base))
    _cur_hit = [w for w in _cur_wins
                if any(abs(w["peak_time_sec"] - k) <= tolerance_sec
                       for k in known)]
    current = {
        "min_ratio": _cur_mr,
        "max_burst_sec": _cur_mb,
        "caught": sum(1 for k in known
                      if any(abs(w["peak_time_sec"] - k) <= tolerance_sec
                             for w in _cur_wins)),
        "of": len(known),
        "false_positives": len(_cur_wins) - len(_cur_hit),
        "windows": len(_cur_wins),
    }

    rows.sort(key=lambda r: (-r["caught"], r["false_positives"],
                             r["min_ratio"]))
    return {
        "known": known,
        "at_swing": at_swing,
        "current": current,
        "rows": rows[:12],
        "curve": curve,
        "duration_sec": trace["duration_sec"],
        "zones_used": trace["zones_used"],
    }


def detect_swings_pi(video_path, *, sample_hz: float = 20.0,
                     zones: Optional[list] = None,
                     zones_fractional: bool = False,
                     person_times: Optional[list] = None,
                     debug: Optional[dict] = None,
                     **params) -> list:
    """Windows the rig would cut from this clip, plus its own debug dict.

    `params` are passed straight through to the rig's `find_swings`,
    so the names here are the names there and the names in config.
    """
    sf = _swingfind()
    trace = motion_trace(video_path, sample_hz=sample_hz, zones=zones,
                         zones_fractional=zones_fractional)
    dbg: dict = {}
    windows = sf.merge_windows(sf.find_swings(
        trace["times"], trace["motion"],
        duration_sec=trace["duration_sec"],
        person_times=person_times,
        debug=dbg,
        **params,
    ))
    if debug is not None:
        debug.update(dbg)
        debug["trace"] = _ratio_curve(
            sf, trace["times"], trace["motion"],
            smooth_seconds=float(params.get(
                "smooth_seconds", sf.DEFAULT_SMOOTH_SECONDS)),
        )
        debug["trace"]["threshold"] = float(
            params.get("min_ratio", sf.DEFAULT_MIN_RATIO))
        debug["sample_hz"] = sample_hz
        debug["zones_used"] = trace["zones_used"]
        debug["duration_sec"] = trace["duration_sec"]
        debug["decoded_frames"] = trace["decoded"]
        debug["person_gate"] = "off (tester)" if person_times is None else "on"
        debug["coverage"] = sf.coverage_fraction(
            windows, trace["duration_sec"]) if windows else 0.0
    return windows
