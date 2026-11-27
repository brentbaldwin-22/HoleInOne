#!/usr/bin/env python3
"""What everyone else is working on, not just what this page started.

`flow_status` answers for ONE recording, and the page only ever asked
about the ones it had started itself. So a produce begun in another
tab, by another operator, or on another backend was invisible: the
card sat there offering Produce Clip for a swing already being
produced, the operator had no idea anything was happening to it, and
the draft turned up later with nothing having said it was coming.

ONE CALL FOR ALL OF IT, because the answer is tiny. Produce
serialises site-wide -- one heavy job at a time -- so the list is
almost always a single recording and never more than a handful,
whatever is on the page. Asking per card would be forty requests a
tick to learn the same thing.

AND FROM TWO SOURCES, because neither alone is the answer. This
process's memory has the stage and the queue depth and knows about
flow runs, which never touch the row; the row's `processing_status`
is the only thing that survives a restart and the only thing visible
from another instance. Reading one backend's silence as "nothing is
happening" is the bug this exists to fix.

    python3 tools/flow_active_test.py
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import app.routers.admin as A  # noqa: E402
import app.routers.production_flow as F  # noqa: E402
from app.database import Base  # noqa: E402
from app.models import Course, LongVideoUpload  # noqa: E402

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-58s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


BASE = datetime(2026, 11, 20, 14, 51, 26)


def a_db():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    db.add(Course(id=1, name="Rivertowne"))
    for _id in (1896, 1872, 1405):
        db.add(LongVideoUpload(id=_id, course_id=1,
                               tee_filename="t%d.mp4" % _id,
                               base_captured_at=BASE))
    db.commit()
    return db


def clear_memory():
    with A._debugx_lock:
        A._debugx_state.clear()
    with A._produce_inflight_lock:
        A._produce_inflight.clear()


def by_id(out):
    return {a["upload_id"]: a for a in out["active"]}


print(__doc__.strip().splitlines()[0])

print("\n1. nothing running is an empty answer, not a guess")
clear_memory()
db = a_db()
check("no work, no rows", F.flow_active(db)["active"], [])

print("\n2. a produce this process is running is reported in full")
A._debugx_set("produce", 1896, running=True, stage="Drawing the tracer",
              done=3, total=8)
A._produce_began(1896)
got = by_id(F.flow_active(db))
check("the recording is listed", 1896 in got)
check("...as producing", got[1896]["produce_running"])
check("...not as a flow run", got[1896]["flow_running"], False)
check("...with the stage an operator can read",
      got[1896]["stage"], "Drawing the tracer")
check("...and how far in", (got[1896]["done"], got[1896]["total"]), (3, 8))
check("...and how many are queued on it",
      got[1896]["produce_queued"], 1)

print("\n3. a flow run is reported too, and wins the stage")
A._debugx_set("flow", 1872, running=True, stage="cut|Cutting strike 2 of 5",
              done=1, total=5)
A._debugx_set("produce", 1872, running=True, stage="Queued")
got = by_id(F.flow_active(db))
check("the recording is listed", 1872 in got)
check("...as a flow run", got[1872]["flow_running"])
check("...and as producing", got[1872]["produce_running"])
# THE FLOW'S STAGE IS THE ONE WORTH PRINTING: it moves the whole
# recording and its lines carry the column they belong to.
check("...showing the flow's own stage",
      got[1872]["stage"], "cut|Cutting strike 2 of 5")

print("\n4. a produce on ANOTHER backend is still reported")
clear_memory()
row = db.get(LongVideoUpload, 1405)
row.processing_status = "processing"
row.processing_started_at = BASE - timedelta(minutes=2)
db.commit()
got = by_id(F.flow_active(db))
check("the row alone is enough", 1405 in got)
check("...as producing", got[1405]["produce_running"])
# NOT "QUEUED", NOT "STARTING". This process has no idea what stage
# that run is on, and printing one would be a made-up answer.
check("...saying honestly where it is running",
      got[1405]["stage"], "producing on another backend")
check("...and that this process knows nothing about it",
      got[1405]["known_here"], False)
check("...and since when", got[1405]["since"] is not None)

print("\n5. a finished row is not reported")
row.processing_status = "completed"
db.commit()
check("done is not running", F.flow_active(db)["active"], [])

print("\n6. one entry per recording, however many sources say so")
clear_memory()
row.processing_status = "processing"
db.commit()
A._debugx_set("produce", 1405, running=True, stage="Rendering")
_all = F.flow_active(db)["active"]
check("not listed twice", len(_all), 1)
# THE REAL STAGE WINS over the fallback: this process does know.
check("...and the stage this process has is kept",
      _all[0]["stage"], "Rendering")
clear_memory()

print("\n7. and the page asks, merges and reloads")
page = (ROOT / "frontend" / "src" / "pages"
        / "AdminProductionFlow.jsx").read_text()
check("the page asks on a timer",
      "setInterval(ask, ACTIVE_MS)" in page)
check("...slower than its own watch",
      "const ACTIVE_MS = 5000;" in page and "const POLL_MS = 2500;" in page)
# THE PAGE'S OWN WATCH IS THE BETTER SOURCE where there is one: asked
# more often, and it carries the report the page prints at the end.
check("...and does not overwrite a recording it is watching",
      "if (polling.current.has(a.upload_id)) continue;" in page)
# AND WHEN SOMEBODY ELSE'S WORK STOPS, the row it wrote is new here.
check("a recording that stops is cleared and the columns reloaded",
      "if (stopped.length) await load();" in page)
check("...and its flags cleared, not left saying 'producing'",
      "produce_running: false, produce_stage: null, busy: false," in page)
# A BACKGROUND QUESTION ABOUT OTHER PEOPLE'S WORK. A failed one is
# answered by the next tick; a banner over the page for it is noise.
check("a failed ask is silent", "// SILENT. This is a background" in page)
check("the api call exists",
      "flowActive: (key) =>"
      in (ROOT / "frontend" / "src" / "api.js").read_text())

print("\n" + ("all ok" if not FAILED
               else "%d FAILED:\n  %s" % (len(FAILED), "\n  ".join(FAILED))))
sys.exit(1 if FAILED else 0)
