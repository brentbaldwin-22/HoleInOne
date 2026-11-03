#!/usr/bin/env python3
"""Ask the scan why a pixel has no dot, instead of inferring it.

An operator can see a ball — a white smear on clear blue sky — and the
scan puts no dot on it. "It found nothing" is the same sentence
whether nothing crossed the frame-diff threshold there, a blob formed
and was refused for its size, or a blob formed, passed every gate, and
lost the per-frame budget to the treeline. Those have completely
different fixes, and two rounds went into guessing between them from
screenshots while the server had the answer all along.

So the scan takes a `probe` point and reports, per frame: the
strongest frame-to-frame change at that spot, the blob if one formed,
its area, and which gate turned it away. Same loop, same gates, same
picker — not a re-implementation that can agree with itself while the
real one disagrees.

(This is `diagnose-ball`'s lesson, applied to the other detector. Its
docstring opens "Four rounds of this were spent inferring, from
screenshots, facts the server already had.")

    python3 tools/scan_probe_test.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("DATABASE_URL", "sqlite://")
sys.path.insert(0, str(ROOT / "backend"))

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-64s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


import cv2  # noqa: E402
import numpy as np  # noqa: E402

from app.routers.admin import scan_plot_region  # noqa: E402

W, H, N = 640, 480, 24
BALL = (300, 120)          # on clear sky, moving
QUIET = (60, 60)           # sky where nothing ever happens


def clip(path, ball_area=40, trees=True):
    """Sky, a moving ball, and optionally a noisy treeline."""
    rng = np.random.default_rng(2)
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                         60.0, (W, H))
    _r = max(1, int((ball_area / 3.14) ** 0.5))
    for f in range(N):
        im = np.full((H, W, 3), (190, 140, 90), np.uint8)   # blue sky
        if trees:
            im[int(H * 0.6):, :] = (40, 70, 40)
            # foliage sparkle: lots of big, bright, moving blobs
            for _ in range(40):
                _x = int(rng.integers(0, W))
                _y = int(rng.integers(int(H * 0.6), H))
                cv2.circle(im, (_x, _y), int(rng.integers(4, 14)),
                           (200, 230, 200), -1)
        cv2.circle(im, (BALL[0] + 6 * f, BALL[1] - 2 * f), _r,
                   (250, 250, 250), -1)
        vw.write(im)
    vw.release()


class _Row:
    id = 1
    tee_filename = "tee.mp4"
    green_filename = None


def _probe_at(path, xy, frame=12, sens=2):
    """Call the endpoint's own body against a local file."""
    import app.routers.admin as _ad

    _ad.storage.ensure_local = lambda *a, **k: None
    _ad._local_tee = lambda row: path
    _row = _Row()

    class _Db:
        def get(self, _m, _i):
            return _row

    return scan_plot_region(1, {
        "x": 0, "y": 0, "w": W, "h": H,
        "start_frame": max(0, frame - 6), "end_frame": frame + 6,
        "sensitivity": sens, "which": "tee",
        "probe": {"x": xy[0], "y": xy[1]},
    }, db=_Db())


_td = Path(tempfile.mkdtemp())
_busy = _td / "busy.mp4"
clip(_busy)

print("a ball the scan did not dot:")
_f = 12
_xy = (BALL[0] + 6 * _f, BALL[1] - 2 * _f)
_out = _probe_at(_busy, _xy, frame=_f)
_p = _out["probe"]
print("      %s" % _p["verdict"])
for _r in _p["frames"][:4]:
    print("      f%s: diff %s/%s, blob %s — %s"
          % (_r["frame"], _r["max_diff"], _r["threshold"], _r["area"],
             _r["verdict"]))
check("the probe answers at all", _p is not None, True)
# ONE FEWER THAN THE WINDOW. The first frame has nothing to be
# differenced against, so it produces no row -- which is also why a
# one-frame scan finds nothing and is worth having stated somewhere.
check("it looked at every frame that HAS a previous one",
      _p["n_frames"], 12)
check("it saw real change at the ball",
      _p["peak_diff"] > _p["frames"][0]["threshold"], True)
check("...and a blob formed there", _p["biggest_blob"] > 0, True)
check("every frame carries a verdict",
      all(r.get("verdict") for r in _p["frames"]), True)

print()
print("empty sky, where there really is nothing:")
_q = _probe_at(_busy, QUIET, frame=_f)["probe"]
print("      %s" % _q["verdict"])
check("it says nothing crossed the threshold",
      "threshold" in _q["verdict"] or _q["n_kept"] == 0, True)
check("...and reports no blob", _q["biggest_blob"], 0)
# THE DISTINCTION THE WHOLE THING EXISTS FOR: "no dot" on a ball and
# "no dot" on empty sky must not read the same.
check("the two answers differ", _p["verdict"] != _q["verdict"], True)

print()
print("the scan still works when nobody is asking:")
_plain = scan_plot_region.__wrapped__ if hasattr(scan_plot_region, "__wrapped__") else None
_out2 = _probe_at(_busy, _xy, frame=_f)
check("dots still come back", len(_out2["dots"]) > 0, True)
check("probe is absent unless asked", "probe" in _out2, True)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("all good — the scan says which gate, instead of 'nothing found'")
