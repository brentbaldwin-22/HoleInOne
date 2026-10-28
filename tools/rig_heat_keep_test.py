#!/usr/bin/env python3
"""The composite the rig's judge saw is kept, and does not pile up.

THE JUDGE IS NOT THE VARIABLE. Test Cut re-runs the SAME function,
with the same prompt and the same model, on its own composite of the
same clip -- and the two disagree. The picture is the only thing left
that can differ, and it was written to a temp file, judged, and
deleted, so it existed for a few milliseconds on a server nobody was
watching.

AND A DIRECTORY THAT ONLY GAINS FILES IS A DISK-FULL OUTAGE WITH A
DELAY ON IT. That has now cost twice on this project: the audio ring
grew until the card filled, and the refused-clip directory shipped
with no sweeper at all. So the sweeper is tested here, not assumed.

    python3 tools/rig_heat_keep_test.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-58s %-14s %s" % (label, repr(got)[:14],
                                "ok" if ok else "FAIL (want %r)" % (want,)))
    if not ok:
        FAILED.append(label)


from backend.app.routers import cameras as _cam  # noqa: E402

print("the endpoint keeps the picture and names it back:")
_src = Path("backend/app/routers/cameras.py").read_text()
check("the composite is written into CLIPS_DIR",
      'CLIPS_DIR / _name' in _src, True)
check("...and the name is returned to the rig",
      'out["image"] = _name' in _src, True)
# IT MUST STILL ANSWER. A verdict that cannot be had uploads, and a
# disk that will not take the picture must not change that.
check("a write that fails does not stop the judge",
      _src.index('log.warning("verify-swing: could not keep')
      < _src.index("judge_swing_heat_image"), True)

print()
print("and the rig carries both back onto the event:")
_ce = Path("pi-agent/agent/copyengine.py").read_text()
check("the picture's name", 'detail["verdict_image"]' in _ce, True)
# WITHOUT THE INSTANT THE PICTURES CANNOT BE COMPARED, only their
# verdicts -- which is how a disagreement becomes unarguable.
check("...and the instant it was built from",
      'detail["verdict_at_sec"]' in _ce, True)
check("the name is capped", '[:120]' in _ce, True)

print()
print("the kept pictures are swept:")
_tmp = Path(tempfile.mkdtemp())
_real_dir, _real_at = _cam.CLIPS_DIR, _cam._rig_heat_swept_at
try:
    _cam.CLIPS_DIR = _tmp
    _cam._rig_heat_swept_at = 0.0
    _old = _tmp / "righeat-1-aaaaaa.jpg"
    _old.write_bytes(b"\xff\xd8\xff")
    _stale = time.time() - _cam.RIG_HEAT_MAX_AGE_SEC - 60
    os.utime(_old, (_stale, _stale))
    _new = _tmp / "righeat-1-bbbbbb.jpg"
    _new.write_bytes(b"\xff\xd8\xff")
    # SOMETHING ELSE'S FILE. The glob has to be specific: this
    # directory holds the clips themselves.
    _clip = _tmp / "camera-event-1484-tee.mp4"
    _clip.write_bytes(b"\0")
    os.utime(_clip, (_stale, _stale))
    _cam._sweep_rig_heat()
    check("one past the age cap goes", _old.exists(), False)
    check("...and a fresh one stays", _new.exists(), True)
    check("A CLIP IS NOT A COMPOSITE and must survive", _clip.exists(), True)

    # ONCE AN HOUR, NOT ONCE A TRIGGER. This runs inside the request
    # the rig is waiting on, and a listdir per swing on a busy tee is
    # latency spent on the modem's clock.
    _old2 = _tmp / "righeat-1-cccccc.jpg"
    _old2.write_bytes(b"\xff\xd8\xff")
    os.utime(_old2, (_stale, _stale))
    _cam._sweep_rig_heat()
    check("a second call inside the hour does nothing",
          _old2.exists(), True)

    _cam._rig_heat_swept_at = 0.0
    _cap = _cam.RIG_HEAT_MAX_FILES
    try:
        _cam.RIG_HEAT_MAX_FILES = 2
        for i in range(6):
            (_tmp / ("righeat-9-%06d.jpg" % i)).write_bytes(b"\xff\xd8\xff")
            time.sleep(0.01)
        _cam._sweep_rig_heat()
        check("the count is held at the cap",
              len(list(_tmp.glob("righeat-*.jpg"))) <= 2, True)
    finally:
        _cam.RIG_HEAT_MAX_FILES = _cap
finally:
    _cam.CLIPS_DIR, _cam._rig_heat_swept_at = _real_dir, _real_at
    for q in _tmp.glob("*"):
        q.unlink(missing_ok=True)
    _tmp.rmdir()

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
