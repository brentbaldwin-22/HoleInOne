#!/usr/bin/env python3
"""What the Pi says about WHY it fired, and what the server keeps.

THE EVENTS PAGE HAS ALWAYS SAID AN EVENT HAPPENED and never which
mechanism produced it, so a clip of somebody walking to their cart and
a clip of a struck ball read identically. This is the provenance that
tells them apart, and the properties worth pinning are the ones that
would make it lie rather than merely be absent:

  * the detail must never cost the trigger it describes -- every
    builder swallows everything and returns None;
  * a dwell must carry what the EARS were doing at the time, since
    "listening and heard nothing" and "not listening" need different
    fixes and look identical on the page;
  * the server must accept nothing it cannot store, and refuse nothing
    because of it.

    python3 tools/trigger_detail_test.py
"""
import json
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "pi-agent"))

from agent import tee as tee_mod  # noqa: E402

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-56s %-22s %s" % (label, repr(got)[:22],
                                "ok" if ok else "FAIL (want %r)" % (want,)))
    if not ok:
        FAILED.append(label)


ROI = {"boxes": [{"x": 0, "y": 0, "w": 10, "h": 10, "label": "Back"},
                 {"x": 100, "y": 0, "w": 10, "h": 10, "label": "Middle"},
                 {"x": 200, "y": 0, "w": 10, "h": 10}],
       "frame": {"w": 1920, "h": 1080}}
BACK, MIDDLE, UNLABELLED, OUTSIDE = (5, 5), (105, 5), (205, 5), (500, 500)


class FakeDwell:
    def __init__(self, first_seen=90.0, restarts=0, dwell=10.0):
        self.first_seen = first_seen
        self.restarts = restarts
        self.dwell = dwell


class FakeEars:
    def __init__(self, healthy=True, shadow=False, heard=3, confirmed=2,
                 cuts=1, tol=6.0):
        self._healthy = healthy
        self.shadow = shadow
        self.heard = heard
        self.confirmed = confirmed
        self.cut_count = cuts
        self.confirm_tolerance_sec = tol

    def healthy(self):
        return self._healthy


def runner(ears=None, roi=ROI):
    r = object.__new__(tee_mod.TeeAgent)
    r._audio_trigger = ears
    r.roi = roi
    r._roi_lock = threading.Lock()
    return r


def dwell_detail(*, stance=BACK, roi=ROI, dwell=None, forced=False,
                 pending=None, ears=None, now=101.4):
    return runner(ears, roi)._dwell_detail(
        now=now, stance=stance, roi=roi, dwell=dwell or FakeDwell(),
        forced=forced, pending_secs=pending)


# ---- the dwell account -------------------------------------------------
print("a dwell says which tee, for how long, and against what threshold:")
d = dwell_detail()
check("kind", d["kind"], "dwell")
check("zone index", d["zone"], 0)
check("the operator's own label", d["zone_label"], "Back")
check("how long they stood", d["stood_sec"], 11.4)
check("the threshold it had to beat", d["dwell_sec"], 10.0)
check("where their feet were", d["stance"], [5, 5])

d = dwell_detail(stance=MIDDLE, dwell=FakeDwell(restarts=2))
check("a different zone is named", d["zone_label"], "Middle")
check("blinks forgiven are counted", d["restarts"], 2)

d = dwell_detail(stance=UNLABELLED)
check("an unlabelled zone keeps its index", d["zone"], 2)
check("...and claims no label", d["zone_label"], None)

print()
print("the other mechanisms name themselves:")
check("operator capture", dwell_detail(forced=True, pending=30.0)["kind"],
      "capture")
check("...and how long was asked for",
      dwell_detail(forced=True, pending=30.0)["capture_sec"], 30.0)
check("the ssh sentinel", dwell_detail(forced=True)["kind"], "manual")
check("a manual trigger claims no dwell",
      "stood_sec" in dwell_detail(forced=True), False)

# ---- the ears, reported beside the dwell -------------------------------
print()
print("a dwell must say what the EARS were doing at the time:")
d = dwell_detail(ears=FakeEars(healthy=True, shadow=False))
check("listening and live", d["audio"]["healthy"], True)
check("...and what they have heard", d["audio"]["heard"], 3)
check("...and cut", d["audio"]["cuts"], 1)
d = dwell_detail(ears=FakeEars(healthy=False))
check("NOT hearing the course is reported", d["audio"]["healthy"], False)
check("no audio trigger at all -> no claim either way",
      "audio" in dwell_detail(ears=None), False)

