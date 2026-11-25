#!/usr/bin/env python3
"""A swing with no ball in the sky really does end up as a clip on disk.

THE COMPANION TO `tracerless_draft_test.py`, WHICH READS THE SOURCE.
That test passed in full while nothing worked: the produce built the
tracerless substitution, logged that it was cutting a draft with no
line -- and then the renderer's own swing filter, which required a
flight unconditionally, removed the swing and returned "no swing with
a flight to produce". Every assertion about the substitution was true
and no clip was ever made.

So this one does not read anything. It makes a video, hands the
renderer a swing with an impact frame and nothing else, and asks
whether a file came out and whether it holds the right seconds.

Needs ffmpeg. Skips, loudly, without it.

    python3 tools/tracerless_cut_test.py
"""
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

FAILED = []
FPS = 30.0
IMPACT_F = 240          # f240 at 30fps = 8.0s into a 12s recording


def check(label, got, want=True):
    ok = got == want
    print("  %-60s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


def make_video(path: Path, secs: int = 12) -> bool:
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-f", "lavfi", "-i",
             "testsrc=size=640x360:rate=%d:duration=%d" % (int(FPS), secs),
             "-f", "lavfi", "-i", "sine=frequency=400:duration=%d" % secs,
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-shortest", str(path)],
            check=True, capture_output=True, timeout=120)
        return path.exists()
    except Exception as exc:  # noqa: BLE001
        print("  (could not build the test video: %s)" % exc)
        return False


print(__doc__.strip().splitlines()[0])
print()

with TemporaryDirectory() as _td:
    _dir = Path(_td)
    _src = _dir / "tee.mp4"
    if not make_video(_src):
        print("\nSKIPPED — no ffmpeg, so there is nothing to cut.")
        sys.exit(0)

    # CLIPS_DIR IS WHERE THE RENDERER WRITES, and it is read at import
    # time off the settings, so it is pointed at the temp dir before
    # the module is imported rather than patched afterwards.
    os.environ["CLIPS_DIR"] = str(_dir / "clips")
    (_dir / "clips").mkdir(parents=True, exist_ok=True)

    from sqlalchemy import create_engine  # noqa: E402
    from sqlalchemy.orm import sessionmaker  # noqa: E402

    from app.database import Base  # noqa: E402
    from app.models import Course, LongVideoUpload, VideoClip  # noqa: E402
    from app.routers import admin as A  # noqa: E402

    A.CLIPS_DIR = _dir / "clips"
    from app.services import video as _V  # noqa: E402

    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    db.add(Course(id=1, name="Rolling Hills"))
    up = LongVideoUpload(id=1, course_id=1, tee_filename="tee.mp4",
                         base_captured_at=datetime.now())
    db.add(up)
    db.commit()

    # THE WHOLE INPUT: a swing, an impact frame, no ball and no flight.
    # This is what the ascent search leaves behind when it finds
    # nothing in the sky, and it is what the operator says should be
    # enough -- "we're cutting the right video with the audio cut, and
    # we have the right audio impact frame".
    rep = {"swings": [{"idx": 0, "impact_frame": IMPACT_F, "flight": [],
                       "no_tracer": "no chain cleared the band"}]}
    out = A._d3_fast_produce(up, _src, db, rep, FPS) or {}

    print("1. a clip comes out")
    check("the renderer reports success", bool(out.get("ok")))
    check("...with a clip in the list", len(out.get("clips") or []), 1)
    if not out.get("ok"):
        print("     error: %r" % (out.get("error"),))
    _rows = db.query(VideoClip).all()
    check("a clip row exists", len(_rows), 1)
    _f = None
    if _rows and _rows[0].source_url:
        _f = A.CLIPS_DIR / _rows[0].source_url.rsplit("/", 1)[-1]
        check("and its file is on disk", bool(_f.exists()))
        check("...with something in it",
              bool(_f.exists() and _f.stat().st_size > 1000))

    print("\n2. it holds the seconds the strike is in")
    # THE POINT OF THE WHOLE CHANGE. The clip is cut from the IMPACT
    # FRAME, so a strike at f240 (8.0s) must be inside it -- this is
    # the #1916 failure, where a clip claiming a strike at f1920 held
    # f1527-f1781 because the window came off an ascent chain found
    # somewhere else entirely.
    _info = _V.probe_video_info(_f) if (_f and _f.exists()) else {}
    _dur = float(_info.get("duration") or 0.0)
    check("the clip is a sensible length", 4.0 <= _dur <= 20.0,
          True)
    if _rows:
        _em = (db.get(LongVideoUpload, 1).edit_metrics or {})
        _sw = (_em.get("swings") or [{}])[0]
        _a, _b = _sw.get("start_frame"), _sw.get("end_frame")
        check("the recorded window brackets the impact frame",
              _a is not None and _b is not None
              and int(_a) <= IMPACT_F <= int(_b), True)
        check("...and starts before it, not after",
              _a is not None and int(_a) < IMPACT_F, True)
        print("     window f%s-f%s around the impact f%d, %.1fs of video"
              % (_a, _b, IMPACT_F, _dur))

        print("\n3. and it says why there is no line on it")
        check("the swing record carries the reason",
              _sw.get("no_tracer"), "no chain cleared the band")
        # A BALL NOBODY MEASURED MUST NOT BE WRITTEN. `_d3_save_swing`
        # merges with update(), so a None or a (0,0) here would outlive
        # the run and the wizard would open on a placed ball.
        check("...and no ball was invented", _sw.get("ball"), None)
        # `tracer_url` IS NOT EVIDENCE OF A TRACER. It is the produced
        # clip's own url, named after the tracer because for years
        # every produced clip had one. The claim "this clip has no
        # line on it" lives in `no_tracer`, and the page reads it from
        # there -- checking the url would have asserted the opposite
        # of what it looks like it asserts.
        from app.routers.production_flow import flow_clips  # noqa: E402
        _row = (flow_clips(flow_only=0, db=db).get("clips") or [{}])[0]
        check("the flow's clip row says it has no tracer",
              _row.get("no_tracer"), "no chain cleared the band")
        check("...and reports the frames it was cut from",
              _row.get("cut_frames"), [_a, _b])
        check("...and the impact they bracket",
              _row.get("impact_frame"), IMPACT_F)
        # THE CONTRADICTION CHECK MUST NOT FIRE on a clip that is
        # correctly cut: `_frames_disagree` is the thing that caught
        # #1916, and a tracerless draft cut from the impact is exactly
        # the case it must call fine.
        check("...and does not claim the frames disagree",
              _row.get("frames_disagree"), None)

print("\n%s" % ("all ok" if not FAILED else "%d FAILED:\n  %s" % (
    len(FAILED), "\n  ".join(FAILED))))
sys.exit(1 if FAILED else 0)
