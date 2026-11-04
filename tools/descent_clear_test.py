#!/usr/bin/env python3
"""Can an operator actually DELETE a descent they do not believe?

A produced swing carried a wildly wrong descent. The operator opened
the wizard, pressed "clear descent (20)", watched the card go to "not
set", pressed Save & close -- and on reopening the twenty points were
back, and the clip was produced from them again.

THE SAME BUG IN TWO LAYERS, A MONTH APART. The frontend used to omit
the landing keys entirely when there was none, so the patch never
mentioned them and the old values survived; that was fixed by sending
explicit nulls. `_d3_save_swing` then grew a rule that restores
landing_frame, landing_spot and green_track whenever the record
carries none -- right for a produce that found nothing, and fatal
here, because `_by_hand` is true exactly when a hand-plotted descent
exists. The three nulls went in and the twenty points came back out.
A hand-plotted descent could not be deleted at all.

A NULL AND A CLEARANCE ARE DIFFERENT STATEMENTS. Null is "this run has
nothing to say"; `descent_cleared` is "a person looked and says there
is none". Only the wizard sends the second, the same way only the
wizard sends ball_manual and impact_manual.

    python3 tools/descent_clear_test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


TRACK = [{"frame": 2324, "x": 660, "y": 520},
         {"frame": 2391, "x": 652, "y": 160}]


class Row:
    """Just enough LongVideoUpload for the merge."""

    def __init__(self, swings):
        self.edit_metrics = {"swings": swings}


class DB:
    """A db.get that hands back the one row, and a no-op commit."""

    def __init__(self, row):
        self.row = row
        self.committed = False

    def get(self, _model, _id):
        return self.row

    def add(self, _obj):
        pass

    def commit(self):
        self.committed = True


def save(prior: dict, rec: dict) -> dict:
    """Run the REAL merge and hand back the stored swing."""
    from app.routers import admin

    row = Row([dict(prior)])
    db = DB(row)
    admin._d3_save_swing(db, 1, 0, dict(rec), 0.25)
    out = [s for s in row.edit_metrics["swings"]
           if int(s.get("idx", -1)) == 0]
    return out[0] if out else {}


def main() -> int:
    PRIOR = {"idx": 0, "green_track": TRACK, "landing_frame": 2391,
             "landing_spot": {"x": 652, "y": 160}}

    # ---- the bug, as the operator hit it ---------------------------
    _was = save(PRIOR, {"idx": 0, "green_track": None,
                        "landing_frame": None, "landing_spot": None})
    check("nulls alone still do NOT delete a hand-plotted descent",
          _was.get("green_track"), TRACK)
    print("      (that is the rule that protects it from a produce)")

    # ---- and the clearance, which does -----------------------------
    _now = save(PRIOR, {"idx": 0, "green_track": None,
                        "landing_frame": None, "landing_spot": None,
                        "descent_cleared": True})
    check("a clearance deletes the comet", _now.get("green_track"), None)
    check("  and the landing frame with it", _now.get("landing_frame"), None)
    check("  and the landing spot", _now.get("landing_spot"), None)
    check("  leaving the swing itself in place", _now.get("idx"), 0)
    # NOT STORED. Left on the record it would sit in `prior` for ever
    # and clear every descent a later produce managed to find.
    check("  and the instruction is not kept as a fact",
          "descent_cleared" in _now, False)

    # ---- a produce after a clearance can still find one ------------
    _after = save(_now, {"idx": 0, "green_track": TRACK,
                         "landing_frame": 2391})
    check("a later produce may still write a descent",
          _after.get("green_track"), TRACK)

    # ---- and a produce that finds nothing still cannot erase -------
    # The rule the clearance makes an exception to has to survive it.
    _kept = save(PRIOR, {"idx": 0, "green_track": None,
                         "landing_frame": None, "landing_searched": True})
    check("a produce that searched and found nothing cannot erase "
          "hand-plotted points", _kept.get("green_track"), TRACK)

    # ---- the other operator flags are untouched --------------------
    _flags = save({"idx": 0, "ball": {"x": 1, "y": 2}, "ball_manual": True,
                   "impact_frame": 277, "impact_manual": True,
                   "green_track": TRACK},
                  {"idx": 0, "ball": {"x": 9, "y": 9},
                   "impact_frame": 289, "green_track": None,
                   "landing_frame": None, "descent_cleared": True})
    check("clearing a descent does not move an operator's ball",
          _flags.get("ball"), {"x": 1, "y": 2})
    check("  nor their impact frame", _flags.get("impact_frame"), 277)
    check("  while the descent still goes", _flags.get("green_track"), None)

    # ---- the wizard actually sends it ------------------------------
    page = (ROOT / "frontend" / "src" / "pages"
            / "AdminProduction.jsx").read_text()
    check("the wizard sends the clearance with its nulls",
          "descent_cleared: true," in page, True)

    print()
    if FAIL:
        print(f"FAILED {len(FAIL)}: " + ", ".join(FAIL))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
