#!/usr/bin/env python3
"""A waggle is a swing to the judge, so the first one must not just win.

EVENT 1492. A golfer waggled at 4.85s and hit at 10.46s. The judge
called the waggle a swing with MEDIUM confidence and the strike a
swing with HIGH -- but the rig never asked it about the strike. The
first confirmed swing started the 12s refractory, the ears closed, and
the clip that went up the link was the waggle. The first candidate did
not merely win; it was the only entrant.

So a confirmed swing now OPENS a window instead of ending one, and the
refractory starts when a swing SHIPS.

    python3 tools/pick_best_swing_test.py
"""
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi-agent"))

from agent import audiotrigger, copyengine  # noqa: E402

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-56s %-16s %s" % (label, repr(got)[:16],
                                "ok" if ok else "FAIL (want %r)" % (want,)))
    if not ok:
        FAILED.append(label)


class FakeUploader:
    def __init__(self):
        self.sent = []

    def enqueue(self, sid, path, start, **kw):
        self.sent.append(sid)


class FakeClient:
    """The judge, answering from a script of (is_swing, confidence)."""

    def __init__(self, script):
        self.script = list(script)
        self.registered = []

    def event_trigger(self, sid, **kw):
        # THE INSTANT, not just the id. "Something shipped" is not the
        # claim being made here -- "the STRIKE shipped" is.
        self.registered.append((sid, kw.get("impact_at")))
        return {}

    def verify_swing(self, image_path, timeout=25.0):
        v, c = self.script.pop(0)
        return {"is_swing": v, "confidence": c, "reason": "because"}


class FakeRing:
    def span(self):
        return (0.0, 1000.0)

    def extract(self, t0, t1, out):
        Path(out).write_bytes(b"\0" * 1024)
        return {"ok": True, "start": t0, "seconds": t1 - t0, "bytes": 1024}


def rig(script, *, pick_best=12.0, refractory=12.0, tmp=None):
    eng = object.__new__(copyengine.CopyEngine)
    eng.ring = FakeRing()
    eng.clips_dir = tmp
    eng.keep_audio = True
    eng._held = None

    trig = object.__new__(audiotrigger.AudioTrigger)
    trig.verify = True
    trig.verify_drops = True
    trig.verify_window_sec = 0.5
    trig.pick_best_sec = pick_best
    trig.refractory_sec = refractory
    trig._quiet_until = 0.0
    trig.ring = None

    runner = types.SimpleNamespace(
        client=FakeClient(script), uploader=FakeUploader(),
        _audio_trigger=trig, roi=None,
    )
    return eng, runner, trig


def cut(eng, runner, at):
    import agent.swingheat as sh
    _orig = sh.build
    sh.build = lambda *a, **k: (eng.clips_dir / "heat.jpg")
    (eng.clips_dir / "heat.jpg").write_bytes(b"\xff\xd8\xff")
    try:
        return copyengine.CopyEngine.cut_at_impact(
            eng, runner, float(at), before_sec=4.5, after_sec=9.0,
            detail={"kind": "audio"})
    finally:
        sh.build = _orig


TMP = Path(tempfile.mkdtemp())

print("EVENT 1492: a medium waggle, then a high strike.")
eng, runner, trig = rig([(True, "medium"), (True, "high")], tmp=TMP)
cut(eng, runner, 100.0)                       # the waggle
check("nothing shipped yet -- it is held", runner.uploader.sent, [])
# THE EARS MUST STAY OPEN. This is the whole bug: the refractory used
# to start here, and the strike was never cut at all.
check("and the ears are still open", trig._quiet_until, 0.0)
cut(eng, runner, 105.6)                       # the strike
check("still nothing shipped", runner.uploader.sent, [])
eng.flush_pick_best(runner, now=eng._held["until"] + 1)
check("ONE clip shipped", len(runner.uploader.sent), 1)
check("...and it is the STRIKE at 105.6, not the waggle at 100.0",
      runner.client.registered[0][1], 105.6)
check("the waggle was never registered -- the green saw one event",
      len(runner.client.registered), 1)
check("the refractory starts on the SHIPPED swing",
      trig._quiet_until, 105.6 + 12.0)

print()
print("a tie keeps the FIRST, deliberately:")
# The codebase already knows "a golfer holding and lowering his
# follow-through paints the same figure-and-plume a swing does", and
# that ghost arrives AFTER the strike. Preferring the later of two
# equal candidates would hand it the clip.
eng, runner, trig = rig([(True, "high"), (True, "high")], tmp=TMP)
_first = cut(eng, runner, 200.0)
cut(eng, runner, 203.0)
check("the first is still the one held", eng._held["impact_ts"], 200.0)
eng.flush_pick_best(runner, now=eng._held["until"] + 1)
check("one clip", len(runner.uploader.sent), 1)
check("...and it is the FIRST of the two", runner.client.registered[0][1],
      200.0)

