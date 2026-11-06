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

ON THE RIG, where the clips are and ~1 GB of them is not coming down
a cellular modem. update.sh installs this beside agent/, so:

    # THE VENV'S PYTHON, not the system one: opencv is only in the
    # venv, and `python3 judge_bench.py` fails with ModuleNotFoundError
    # on a rig that is working perfectly.
    PY=/opt/golfreelz-agent/venv/bin/python3
    sudo $PY /opt/golfreelz-agent/judge_bench.py \\
         --clips /var/lib/golfreelz-agent/notswing --init
    sudo $PY /opt/golfreelz-agent/judge_bench.py \\
         --clips /var/lib/golfreelz-agent/notswing \\
         --sample 40 --export /tmp/corpus

That renders ~250 KB a clip instead of 6.5 MB. Copy /tmp/corpus off
and everything below works on it with --prebuilt; the clips never
have to move.

ANYWHERE ELSE, on a folder of clips downloaded off the Production card:

    # 1. lay out a labels file
    python3 judge_bench.py --clips ./corpus --init

    # 2. render contact cards and fill in the is_swing column
    python3 judge_bench.py --clips ./corpus --sheet ./sheet

    # 3. score. --dry-run spends no API calls: it scores the gates
    #    alone, which is free and catches most mistakes.
    python3 judge_bench.py --clips ./corpus --dry-run
    ANTHROPIC_API_KEY=... python3 judge_bench.py --clips ./corpus \\
        --variant baseline --variant gates --variant frames --variant all

WHAT EACH HALF OF THE CORPUS CAN MEASURE, because it is easy to get
this backwards and label the wrong 162 clips. notswing/ holds only
clips the pipeline REFUSED, so it measures one direction: real swings
being thrown away. The false positives are the clips that were
ACCEPTED, and those went up -- they are on the Production card, not on
the rig. Both halves or the table lies.

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

# RUNS IN THREE PLACES, and has to find `agent` in all of them: the
# repo, a laptop with clips downloaded off the Production card, and
# the rig itself -- /var/lib/golfreelz-agent/notswing holds 162 refused
# clips and ~1 GB of them is not coming down a cellular modem, so the
# rendering has to be doable where they are. The backend is optional:
# only the judge needs it, and --dry-run and --export do not.
ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "pi-agent", ROOT / "backend", ROOT,
           Path("/opt/golfreelz-agent")):
    if (_p / "agent").is_dir() or (_p / "app").is_dir():
        sys.path.insert(0, str(_p))

try:
    import cv2                                        # noqa: E402
    import numpy as np                                # noqa: E402

    from agent import swingheat, swingjudge           # noqa: E402
except ModuleNotFoundError as _exc:                   # pragma: no cover
    # THE AGENT RUNS IN A VENV AND `python3` IS NOT IT. On a rig,
    # opencv lives in /opt/golfreelz-agent/venv and the system
    # interpreter has never heard of it, so the obvious command is the
    # wrong one and the traceback does not say why.
    sys.stderr.write(
        "judge_bench: %s\n\n"
        "On a rig, run it with the agent's own interpreter -- the\n"
        "system python3 has no opencv:\n\n"
        "    sudo /opt/golfreelz-agent/venv/bin/python3 \\\n"
        "         /opt/golfreelz-agent/judge_bench.py --clips ... \n\n"
        "Anywhere else: pip install opencv-python-headless numpy\n"
        % _exc)
    raise SystemExit(2)

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


def corpus_from_events(events_json: Path, clips_dir: Path, out: Path,
                       gate_ratio: float):
    """Turn a camera-events dump into a labelled corpus, minus the label.

    THE EVENT ALREADY KNOWS MOST OF IT. `verdict_at_sec` is the exact
    offset into the clip the rig built its composite at -- not the
    middle of the clip, which is what --init has to guess -- and
    `ratio` is the strike the microphone measured. Typing either by
    hand would be typing in numbers the rig has already recorded, and
    getting impact_sec wrong scores the renderer on the wrong second.

    So the only column left is is_swing, which is the one a person
    has to answer.

    Links rather than copies: these are the backend's own clips and
    the bench only reads them.
    """
    import json
    import os
    rows = json.loads(Path(events_json).read_text())
    out.mkdir(parents=True, exist_ok=True)
    recs, miss = [], 0
    for r in rows:
        url = r.get("tee_url") or ""
        name = url.rsplit("/", 1)[-1] if url else ""
        if not name:
            continue
        src = Path(clips_dir) / name
        if not src.exists():
            miss += 1
            continue
        dst = out / name
        if not dst.exists():
            try:
                os.symlink(src.resolve(), dst)
            except OSError:
                import shutil
                shutil.copy(src, dst)
        td_ratio = r.get("ratio")
        recs.append({
            "clip": name,
            "is_swing": "",
            "impact_sec": ("%.2f" % r["at_sec"]) if r.get("at_sec") is not None
                          else "",
            "ratio": ("%s" % td_ratio) if td_ratio is not None else "",
            "gate_ratio": ("%g" % gate_ratio) if gate_ratio else "",
            "stance_x": "", "stance_y": "",
            # WHAT THE RIG DECIDED, carried through so a disagreement
            # can be read off the table without going back to the card.
            # NOT the label: that is the whole point of the exercise.
            "note": "event %s  rig said %s/%s" % (
                r.get("id"), r.get("verdict"), r.get("conf")),
        })
    with (out / LABELS).open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(recs)
    return len(recs), miss


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

