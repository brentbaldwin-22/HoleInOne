#!/usr/bin/env python3
"""The tile bracket must be built on the ball, not on `in_rule`.

THE TRAP, and I walked into it. `ball_at_address` returns a record
per tile with an `in_rule` field, and it reads exactly like the field
for "did this tile see the ball". It is not. The source sets it to

    "in_rule": _d is not None and _role != "strike",

so it means "did this frame get a vote in the two-of-three rule". It
is True on every READABLE NON-STRIKE tile whether the ball was there
or not, and False on the strike tile for a structural reason that has
nothing to do with the ball.

Read as "ball present", the six usual offsets give

    -1.20  -0.60  -0.20   0.00   +0.20   +0.60
     True   True   True   False   True    True
                           ^ the strike tile, always False

A bracket built on that either lands on the strike tile by luck or,
once "a sighting after a gap reopens the bracket" is handled honestly,
never closes at all -- and then the watch falls back to its default
fifth of a second, which is the window that produced f797: a frame
that was only where we began looking.

So the question goes to the thing that answers it: did this tile find
a detection AT THE BALL'S SPOT.

    python3 tools/tile_bracket_test.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.routers.admin import _tile_bracket  # noqa: E402

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-58s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


SPOT = [669, 727]
OFFS = (-1.20, -0.60, -0.20, 0.00, 0.20, 0.60)


def tile(at, ball, read=True, role="before", elsewhere=False):
    """One tile record, shaped as `ball_at_address` returns them."""
    spots = []
    if ball:
        spots.append([SPOT[0] + 2, SPOT[1] - 1])
    if elsewhere:
        spots.append([SPOT[0] + 180, SPOT[1] - 90])
    return {"at": at, "role": role, "read": read,
            "kept": len(spots), "spots": spots,
            # SET HONESTLY, exactly as the service sets it -- so the
            # shape that fooled me is the shape under test.
            "in_rule": read and role != "strike"}


def addr(tiles, spot=SPOT, seen_at=None, state="BALL_PRESENT_DISAPPEARED"):
    """What `ball_at_address` returns, as it returns it.

    `seen_at` is the field the service publishes from `_lag_from`:
    the offsets of the tiles holding the cluster `classify_balls`
    judged to have actually LEFT. That is the authoritative answer and
    the bracket reads it. Pass seen_at=None to exercise the fallback,
    which asks the tiles directly.
    """
    out = {"spot": spot, "frames": list(tiles)}
    if state is not None:
        out["state"] = state
    if seen_at is not None:
        out["seen_at"] = list(seen_at)
        out["left"] = True
    return out


print("the shape that fooled a bracket built on `in_rule`:")
# The ball is at its spot on the first three tiles and gone after.
# `in_rule` is True/True/True/False/True/True -- note the False is the
# STRIKE tile, where the ball was still physically there.
real = addr([
    tile(-1.20, True, role="before"),
    tile(-0.60, True, role="before"),
    tile(-0.20, True, role="before"),
    tile(0.00, True, role="strike"),     # ball STILL THERE, in_rule False
    tile(0.20, False, role="after"),
    tile(0.60, False, role="after"),
])
seen, gone, why = _tile_bracket(real)
check("the ball is still there on the strike tile", seen, 0.00)
check("  and gone on the next one", gone, 0.20)
check("  which `in_rule` would have called -0.20/0.00",
      (seen, gone) != (-0.20, 0.00))
check("  and it says so plainly", "there at +0.00s" in why)

print()
print("a sighting after a gap reopens the bracket:")
# A missed detection mid-flight must not close the bracket early.
flick = addr([
    tile(-1.20, True), tile(-0.60, False),   # a miss
    tile(-0.20, True), tile(0.00, True, role="strike"),
    tile(0.20, False, role="after"),
])
seen, gone, why = _tile_bracket(flick)
check("the last sighting wins", seen, 0.00)
check("  not the first gap", gone, 0.20)

print()
print("an unreadable tile is UNKNOWN, not empty:")
# A frame nobody could decode is not evidence the ball had left.
dark = addr([
    tile(-0.60, True), tile(-0.20, False, read=False),
    tile(0.00, False, read=False, role="strike"),
    tile(0.20, False, role="after"),
])
seen, gone, why = _tile_bracket(dark)
check("the undecodable tiles are not the gone bound", gone, 0.20)
check("  and the sighting still stands", seen, -0.60)

print()
print("a detection somewhere else is not the ball:")
# A divot, a shoe, a club head. Only the ball's own spot counts.
far = addr([
    tile(-0.60, True),
    tile(-0.20, False, elsewhere=True),
    tile(0.00, False, elsewhere=True, role="strike"),
])
seen, gone, why = _tile_bracket(far)
check("the ball was gone by -0.20", (seen, gone), (-0.60, -0.20))

print()
print("and it refuses rather than inventing a bracket:")
seen, gone, why = _tile_bracket(addr([tile(-0.60, True), tile(0.0, True)]))
check("ball on every tile -> no gone bound", gone, None)
check("  and says why", "last readable" in why)

seen, gone, why = _tile_bracket(addr([tile(-0.60, False),
                                      tile(0.0, False)]))
check("ball on no tile -> no bracket", (seen, gone), (None, None))
check("  and says why", "not found at its own spot" in why)

seen, gone, why = _tile_bracket(addr([tile(-0.6, True)], spot=None))
check("no address spot -> no bracket", (seen, gone), (None, None))
check("  and says why", "no address spot" in why)

check("no tiles -> no bracket", _tile_bracket(addr([]))[2],
      "no tiles were judged")
check("nothing at all -> no bracket, no raise",
      _tile_bracket(None)[:2], (None, None))

print()
print("it reads the sightings the service already found:")
# `_lag_from` picks the cluster `classify_balls` judged to have LEFT,
# so `seen_at` excludes the divot and the shadow -- the clusters that
# stay bright at the ball's spot after the ball has gone, and the
# documented reason the watch "frequently never concluded".
divot = addr(
    [tile(-0.60, True), tile(-0.20, True), tile(0.00, True,
                                                role="strike"),
     tile(0.20, True, role="after")],
    # The service says the BALL was only on the first tile; the later
    # detections at that spot are what stayed behind.
    seen_at=[-0.60])
seen, gone, why = _tile_bracket(divot)
check("the service's sightings win over the raw detections",
      (seen, gone), (-0.60, -0.20))
check("  and it says where the answer came from",
      "the address check saw the ball on" in why)

print()
print("a ball that never left has no bracket:")
stayed = addr([tile(-0.60, True), tile(0.0, True, role="strike")],
              seen_at=[-0.60, 0.0], state="BALL_PRESENT_REMAINED")
check("no bracket", _tile_bracket(stayed)[:2], (None, None))
check("  and says which state it saw",
      "BALL_PRESENT_REMAINED" in _tile_bracket(stayed)[2])
nodet = addr([tile(-0.60, False)], state="NO_BALL_DETECTED")
check("nothing detected -> no bracket", _tile_bracket(nodet)[:2],
      (None, None))

print()
print("THE SWING THIS WAS BUILT FOR, by its real numbers:")
# The rig's instant on this clip is f808 (the produce that reported
# f797 began looking 0.20s before it, at f796). The audio peak is
# LATER than the strike, which is at f789 -- so at 60fps the six
# offsets land on:
#
#   -1.20 f736   -0.60 f772   -0.20 f796
#    0.00 f808   +0.20 f820   +0.60 f844
#
# and the strike at f789 falls between f772 and f796. The ball is
# therefore at its spot on the first two tiles and gone from the
# rest, which is a bracket of (-0.60, -0.20).
RIG_F, FPS, TRUE_IMPACT = 808, 60.0, 789
real = addr([
    tile(-1.20, True), tile(-0.60, True),
    tile(-0.20, False), tile(0.00, False, role="strike"),
    tile(0.20, False, role="after"), tile(0.60, False, role="after"),
], seen_at=[-1.20, -0.60])
seen, gone, why = _tile_bracket(real)
check("the bracket is the pair either side of the strike",
      (seen, gone), (-0.60, -0.20))
_fs = int((RIG_F / FPS + seen) * FPS)
_fg = int((RIG_F / FPS + gone) * FPS)
check("  which is f772 to f796", (_fs, _fg), (772, 796))
check("  and the true strike is inside it", _fs < TRUE_IMPACT < _fg)
# The tiles alone give the midpoint; the frame-by-frame watch refines
# inside this window. Either way the answer must not be LATE.
_mid = (_fs + _fg) // 2
check("  the tiles' own midpoint is f784", _mid, 784)
check("  which is early, the harmless direction",
      _mid <= TRUE_IMPACT)
check("  and within a few frames", TRUE_IMPACT - _mid <= 8)
# THE WINDOW HANDED TO THE WATCH, by the same arithmetic the router
# uses. Note after_sec is NEGATIVE: the strike is before the peak.
_before, _after = max(0.0, -seen), gone + 0.10
check("  the watch looks from f772", int(round((RIG_F / FPS - _before)
                                               * FPS)), 772)
check("  to f802", int(round((RIG_F / FPS + _after) * FPS)), 802)
check("  a non-empty window despite a negative after_sec",
      _after > -_before)
check("  containing the true strike",
      772 <= TRUE_IMPACT <= 802)
# AND NOT THE DOWNSWING. The window starts where the ball was still
# observed at rest, so the club has not reached it yet. The produce
# that reported f773 looked from f772-36=f736.
check("  and not reaching back to f736 where the club was",
      int(round((RIG_F / FPS - _before) * FPS)) > 736)

print()
print("the bracket is read in TIME order, not request order:")
# The tiles are judged in whatever order they were asked for.
shuffled = addr([
    tile(0.20, False, role="after"),
    tile(-1.20, True),
    tile(0.00, True, role="strike"),
    tile(-0.60, True),
])
seen, gone, why = _tile_bracket(shuffled)
check("sorted before reading", (seen, gone), (0.00, 0.20))

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("all ok — the bracket asks about the ball, not about the rule")
