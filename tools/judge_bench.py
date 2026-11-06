#!/usr/bin/env python3
"""Score the swing judge against clips whose answer we already know.

WHY THIS EXISTS. On 4 Nov the judge prompt was rewritten three times
in one morning and each attempt was assessed on one or two clips. Each
made things worse, and the third was reverted along with the other
two. The reason none of it worked is not that the ideas were bad: it
is that "did that help?" was never a number.

This makes it one. Point it at a folder of clips and a file saying
which of them are really swings, and it runs them through the REAL
renderer and the REAL judge and prints what each change keeps and
kills. Every gate and prompt in swingjudge.py and ai_tracer.py is
UNMEASURED until it has been through here.

WHERE THE CLIPS COME FROM. Both rigs already spool everything the
pipeline threw away:

    /var/lib/golfreelz-agent/notswing/     refused by the judge, and
                                           parked by the pick-best
                                           contest

and the clips it accepted are on the Production card. So a corpus
exists; it has never been labelled. Copy a few dozen of each off the
rigs, run --sheet, label them once, and every future change to the
judge costs one command instead of an argument.

USAGE

    # 1. lay out a labels file for a folder of clips
    python3 tools/judge_bench.py --clips ./corpus --init

    # 2. render a contact sheet and fill in the is_swing column
    python3 tools/judge_bench.py --clips ./corpus --sheet ./sheet

    # 3. score. --dry-run spends no API calls: it scores the gates
    #    alone, which is free and catches most mistakes.
    python3 tools/judge_bench.py --clips ./corpus --dry-run
    ANTHROPIC_API_KEY=... python3 tools/judge_bench.py --clips ./corpus \\
        --variant baseline --variant gates --variant frames --variant all

VARIANTS
    baseline   the composite alone, no gates    (what shipped before)
    gates      + the shape/stance/confidence gates in swingjudge
    frames     + the four-frame strip, judged by judge_swing_with_frames
    all        gates and frames together

THE NUMBER THAT MATTERS IS NOT ACCURACY. A gate that drops a real
swing costs a shot that never existed; a false positive costs a clip
nobody watches. They are not the same price and the table prints them
separately for that reason.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi-agent"))
sys.path.insert(0, str(ROOT / "backend"))

import cv2                                            # noqa: E402
import numpy as np                                    # noqa: E402

from agent import swingheat, swingjudge               # noqa: E402

LABELS = "labels.csv"
COLUMNS = ["clip", "is_swing", "impact_sec", "ratio", "gate_ratio",
           "stance_x", "stance_y", "note"]
VARIANTS = ("baseline", "gates", "frames", "all")
CLIP_GLOBS = ("*.mp4", "*.MP4", "*.mkv", "*.mov")


# ---- the corpus -------------------------------------------------------

def clips_in(d: Path):
    out = []
    for g in CLIP_GLOBS:
        out.extend(d.glob(g))
    return sorted(set(out))


def init_labels(d: Path) -> Path:
    """Write a labels file with a row per clip for a human to fill in.

    IMPACT DEFAULTS TO THE MIDDLE OF THE CLIP, which is right for the
    rig's own impact cuts -- they are cut around the strike -- and
    wrong for anything else. Fix those by hand; a wrong impact time
    scores the renderer on the wrong second and tells you nothing.
    """
    path = d / LABELS
    have = {}
    if path.exists():
        with path.open(newline="") as fh:
            for row in csv.DictReader(fh):
                have[row.get("clip", "")] = row
    rows = []
    for c in clips_in(d):
        prev = have.get(c.name, {})
        mid = ""
        if not prev.get("impact_sec"):
            cap = cv2.VideoCapture(str(c))
            try:
                fps = float(cap.get(cv2.CAP_PROP_FPS)) or 30.0
                n = float(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0.0
                if n > 0:
                    mid = "%.2f" % (n / fps / 2.0)
            finally:
                cap.release()
        rows.append({
            "clip": c.name,
            "is_swing": prev.get("is_swing", ""),
            "impact_sec": prev.get("impact_sec") or mid,
            "ratio": prev.get("ratio", ""),
            "gate_ratio": prev.get("gate_ratio", ""),
            "stance_x": prev.get("stance_x", ""),
            "stance_y": prev.get("stance_y", ""),
            "note": prev.get("note", ""),
        })
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return path


def read_labels(d: Path):
    path = d / LABELS
    if not path.exists():
        return None
    out = []
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            lab = str(row.get("is_swing", "")).strip().lower()
            if lab in ("y", "yes", "1", "true", "t"):
                truth = True
            elif lab in ("n", "no", "0", "false", "f"):
                truth = False
            else:
                truth = None            # unlabelled; skipped when scoring
            def _f(k):
                v = str(row.get(k, "")).strip()
                try:
                    return float(v)
                except ValueError:
                    return None
            out.append({
                "clip": row["clip"], "truth": truth,
                "impact_sec": _f("impact_sec"),
                "ratio": _f("ratio"), "gate_ratio": _f("gate_ratio"),
                "stance": ((_f("stance_x"), _f("stance_y"))
                           if _f("stance_x") is not None
                           and _f("stance_y") is not None else None),
                "note": row.get("note", ""),
            })
    return out


# ---- running one clip -------------------------------------------------

def render(d: Path, row: dict, work: Path, want_frames: bool):
    """Build the composite (and strip) exactly as the rig does."""
    clip = d / row["clip"]
    at = row["impact_sec"]
    if at is None:
        return None, None, None, "no impact_sec"
    meta: dict = {}
    stem = work / Path(row["clip"]).stem
    fo = Path(str(stem) + ".frames.jpg") if want_frames else None
    img = swingheat.build(clip, float(at), meta=meta, frames_out=fo,
                          out_path=Path(str(stem) + ".heat.jpg"))
    if img is None:
        return None, None, meta, "composite failed"
    return img, (fo if fo and fo.exists() else None), meta, None


def run_variant(variant: str, row, img, strip, meta, dry: bool):
    """One clip through one variant. Returns the decision and why."""
    geo = (meta or {}).get("geo") or {}
    res = {"asked": False, "is_swing": None, "confidence": None,
           "reason": "", "gate": "", "kept_by": "", "killed_by": ""}
    use_gates = variant in ("gates", "all")
    use_frames = variant in ("frames", "all")

    if use_gates:
        pj = swingjudge.prejudge(geo, row.get("stance"))
        if not pj["ask"]:
            res["gate"] = pj["why"]
            res["killed_by"] = "pre-gate"
            res["is_swing"] = False
            return res
    if dry:
        res["reason"] = "dry run: no judge call"
        return res

    from app.services.ai_tracer import (        # noqa: PLC0415
        judge_swing_heat_image, judge_swing_with_frames,
    )
    res["asked"] = True
    if use_frames and strip is not None:
        j = judge_swing_with_frames(strip, img)
    else:
        j = judge_swing_heat_image(img)
    res["is_swing"] = j.get("is_swing")
    res["confidence"] = j.get("confidence")
    res["reason"] = (j.get("reason") or "")[:90]

    if use_gates:
        post = swingjudge.postjudge(
            res["is_swing"], res["confidence"],
            row.get("ratio"), row.get("gate_ratio"))
        if not post["keep"]:
            res["gate"] = post["why"]
            res["killed_by"] = "confidence gate"
            res["is_swing"] = False
    return res


# ---- scoring ----------------------------------------------------------

def score(rows):
    """Confusion counts. `None` from the judge means 'could not tell',
    which the rig is REQUIRED to treat as upload, so it counts as a
    yes here -- scoring it as a no would flatter every variant."""
    tp = fp = tn = fn = 0
    for r in rows:
        said_yes = r["is_swing"] is not False
        if r["truth"] and said_yes:
            tp += 1
        elif r["truth"] and not said_yes:
            fn += 1
        elif not r["truth"] and said_yes:
            fp += 1
        else:
            tn += 1
    return {"tp": tp, "fp": fp, "tn": tn, "fn": fn}


def sheet(d: Path, rows, work: Path, out: Path):
    """A contact sheet per clip, so labelling is a few minutes not an
    afternoon. The composite and the frame strip side by side is also
    the fastest way to see WHY a verdict went the way it did."""
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for row in rows:
        img, strip, meta, err = render(d, row, work, True)
        if img is None:
            print(f"  !! {row['clip']}: {err}")
            continue
        a = cv2.imread(str(img))
        parts = [a]
        if strip is not None:
            b = cv2.imread(str(strip))
            if b is not None:
                h = a.shape[0]
                sc = h / float(b.shape[0])
                parts.append(cv2.resize(b, (int(b.shape[1] * sc), h)))
        card = np.hstack(parts) if len(parts) > 1 else a
        g = (meta or {}).get("geo", {}).get("main")
        cap = row["clip"]
        if g:
            cap += "   blob %dx%d  h/w %.2f" % (g["w"], g["h"], g["aspect"])
        cv2.putText(card, cap, (8, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                    (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(card, cap, (8, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                    (255, 255, 255), 1, cv2.LINE_AA)
        cv2.imwrite(str(out / (Path(row["clip"]).stem + ".jpg")), card)
        n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--clips", required=True, type=Path)
    ap.add_argument("--init", action="store_true",
                    help="write/refresh labels.csv and stop")
    ap.add_argument("--sheet", type=Path,
                    help="render a labelling contact sheet here and stop")
    ap.add_argument("--variant", action="append", choices=VARIANTS,
                    help="repeatable; default baseline+gates")
    ap.add_argument("--dry-run", action="store_true",
                    help="score the gates only; no API calls, no key needed")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    d = a.clips
    if not d.is_dir():
        print(f"no such folder: {d}")
        return 2
    work = d / "_bench"
    work.mkdir(exist_ok=True)

    if a.init:
        p = init_labels(d)
        print(f"wrote {p}")
        print("Fill in is_swing (y/n) for each clip. impact_sec defaults to")
        print("the middle of the clip, which is right for the rig's impact")
        print("cuts and wrong for anything else. ratio/gate_ratio enable the")
        print("confidence gate; stance_x/y enable the stance gate.")
        return 0

    rows = read_labels(d)
    if rows is None:
        print(f"no {LABELS} in {d} — run with --init first")
        return 2
    if a.limit:
        rows = rows[:a.limit]

    if a.sheet:
        n = sheet(d, rows, work, a.sheet)
        print(f"wrote {n} cards to {a.sheet}")
        return 0

    labelled = [r for r in rows if r["truth"] is not None]
    if not labelled:
        print(f"{len(rows)} clips, none labelled yet — fill in is_swing")
        return 2
    print(f"{len(labelled)} labelled clips "
          f"({sum(1 for r in labelled if r['truth'])} swings, "
          f"{sum(1 for r in labelled if not r['truth'])} not)\n")

    variants = a.variant or ["baseline", "gates"]
    if not a.dry_run and not os.environ.get("ANTHROPIC_API_KEY"):
        print("!! no ANTHROPIC_API_KEY — falling back to --dry-run\n")
        a.dry_run = True

    rendered = {}
    want_frames = any(v in ("frames", "all") for v in variants)
    print("rendering…", end="", flush=True)
    t0 = time.time()
    for r in labelled:
        rendered[r["clip"]] = render(d, r, work, want_frames)
    print(f" {time.time() - t0:.1f}s\n")

    results = {}
    for v in variants:
        out = []
        for r in labelled:
            img, strip, meta, err = rendered[r["clip"]]
            if img is None:
                # A CLIP THAT WOULD NOT RENDER IS NOT A JUDGE FAILURE.
                # The rig uploads unjudged when the composite cannot be
                # built, so it counts as a yes, exactly as it would.
                out.append({**r, "is_swing": None, "reason": err,
                            "gate": "", "killed_by": "", "asked": False})
                continue
            res = run_variant(v, r, img, strip, meta, a.dry_run)
            out.append({**r, **res})
        results[v] = out

    for v in variants:
        rows_v = results[v]
        s = score(rows_v)
        asked = sum(1 for r in rows_v if r.get("asked"))
        print(f"=== {v}")
        print(f"    real swings kept      {s['tp']:3d}"
              f"      MISSED {s['fn']:3d}   <- the expensive column")
        print(f"    false positives       {s['fp']:3d}"
              f"      correctly refused {s['tn']:3d}")
        print(f"    judge calls spent     {asked:3d} of {len(rows_v)}")
        for r in rows_v:
            if r["truth"] and r["is_swing"] is False:
                print(f"      MISS  {r['clip']}: "
                      f"{r.get('gate') or r.get('reason')}")
        print()

    if len(variants) > 1:
        base = variants[0]
        print(f"against {base}:")
        b = score(results[base])
        for v in variants[1:]:
            s = score(results[v])
            print(f"  {v:9s} false positives {b['fp']}->{s['fp']}"
                  f"   swings missed {b['fn']}->{s['fn']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