def render(d: Path, row: dict, work: Path, want_frames: bool,
           prebuilt: bool = False):
    """Build the composite (and strip) exactly as the rig does.

    `prebuilt` reads renders an earlier --export made instead, so the
    rendering can happen on the rig where the clips are and the
    judging somewhere with an API key. The composite is ~100 KB and
    the strip ~150; the clip they came from is ~6.5 MB.
    """
    stem = work / Path(row["clip"]).stem
    himg = Path(str(stem) + ".heat.jpg")
    fo = Path(str(stem) + ".frames.jpg")
    if prebuilt:
        if not himg.exists():
            return None, None, None, "no exported composite"
        meta = {}
        try:
            import json as _json
            mp = Path(str(stem) + ".geo.json")
            if mp.exists():
                meta = _json.loads(mp.read_text())
        except Exception:  # noqa: BLE001
            meta = {}
        return himg, (fo if fo.exists() else None), meta, None

    clip = d / row["clip"]
    at = row["impact_sec"]
    if at is None:
        return None, None, None, "no impact_sec"
    meta: dict = {}
    img = swingheat.build(clip, float(at), meta=meta,
                          frames_out=(fo if want_frames else None),
                          out_path=himg)
    if img is None:
        return None, None, meta, "composite failed"
    # THE MEASUREMENT TRAVELS WITH THE PICTURE. Without it an exported
    # corpus can be judged elsewhere but not gated, and the gates are
    # half the thing being measured.
    try:
        import json as _json
        Path(str(stem) + ".geo.json").write_text(_json.dumps(meta))
    except Exception:  # noqa: BLE001
        pass
    return img, (fo if fo.exists() else None), meta, None


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


def sheet(d: Path, rows, work: Path, out: Path, prebuilt: bool = False):
    """A contact sheet per clip, so labelling is a few minutes not an
    afternoon. The composite and the frame strip side by side is also
    the fastest way to see WHY a verdict went the way it did."""
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for row in rows:
        img, strip, meta, err = render(d, row, work, True, prebuilt)
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
    ap.add_argument("--sample", type=int, default=0,
                    help="score a fixed random subset; 162 clips is more "
                         "than anyone will label and 40 is plenty")
    ap.add_argument("--export", type=Path,
                    help="render every clip's composite, strip and "
                         "measurement into this folder and stop. ~250 KB a "
                         "clip against ~6.5 MB, so a rig's whole notswing/ "
                         "folder fits in a copy you can actually make.")
    ap.add_argument("--from-events", type=Path,
                    help="build the corpus from a camera-events JSON dump "
                         "(see the module docstring). Fills in impact_sec "
                         "and ratio from what the rig recorded, so the only "
                         "column left to type is is_swing.")
    ap.add_argument("--clips-dir", type=Path,
                    help="where the clips named in --from-events live; on "
                         "the backend that is <upload_dir>/clips")
    ap.add_argument("--gate-ratio", type=float, default=0.0,
                    help="the rig's audio min_ratio, for the confidence "
                         "gate. The startup line prints it: 'gate x4.0'.")
    ap.add_argument("--prebuilt", action="store_true",
                    help="score renders a previous --export made, instead "
                         "of decoding the clips again (or at all)")
    a = ap.parse_args()

    d = a.clips
    if a.from_events:
        # --clips is the corpus being BUILT here, not one being read.
        d.mkdir(parents=True, exist_ok=True)
    if not d.is_dir():
        print(f"no such folder: {d}")
        return 2
    work = d / "_bench"
    work.mkdir(exist_ok=True)

    if a.from_events:
        n, miss = corpus_from_events(a.from_events, a.clips_dir or d, d,
                                     a.gate_ratio)
        print(f"{n} clip(s) linked into {d}, {miss} named but not on disk")
        print(f"wrote {d / LABELS} with impact_sec and ratio already "
              f"filled in")
        print("Only is_swing is left. Render cards to label from:")
        print(f"  --clips {d} --sheet {d}/sheet")
        return 0

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
    if a.sample and a.sample < len(rows):
        import random
        # SEEDED. A corpus that reshuffles between runs cannot be used
        # to compare two variants, which is the only thing this is for.
        random.Random(1979).shuffle(rows)
        rows = rows[:a.sample]

    if a.export:
        a.export.mkdir(parents=True, exist_ok=True)
        n = 0
        for r in rows:
            img, strip, meta, err = render(d, r, a.export, True)
            if img is None:
                print(f"  !! {r['clip']}: {err}")
                continue
            n += 1
        import shutil
        src = d / LABELS
        if src.exists():
            shutil.copy(src, a.export / LABELS)
        print(f"exported {n} renders to {a.export}")
        print("Copy that folder somewhere with an API key and run:")
        print(f"  python3 judge_bench.py --clips {a.export} --prebuilt \\")
        print("      --variant baseline --variant gates --variant frames")
        return 0

    if a.sheet:
        n = sheet(d, rows, d if a.prebuilt else work, a.sheet, a.prebuilt)
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
    src = d if a.prebuilt else work
    print("reading renders…" if a.prebuilt else "rendering…",
          end="", flush=True)
    t0 = time.time()
    for r in labelled:
        rendered[r["clip"]] = render(d, r, src, want_frames, a.prebuilt)
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
