#!/usr/bin/env python3
"""Debug has something to render on an audio-triggered clip.

THE PANEL SAT ON "Analyzing... 0/? swings" AND NEVER MOVED.

`_run_produce_debug_job` renders from the produce run's own work
record -- that is the single-run contract, so Debug can never show a
different run from the one that cut the clips. It waits up to THIRTY
MINUTES for that record and then gives up.

And on an audio-triggered clip the record was never published:

    if not segs and auto_detect_swings:
        pinned_from_rig = _audio_impact_segment(...)
        if pinned_from_rig is not None:
            segs = [pinned_from_rig]      # segs is now non-empty
    if not segs and auto_detect_swings:   # ...so this is skipped
        ...
        _work["published"] = time.time()  # ...and so is this

Taking the rig's impact short-circuits detection, and the publish
lived inside the detection block. Every clip is audio-triggered now
that the dwell is off, so Debug had stopped working altogether.

    python3 tools/debug_work_record_test.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Mostly a source-level test, but the heavy gate's shape is a fact
# about the running code rather than about the text, so it is asked
# directly.
sys.path.insert(0, str(ROOT))
FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


_src = (ROOT / "backend" / "app" / "routers"
        / "admin.py").read_text(encoding="utf-8")

# The rig-impact branch, on its own.
_i = _src.index("pinned_from_rig = _audio_impact_segment(")
_branch = _src[_i:_src.index("\n            if not segs and auto_detect_swings:", _i)]

print("the rig-impact path publishes a record:")
check("it writes to the shared work state",
      "_produce_work_state[upload_id] = {" in _branch, True)
check("...under the lock the reader takes",
      "with _produce_work_lock:" in _branch, True)
check("...stamped published, which is what the reader waits on",
      bool(re.search(r'"published": time\.time\(\)', _branch)), True)
check("...and says so in the log", "so Debug" in _branch, True)

print()
print("and it carries every key the report reads:")
# Whatever _run_produce_debug_job pulls off the record has to be
# there, or the rig path renders a report full of holes instead of
# one that says detection did not run.
_rd = _src[_src.index("def _run_produce_debug_job("):]
_rd = _rd[:_rd.index("\n@router") if "\n@router" in _rd else 20000]
_keys = sorted(set(re.findall(r'work\.get\("(\w+)"\)', _rd)))
check("the reader's keys were found", len(_keys) >= 5, True)
for k in _keys:
    check('"%s" is published by the rig path' % k,
          ('"%s":' % k) in _branch, True)

print()
print("what it reports, given nothing was detected:")
# One decision -- the rig's -- kept, and no pretence that a detector
# ran. A report claiming zero detections would read as "the pipeline
# found nothing", which is the opposite of what happened.
check("exactly one decision", _branch.count('"t": round(_t_rig, 2)'), 1)
check("...marked kept", '"kept": True' in _branch, True)
check("...and honest about why",
      "no \\n                                       \"detection was run"
      in _branch or "detection was run" in _branch, True)
check("the detector fields are empty, not invented",
      '"pose_debug": {}' in _branch and '"used_pose": False' in _branch, True)

print()
print("and Debug takes the long way round, so there is work to show:")
# The shortcut skips the ball-departure classifier, the AI anchor walk
# and the AI launch plot -- all three live inside the detection block
# it jumps over. Debug exists to look at exactly those, and already
# accepts that it re-produces the clip, so it declines the shortcut.
check("the shortcut is off for a debug run",
      "if not segs and auto_detect_swings and not debug_artifacts:" in _src,
      True)
check("...and the plain produce path still takes it",
      _src.count("pinned_from_rig = _audio_impact_segment(") , 1)
# The three things that only exist down the long route.
for _fn in ("classify_swing_shot", "verify_rest_and_impact_ai",
            "plot_launch_frames_ai"):
    _i = _src.index("if not segs and auto_detect_swings and not "
                    "debug_artifacts:")
    check("%s is past the shortcut" % _fn, _src.index(_fn, _i) > _i, True)

print()
print("and it says what it is waiting for, instead of waiting silently:")
_rd2 = _src[_src.index("def _run_produce_debug_job("):]
_rd2 = _rd2[:_rd2.index("\n@router") if "\n@router" in _rd2 else 20000]
check("the wait names a stage",
      "def _waiting(note: str)" in _rd2, True)
# A produce that FAILED publishes nothing and is not going to, so
# waiting out the thirty-minute deadline shows the wrong thing for
# half an hour. Its own error is the answer.
check("a failed produce stops the wait",
      'processing_status == "failed"' in _rd2, True)
check("...and reports the upload's own error",
      "_r.last_error or" in _rd2, True)
_page = (ROOT / "frontend" / "src" / "pages"
         / "AdminProduction.jsx").read_text(encoding="utf-8")
check("the panel shows the stage when there is no count yet",
      'data.stage || "Analyzing"' in _page, True)
# A FIXED STRING IS BARELY BETTER THAN A SPINNER. Debug now takes the
# long route, so minutes of honest work is the NORMAL case — the stage
# has to carry something that changes, or it cannot be told from a run
# that was never kicked.
check("the stage carries the elapsed seconds",
      'waiting %ds for the produce run' in _rd2, True)
check("...and the row's own status, read each pass",
      '_waiting("upload is \'%s\'"' in _rd2, True)
check("...and says whether this click started it",
      "kicked by this Debug click" in _rd2, True)

print()
print("and it names what is actually holding the box up:")
# `heavy_gate` is a single exclusive lock — ONE cpu-heavy media job at
# a time, process-wide. A Debug produce does not start until every
# produce ahead of it finishes, and with the dwell off and the audio
# trigger cutting, clips arrive all round. Thirty minutes of waiting
# on a perfectly healthy box is the expected outcome, and cannot be
# told from a hang unless the queue says so.
from backend.app.services import workload  # noqa: E402

check("the gate is exclusive, not a pool",
      type(workload.heavy_gate).__name__, "RLock")
check("...and it can name its holder",
      hasattr(workload, "current_heavy"), True)
check("the wait reads that holder", "workload.current_heavy()" in _rd2, True)
check("...and puts it in the stage", "the box is busy with" in _rd2, True)

print()
print("a stuck run does not lock the button for ever:")
# The guard refused every retry while a run was marked running, and a
# run stuck in its wait stays marked running — leaving a server
# restart as the only way back.
_pd = _src[_src.index("def produce_debug("):]
_pd = _pd[:_pd.index("\n@router") if "\n@router" in _pd else 6000]
check("a fresh run is still refused",
      "A debug run is already in progress." in _pd, True)
check("...but a stale one is replaced",
      "PRODUCE_STALE_SEC" in _pd, True)
check("...and the state records when it began",
      '"started_at": time.time()' in _rd2, True)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
