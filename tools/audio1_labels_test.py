#!/usr/bin/env python3
"""Labels, and the CSV they exist to fill in.

THE DELIVERABLE OF THE WHOLE audio1 EXERCISE IS A FILE WITH ENOUGH
LABELLED ROWS TO SAY WHICH MEASUREMENTS SEPARATE A STRIKE FROM A
VOICE. Everything else — the bands, the score, the pictures — is a way
of filling that file in. So the things worth testing hardest are the
ones that would quietly corrupt it:

  A MISSED STRIKE IS A ROW. A strike a person can hear that produced
  NO peak is the only way a false negative ever gets recorded, and a
  table that could not hold one would only ever learn about sounds we
  already catch.

  A MISSING MEASUREMENT IS AN EMPTY CELL, never a 0. A column of
  zeros standing in for "could not measure" correlates beautifully
  with nothing at all, and does it invisibly.

  RELABELLING UPDATES IN PLACE. A change of mind that leaves two rows
  means the export counts one sound twice and the two rows disagree.

  THE PERSON'S VERDICT OUTRANKS THE FROZEN NUMBERS. The measurements
  were taken by a machine; the label was given by somebody listening.

    python3 tools/audio1_labels_test.py
"""
import csv
import io
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.database import Base  # noqa: E402
from app.models import AudioPeakLabel, Course, LongVideoUpload  # noqa: E402
from app.routers.admin import (  # noqa: E402
    AUDIO1_LABELS, delete_audio_label, export_audio_labels,
    list_audio_labels, save_audio_label,
)
from app.services import audio1 as a1  # noqa: E402

FAILED = []


def save(upload_id, peak_time_sec, label, kind="detected", note="",
         measurements_json="", db=None):
    """Call the endpoint the way FastAPI would.

    Called directly, an unset `Form(...)` default arrives as the Form
    object itself rather than its default — so every argument is
    passed here, which is also a fair statement of what the route
    receives in production.
    """
    return save_audio_label(upload_id, peak_time_sec=peak_time_sec,
                            label=label, kind=kind, note=note,
                            measurements_json=measurements_json, db=db)


def check(label, got, want=True):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


eng = create_engine("sqlite://")
Base.metadata.create_all(eng)
db = sessionmaker(bind=eng)()
db.add(Course(id=1, name="Rivertowne"))
db.add(LongVideoUpload(id=77, course_id=1, base_captured_at=datetime.now(),
                       tee_filename="t.mp4"))
db.commit()

# What audio1 hands the page for one peak, trimmed to what matters.
MEAS = {
    "peak_time_sec": 28.16, "passed_gate": True, "snr_db": 31.2,
    "attack_ms": 6.4, "decay_ms": 72.0, "transient_duration_ms": 78.4,
    "pct_above": {"pct_above_1500": 86.0, "pct_above_3000": None},
    "bands": [{"name": "low", "pct": 4.0}, {"name": "mid", "pct": 10.0},
              {"name": "impact", "pct": 56.0}, {"name": "sharp",
                                                "pct": None}],
    "crest_factor": 7.1, "clipped": False, "n_secondary_peaks": 1,
    "isolation_db": 14.0, "spectral_centroid_hz": 2810.0,
    "probe": {"sample_rate": 48000},
    "confidence": {"score": 81.4, "band": "high",
                   "components": {"frequency": 0.9,
                                  "peak_to_background": 0.8,
                                  "attack": 0.9, "decay": 1.0,
                                  "duration": 1.0, "isolation": None}},
}

print("\n1. the six classifications the request named")
check("exactly those", list(AUDIO1_LABELS),
      ["strike", "ground", "wind", "voice", "other", "uncertain"])

print("\n2. a detected peak is labelled with its numbers frozen beside it")
r = save(77, peak_time_sec=28.164, label="strike",
                     measurements_json=json.dumps(MEAS), db=db)
check("saved", r["ok"], True)
check("the time is rounded so one sound is one row", r["peak_time_sec"],
      28.16)
rec = db.query(AudioPeakLabel).one()
check("the score is pulled out for SQL", rec.confidence_score, 81.4)
check("...and the band", rec.confidence_band, "high")
check("...and whether the detector cut it", rec.passed_gate, True)
check("the measurements are stored whole",
      rec.measurements["attack_ms"], 6.4)

print("\n3. relabelling the same peak UPDATES it")
save(77, peak_time_sec=28.16, label="voice",
                 measurements_json=json.dumps(MEAS), db=db)
