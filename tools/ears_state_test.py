#!/usr/bin/env python3
"""Which silence is this? — the four states, and the warning.

SWITCHING THE DWELL OFF CAN SILENCE A RIG COMPLETELY, and the reason
is not visible anywhere on the Cameras page without this. The audio
trigger's `shadow` flag DEFAULTS TO TRUE in config.yaml: in shadow it
decides and deliberately does not cut. So on a rig nobody has taken
out of shadow, switching the dwell off leaves the ears deciding, the
dwell silent, and nothing recording -- which looks exactly like a dead
microphone, a rig that was never set up to listen, and a camera that
simply nobody has walked in front of.

`replaces_dwell`, the automatic version of the same decision, has
always refused to stand the dwell down for a shadow trigger or an
unhealthy one. The manual switch shipped without that check. This
tests the state reader that now tells them apart, and the rule that
warns before the dwell goes off onto ears that are not cutting.

The reader is JS on the Cameras page; this checks the same truth table
against the agent's own field names, so a rename on either side shows
up here.

    python3 tools/ears_state_test.py
"""
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


def ears(audio_info, reported_sec_ago=0.0):
    """earsState() from AdminCameras.jsx, same order, same names."""
    a = audio_info
    if not isinstance(a, dict):
        return None
    if reported_sec_ago > 15 * 60:
        return "last known"
    if not a.get("configured"):
        return "not set up"
    if not a.get("running"):
        return "off"
    if not a.get("healthy"):
        return "deaf"
    if a.get("shadow"):
        return "SHADOW"
    return "cutting"


def warns(audio_info, reported_sec_ago=0.0):
    """Does switching the DWELL off warn first?

    Only a rig whose ears are actually cutting is safe to do it to.
    """
    return ears(audio_info, reported_sec_ago) != "cutting"


LIVE = {"configured": True, "running": True, "healthy": True,
        "shadow": False, "heard": 9, "confirmed": 7, "cuts": 7}

print("telling the four silences apart:")
check("never asked to listen",
      ears({"configured": False, "running": False}), "not set up")
check("switched off at the backend",
      ears({"configured": True, "running": False, "wanted": False}), "off")
check("ring up, microphone dead (Audio in unchecked)",
      ears({"configured": True, "running": True, "healthy": False,
            "floor": 0.0}), "deaf")
# THE ONE THAT CAUGHT THIS RIG. Healthy, hearing, confirming -- and
# cutting nothing, on purpose, because shadow is the default.
check("shadow: deciding and not cutting",
      ears({"configured": True, "running": True, "healthy": True,
            "shadow": True, "heard": 9, "confirmed": 7, "cuts": 0}),
      "SHADOW")
check("live and cutting", ears(LIVE), "cutting")
check("a rig that has not reported at all", ears(None), None)
# A rig that went offline keeps its last answer, and a stale answer
# must not read as the current state.
check("an answer from twenty minutes ago is not current",
      ears(LIVE, reported_sec_ago=20 * 60), "last known")

print()
print("switching the dwell off — when is it safe and silent:")
check("live and cutting: no warning", warns(LIVE), False)
check("shadow: warn",
      warns({"configured": True, "running": True, "healthy": True,
             "shadow": True}), True)
check("deaf: warn",
      warns({"configured": True, "running": True, "healthy": False}), True)
check("not set up: warn", warns({"configured": False}), True)
check("audio already switched off: warn",
      warns({"configured": True, "running": False, "wanted": False}), True)
check("nothing reported yet: warn", warns(None), True)
check("a stale answer: warn even though it said cutting",
      warns(LIVE, reported_sec_ago=20 * 60), True)

print()
print("the agent and the page agree on the field names:")

_tee = (ROOT / "pi-agent" / "agent" / "tee.py").read_text(encoding="utf-8")
_page = (ROOT / "frontend" / "src" / "pages"
         / "AdminCameras.jsx").read_text(encoding="utf-8")
# A rename on either side is a pill that silently stops working, which
# is the same class of bug as the thing it exists to report.
for _f in ("configured", "running", "healthy", "shadow", "wanted",
           "heard", "confirmed", "cuts", "floor", "gate"):
    check('the agent reports "%s"' % _f,
          bool(re.search(r'"%s":' % _f, _tee)), True)
    check('...and the page reads it', ("a.%s" % _f) in _page
          or ('a["%s"]' % _f) in _page, True)

check("the agent sends it as audio_info",
      '"audio_info": json.dumps(info)' in _tee, True)
_cams = (ROOT / "backend" / "app" / "routers"
         / "cameras.py").read_text(encoding="utf-8")
check("the heartbeat accepts audio_info",
      "audio_info: str | None = Form(None)" in _cams, True)
check("...and stores it with a timestamp",
      "cam.audio_info_at = _utcnow_naive()" in _cams, True)
_admin = (ROOT / "backend" / "app" / "routers"
          / "admin.py").read_text(encoding="utf-8")
check("the cameras list serves it",
      '"audio_info": c.audio_info,' in _admin, True)

# AND THE WARNING IS ACTUALLY WIRED to the dwell switch.
check("the dwell switch warns on anything but ok",
      bool(re.search(
          r'if \(mech === "dwell" && _turningOff\).*?'
          r'_ears\.tone !== "ok".*?window\.confirm', _page, re.S)), True)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
