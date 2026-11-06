#!/usr/bin/env python3
"""Does showing the judge real frames change its mind? Run on the backend.

WHY HERE AND NOT IN THE REPO'S tools/. The judge needs an API key and
the clips need to be on disk, and the only machine with both is this
one. A cloud session cannot call api.anthropic.com at all -- no user
credential can be saved for Anthropic's own hosts -- so every number
quoted so far has come from the geometry gates alone. The frames, the
single biggest change to the judge, have never been measured.

WHAT IT MEASURES, precisely. It runs the SERVER's renderer
(tracer.swing_heat_check), which is what Test Cut uses, not the rig's
agent/swingheat. The two build different pictures from the same clip
-- different windows, so clip 1768 measures 196x293 on the rig and
727x294 here -- so this answers "do frames help TEST CUT", and is a
strong hint about the rig rather than a measurement of it.

    cd /app/backend
    python3 bench_judge.py                 # last 40 audio events
    python3 bench_judge.py --limit 15
    python3 bench_judge.py --no-frames     # heat only, to cost-check

It prints, per clip, what the heat composite alone says and what the
heat plus four frames says, and flags every row where they disagree.
Two judge calls per clip; nothing is written to the database.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.main import CLIPS_DIR                                # noqa: E402
from app.models import CameraEvent                            # noqa: E402
from app.database import SessionLocal                               # noqa: E402
from app.services import swingjudge as sj                     # noqa: E402
from app.services.tracer import frames_strip, swing_heat_check  # noqa: E402
from app.services.ai_tracer import (                          # noqa: E402
    judge_swing_heat_image, judge_swing_with_frames,
)


def rows(limit: int):
    db = SessionLocal()
    try:
        out = []
        q = (db.query(CameraEvent)
               .order_by(CameraEvent.triggered_at.desc())
               .limit(400).all())
        for e in q:
            d = e.trigger_detail or {}
            if d.get("kind") != "audio":
                continue
            name = e.tee_clip_filename
            if not name or not (CLIPS_DIR / name).exists():
                continue
            # verdict_at_sec is the offset the RIG built its composite
            # at. Guessing it would score the renderer on the wrong
            # second, which is the one mistake that invalidates
            # everything downstream.
            at = d.get("verdict_at_sec")
            if at is None:
                continue
            out.append({"id": e.id, "clip": CLIPS_DIR / name,
                        "at": float(at), "ratio": d.get("ratio"),
                        "rig_conf": d.get("verdict_confidence"),
                        "stance": d.get("stance")})
            if len(out) >= limit:
                break
        return out
    finally:
        db.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--gate-ratio", type=float, default=4.0)
    ap.add_argument("--no-frames", action="store_true")
    a = ap.parse_args()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("no ANTHROPIC_API_KEY in this environment")
        return 2

    work = Path("/tmp/benchjudge")
    work.mkdir(exist_ok=True)
    rs = rows(a.limit)
    print(f"{len(rs)} audio-triggered clips with a recorded impact\n")
    print(" id    ratio conf    heat-only          +frames            gate")
    print(" " + "-" * 72)
    n_flip = n_gate = n_same = 0
    for r in rs:
        chk = swing_heat_check(r["clip"], r["at"], 60.0, debug_dir=work,
                               debug_prefix="bj-%d" % r["id"]) or {}
        if not chk.get("image_clean"):
            print(" %-5d  (no composite)" % r["id"])
            continue
        geo = chk.get("geo") or {}
        m = geo.get("main") or {}
        gate = sj.prejudge(geo, r["stance"])

        j1 = judge_swing_heat_image(work / chk["image_clean"])
        v1 = j1.get("is_swing")
        c1 = j1.get("confidence")

        v2 = c2 = None
        if not a.no_frames:
            sp = work / ("bj-%d-frames.jpg" % r["id"])
            ok = frames_strip(
                r["clip"], r["at"], sp,
                box=((m.get("frame_x"), m.get("frame_y"),
                      m.get("w"), m.get("h")) if m.get("w") else None))
            if ok:
                j2 = judge_swing_with_frames(sp, work / chk["image_clean"])
                v2, c2 = j2.get("is_swing"), j2.get("confidence")

        def fmt(v, c):
            return "%-5s %-8s" % ({True: "SWING", False: "not"}.get(v, "?"),
                                  c or "-")
        flip = (v2 is not None and v2 != v1)
        if flip:
            n_flip += 1
        elif v2 is not None:
            n_same += 1
        if not gate["ask"]:
            n_gate += 1
        print(" %-5d x%-5s %-7s %s %s %s%s" % (
            r["id"], r["ratio"], r["rig_conf"] or "-",
            fmt(v1, c1), fmt(v2, c2),
            "REFUSED" if not gate["ask"] else "ok",
            "   <-- CHANGED" if flip else ""))
        # the confidence rule, on whichever verdict stands
        post = sj.postjudge(v2 if v2 is not None else v1,
                            c2 if v2 is not None else c1,
                            r["ratio"], a.gate_ratio)
        if not post["keep"]:
            print("         overruled: %s" % post["why"])

    print()
    print("frames changed the verdict on %d of %d, agreed on %d" % (
        n_flip, len(rs), n_same))
    print("the pre-gate refused %d before any call" % n_gate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
