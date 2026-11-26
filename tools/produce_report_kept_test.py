#!/usr/bin/env python3
"""The produce's reasoning survives the run that made it.

THE FLOW RUNS THE SAME PRODUCE AND SHOWED NONE OF ITS WORK. Phase 3
of the flow and the Production page's "Produce from ascents" are ONE
function -- `_produce_run` -- and the only difference between them is
which key the live progress is filed under, which is what decides
whether the card narrates it or the modal does. So the flow could run
the identical detector, land on a frame nobody expected, and offer no
way to ask why.

The report itself was always thrown away: it lived in one backend
process's memory under the key the live modal polls, readable for the
minute the run took and gone for good on a restart. Which meant "why
did it produce that" could only be answered by producing AGAIN -- and
producing again replaces the clips you were asking about.

It is kept on the row now, reduced. THE REDUCTION IS THE RISK: drop a
key the panel renders and the panel shows an empty section, which
reads as the step having done nothing. So this pins the drop list
against what is big, and against what the panel reads.

    python3 tools/produce_report_kept_test.py
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.routers.admin import (  # noqa: E402
    _PRODUCE_REPORT_CAP, _PRODUCE_REPORT_HEAVY, _reduce_produce_report,
)

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-56s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


def pts(n, f0=1900):
    return [{"frame": f0 + i, "x": 900.0 + i, "y": 700.0 - 3 * i}
            for i in range(n)]


def a_fat_report():
    """What a busy clip's produce really hands back."""
    return {
        "upload_id": 1916, "ok": True, "produced": True,
        "reason": "3 of 3 swing(s) produced", "n_produced": 3,
        "n_candidates": 3, "cleared_clips": 3,
        "window": {"cutter": "audio", "cutter_why": "set for the hole",
                   "ran": "step 1's impact frames", "frames": [2009]},
        "timing": {"total_sec": 268.4},
        "stages": [{"n": i, "title": "step %d" % i, "detail": "x" * 1200,
                    "seconds": 12.0, "count": 3, "counts": "swings",
                    "work": {"images": [{"caption": "c", "url": "/u/a.jpg"}],
                             "rows": [{"label": "l", "value": "v"}] * 12}}
                   for i in (1, 2, 3, 4)],
        "ascents": [{"first_frame": 2010, "last_frame": 2070,
                     "n_points": 24, "points": pts(24),
                     "image_url": "/u/c.jpg"}] * 3,
        "clips": [{"n": i, "ok": True, "ball": [900, 700],
                   "impact_frame": 2009, "clip_url": "/u/x.mp4",
                   "flight_points": pts(60), "candidates": pts(1500),
                   "tracks_preview": [{"i": j} for j in range(200)],
                   "fit": {"rms_px": 1.2}, "n_tracks": 37}
                  for i in (1, 2, 3)],
        "swings": [{"idx": i, "impact_frame": 2009,
                    "impact_from": "measured from the ball",
                    "considered": [{"first_frame": 2000 + j,
                                    "n_points": 20, "why": ["a" * 160]}
                                   for j in range(11)],
                    "ai_launch": {"cand_points": pts(1500),
                                  "measured_why": "y" * 400}}
                   for i in range(4)],
        "swing_steps": [{"name": "rest", "title": "t", "why": "w" * 900,
                         "url": "/u/r.jpg"}] * 12,
        "sweep": {"n_dets": 4000, "dets": pts(4000),
                  "considered": [{"g": 1}] * 11, "sens": 2},
    }


print(__doc__.strip().splitlines()[0])

rep = a_fat_report()
full = len(json.dumps(rep, default=str))
small = _reduce_produce_report(rep)
kept = len(json.dumps(small, default=str))

