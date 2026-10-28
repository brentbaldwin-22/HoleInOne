#!/usr/bin/env python3
"""Can the judge's answer cost a swing? Only when asked, and only on no.

THE RIG CUTS A CLIP the moment it hears an impact, then has to decide
whether to spend ~7 MB of a cellular link on it. The audio gate cannot
tell a club from a dropped bag. The judge can -- but a judge wired
wrongly throws away golf, so every direction here is about what must
STILL upload:

    verdict True   -> upload
    verdict None   -> upload   (no key, no model, dead link: unknown
                                is not "no")
    verdict False  -> upload, unless the operator asked otherwise
    anything raised-> upload

    python3 tools/swing_verify_test.py
"""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi-agent"))

from agent import audiotrigger, copyengine  # noqa: E402

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-58s %-12s %s" % (label, repr(got)[:12],
                                "ok" if ok else "FAIL (want %r)" % (want,)))
    if not ok:
        FAILED.append(label)


class FakeUploader:
    def __init__(self):
        self.sent = []

    def enqueue(self, sid, path, start, **kw):
        self.sent.append(sid)


class FakeClient:
    def __init__(self, verdict, raises=False, reason="because"):
        self.verdict = verdict
        self.raises = raises
        self.reason = reason
        self.asked = 0
        self.registered = 0
        self.order = []

    def event_trigger(self, *a, **kw):
        self.registered += 1
        self.order.append("register")
        return {}

    def verify_swing(self, image_path, timeout=25.0):
        self.asked += 1
        self.order.append("ask")
        if self.raises:
            raise RuntimeError("link died")
        return {"is_swing": self.verdict, "confidence": "high",
                "reason": self.reason}


class FakeRing:
    def span(self):
        return (0.0, 1000.0)

    def extract(self, t0, t1, out):
        Path(out).write_bytes(b"\0" * 1024)
        return {"ok": True, "start": t0, "seconds": t1 - t0, "bytes": 1024}


def run(verdict, *, verify=True, drops=True, raises=False,
        composite=True, tmp=None, detail=None, reason="because"):
    """One cut_at_impact, with the judge stubbed. Returns (uploaded, kept)."""
    eng = object.__new__(copyengine.CopyEngine)
    eng.ring = FakeRing()
    eng.clips_dir = tmp
    eng.keep_audio = True

    trig = object.__new__(audiotrigger.AudioTrigger)
    trig.verify = verify
    trig.verify_drops = drops
    trig.verify_window_sec = 0.5
    trig.ring = None

    runner = types.SimpleNamespace(
        client=FakeClient(verdict, raises=raises, reason=reason),
        uploader=FakeUploader(), _audio_trigger=trig, roi=None,
    )

    # The composite builder is the only part that needs a real video.
    _real = copyengine.swingheat if hasattr(copyengine, "swingheat") else None
    import agent.swingheat as sh
    _orig = sh.build
    sh.build = (lambda *a, **k: (tmp / "heat.jpg")) if composite else (
        lambda *a, **k: None)
    if composite:
        (tmp / "heat.jpg").write_bytes(b"\xff\xd8\xff")
    try:
        copyengine.CopyEngine.cut_at_impact(
            eng, runner, 100.0, before_sec=4.5, after_sec=9.0, detail=detail)
    finally:
        sh.build = _orig
    return (bool(runner.uploader.sent), runner.client.asked,
            runner.client.order)


import tempfile  # noqa: E402

TMP = Path(tempfile.mkdtemp())

print("what still uploads:")
check("a swing uploads", run(True, tmp=TMP)[0], True)
# THE ONE THAT MATTERS MOST. None is "could not tell", not "no".
check("COULD NOT TELL uploads", run(None, tmp=TMP)[0], True)
check("a judge that throws uploads", run(None, raises=True, tmp=TMP)[0], True)
check("no composite uploads, unasked", run(True, composite=False, tmp=TMP)[0],
      True)
check("verify off uploads without asking",
      run(False, verify=False, tmp=TMP)[:2], (True, 0))
check("asked but not allowed to drop: uploads anyway",
      run(False, drops=False, tmp=TMP)[0], True)

print()
print("the only thing that stops an upload:")
check("a flat NO, when the operator allowed it",
      run(False, tmp=TMP)[0], False)
# AND THE CLIP SURVIVES IT. A wrong verdict costs a swing; the
# cheapest insurance is the footage still being on the card.
_refused = TMP / copyengine.REFUSED_DIR
check("...and the clip is kept, not deleted",
      _refused.is_dir() and any(_refused.glob("*.mp4")), True)

print()
print("but refused clips cannot pile up for ever:")
# NOTHING ELSE SWEEPS THE WORK DIRECTORY. The spool has _prune_spool;
# this had nothing, so 7 MB per false positive would accumulate until
# the card filled. The audio ring made this exact mistake and nobody
# noticed for a week.
import os, time as _t  # noqa: E402
_old = _refused / "ancient.mp4"
_old.write_bytes(b"\0" * 1024)
_stale = _t.time() - copyengine.REFUSED_MAX_AGE_SEC - 60
os.utime(_old, (_stale, _stale))
copyengine._keep_refused(TMP, (TMP / "fresh.mp4"))  # does not exist: no-op
(TMP / "fresh.mp4").write_bytes(b"\0" * 1024)
copyengine._keep_refused(TMP, (TMP / "fresh.mp4"))
check("one older than the age cap is swept", _old.exists(), False)
check("...and the fresh one is not",
      (_refused / "fresh.mp4").exists(), True)

