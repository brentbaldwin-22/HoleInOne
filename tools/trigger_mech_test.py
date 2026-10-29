#!/usr/bin/env python3
"""Turning one trigger mechanism off, per camera.

A TEE CAMERA HAS TWO WAYS TO DECIDE A SWING IS HAPPENING: somebody
dwelling in a trigger zone, and the microphone hearing the strike.
They are not equally good on every rig -- the ears are exact about the
instant and need the course to be audible, the dwell always works and
costs a clip per golfer who stands there -- so an operator needs to be
able to run one without the other.

THE REFUSAL RIDES ON A KEY THE FIELD ALREADY OBEYS. Agents have read
`triggering_disabled` out of the trigger ack for a while and do the
right thing with it: nothing recorded, nothing uploaded. Answering a
switched-off mechanism with that same key is what makes this work on
rigs that have not been updated -- so it is tested, not assumed.

AND `impact_at` IS THE DISCRIMINATOR, which is not a new idea either:
an audio trigger carries the instant the club met the ball, and a
dwell has no instant to carry because nothing has happened yet beyond
somebody standing there.

    python3 tools/trigger_mech_test.py
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


def verdict(triggering_enabled, audio_on, dwell_on, impact_at):
    """The gate, exactly as /event-trigger applies it.

    Returns None to admit the trigger, or the mechanism that refused
    it. `triggering_enabled` is checked first and answers for both.
    """
    if not triggering_enabled:
        return "paused"
    mech = "audio" if impact_at is not None else "dwell"
    on = audio_on if mech == "audio" else dwell_on
    # ABSENT IS NOT OFF. A row written before the columns existed reads
    # None until the backfill runs, and None has to mean "nobody turned
    # this off" -- the alternative is a deploy that silently stops every
    # trigger on every rig in the field.
    if on is not None and not on:
        return mech
    return None


print("which mechanism fired:")
check("an instant means the ears heard it",
      verdict(True, False, True, impact_at=1761710000.0), "audio")
check("no instant means somebody is standing there",
      verdict(True, True, False, impact_at=None), "dwell")

print()
print("audio on, dwell off — what the operator asked for:")
check("an audio trigger is admitted",
      verdict(True, True, False, impact_at=1761710000.0), None)
check("a dwell trigger is refused",
      verdict(True, True, False, impact_at=None), "dwell")

print()
print("the other way round:")
check("a dwell trigger is admitted",
      verdict(True, False, True, impact_at=None), None)
check("an audio trigger is refused",
      verdict(True, False, True, impact_at=1761710000.0), "audio")

print()
print("the states that must not surprise anybody:")
check("both on admits a dwell", verdict(True, True, True, None), None)
check("both on admits an audio", verdict(True, True, True, 1.0), None)
check("both off refuses a dwell", verdict(True, False, False, None), "dwell")
check("both off refuses an audio",
      verdict(True, False, False, 1.0), "audio")
# THE OLD SWITCH STILL ANSWERS FOR BOTH, and answers first: a paused
# camera is paused whatever the mechanisms say.
check("paused beats a live audio trigger",
      verdict(False, True, True, 1.0), "paused")
check("paused beats a live dwell", verdict(False, True, True, None),
      "paused")
# A ROW THAT PREDATES THE COLUMNS. This is every camera in the field at
# the moment the deploy lands, before the backfill has run.
check("an un-backfilled audio column is not off",
      verdict(True, None, None, 1.0), None)
check("an un-backfilled dwell column is not off",
      verdict(True, None, None, None), None)

print()
print("the wiring that makes it work on rigs nobody has updated:")

_cams = (ROOT / "backend" / "app" / "routers" / "cameras.py").read_text(
    encoding="utf-8")
# The refusal must carry the key old agents read. A new key alone would
# leave every un-updated rig recording and uploading dwells that the
# backend then throws away.
_block = re.search(
    r'if _mech_on is not None and not _mech_on:.*?\n\n',
    _cams, re.S)
check("the mechanism refusal exists", _block is not None, True)
_txt = _block.group(0) if _block else ""
check("...and answers with triggering_disabled",
      '"triggering_disabled": True' in _txt, True)
check("...and names the mechanism beside it",
      '"trigger_mechanism_disabled": _mech' in _txt, True)
check("...and creates no event", '"event_id": None' in _txt, True)

# The audio path on the Pi used to throw the ack away, so a refusal
# stopped the event row and not the upload.
_copy = (ROOT / "pi-agent" / "agent" / "copyengine.py").read_text(
    encoding="utf-8")
check("the audio path reads the ack",
      "_ack = runner.client.event_trigger(" in _copy, True)
check("...and drops the clip when it is refused",
      bool(re.search(
          r'_ack\.get\("triggering_disabled"\).*?'
          r'clip_path\.unlink\(missing_ok=True\).*?return None',
          _copy, re.S)), True)

# Both columns have to arrive ON, in the model and in the backfill, or
# the deploy itself is the outage.
_models = (ROOT / "backend" / "app" / "models.py").read_text(
    encoding="utf-8")
for _col in ("audio_trigger_enabled", "dwell_trigger_enabled"):
    check("%s defaults to True in the model" % _col,
          bool(re.search(
              _col + r': Mapped\[bool\] = mapped_column\(\s*Boolean,'
              r' default=True\)', _models)), True)

_main = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
check("both columns are added and backfilled TRUE",
      bool(re.search(
          r'for _tc in \("audio_trigger_enabled", "dwell_trigger_enabled"\)',
          _main))
      and "BOOLEAN DEFAULT TRUE" in _main
      and f"UPDATE cameras SET {{_tc}} = TRUE" in _main, True)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
