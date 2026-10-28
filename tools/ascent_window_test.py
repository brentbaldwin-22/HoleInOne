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
print("a narrowed search says it was narrowed, and nothing else does:")


# Lifted exactly as sweep_ascents reports it.
def windowed(windows, n_frames):
    return bool(windows and spans(windows, n_frames) != [(0, n_frames - 1)])


check("a real window", windowed([(60, 300)], 5400), True)
check("no windows is not a narrowed search", windowed(None, 5400), False)
check("nor an empty list", windowed([], 5400), False)
# THE TWO THAT FALL BACK. Both end up scanning the whole clip, and
# reporting "searched 0.0-89.9s" on a failure would be a line that
# teaches the reader to skip the line that matters.
check("one entirely past the end fell back, so: not narrowed",
      windowed([(9000, 9900)], 5400), False)
check("an impossible one fell back too",
      windowed([(300, 100)], 5400), False)
check("a window spelling out the whole clip is not a narrowing",
      windowed([(0, 5399)], 5400), False)

print()
print("the sweep accepts them:")
import inspect  # noqa: E402
sig = inspect.signature(sweep_ascents).parameters
check("sweep_ascents takes windows", "windows" in sig, True)
check("...defaulting to None, i.e. the whole clip",
      sig["windows"].default, None)
# THE REPORT THE REFUSAL READS. Without these the failure sentence
# cannot say where it looked, and "0 chain(s)" is unreadable: an empty
# video and a window two seconds off the swing write the same words.
_src = Path("backend/app/services/debug3.py").read_text()
check("the sweep reports whether it narrowed",
      'out["windowed"]' in _src, True)
check("...and the fps the spans are measured in",
      'out["fps"] = round(float(fps' in _src, True)
_adm = Path("backend/app/routers/admin.py").read_text()
check("the refusal reports the span it searched",
      '"searched ' in _adm or "(searched " in _adm, True)
# AND THE TOAST IS DECIDED AFTER THE WINDOW IS. It read "the whole
# clip" unconditionally, which is how a two-second search announced
# itself as a ninety-second one.
check("the window is chosen before the progress line is written",
      _adm.index("_win = _ascent_windows(db, row, src_path, fps, rep)")
      < _adm.index('"Sweeping the whole clip for balls leaving the tee"'),
      True)

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
