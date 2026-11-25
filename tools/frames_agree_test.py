#!/usr/bin/env python3
"""A swing whose ball left before it was struck.

THE RECORD CONTRADICTED ITSELF AND NOTHING READ IT BACK. The clip's
window is built from the LAUNCH frame -- where a detector first picked
the ball out of the sky -- and the impact frame comes from the cutter.
Nothing compared them, so a produce could write a swing saying the
ball left four and a half seconds before the club reached it.

That is what #1916 did: a clip cut from f1527-f1781, an impact of
f1920, and an edit wizard showing both numbers with nothing saying
they disagree. It is not a late ascent or a loose gate; a launch
before the impact means the chain belongs to another swing.

RECORDED, NOT CORRECTED. Which of the two is wrong is a question
about the ascent search, and moving one to agree with the other would
destroy the evidence for answering it.

    python3 tools/frames_agree_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.routers.admin import _frames_disagree  # noqa: E402

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


print(__doc__.strip().splitlines()[0])

print("\n1. a swing that holds together says nothing")
check("impact inside the window, launch after it",
      _frames_disagree(1600, 1650, 1527, 1781, 60), None)
check("launch on the impact frame itself is fine",
      _frames_disagree(1600, 1600, 1527, 1781, 60), None)
check("and the first frame of the window is inside it",
      _frames_disagree(1527, 1600, 1527, 1781, 60), None)
check("as is the last", _frames_disagree(1781, 1781, 1527, 1781, 60), None)

print("\n2. #1916, reproduced")
_m = _frames_disagree(1920, 1647, 1527, 1781, 60)
check("it is reported", bool(_m))
check("...naming the launch frame", "f1647" in _m)
check("...and the impact", "f1920" in _m)
check("...and how far apart in seconds", "4.55s" in _m)
check("...and that a ball cannot leave before it is struck",
      "cannot leave before it is struck" in _m)
check("...and what that most likely means",
      "another swing" in _m)

print("\n3. an impact outside the clip it was cut from")
check("before the window",
      "outside the frames" in (_frames_disagree(1500, 1600, 1527, 1781, 60)
                               or ""))
check("after the window",
      "outside the frames" in (_frames_disagree(2000, 2100, 1527, 1781, 60)
                               or ""))
check("...and it says the clip does not contain its own strike",
      "does not contain the strike"
      in (_frames_disagree(2000, 2100, 1527, 1781, 60) or ""))

print("\n4. it never throws, whatever the produce hands it")
for args in ((None, 1647, 1527, 1781, 60), ("x", 1647, 1527, 1781, 60),
             (1920, None, 1527, 1781, 60), (1920, 1647, 1527, 1781, 0),
             (1920, 1647, None, 1781, 60)):
    try:
        _frames_disagree(*args)
        check("  %r survives" % (args,), True)
    except Exception as exc:  # noqa: BLE001
        check("  %r survives" % (args,), "raised %s" % exc)
# A ZERO FPS MUST NOT DIVIDE BY ZERO: it falls back rather than
# taking the produce down on the last line of a successful render.
check("zero fps still reports", bool(_frames_disagree(1920, 1647, 1527,
                                                      1781, 0)))

print("\n5. the produce stores it, and the wizard shows it")
SRC = (ROOT / "backend/app/routers/admin.py").read_text()
check("stored on the swing record", '"frames_disagree": _frames_disagree('
      in SRC)
check("...from the clip's own window",
      "int(round(t0 * fps)), int(round(t1 * fps)), fps)" in SRC)
PAGE = (ROOT / "frontend/src/pages/AdminProduction.jsx").read_text()
check("the wizard reads it", "swing?.frames_disagree" in PAGE)
check("...beside the impact frame it contradicts",
      PAGE.index("swing?.frames_disagree")
      < PAGE.index('label="Impact frame"'), True)

print("\n6. the screens work it out, rather than reading a stored flag")
# A FLAG THE PRODUCE STAMPED ONLY APPEARS ON CLIPS PRODUCED SINCE IT
# EXISTED, and the clip somebody is staring at is usually an older
# one. Both screens compute it from the three numbers they already
# have, so an old record is judged on what it says, not on whether
# the produce that wrote it knew to check.
PAGE = (ROOT / "frontend/src/pages/AdminProduction.jsx").read_text()
check("the wizard computes it", "const framesDisagree = (() => {" in PAGE)
check("...from the swing's own numbers",
      "swing?.start_frame" in PAGE and "swing?.impact_frame" in PAGE)
check("...and still honours a stored one as a fallback",
      "swing?.frames_disagree || null" in PAGE)
FLOW = (ROOT / "backend/app/routers/production_flow.py").read_text()
check("the flow's clip row carries the window",
      '_r["cut_frames"]' in FLOW)
check("...and the impact it claims", '_r["impact_frame"]' in FLOW)
check("...and works the contradiction out itself",
      'int(_lo) <= int(_i) <= int(_hi)' in FLOW)
CARD = (ROOT / "frontend/src/pages/AdminProductionFlow.jsx").read_text()
check("the draft card prints the window", "cut f{c.cut_frames[0]}" in CARD)
check("...and says when it does not contain its own impact",
      "c.frames_disagree" in CARD)

print("\n%s" % ("all ok" if not FAILED else "%d FAILED:\n  %s" % (
    len(FAILED), "\n  ".join(FAILED))))
sys.exit(1 if FAILED else 0)
