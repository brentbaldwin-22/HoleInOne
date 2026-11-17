#!/usr/bin/env python3
"""The drawn cone must be the cone the gate applied.

A picture that illustrates a gate is believed, so it is worse than no
picture when it disagrees. The gate measures a chain's heading as

    bearing = degrees(atan2(vx, -vy))

-- 0 straight up, positive leaning RIGHT, because y grows downward in
an image. Draw the wedge with any other convention and it comes out
mirrored or rotated, and every chain in the picture then sits on the
wrong side of a boundary the gate applied correctly. The operator
would be reading a cone that was never used.

So this checks the pixels: for a cone of +45..+77, the up-and-right
direction from the apex must be inside the shaded wedge and the
up-and-left must not.

It also checks the window, which is the other half of the question
("frames impact..impact+N"), and that every refusal says why.

    python3 tools/cone_picture_test.py
"""
import math
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from app.routers import admin  # noqa: E402

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-58s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


W, H = 640, 480
APEX = (320, 400)


def _clip(path, frames=120):
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                         30.0, (W, H))
    for _ in range(frames):
        vw.write(np.full((H, W, 3), 40, dtype=np.uint8))
    vw.release()


def _chain(first_frame, pts, why=None):
    return {"first_frame": first_frame,
            "points": [{"x": x, "y": y} for x, y in pts],
            "why": list(why or []),
            "why_primary": (why or [None])[0]}


def _run(considered, cone=(45.0, 77.0), cone_on=True, win=50,
         impact=60, ball=APEX):
    sweep = {"considered": considered, "cone_deg": list(cone),
             "cone_on": cone_on, "window_frames": win,
             "why_colors": {"cone": "#ff5555", "tilt": "#ffaa00"},
             "tee_line_y": 410}
    al = {"measured_ball": ball, "measured_impact_frame": impact}

    class _Row:
        id = 9001
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "tee.mp4"
        _clip(src)
        _was = admin.CLIPS_DIR
        admin.CLIPS_DIR = Path(td)
        try:
            return admin._ascent_cone_image(
                _Row(), src, sweep, al, {}, 30.0), Path(td)
        finally:
            admin.CLIPS_DIR = _was


# ── 1. THE CONVENTION ────────────────────────────────────────────────
print("the wedge is drawn in the gate's own angle convention:")


def _drawn(cone=(45.0, 77.0)):
    """Return the written image for a cone, as an array."""
    # A CHAIN, because the function rightly declines when the sweep
    # weighed none -- there is no cone picture to draw for a search
    # that considered nothing. Put it far from the rays being probed.
    sweep = {"considered": [_chain(70, [(20, 460), (24, 455)])],
             "cone_deg": list(cone), "cone_on": True,
             "window_frames": 50, "why_colors": {}, "tee_line_y": 410}
    al = {"measured_ball": APEX, "measured_impact_frame": 60}

    class _Row:
        id = 9001
    td = tempfile.mkdtemp()
    src = Path(td) / "tee.mp4"
    _clip(src)
    _was = admin.CLIPS_DIR
    admin.CLIPS_DIR = Path(td)
    try:
        o = admin._ascent_cone_image(_Row(), src, sweep, al, {}, 30.0)
        if not o.get("ok"):
            return None, o
        f = sorted(Path(td).glob("cone-*.jpg"))
        return cv2.imread(str(f[0])), o
    finally:
        admin.CLIPS_DIR = _was


