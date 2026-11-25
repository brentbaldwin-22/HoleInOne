#!/usr/bin/env python3
"""A save about audio must not throw away the hole's other gates.

THE LOSS WAS SILENT AND SLOW. `set_gate_tuning` replaces the hole's
whole entry with what was posted -- right for the hole's own editor,
which shows every gate and where clearing one has to mean something.
But the per-tee audio panel and the preset writer behind the sandbox
post ONLY audio gates, so every save of a crest factor also deleted
that hole's ascent cone and its ball contrast. Nothing said so. The
first symptom is a produce coming out wrong weeks later with nothing
in the history to say a gate was ever set.

`merge` keeps what the body does not mention. An explicit null still
clears the gate it names, because "set this back to the default" has
to stay expressible -- otherwise a merging editor can only ever add.

    python3 tools/gate_merge_test.py
"""
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import app.routers.admin as A  # noqa: E402
from app.database import Base  # noqa: E402
from app.models import Course, LongVideoUpload  # noqa: E402

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


eng = create_engine("sqlite://")
Base.metadata.create_all(eng)
db = sessionmaker(bind=eng)()
course = Course(id=1, name="Rivertowne")
db.add(course)
db.add(LongVideoUpload(id=1916, course_id=1, tee_filename="t.mp4",
                       base_captured_at=datetime.now()))
db.commit()
KEY = "course:1:hole:4"


def post(gates, merge=False, **kw):
    body = {"gates": gates}
    if merge:
        body["merge"] = True
    with patch.object(A, "_view_map_for", lambda d, r: (course, 4, None)), \
         patch.object(A, "_view_map_key", lambda d, r: (KEY, None, "w")):
        return A.set_gate_tuning(1916, body, db=db, **kw)


def stored():
    db.refresh(course)
    return dict((course.gate_tuning or {}).get(KEY) or {})


print(__doc__.strip().splitlines()[0])

print("\n1. the hole is tuned with gates that have nothing to do with audio")
post({"ascent_cone_from_deg": -30.0, "ball_min_contrast": 18.0})
check("the cone is stored", "ascent_cone_from_deg" in stored())
check("the contrast too", "ball_min_contrast" in stored())

print("\n2. a merging save of audio gates keeps them")
post({"audio_min_crest@1": 4.5, "audio1_armed@1": 1}, merge=True)
g = stored()
check("the crest arrived", g.get("audio_min_crest@1"), 4.5)
check("arming arrived", g.get("audio1_armed@1"), 1)
check("THE CONE SURVIVED", "ascent_cone_from_deg" in g)
check("so did the contrast", "ball_min_contrast" in g)

print("\n3. a null still clears the gate it names, and only that one")
post({"audio_min_crest@1": None}, merge=True)
g = stored()
check("the crest is gone", "audio_min_crest@1" in g, False)
check("arming is untouched", g.get("audio1_armed@1"), 1)
check("and so is the cone", "ascent_cone_from_deg" in g)

print("\n4. without merge it still replaces, which the hole editor needs")
post({"ascent_cone_from_deg": -30.0})
g = stored()
check("only what was posted remains", sorted(g), ["ascent_cone_from_deg"])
check("the audio gates are gone", "audio1_armed@1" in g, False)

print("\n5. the audio writers ask for a merge")
SRC = (ROOT / "backend/app/routers/admin.py").read_text()
_fn = SRC[SRC.index("def apply_audio_gate_preset("):]
_fn = _fn[:_fn.index("\n@router.")]
check("the preset writer merges", '"merge": True' in _fn)
PANEL = (ROOT / "frontend/src/components/AudioGatesPanel.jsx").read_text()
check("the per-tee panel merges",
      "setGateTuning(adminPassword, uploadId, gates, undefined,\n"
      "                              true)" in PANEL
      or "undefined,\n                              true)" in PANEL)
API = (ROOT / "frontend/src/api.js").read_text()
check("and the helper can express it", "merge ? { merge: true }" in API)

print("\n6. merging an empty body changes nothing")
post({"audio_min_crest@1": 4.5}, merge=True)
_before = stored()
post({}, merge=True)
check("the entry is untouched", stored(), _before)

print("\n%s" % ("all ok" if not FAILED else "%d FAILED:\n  %s" % (
    len(FAILED), "\n  ".join(FAILED))))
sys.exit(1 if FAILED else 0)
