#!/usr/bin/env python3
"""The rig's verdict has to come with the pictures it was made from.

THE OPERATOR, on event #1515: "how did the rig trigger this as a
swing? test cut didn't identify anything."

The card said AUDIO x11.2 · JUDGE: SWING @ 5.33s. Test cut on the
same clip said "2 of 2 say not a swing", with reasons: "highest heat
is at shoulder height beside the body, with no narrow extension
rising above the head", and "the person stands in place across all
frames with no visible club motion".

THEY ARE NOT LOOKING AT THE SAME PICTURE, and that is the thing to
know. The rig built its composite from its own clip, its own crop and
whichever golfer it picked, and posted it to /verify-swing. Test cut
rebuilds all of that from the uploaded clip with `swing_heat_check`.
Different crop, possibly a different golfer -- #1515 had three people
and two of them level in the zones, 4 px apart against a 28 px
margin -- and a judge that samples, so even one picture can get two
answers.

The pictures the rig's judge saw WERE written to disk, under a name
nothing recorded. So the verdict was on the card and its evidence was
unreachable, and the only way to ask about it was to press Test cut,
which answers a different question.

    python3 tools/rig_evidence_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_fails = []


def ok(label, got, want):
    _ok = got == want
    print("%-4s %s: got %r, want %r" % ("ok" if _ok else "FAIL",
                                        label, got, want))
    if not _ok:
        _fails.append(label)


def main():
    src = (ROOT / "backend/app/routers/cameras.py").read_text()
    _blk = src[src.index('out["_reusable"] = {'):]
    _blk = _blk[:_blk.index("    return out")]

    # ── THE PICTURES ────────────────────────────────────────────────
    ok("the heat picture is kept", '"image":' in _blk, True)
    ok("  and the frames strip", '"frames_image":' in _blk, True)
    ok("  preferring the copy with the zones drawn on",
       'r.get("image_zones") or r.get("image")' in _blk, True)

    # ── AND WHY IT SAID WHAT IT SAID ────────────────────────────────
    #
    # Both halves separately, not just the headline. "They agreed" and
    # "the frames carried it over the heat's objection" are different
    # events and the single `is_swing` cannot tell them apart.
    for _k in ("heat_is_swing", "heat_reason",
               "frames_is_swing", "frames_reason", "reason"):
        ok("  %s is kept" % _k, '"%s":' % _k in _blk, True)

    # ── AND WHICH GEOMETRY PRODUCED IT ──────────────────────────────
    #
    # The three things that decide what the judge was shown. Without
    # them a disagreement with Test cut cannot be attributed.
    for _k in ("zoom_from", "heat_map_from", "frames_used"):
        ok("  %s is kept" % _k, '"%s":' % _k in _blk, True)

    # The geometry the produce reuses must still be there: this block
    # exists for that first, and the evidence rides along with it.
    for _k in ("golfer_box", "golfer_feet", "zone_floor_y", "crop",
               "ball_spot", "picked_front"):
        ok("  %s still kept for the produce" % _k, '"%s":' % _k in _blk, True)

    # ── AND IT REACHES THE EVENT ────────────────────────────────────
    #
    # `_keep_swing_analysis` spreads `reusable` into swing_analysis,
    # so anything added above lands on the event without a second
    # list to keep in step. That spread is the mechanism; pin it.
    ok("the whole block is spread onto the event",
       "**{k: v for k, v in (reusable or {}).items()}" in src, True)

    print()
    if _fails:
        print("%d FAILED: %s" % (len(_fails), ", ".join(_fails)))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
