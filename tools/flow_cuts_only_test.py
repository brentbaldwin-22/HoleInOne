#!/usr/bin/env python3
"""Cut on audio and stop there.

THE PRODUCE IS THE EXPENSIVE HALF BY A WIDE MARGIN -- minutes of sky
searching per swing against seconds of ffmpeg -- and it is also the
half most often done by hand. On a recording where the ascent search
is going to find nothing anyway, waiting for it to fail four times
before being allowed to plot the swings is the long way round to the
same place.

So this is the same run, stopped after phase 2. THE SAME RUN, not a
second cutter: which gates ran, which tee box they came from and
which strikes cleared them are decided in one place, so the cuts this
makes are the cuts the full flow would have made. Every one of them
then carries its own Produce Clip button.

AND IT DOES NOT THROW ANYBODY'S WORK AWAY. Re-cutting clears the
earlier run's CUTS, because leaving them would show the cuts of two
gate settings at once. Produced clips are somebody's work and a cut
is not an instruction to delete them -- they simply stop naming a cut
that no longer exists, which the card already says out loud.

    python3 tools/flow_cuts_only_test.py
"""
import inspect
import logging
import sys
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import app.routers.production_flow as F  # noqa: E402
from app.database import Base  # noqa: E402
from app.models import (  # noqa: E402
    AudioCut, Course, LongVideoUpload, VideoClip,
)

logging.disable(logging.CRITICAL)
FAILED = []
HITS = [{"peak_time_sec": 33.4, "ratio": 18.5, "attack_ms": 10.0},
        {"peak_time_sec": 60.1, "ratio": 5.7, "attack_ms": 9.0}]


def check(label, got, want=True):
    ok = got == want
    print("  %-56s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


def a_db(with_old_clip=False):
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    db.add(Course(id=1, name="Rivertowne"))
    up = LongVideoUpload(id=1915, course_id=1, tee_filename="t.mp4",
                         base_captured_at=datetime.now())
    db.add(up)
    db.commit()
    if with_old_clip:
        db.add(VideoClip(id=9, course_id=1, hole_number=8,
                         camera_type="tee", captured_at=datetime.now(),
                         long_upload_id=1915,
                         flow_run_at=datetime.utcnow(),
                         source_url="http://x/old.mp4"))
        db.commit()
    return db, up


def run(db, up, cuts_only, produced):
    def _fake_produce(*a, **k):
        produced["n"] += 1
        return {"ok": True, "n_produced": 2, "stages": []}

    with mock.patch.object(F, "_hit_peak_sec",
                           lambda h: h["peak_time_sec"]), \
        mock.patch("app.routers.admin._cutter_hits",
                   lambda *a, **k: HITS), \
        mock.patch("app.routers.admin.probe_fps", lambda p: 60.0), \
        mock.patch("app.routers.admin._trigger_zone", lambda *a: 1), \
        mock.patch("app.routers.admin._local_green", lambda r: None), \
        mock.patch("app.routers.admin._clip_url_for",
                   lambda n: "http://x/%s" % n), \
        mock.patch("app.routers.admin._audio_lag_sec",
                   lambda *a, **k: 1.0), \
        mock.patch("app.services.video.cut_segment", lambda *a, **k: True), \
        mock.patch("app.routers.admin._produce_run", _fake_produce), \
        mock.patch.object(F, "_claim_clips", lambda *a, **k: (0, 0)):
        return F.flow_run(up, "/nope.mp4", db, cuts_only=cuts_only)


print(__doc__.strip().splitlines()[0])

print("\n1. the cuts are made and the produce is not")
db, up = a_db()
_p = {"n": 0}
rep = run(db, up, True, _p)
check("the run succeeds", rep["ok"])
check("...and says which kind it was", rep.get("cuts_only"), True)
check("both strikes became cuts", rep["n_cuts"], 2)
check("...and the rows are there", db.query(AudioCut).count(), 2)
# THE WHOLE POINT. Not "the produce found nothing" -- it never ran.
check("the produce was never called", _p["n"], 0)
check("...and the phase says so, rather than showing a zero",
      next((p.get("detail") for p in rep["phases"]
            if p["name"] == "produce"), None), "not run — audio cuts only")
check("the reason points at what to do next",
      "waiting for Produce Clip" in rep["reason"], True)

print("\n2. the cuts are the ones the full flow would have made")
# SAME FUNCTION, SAME GATES, SAME TEE BOX. A second cutter would be a
# second answer to "which strikes cleared this hole's gates", and the
# two could disagree while both looked authoritative.
db2, up2 = a_db()
_p2 = {"n": 0}
full = run(db2, up2, False, _p2)
check("the full flow does call the produce", _p2["n"], 1)
_a = [(k.idx, k.peak_time_sec, k.start_sec, k.end_sec, k.trigger_zone,
       k.fps, k.lag_sec) for k in db.query(AudioCut).order_by(AudioCut.idx)]
_b = [(k.idx, k.peak_time_sec, k.start_sec, k.end_sec, k.trigger_zone,
       k.fps, k.lag_sec) for k in db2.query(AudioCut).order_by(AudioCut.idx)]
check("and the cuts are identical either way", _a, _b)
check("...including the frame rate and lag each was made with",
      all(x[5] == 60.0 and x[6] == 1.0 for x in _a), True)
# ONE FUNCTION, so there is nowhere for the two to drift apart.
_src = inspect.getsource(F.flow_run)
check("there is one cutter, with a stop in it",
      "if cuts_only:" in _src, True)
# ONE STATUS KEY FOR ONE RECORDING. Two kinds would let a recording
# appear to be running twice, and the second to finish would be the
# one the columns believed.
check("...filed under the same status key, so one recording cannot "
      "look like two runs",
      '_debugx_start("flow", upload_id, _flow_runner_cuts)'
      in inspect.getsource(F.flow_start_cuts), True)

print("\n3. it clears the cuts, not somebody's clips")
db3, up3 = a_db(with_old_clip=True)
# The old clip names the cut an earlier run made.
_old = F.record_cut(db3, upload=up3, idx=0, peak_time_sec=11.1)
_c = db3.get(VideoClip, 9)
_c.audio_cut_id = _old.id
db3.commit()
_p3 = {"n": 0}
rep3 = run(db3, up3, True, _p3)
check("the earlier run's cuts are replaced", db3.query(AudioCut).count(), 2)
# ── BY WHAT IS IN THEM, NOT BY THEIR IDS ────────────────────────────
#
# SQLite hands a freed rowid straight back, so the replacement cut is
# very likely to BE id 1 again. Asserting the old id no longer
# resolves would fail on a correct clear and pass on a broken one that
# happened to renumber.
check("...and the earlier cut's own peak is gone with it",
      [k.peak_time_sec for k in db3.query(AudioCut).all()],
      [33.4, 60.1])
check("...so nothing of the 11.1s cut survived",
      db3.query(AudioCut).filter(AudioCut.peak_time_sec == 11.1).count(), 0)
# A CUT IS NOT AN INSTRUCTION TO DELETE A CLIP. Deleting one here
# would be this function deciding something it was not asked about.
check("the produced clip is left alone",
      db3.get(VideoClip, 9) is not None, True)
check("...no longer naming a cut that does not exist",
      db3.get(VideoClip, 9).audio_cut_id, None)
check("and it is counted, not silently kept", rep3["n_clips"], 1)
check("...and said out loud in the reason",
      "were left alone" in rep3["reason"], True)

print("\n%s" % ("all ok" if not FAILED else "%d FAILED:\n  %s" % (
    len(FAILED), "\n  ".join(FAILED))))
sys.exit(1 if FAILED else 0)
