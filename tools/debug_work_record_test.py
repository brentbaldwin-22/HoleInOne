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
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
