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
        debug["trace"] = {
            # The signal itself, so the operator can see the peak that
            # missed rather than only being told that one did.
            "times": [round(t, 2) for t in trace["times"]],
            "motion": [round(m, 3) for m in trace["motion"]],
        }
        debug["sample_hz"] = sample_hz
        debug["zones_used"] = trace["zones_used"]
        debug["duration_sec"] = trace["duration_sec"]
        debug["decoded_frames"] = trace["decoded"]
        debug["person_gate"] = "off (tester)" if person_times is None else "on"
        debug["coverage"] = sf.coverage_fraction(
            windows, trace["duration_sec"]) if windows else 0.0
    return windows
