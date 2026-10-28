#!/usr/bin/env python3
"""Where the ascent search looks, and that narrowing cannot hide a ball.

A DWELL CLIP IS NINETY SECONDS and the ball's climb occupies about two
of them. The other eighty-eight are where the birds, the carts and the
branches live -- scanning them costs the time AND supplies the false
chains. "7 chain(s) rose out of the band above the golfers, none of
them a ball leaving the tee" is that sentence written from the other
side.

So the search is narrowed to what something already knows: the rig's
own impact when the clip was cut around one, otherwise the clip's own
audio. Every property below is about the DIRECTION of failure --
narrowing is only ever allowed to fall back to scanning everything.

    python3 tools/ascent_window_test.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.services.debug3 import sweep_ascents  # noqa: E402

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-58s %-16s %s" % (label, repr(got)[:16],
                                "ok" if ok else "FAIL (want %r)" % (want,)))
    if not ok:
        FAILED.append(label)


# The span arithmetic, lifted exactly as sweep_ascents applies it, so
# the clamping is tested without decoding a video.
def spans(windows, n_frames):
    out = []
    for lo, hi in (windows or [(0, n_frames - 1)]):
        lo, hi = max(0, int(lo)), min(n_frames - 1, int(hi))
        if hi > lo:
            out.append((lo, hi))
    return out or [(0, n_frames - 1)]


print("none means the whole clip, exactly as before:")
check("no windows", spans(None, 5400), [(0, 5399)])
check("an empty list", spans([], 5400), [(0, 5399)])

print()
print("windows are clamped into the clip, never outside it:")
check("a negative start", spans([(-200, 300)], 5400), [(0, 300)])
check("an end past the clip", spans([(5000, 99999)], 5400), [(5000, 5399)])
check("one entirely past the end", spans([(9000, 9900)], 5400), [(0, 5399)])
# A WINDOW THAT CLAMPS TO NOTHING MUST NOT MEAN "SCAN NOTHING". That
# would silently produce zero ascents and read exactly like a clip with
# no ball in it.
check("...and an impossible one falls back to everything",
      spans([(300, 100)], 5400), [(0, 5399)])

print()
print("several impacts give several spans, in order:")
check("two strikes", spans([(60, 300), (3000, 3240)], 5400),
      [(60, 300), (3000, 3240)])

print()
print("the sweep accepts them:")
import inspect  # noqa: E402
sig = inspect.signature(sweep_ascents).parameters
check("sweep_ascents takes windows", "windows" in sig, True)
check("...defaulting to None, i.e. the whole clip",
      sig["windows"].default, None)

print()
print("and the gates it searches with are permissive on purpose:")
from backend.app.config import settings  # noqa: E402
# A MISSED IMPACT ONLY WIDENS THE SEARCH; an extra one costs a couple
# of seconds of scanning. So this gate sits BELOW the rig's, which is
# where strictness belongs.
check("the ratio is at or under the rig's floor",
      settings.ascent_audio_min_ratio <= 4.0, True)
check("the attack is at or over the rig's",
      settings.ascent_audio_max_attack_ms >= 18.0, True)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