_tiny = copyengine.REFUSED_MAX_BYTES
try:
    copyengine.REFUSED_MAX_BYTES = 2048
    for _i in range(6):
        _f = TMP / ("big%d.mp4" % _i)
        _f.write_bytes(b"\0" * 1024)
        copyengine._keep_refused(TMP, _f)
    _held = sum(q.stat().st_size for q in _refused.glob("*.mp4"))
    check("the total is held under the byte cap", _held <= 2048 + 1024, True)
finally:
    copyengine.REFUSED_MAX_BYTES = _tiny

print()
print("the judge is asked once, and only when wanted:")
check("asked when verify is on", run(True, tmp=TMP)[1], 1)
check("not asked when verify is off", run(True, verify=False, tmp=TMP)[1], 0)

print()
print("config defaults are off:")
import inspect  # noqa: E402
sig = inspect.signature(audiotrigger.AudioTrigger.__init__).parameters
check("verify defaults off", sig["verify"].default, False)
check("dropping defaults off", sig["verify_drops"].default, False)
check("half a second either side", sig["verify_window_sec"].default, 0.5)

print()
print("A REFUSED CLIP MUST NOT REGISTER AN EVENT:")
# Registration wakes the paired green, which cuts and uploads its own
# half. A clip refused AFTER registration therefore leaves an event
# with a green half and no tee half -- "PARTIAL, TEE never arrived" --
# permanently, plus a green upload spent on a sound that was not a
# swing. Asked first, a refusal costs nothing at all.
_, _, order_no = run(False, tmp=TMP)
check("nothing is registered for a refused clip",
      "register" in order_no, False)
_, _, order_yes = run(True, tmp=TMP)
check("a swing does register", "register" in order_yes, True)
check("...and was asked BEFORE it registered",
      order_yes.index("ask") < order_yes.index("register"), True)

print()
print("the event records that it ASKED, not only what came back:")
# "Nobody asked" and "asked, and nothing came back" arrive at the
# events page as the same absent verdict, and they call for opposite
# fixes: turn verify on, or find out why the judge could not be
# reached. Event 1447 was cut, uploaded, and read as "judge: not
# asked" with no way to tell which of those it was.


def detail_of(verdict, **kw):
    d = {"kind": "audio"}
    run(verdict, tmp=TMP, detail=d, **kw)
    return d


_yes = detail_of(True)
check("a yes is recorded", _yes.get("verdict"), True)
check("...and the ask with it", _yes.get("verdict_asked"), True)
_none = detail_of(None)
check("could not tell leaves no verdict", "verdict" in _none, False)
check("...but does record the ask", _none.get("verdict_asked"), True)
# THE SERVER'S OWN WORDS WHEN IT HAS ANY -- "no key configured" is
# worth more on the page than a generic shrug -- and the shrug only
# when it sent none.
check("...and why, in the server's words", _none.get("verdict_why"), "because")
check("...or a plain shrug when it said nothing",
      detail_of(None, reason=None).get("verdict_why"), "no answer")
_broke = detail_of(None, raises=True)
check("a judge that throws records the ask",
      _broke.get("verdict_asked"), True)
check("...and names the failure",
      str(_broke.get("verdict_why", "")).startswith("check failed:"), True)
_nocomp = detail_of(True, composite=False)
check("no composite records the ask", _nocomp.get("verdict_asked"), True)
check("...and says so", _nocomp.get("verdict_why"), "no composite")
_off = detail_of(True, verify=False)
check("verify off records NOTHING, which is how the page knows",
      _off.get("verdict_asked"), None)
# UNBOUNDED TEXT ON A REQUEST A SWING DEPENDS ON. An exception's
# message has no length, and the whole detail is dropped over 2000
# bytes -- which would cost the provenance of the very event it is
# explaining.
check("the why is capped", len(str(_broke.get("verdict_why", ""))) <= 200,
      True)

print()
print("the quiet period starts on a YES and nothing else:")
# THE RULE THAT MAKES THIS SAFE. min_separation_sec suppresses by
# LOUDNESS across a whole clip, which is how a false positive at
# 15.43s deleted a real swing at 21s. This suppresses by VERDICT, in
# time order, so something that was never a swing cannot silence one.
import agent.audiotrigger as _at  # noqa: E402


def quiet(verdict, refractory=12.0):
    t = object.__new__(_at.AudioTrigger)
    t.refractory_sec = refractory
    t._quiet_until = 0.0
    if verdict is True:
        t.note_swing(100.0)
    return t._quiet_until


check("a confirmed swing goes quiet", quiet(True), 112.0)
check("could not tell does NOT", quiet(None), 0.0)
check("not a swing does NOT", quiet(False), 0.0)
check("off by default",
      inspect.signature(_at.AudioTrigger.__init__)
      .parameters["refractory_sec"].default, 0.0)
check("a zero period never goes quiet", quiet(True, 0.0), 0.0)

import shutil  # noqa: E402
shutil.rmtree(TMP, ignore_errors=True)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
