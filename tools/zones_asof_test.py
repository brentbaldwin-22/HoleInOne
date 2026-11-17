#!/usr/bin/env python3
"""Production must use the zones that were up when the swing happened.

THE BUG THIS PREVENTS. The tee markers move every morning and the
trigger zones are redrawn to match, but `camera.tee_box_roi` holds
only the latest drawing. Producing yesterday's swings after drawing
today's zones therefore judged them against a piece of grass nobody
had teed off from -- and nothing on the page said so, because the
zones were never printed beside the verdict. A wrong answer that
looks exactly like a right one.

Reported from the field as: "we can't have updated trigger zones for
today affect production of swings from yesterday or earlier."

Three sources, best first, and the test is as much about the ORDER as
about any one of them:

  1. the event's own snapshot, taken when the rig fired -- exact, and
     the only one that survives a redraw halfway through a round;
  2. the camera's dated history, resolved against the clip's own
     capture day;
  3. the camera's current zones, right only for clips shot since the
     last redraw -- and it must SAY so.

    python3 tools/zones_asof_test.py
"""
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.routers import admin  # noqa: E402
from app.services import tee_roi  # noqa: E402

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-58s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


def box(x):
    """A distinguishable zone drawing."""
    return {"boxes": [{"x": x, "y": 10, "w": 40, "h": 40}],
            "frame": {"w": 1920, "h": 1080}}


YDAY, TODAY = "2026-11-15", "2026-11-16"


class _Cam:
    id = 7

    def __init__(self, current, days=None):
        self.tee_box_roi = current
        self.tee_box_roi_days = days or {}


class _Ev:
    tee_camera_id = 7

    def __init__(self, snap=None):
        self.tee_box_roi_at_trigger = snap


class _Row:
    camera_event_id = 99

    def __init__(self, day=YDAY):
        self.base_captured_at = datetime.strptime(day, "%Y-%m-%d")


class _Db:
    def __init__(self, ev, cam):
        self._ev, self._cam = ev, cam

    def get(self, model, _id):
        return self._ev if model.__name__ == "CameraEvent" else self._cam


def asof(ev, cam, row=None):
    return admin._tee_box_roi_asof(_Db(ev, cam), row or _Row())


print("the event's own snapshot wins over everything:")
roi, why = asof(_Ev(box(1)), _Cam(box(9), {YDAY: box(5), TODAY: box(9)}))
check("it used the snapshot", tee_roi.boxes(roi)[0]["x"], 1)
check("  and says it was this swing's own",
      "actually fired on" in (why or ""))

print()
print("without one, the clip's OWN DAY, not today's drawing:")
# THE BUG, EXACTLY: yesterday's clip, today's zones already redrawn.
roi, why = asof(_Ev(None), _Cam(box(9), {YDAY: box(5), TODAY: box(9)}))
check("it used yesterday's drawing", tee_roi.boxes(roi)[0]["x"], 5)
check("  not today's", tee_roi.boxes(roi)[0]["x"] != 9)
check("  and names the day", YDAY in (why or ""))

print()
print("nothing filed for that day -> the last drawing BEFORE it:")
# Never a LATER one, however close: zones drawn after a clip was shot
# describe grass it was never hit from.
roi, why = asof(_Ev(None),
                _Cam(box(9), {"2026-11-10": box(3), TODAY: box(9)}))
check("it reached back, not forward", tee_roi.boxes(roi)[0]["x"], 3)
check("  and says which day it took", "2026-11-10" in (why or ""))

print()
print("no history at all -> current, and it must admit it:")
roi, why = asof(_Ev(None), _Cam(box(9), {}))
check("it fell back to current", tee_roi.boxes(roi)[0]["x"], 9)
check("  and says they may have been redrawn since",
      "redrawn since" in (why or ""))

print()
print("and nothing to go on is None, not a guess:")
check("no camera on the event",
      asof(_Ev(None), None)[0], None)
check("no zones anywhere",
      asof(_Ev(None), _Cam(None, {}))[0], None)

print()
print("the save files today's drawing under today:")
cam = _Cam(None, {})
day = admin._keep_tee_zone_day(cam, box(4))
check("it filed something", bool(day))
check("  under today", day, datetime.utcnow().strftime("%Y-%m-%d"))
check("  and the drawing is retrievable",
      tee_roi.boxes(cam.tee_box_roi_days[day])[0]["x"], 4)
# AN OPERATING RECORD, NOT A HISTORY -- a month, like tee_boxes.
from datetime import timedelta  # noqa: E402

_start = datetime(2020, 1, 1)
_keys = [(_start + timedelta(days=i)).strftime("%Y-%m-%d")
         for i in range(40)]
cam2 = _Cam(None, {k: box(i) for i, k in enumerate(_keys)})
admin._keep_tee_zone_day(cam2, box(99))
check("it keeps a month and prunes the rest",
      len(cam2.tee_box_roi_days) <= 31)
check("  the OLDEST went", _keys[0] not in cam2.tee_box_roi_days)
check("  the newest stayed", _keys[-1] in cam2.tee_box_roi_days)

print()
print("the trigger snapshot is taken server-side, with no rig change:")
_cams = (ROOT / "backend" / "app" / "routers" / "cameras.py").read_text()
check("the event records the zones it fired on",
      "tee_box_roi_at_trigger=cam.tee_box_roi" in _cams)

print()
print("and the run says which drawing it used:")
_adm = (ROOT / "backend" / "app" / "routers" / "admin.py").read_text()
check("the report carries it", 'rep["zones_from"]' in _adm)
_prod = (ROOT / "frontend" / "src" / "pages"
         / "AdminProduction.jsx").read_text()
check("the page prints it", "zones_from" in _prod)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("all ok — a swing is judged on the grass it was hit from")