im, o = _drawn((45.0, 77.0))
check("a cone of +45..+77 drew", im is not None)
if im is not None:
    def _at(deg, dist=120):
        r = math.radians(deg)
        x = int(round(APEX[0] + math.sin(r) * dist))
        y = int(round(APEX[1] - math.cos(r) * dist))
        return im[y, x].astype(int)

    def _greener(deg):
        """The wedge is a green wash, so inside is greener than the
        same distance outside it on the flat background."""
        b, g, r = _at(deg)
        return int(g) - int((b + r) // 2)

    inside = _greener(61.0)      # the middle of +45..+77
    left = _greener(-61.0)       # mirrored: must NOT be shaded
    up = _greener(0.0)           # straight up: outside this cone
    check("up-and-RIGHT at +61 is inside the wedge", inside > 6)
    check("up-and-LEFT at -61 is NOT (not mirrored)", left <= 6)
    check("straight up is NOT (not rotated)", up <= 6)

# ── 2. THE WINDOW ────────────────────────────────────────────────────
print()
print("only chains starting in impact..impact+N are counted:")
_in = _chain(70, [(330, 390), (345, 350), (360, 310)])
_late = _chain(400, [(330, 390), (345, 350)])
_early = _chain(10, [(330, 390), (345, 350)])
out, _ = _run([_in, _late, _early], win=50, impact=60)
check("one chain in the window", out.get("n_in_window"), 1)
check("and it was kept", out.get("n_kept"), 1)
check("the window is reported", out.get("last_frame"), 110)

print()
print("a chain the cone refused is counted and named:")
out, _ = _run([_chain(70, [(330, 390), (300, 350)], why=["cone"]),
               _chain(75, [(330, 390), (345, 350)])])
check("one refused by the cone", out.get("n_refused_by_cone"), 1)
check("one kept", out.get("n_kept"), 1)
check("the tally names the gate", out.get("why_tally"), {"cone": 1})

# ── 3. EVERY REFUSAL SAYS WHY ────────────────────────────────────────
print()
print("and it never fails silently:")
out, _ = _run([_chain(70, [(1, 1), (2, 2)])], cone_on=False)
check("no cone set -> declines", out.get("ok"), False)
check("...and says to set one",
      "ascent_cone_from_deg" in (out.get("why") or ""))


class _R2:
    id = 9002


with tempfile.TemporaryDirectory() as td:
    src = Path(td) / "t.mp4"
    _clip(src)
    o = admin._ascent_cone_image(
        _R2(), src,
        {"considered": [_chain(70, [(1, 1)])], "cone_deg": [45.0, 77.0],
         "cone_on": True, "window_frames": 50},
        {}, {}, 30.0)
    check("no impact frame -> declines", o.get("ok"), False)
    check("...and says so", "no impact frame" in (o.get("why") or ""))

    o = admin._ascent_cone_image(
        _R2(), src,
        {"considered": [], "cone_deg": [45.0, 77.0], "cone_on": True,
         "window_frames": 50},
        {"measured_ball": APEX, "measured_impact_frame": 60}, {}, 30.0)
    check("no chains at all -> declines", o.get("ok"), False)
    check("...and says the sweep weighed none",
          "weighed no chains" in (o.get("why") or ""))

print()
print("the page renders it, and is not behind a closed fold:")
prod = (ROOT / "frontend" / "src" / "pages"
        / "AdminProduction.jsx").read_text()
check("cone_search is rendered", "cone_search" in prod)
import re  # noqa: E402


def _in_closed_details(text, idx):
    """True when idx sits inside a <details> that has no `open`.

    JSX COMMENTS ARE NOT MARKUP. These files explain themselves, and
    several of those explanations are ABOUT <details> -- so counting
    tags inside `{/* ... */}` made every line after a comment look
    nested and the check cried wolf on a card that is not folded at
    all. A check that cries wolf is worse than none: it gets deleted,
    and the real one goes with it.
    """
    body = re.sub(r'\{/\*.*?\*/\}', '', text[:idx], flags=re.S)
    depth = 0
    for m in re.finditer(r'<details([^>]*)>|</details>', body):
        depth += -1 if m.group(0).startswith("</") else 1
    return depth > 0


_u = [m.start() for m in re.finditer(r'cone_search', prod)]
check("at least one use is not inside a collapsed <details>",
      any(not _in_closed_details(prod, i) for i in _u))
check("and the failure reason is rendered too",
      "cone_search.why" in prod)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("all ok — the drawn cone is the cone the gate applied")