print()
print("and a weaker challenger never displaces a stronger leader:")
eng, runner, trig = rig([(True, "high"), (True, "low")], tmp=TMP)
cut(eng, runner, 300.0)
cut(eng, runner, 304.0)
check("the high one is held", eng._held["conf"], "high")
check("...at its own instant", eng._held["impact_ts"], 300.0)

print()
print("THE WINDOW DOES NOT MOVE when a challenger arrives:")
# A late entrant extending the deadline would let a noisy tee sit on a
# swing indefinitely.
eng, runner, trig = rig([(True, "medium"), (True, "high")], tmp=TMP)
cut(eng, runner, 400.0)
_deadline = eng._held["until"]
cut(eng, runner, 405.0)
check("same deadline after a challenger", eng._held["until"], _deadline)

print()
print("a window entertains only so many entrants:")
eng, runner, trig = rig([(True, "low")] * 6, tmp=TMP)
for i in range(6):
    cut(eng, runner, 500.0 + i * 2.0)
    if runner.uploader.sent:
        break
check("it closes itself rather than judging for ever",
      len(runner.uploader.sent), 1)
check("...at the documented cap", copyengine._PICK_BEST_MAX_ENTRANTS, 4)

print()
print("nothing is deleted -- the losers are parked, as refused clips are:")
_refused = TMP / copyengine.REFUSED_DIR
check("the parking directory is the swept one", _refused.is_dir(), True)
check("...and it has losers in it",
      len(list(_refused.glob("*.mp4"))) > 0, True)

print()
print("with the window OFF, nothing changes:")
eng, runner, trig = rig([(True, "medium")], pick_best=0.0, tmp=TMP)
cut(eng, runner, 600.0)
check("it ships at once", len(runner.uploader.sent), 1)
check("and the refractory starts", trig._quiet_until, 612.0)
check("nothing is held", eng._held, None)

print()
print("a REFUSAL still refuses, window or no window:")
eng, runner, trig = rig([(False, "high")], tmp=TMP)
cut(eng, runner, 700.0)
check("not uploaded", runner.uploader.sent, [])
check("not registered -- the green is never woken",
      runner.client.registered, [])
check("and nothing is held waiting", eng._held, None)

print()
print("COULD NOT TELL still uploads, and is not held:")
# None is not "no", and it is not "yes" either: there is no confidence
# to compare, so it cannot enter a contest.
eng, runner, trig = rig([(None, None)], tmp=TMP)
cut(eng, runner, 800.0)
check("uploaded", len(runner.uploader.sent), 1)
check("nothing held", eng._held, None)
check("and it does NOT start the quiet period", trig._quiet_until, 0.0)

print()
print("the flush is safe to call when there is nothing to flush:")
eng, runner, trig = rig([], tmp=TMP)
check("no held swing", eng.flush_pick_best(runner), None)
eng._held = None
check("twice", eng.flush_pick_best(runner, now=1e12), None)

print()
print("and it does not ship early:")
eng, runner, trig = rig([(True, "high")], tmp=TMP)
cut(eng, runner, 900.0)
eng.flush_pick_best(runner, now=eng._held["until"] - 0.1)
check("still holding a tenth of a second out",
      len(runner.uploader.sent), 0)
eng.flush_pick_best(runner, now=eng._held["until"])
check("ships on the deadline", len(runner.uploader.sent), 1)

print()
print("the config reaches it:")
import inspect  # noqa: E402
_sig = inspect.signature(audiotrigger.AudioTrigger.__init__).parameters
check("pick_best_sec exists", "pick_best_sec" in _sig, True)
check("...and is OFF by default", _sig["pick_best_sec"].default, 0.0)
_src = Path(ROOT / "pi-agent/agent/audiotrigger.py").read_text()
check("read from config.yaml", 'a.get("pick_best_sec"' in _src, True)
_tee = Path(ROOT / "pi-agent/agent/tee.py").read_text()
# A SWING HELD AND NEVER SHIPPED is worse than one never held: the
# clip is on the card and nobody is waiting for it.
check("and the loop drives the flush",
      "flush_pick_best" in _tee, True)

for q in TMP.rglob("*"):
    if q.is_file():
        q.unlink(missing_ok=True)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
