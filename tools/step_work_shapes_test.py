#!/usr/bin/env python3
"""A step's work panel must never be able to kill the produce.

THE CRASH: `_ascent_step3_work` reached into the reverse walk for the
frames it searched —

    _v = (_wk.get("work") or {}).get(_k)

— and `work` is not a dict. greendescent sets it to a SENTENCE:

    out["work"] = ("%d frame(s) looked at, every frame, backwards; ..."

so every produce on that path died with

    'str' object has no attribute 'get'

after the scan, the grading and the walk had all finished. The run had
succeeded; only the formatting of its report had not. Upload #1780
came back as a red line in a modal with nothing to show for several
minutes of work.

Two things are tested here, because the first alone would not have
caught it:

  1. the builders are called with REAL shapes, straight from the
     producers, rather than asserted against by substring; and
  2. `_work` wraps them, so the next mistake in a report costs a
     panel and not a produce.

    python3 tools/step_work_shapes_test.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.routers.admin import _ascent_step3_work  # noqa: E402

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


# THE SHAPE `_ascent_descents` ACTUALLY BUILDS, key for key: `work` a
# sentence, `frames` the dict that holds what the walk searched, `log`
# a list of per-frame dicts.
WALK = {
    "swing": 1,
    "ok": True,
    "n_points": 9,
    "n_missed": 3,
    "fall_px": 214,
    "first_frame": 1402,
    "last_frame": 1463,
    "moved_at_frame": 1455,
    "lost_at": 1402,
    "detector": {"zoom": "6x", "kind": "white top-hat"},
    "work": "61 frame(s) looked at, every frame, backwards; windows "
            "320-640px wide at 2x-6x zoom; 41 candidate(s) in 22 "
            "frame(s); 9 taken",
    "frames": {
        "at_rest": 1463,
        "at_rest_from": "the ball was still for 1.2s from here",
        "at_rest_sec": 24.38,
        "walk_searched": [1402, 1463],
        "walk_first_moved": 1455,
    },
    "log_every": 1,
    "log": [{"frame": 1463 - i, "looked_at": [900, 400], "window_px": 320,
             "zoom": 6, "match_within_px": 40, "n_cands": 2,
             "nearest_px": 11.2, "took": [901, 396 - i], "area": 7,
             "contrast": 38} for i in range(12)],
    "windows": [], "lost_windows": [], "counts": {},
    "image_url": "/uploads/walk.png",
}

DESC = {
    "views": [{
        "swing": 1, "frames": [1283, 1763], "secs": "21.3–29.4",
        "n_dets": 412, "chains": [], "chosen": None,
        "reason": "no chain cleared the gates",
        "image_url": "/uploads/green.png",
    }],
    "descents_back": [WALK],
}

print("\n1. the step-3 panel survives the shapes it is actually given")
try:
    out = _ascent_step3_work(DESC, {"swings": [{"idx": 0}]})
    built = True
    err = None
except Exception as exc:  # noqa: BLE001
    out, built, err = None, False, "%s: %s" % (type(exc).__name__, exc)
check("it builds at all", built, True)
if err:
    print("     -> %s" % err)

if built:
    g = (out.get("groups") or [{}])[0]
    labels = {r.get("label") for r in (g.get("rows") or [])}
    check("the frames it walked come from `frames`, not `work`",
          "frames walked" in labels, True)
    check("...with the frames themselves",
          any(r.get("value") == "f1402–f1463"
              for r in (g.get("rows") or [])), True)
    check("the ball-at-rest frame is stated", "started on the ball at"
          in labels, True)
    check("and the walk's own sentence is shown as one",
          any(r.get("label") == "what the walk did"
              and r.get("value", "").startswith("61 frame(s)")
              for r in (g.get("rows") or [])), True)
    check("the per-frame log is the table", bool(g.get("table")), True)
    check("...and the chains table is kept beside it",
          "table2" in g, True)

print("\n2. a `work` that is a dict, or missing, is not a crash either")
for _w in ({"anything": 1}, None, "", 7):
    _d = {"views": DESC["views"], "descents_back": [dict(WALK, work=_w)]}
    try:
        _ascent_step3_work(_d, {})
        _ok = True
    except Exception as exc:  # noqa: BLE001
        _ok = "%s: %s" % (type(exc).__name__, exc)
    check("  work=%r" % (_w,), _ok, True)

print("\n3. and a broken panel costs a panel, not the produce")
SRC = (ROOT / "backend/app/routers/admin.py").read_text()
_run = SRC[SRC.index("def _ascent_run("):]
_run = _run[:_run.index("\ndef ", 10)]
check("there is a guard around the work builders",
      "def _work(fn, *a):" in _run, True)
check("it catches and reports rather than raising",
      bool(re.search(r"def _work\(fn, \*a\):.*?except Exception.*?"
                     r"the work panel could not be built", _run, re.S)), True)
for _n, _call in ((1, "_work(_ascent_step1_work,"),
                  (3, "_work(_ascent_step3_work,"),
                  (4, "_work(lambda: {")):
    check("  step %d goes through it" % _n, _call in _run, True)
check("nothing calls a builder bare any more",
      re.search(r"work=_ascent_step\d_work\(", _run) is None, True)

print()
if FAILED:
    print("FAILED (%d):" % len(FAILED))
    for f in FAILED:
        print("  -", f)
    sys.exit(1)
print("all ok")