check("still one row", db.query(AudioPeakLabel).count(), 1)
check("with the new verdict", db.query(AudioPeakLabel).one().label, "voice")

print("\n4. A MISSED STRIKE IS A ROW, with nothing measured")
save(77, peak_time_sec=41.5, label="strike", kind="missed",
                 note="clearly audible, no peak", db=db)
miss = (db.query(AudioPeakLabel)
        .filter(AudioPeakLabel.kind == "missed").one())
check("recorded", miss.label, "strike")
check("with no measurements, because there was no peak",
      miss.measurements, None)
check("and no score invented for it", miss.confidence_score, None)
check("the note is kept", miss.note, "clearly audible, no peak")

print("\n5. measurements on a missed strike are refused, not stored")
save(77, peak_time_sec=41.5, label="strike", kind="missed",
                 measurements_json=json.dumps(MEAS), db=db)
check("still nothing measured", (db.query(AudioPeakLabel)
                                 .filter(AudioPeakLabel.kind == "missed")
                                 .one().measurements), None)

print("\n6. a label nobody defined is refused")
try:
    save(77, peak_time_sec=5.0, label="banana", db=db)
    check("rejected", False, True)
except Exception as exc:  # noqa: BLE001
    check("rejected", "label must be one of" in str(exc), True)

print("\n7. unreadable numbers cost the numbers, never the label")
save(77, peak_time_sec=9.0, label="ground",
                 measurements_json="{not json", db=db)
kept = (db.query(AudioPeakLabel)
        .filter(AudioPeakLabel.peak_time_sec == 9.0).one())
check("the person's verdict survived", kept.label, "ground")
check("without numbers", kept.measurements, None)

print("\n8. the CSV has one row per label and no invented zeros")
res = export_audio_labels(upload_id=77, db=db)
text = res.body.decode() if hasattr(res, "body") else res.content
rows = list(csv.reader(io.StringIO(text)))
head, body = rows[0], rows[1:]
check("a header from the shared column list", head,
      [h for _k, h in a1.CSV_COLUMNS])
check("three labels, three rows", len(body), 3)
by = {r[head.index("peak time (s)")]: r for r in body}
strike_row = by["28.16"]
check("the label is the person's, not the machine's",
      strike_row[head.index("label")], "voice")
check("a measured number is there",
      strike_row[head.index("attack 10-90% (ms)")], "6.4")
# THE ONE THAT MATTERS. `pct_above_3000` was None because the band was
# above Nyquist; `sharp` likewise.
check("an unmeasurable number is an EMPTY CELL",
      strike_row[head.index("above 3000Hz (%)")], "")
check("...and so is the band it came from",
      strike_row[head.index("sharp 3000-8000Hz (%)")], "")
check("not a zero anywhere in those two",
      [strike_row[head.index("above 3000Hz (%)")],
       strike_row[head.index("sharp 3000-8000Hz (%)")]] == ["0", "0"],
      False)
check("an unmeasured component is empty too",
      strike_row[head.index("c: isolation")], "")
check("the missed strike carries its kind",
      by["41.5"][head.index("kind")], "missed")
check("...and has no measurements at all",
      by["41.5"][head.index("snr (dB)".replace("snr", "SNR"))], "")

print("\n9. listing, and deleting")
lst = list_audio_labels(77, db=db)
check("three, in time order", [round(x["peak_time_sec"], 2) for x in lst],
      [9.0, 28.16, 41.5])
delete_audio_label(77, lst[0]["id"], db=db)
check("two left", db.query(AudioPeakLabel).count(), 2)
try:
    delete_audio_label(99, lst[1]["id"], db=db)
    check("a label on another upload cannot be deleted", False, True)
except Exception as exc:  # noqa: BLE001
    check("a label on another upload cannot be deleted",
          "not found on this upload" in str(exc), True)

print("\n10. exporting everything is the normal case")
db.add(LongVideoUpload(id=78, course_id=1, base_captured_at=datetime.now(),
                       tee_filename="u.mp4"))
db.commit()
save(78, peak_time_sec=3.0, label="wind", db=db)
allrows = list(csv.reader(io.StringIO(
    export_audio_labels(upload_id=0, db=db).body.decode())))[1:]
check("both uploads are in it",
      sorted({r[0] for r in allrows}), ["77", "78"])

print()
if FAILED:
    print("FAILED (%d):" % len(FAILED))
    for f in FAILED:
        print("  -", f)
    sys.exit(1)
print("all ok")