# ---- it must never cost the trigger ------------------------------------
print()
print("an annotation must never be able to cost the recording:")
check("a stance in no zone still describes the dwell",
      dwell_detail(stance=OUTSIDE)["zone"], None)
check("no stance at all", dwell_detail(stance=None)["zone"], None)
check("garbage roi", dwell_detail(roi="not an roi")["zone"], None)


class Exploding:
    @property
    def dwell(self):
        raise RuntimeError("boom")

    first_seen = 0.0
    restarts = 0


check("a dwell object that throws -> None, not a crash",
      dwell_detail(dwell=Exploding()), None)


class ExplodingEars(FakeEars):
    def healthy(self):
        raise RuntimeError("boom")


check("ears that throw -> None, not a crash",
      dwell_detail(ears=ExplodingEars()), None)

# ---- the audio account -------------------------------------------------
print()
print("an audio cut carries the measurement that convinced it:")


class FakeCopy:
    def __init__(self):
        self.seen = None

    def cut_at_impact(self, runner, ts, **kw):
        self.seen = kw.get("detail")
        return "sid"


def audio_detail(decision, ears=None, roi=ROI):
    r = runner(ears or FakeEars(), roi)
    r._copy = FakeCopy()
    r._cut_at_impact(decision)
    return r._copy.seen


DEC = {"at": 1000.25, "ratio": 19.4, "attack_ms": 6.2, "zone": 0,
       "start": 998.25, "end": 1010.25, "why": "person in zone 0",
       "confirmed": True, "cut": True}
a = audio_detail(DEC)
check("kind", a["kind"], "audio")
check("when the club met the ball", a["at"], 1000.25)
check("how far over the floor", a["ratio"], 19.4)
check("how fast it rose", a["attack_ms"], 6.2)
check("which tee the movement was on", a["zone_label"], "Back")
check("the window it cut", a["window"], [998.25, 1010.25])
check("how near the movement had to be", a["confirm_tolerance_sec"], 6.0)

a = audio_detail(dict(DEC, zone=None, why="no person detector; not gating"))
check("an unattributed cut keeps its reason", a["zone_label"], None)
check("...and says what it was",
      a["why"], "no person detector; not gating")

a = audio_detail(dict(DEC, ratio=None, attack_ms=None))
check("a decision missing its numbers still describes itself",
      a["kind"], "audio")

# A decision this cannot read must still cut: the clip is the point.
_r = runner(FakeEars(), ROI)
_r._copy = FakeCopy()
try:
    _r._cut_at_impact({"at": 5.0, "start": 3.0, "end": 9.0})
    _crashed = False
except Exception:
    _crashed = True
check("a decision missing keys does not stop the cut", _crashed, False)
check("...and the cut still happened", _r._copy.seen is not None, True)

# ---- what the server keeps ---------------------------------------------
print()
print("the server stores what it can and refuses nothing over it:")
from backend.app.routers.cameras import (  # noqa: E402
    _TRIGGER_DETAIL_MAX, _parse_trigger_detail as parse,
)

check("nothing sent", parse(None), None)
check("empty string", parse(""), None)
check("a dict survives the round trip",
      parse(json.dumps({"kind": "dwell", "zone": 1})),
      {"kind": "dwell", "zone": 1})
check("a JSON array is not a detail", parse("[1, 2]"), None)
check("a bare JSON string is not a detail", parse('"dwell"'), None)
check("malformed JSON", parse("{nope"), None)
check("oversize is dropped, not truncated",
      parse(json.dumps({"x": "y" * _TRIGGER_DETAIL_MAX})), None)

# THE CAPS MUST MATCH. The Pi's cap protects the modem and the
# server's protects the database; if the Pi's were the larger, every
# oversize blob would cross a bad link only to be thrown away here.
from agent.common import _TRIGGER_DETAIL_MAX as PI_MAX  # noqa: E402
check("the Pi does not send more than the server keeps",
      PI_MAX <= _TRIGGER_DETAIL_MAX, True)

# And a real detail has to fit inside it with room to spare.
_real = json.dumps(dwell_detail(ears=FakeEars()))
check("a real dwell detail fits the cap (%d bytes)" % len(_real),
      len(_real) < _TRIGGER_DETAIL_MAX // 2, True)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
