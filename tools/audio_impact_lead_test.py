#!/usr/bin/env python3
"""The rig's audio impact lands late, and both readers must agree by how much.

An audio-triggered clip carries the instant the microphone heard the
strike, and produce uses it instead of detecting one. It arrives
consistently LATE against the picture — measured on this course at
about 15 frames, a quarter of a second on its 60fps cameras — because
the audio comes down a second RTSP session in one-second segments and
carries that pipeline's latency while the video ring does not.

IN SECONDS, NOT FRAMES. What causes it is time. A camera on another
stream profile, or a course at another frame rate, has the same delay
in seconds and a different count in frames — so a frame count stored
here would be wrong on the next rig.

AND TWO READERS, WHICH MUST NOT DISAGREE. One places the ascent search
window, the other becomes the impact frame produce draws from. A
correction applied to one and not the other puts them a quarter of a
second apart, which is most of an ascent.

    python3 tools/audio_impact_lead_test.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.config import settings  # noqa: E402

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


def corrected(heard_sec, lead):
    """What both readers do with the rig's instant."""
    return max(0.0, heard_sec - lead)


LEAD = settings.audio_impact_lead_sec

print("the default, in the units the cameras actually run at:")
check("a quarter of a second", LEAD, 0.25)
check("...which is 15 frames at 60fps", round(LEAD * 60), 15)
check("...and 7-8 at 30fps — the same delay, a different count",
      round(LEAD * 30), 8)

print()
print("moving the instant back:")
check("heard at 4.80s, used at 4.55s", round(corrected(4.80, LEAD), 2), 4.55)
check("heard at 0.10s cannot go negative", corrected(0.10, LEAD), 0.0)
check("a lead of 0 leaves it exactly as reported",
      corrected(4.80, 0.0), 4.80)

print()
print("both readers apply it:")
_src = (ROOT / "backend" / "app" / "routers"
        / "admin.py").read_text(encoding="utf-8")
# The produce side: _audio_impact_segment turns the instant into the
# offset every later stage uses.
_seg = _src[_src.index("def _audio_impact_segment("):]
_seg = _seg[:_seg.index("\n@router") if "\n@router" in _seg else 4000]
check("the produce reader subtracts the lead",
      bool(re.search(r"offset = max\(0\.0, offset - _lead\)", _seg)), True)
check("...and logs what it heard as well as what it used",
      "heard at" in _seg and "using" in _seg, True)
# The search-window side reads the same CameraEvent field.
_win = _src[_src.index("the rig's impact at"):]
_win = _win[max(0, _win.find("off = ") - 600):][:1200]
check("the window reader subtracts it too",
      bool(re.search(r"off = max\(0\.0, off - _lead\)", _src)), True)

print()
print("it is tunable without a deploy:")
check("the setting exists on Settings", hasattr(settings, "audio_impact_lead_sec"),
      True)
# No env_prefix on this Settings class, so the variable is the field
# name upper-cased. Named here because getting it wrong means a knob
# that silently does nothing.
_cfg = (ROOT / "backend" / "app" / "config.py").read_text(encoding="utf-8")
check("no env prefix is configured", "env_prefix" in _cfg, False)
check("...so the env var is AUDIO_IMPACT_LEAD_SEC",
      "AUDIO_IMPACT_LEAD_SEC" in _cfg, True)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
