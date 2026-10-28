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
    def __init__(self, verdict, raises=False):
        self.verdict = verdict
        self.raises = raises
        self.asked = 0

    def event_trigger(self, *a, **kw):
        return {}

    def verify_swing(self, image_path, timeout=25.0):
        self.asked += 1
        if self.raises:
            raise RuntimeError("link died")
        return {"is_swing": self.verdict, "confidence": "high",
                "reason": "because"}


class FakeRing:
    def span(self):
        return (0.0, 1000.0)

    def extract(self, t0, t1, out):
        Path(out).write_bytes(b"\0" * 1024)
        return {"ok": True, "start": t0, "seconds": t1 - t0, "bytes": 1024}


def run(verdict, *, verify=True, drops=True, raises=False,
        composite=True, tmp=None):
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
        client=FakeClient(verdict, raises=raises),
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
            eng, runner, 100.0, before_sec=4.5, after_sec=9.0)
    finally:
        sh.build = _orig
    return bool(runner.uploader.sent), runner.client.asked


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
      run(False, verify=False, tmp=TMP), (True, 0))
check("asked but not allowed to drop: uploads anyway",
      run(False, drops=False, tmp=TMP)[0], True)

print()
print("the only thing that stops an upload:")
check("a flat NO, when the operator allowed it",
      run(False, tmp=TMP)[0], False)
# AND THE CLIP SURVIVES IT. A wrong verdict costs a swing; the
# cheapest insurance is the footage still being on the card.
check("...and the clip is kept, not deleted",
      any(p.name.endswith(".notswing.mp4") for p in TMP.iterdir()), True)

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

import shutil  # noqa: E402
shutil.rmtree(TMP, ignore_errors=True)

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

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