print("\n1. it gets small enough to keep on a row")
check("the fat report really is fat", full > 400_000, True)
check("and what is kept is a fraction of it", kept < full // 8, True)
check("...well under the cap", kept < _PRODUCE_REPORT_CAP, True)
print("     %d bytes -> %d (%.1f%%)" % (full, kept, 100.0 * kept / full))

print("\n2. everything the panel renders is still there")
# READ OFF THE PANEL'S OWN SOURCE, not from a list kept by hand here --
# a hand-kept list is one that stops matching the panel the next time
# a section is added to it.
_src = (ROOT / "frontend/src/pages/AdminProduction.jsx").read_text()
_i = _src.index("export function AscentProduceModal")
_j = min(x for x in (_src.find("\nfunction ", _i + 10),
                     _src.find("\nexport function ", _i + 10)) if x > 0)
_modal = _src[_i:_j]
_reads = sorted(set(re.findall(r"rep\??\.([A-Za-z_]+)", _modal)))
check("the panel's reads were found", len(_reads) > 15, True)
_missing = [k for k in _reads if k in rep and k not in small]
check("none of them was dropped", _missing, [])
# The sections that ARE the reasoning, named explicitly: a generic
# key sweep would pass if the panel stopped reading them.
check("the cutter's own window survives", small["window"]["frames"],
      [2009])
check("...including which frames it searched",
      "frames" not in _PRODUCE_REPORT_HEAVY, True)
check("the four steps and their work survive",
      len(small["stages"]), 4)
check("...with their pictures, which are only URLs",
      small["stages"][0]["work"]["images"][0]["url"], "/u/a.jpg")
check("every chain's verdict survives",
      len(small["swings"][0]["considered"]), 11)
check("the kept chain's own points survive",
      len(small["ascents"][0]["points"]), 24)
check("the per-swing cards survive", len(small["swing_steps"]), 12)
check("and the clips table", len(small["clips"]), 3)

print("\n3. what went, went for a reason, and says so")
check("the click-to-plot pool is gone",
      "candidates" in small["reduced"]["dropped"], True)
check("...and the raw detection pool", "dets" in small["reduced"]["dropped"],
      True)
check("the count survives the list it replaced",
      small["clips"][0]["n_candidates"], 1500)
check("...so '1500 candidates' is still answerable",
      "candidates" in small["clips"][0], False)
check("and the panel can say why the map is not here",
      "click-to-plot" in small["reduced"]["why"], True)

print("\n4. a runaway report is bounded, not trusted")
_huge = dict(a_fat_report(),
             mystery=[{"blob": "z" * 400, "i": i} for i in range(200)])
# Something unforeseen and large: the cap keeps the steps and says so
# rather than writing an unbounded row.
_cut = _reduce_produce_report(_huge, cap=20_000)
check("over the cap, the steps are still kept",
      len(_cut.get("stages") or []), 4)
check("...and the timing", (_cut.get("timing") or {}).get("total_sec"),
      268.4)
check("...and the reason", bool(_cut.get("reason")), True)
check("...and it names what it had to drop",
      "clips" in _cut["reduced"]["dropped"], True)
check("...and says how big it was", "over the" in _cut["reduced"]["why"],
      True)

print("\n5. a report that cannot be reduced is not a crash")
class _Bad:
    def __repr__(self):
        raise RuntimeError("no")
# `default=str` calls repr; a value that cannot even be stringified
# must come back as a note, because the produce has already happened
# and a report that cannot be saved is a missing panel, not a
# missing clip.
_out = _reduce_produce_report({"x": _Bad()})
check("it comes back as a note", "reduced" in _out, True)

print("\n6. it reaches the row, and the page, and survives a restart")
# ON THE ROW, not in a process's memory -- which is the whole point.
from datetime import datetime  # noqa: E402

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.database import Base  # noqa: E402
from app.models import Course, LongVideoUpload  # noqa: E402
from app.routers.admin import CLIPS_DIR  # noqa: E402
from app.routers.admin import _keep_produce_report  # noqa: E402
from app.routers.production_flow import (  # noqa: E402
    flow_produce_report, flow_raw,
)

eng = create_engine("sqlite://")
Base.metadata.create_all(eng)
db = sessionmaker(bind=eng)()
db.add(Course(id=1, name="Rolling Hills"))
# THE SOURCE HAS TO BE THERE: a recording whose video is gone is not
# listed any more, so a row with no file lists nothing.
_src = CLIPS_DIR / "prr-1916.mp4"
_src.write_bytes(b"x" * 32)
db.add(LongVideoUpload(id=1916, course_id=1, tee_filename=_src.name,
                       base_captured_at=datetime(2026, 11, 25, 14, 0)))
db.commit()

# BEFORE: nothing to show, which is not an error.
_none = flow_produce_report(1916, db=db)
check("a recording never produced has nothing to show", _none["ok"], False)
check("...and says why rather than failing",
      "not been produced" in (_none["reason"] or ""), True)
check("...and the list does not offer the button",
      flow_raw(db=db)["raw"][0]["produce_report_at"], None)

_keep_produce_report(db, 1916, a_fat_report())
# A RESTART LOSES `_debugx_state` AND NOTHING ELSE. Expiring every
# object is the closest thing to one that an in-memory test has.
db.expire_all()
_got = flow_produce_report(1916, db=db)
check("after a produce, the work is there", _got["ok"], True)
check("...read back off the row", _got["report"]["upload_id"], 1916)
check("...with the steps intact", len(_got["report"]["stages"]), 4)
check("...and when it was done", bool(_got["at"]), True)
check("and the list now offers the button",
      bool(flow_raw(db=db)["raw"][0]["produce_report_at"]), True)

# A SECOND PRODUCE REPLACES IT. The question is always about the last
# run -- the clips from the earlier one are gone.
_keep_produce_report(db, 1916, dict(a_fat_report(), n_produced=1,
                                    reason="1 of 3 swing(s) produced"))
db.expire_all()
check("a later produce replaces the report",
      flow_produce_report(1916, db=db)["report"]["reason"],
      "1 of 3 swing(s) produced")

_src.unlink(missing_ok=True)

print("\n%s" % ("all ok" if not FAILED else "%d FAILED:\n  %s" % (
    len(FAILED), "\n  ".join(FAILED))))
sys.exit(1 if FAILED else 0)
