#!/usr/bin/env python3
"""Does the report SHOW how the impact frame was arrived at?

"IMPACT F277" IS UNARGUABLE AND UNCHECKABLE AT THE SAME TIME. It does
not say the microphone heard the strike at 4.88s, that the ears run a
quarter of a second late, or that 4.88 - 0.25 is where f277 came from.
An operator asked why the wizard showed f289 when the audio said 4.88s,
and the answer -- a separate override, and then a double rounding --
took two rounds to find because neither screen showed its working.

And the sum has been wrong. Rounding where it should have floored put
the impact a frame after the strike: 4.63s at 60fps is f277, which
spans 4.617s to 4.633s, and round() lands on f278. That is the class
of error a written-out sum catches and a bare number hides.

Two things are asserted here, and the second is the one that matters:

  * the sum is right, including that it FLOORS;
  * the sum ADDS UP as printed. heard_frame and impact_frame are each
    a floor, so a lead expressed in frames as int(lead * fps) can be
    one off the difference between them -- and "heard f292, subtract
    16, impact f277" on screen destroys trust in every other number on
    the page.

    python3 tools/impact_sum_test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.routers.admin import rig_impact_explained as explain  # noqa: E402

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


def rig(heard, lead):
    return {"heard_sec": heard, "lead_sec": lead,
            "sec": max(0.0, heard - lead), "event_id": 7,
            "hole_number": 8}


def main() -> int:
    # ---- the case the operator worked through by hand ---------------
    # 4.88s heard, 0.25s lead, 60fps. They said: "4.88s is the audio
    # time, so 4.63s is when we should have the impact (f277)".
    e = explain(rig(4.88, 0.25), 60.0)
    check("the heard instant becomes a frame", e["heard_frame"], 292)
    check("  the corrected instant too", e["impact_frame"], 277)
    check("  and the lead is the difference of the two",
          e["lead_frames"], 15)
    check("  which is what the operator worked out by hand",
          e["impact_sec"], 4.63)

    # ---- IT FLOORS ---------------------------------------------------
    # 4.63 * 60 = 277.8. Rounding gives 278, one frame after the strike.
    check("flooring, not rounding, decides the frame",
          explain(rig(4.88, 0.25), 60.0)["impact_frame"], 277)
    # An instant that lands exactly on a boundary is that frame, not
    # the one before it.
    check("  an exact boundary is its own frame",
          explain(rig(5.0, 0.0), 60.0)["impact_frame"], 300)

    # ---- THE SUM ADDS UP, WHICH IS THE POINT ------------------------
    # Both ends are floors, so the lead in frames has to be their
    # DIFFERENCE. int(lead * fps) is right by luck at 0.25s/60fps and
    # wrong elsewhere -- and the failure is a sentence on screen whose
    # own subtraction does not work.
    _bad = 0
    for fps in (23.976, 25.0, 29.97, 30.0, 50.0, 59.94, 60.0, 120.0):
        for heard in (1.001, 2.5, 4.88, 7.333, 11.0, 13.6667, 19.95):
            for lead in (0.0, 0.08, 0.25, 0.3, 0.41666):
                e = explain(rig(heard, lead), fps)
                if e["heard_frame"] - e["lead_frames"] != e["impact_frame"]:
                    _bad += 1
                if f"f{e['heard_frame']}" not in e["line"]:
                    _bad += 1
                if f"impact f{e['impact_frame']}" not in e["line"]:
                    _bad += 1
    check("the printed subtraction works at every fps and lead tried",
          _bad, 0)

    # The naive version, for contrast: this is what would have shipped.
    _naive_wrong = sum(
        1
        for fps in (23.976, 25.0, 29.97, 50.0, 59.94, 120.0)
        for heard in (1.001, 4.88, 7.333, 13.6667)
        for lead in (0.08, 0.25, 0.3, 0.41666)
        if (max(0, int(heard * fps)) - int(lead * fps)
            != max(0, int(max(0.0, heard - lead) * fps)))
    )
    check("  where int(lead x fps) would not have",
          _naive_wrong > 0, True)

    # ---- the cut is a SEPARATE number, and labelled as one ----------
    # f277 in the tee clip and f277 in the produced clip are different
    # instants, and showing one as the other is how two screens
    # disagreed in the first place.
    e = explain(rig(4.88, 0.25), 60.0, cut_start_sec=1.5)
    check("a cut start gives the frame inside the cut",
          e["frame_in_cut"], 277 - 90)
    check("  and names the cut it came from", e["cut_start_frame"], 90)
    check("  while the tee-clip frame is unchanged", e["impact_frame"], 277)
    check("  the sentence says both",
          "produced clip that is f187" in e["line"], True)
    check("without one, no cut frame is invented",
          "frame_in_cut" in explain(rig(4.88, 0.25), 60.0), False)

    # ---- it declines rather than inventing --------------------------
    check("no rig reading, no sum", explain(None, 60.0), None)
    check("  no fps, no sum", explain(rig(4.88, 0.25), 0), None)
    check("  a clip with no lead configured says so",
          "no lead correction" in explain(rig(4.88, 0.0), 60.0)["line"],
          True)

    # ---- and the page shows it --------------------------------------
    # Every number above was already computable and none of it was
    # drawn; that is the actual defect being fixed.
    page = (ROOT / "frontend" / "src" / "pages"
            / "AdminProduction.jsx").read_text()
    check("the report draws the sum", "function ImpactFrameSum" in page, True)
    check("  above the steps that use the frame",
          "<ImpactFrameSum rig={rep?.rig_impact} />" in page, True)
    for k in ("heard_frame", "lead_frames", "impact_frame", "impact_sec",
              "frame_in_cut", "cut_start_frame"):
        check(f"  reading {k}", f"explain.{k}" in page or f".{k}" in page,
              True)
    check("  and says the rule it floors by",
          "flooring, not rounding" in page, True)

    adm = (ROOT / "backend" / "app" / "routers" / "admin.py").read_text()
    check("the ascent report carries the sum",
          '_note["explain"] = rig_impact_explained(_rig, _fps)' in adm, True)
    check("  the AI launch step carries the same one",
          'out["explain"] = rig_impact_explained(_rig, _fps)' in adm, True)
    check("  and a clip the rig never heard says why not",
          "the rig heard no impact for this clip" in adm, True)

    print()
    if FAIL:
        print(f"FAILED {len(FAIL)}: " + ", ".join(FAIL))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
