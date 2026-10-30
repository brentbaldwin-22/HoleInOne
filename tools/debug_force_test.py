#!/usr/bin/env python3
"""Dev tools in production: deliberate yes, leaked no.

`_strip_dev_only_in_production` forces every dev-only flag off when
RENDER or REPLIT_DEPLOYMENT is set, and says why: the flags go off
"regardless of any secret/env-var that may be leaking in from the
global secrets store." A shared secret store is a real hazard and that
gate is the thing standing in front of it.

It also meant the diagnostic buttons could not be reached on the ONLY
deployment that has footage on it, so comparing a method against real
clips cost a code change and a deploy each way.

PRODUCE_DEBUG_FORCE threads that: a second switch, deliberately NOT
the name of the flag it reprieves, so the leak this gate exists to
stop still cannot reach it. Setting it is a decision somebody made for
this one service.

The truth table is the whole safety argument, so it is pinned here:

  dev box, nothing set              off
  dev box, PRODUCE_DEBUG_ENABLED    ON     — unchanged, that is dev
  production, PRODUCE_DEBUG_ENABLED off    — the leak, still blocked
  production, PRODUCE_DEBUG_FORCE   ON     — deliberate
  production, FORCE + mirror leak   mirror STILL stripped

That last row matters most: this reprieves ONE flag. A production box
pointed at another deployment is a different order of mistake from one
showing extra buttons, and the mirror settings stay stripped
unconditionally.

    python3 tools/debug_force_test.py
"""
import importlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILED = []
_KEYS = ("RENDER", "REPLIT_DEPLOYMENT", "PRODUCE_DEBUG_ENABLED",
         "PRODUCE_DEBUG_FORCE", "MIRROR_SOURCE_URL", "MIRROR_COURSE_ID")


def check(label, got, want):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


def boot(**env):
    """A fresh Settings under exactly this environment."""
    saved = {k: os.environ.pop(k, None) for k in _KEYS}
    os.environ.update({k: v for k, v in env.items()})
    try:
        for m in [m for m in sys.modules if m.startswith("backend.app.config")]:
            del sys.modules[m]
        import backend.app.config as c
        importlib.reload(c)
        return c.Settings()
    finally:
        for k in _KEYS:
            os.environ.pop(k, None)
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


print("off a deployment, nothing changes:")
check("nothing set, nothing on", boot().produce_debug_enabled, False)
check("the dev flag still works on a dev box",
      boot(PRODUCE_DEBUG_ENABLED="1").produce_debug_enabled, True)

print()
print("in production, the leak this gate exists for:")
for host in ("RENDER", "REPLIT_DEPLOYMENT"):
    check("%s + PRODUCE_DEBUG_ENABLED stays off" % host,
          boot(**{host: "1", "PRODUCE_DEBUG_ENABLED": "1"})
          .produce_debug_enabled, False)

print()
print("in production, said on purpose:")
for host in ("RENDER", "REPLIT_DEPLOYMENT"):
    check("%s + PRODUCE_DEBUG_FORCE turns it on" % host,
          boot(**{host: "1", "PRODUCE_DEBUG_FORCE": "1"})
          .produce_debug_enabled, True)
check("and with no host set it is simply the default",
      boot(PRODUCE_DEBUG_FORCE="1").produce_debug_enabled, False)

print()
print("it reprieves ONE flag, not the gate:")
_s = boot(RENDER="1", PRODUCE_DEBUG_FORCE="1",
          MIRROR_SOURCE_URL="https://old.example", MIRROR_COURSE_ID="7")
check("the debug flag is on", _s.produce_debug_enabled, True)
check("...and the mirror url is STILL stripped", _s.mirror_source_url, "")
check("...and the mirror course too", _s.mirror_course_id, 0)

print()
print("it announces itself:")
_cfg = (ROOT / "backend" / "app" / "config.py").read_text(encoding="utf-8")
check("the override logs at WARNING",
      'logging.getLogger("golfreelz.config").warning(' in _cfg, True)
check("...and says to unset it", "unset it when you are done" in _cfg, True)
# The phrase is split over a line continuation in the source, so it is
# matched on the warning as PYTHON would join it, not as the file
# spells it.
_joined = _cfg.replace('"\n                    "', "")
check("...and names the cost",
      "write artifacts into the clips directory" in _joined, True)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
