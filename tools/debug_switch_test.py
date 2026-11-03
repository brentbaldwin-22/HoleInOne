#!/usr/bin/env python3
"""One switch hides every diagnostic button on a production row.

SEVEN BUTTONS UNDER EVERY UPLOAD. Debug, Debug2, Debug3, Swing test,
Scan & produce, Find ascents and Produce ascents, with the one an
operator actually needs -- Produce -- sitting in the middle of them.
Each had arrived on its own terms and each was defensible on its own:
the two retired ones behind a "legacy debug" switch, three more behind
the deployment's dev flag, and the two ascent buttons unconditionally,
because at the time they were the only view of what produce did.

Now the switch governs the lot, and it is called what it does.

    python3 tools/debug_switch_test.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-64s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


_jsx = (ROOT / "frontend" / "src" / "pages"
        / "AdminProduction.jsx").read_text(encoding="utf-8")

print("the switch:")
check("it is called what it does", ">debug buttons<" in
      _jsx.replace("<span className=\"muted\">", ">")
          .replace("</span>", "<"), True)
# THE PREFERENCE SURVIVES THE RENAME. Changing the key would silently
# turn the switch off for anyone who had it on.
check("the stored key is unchanged",
      'DEBUG_BUTTONS_STORAGE = "golfreelz.showLegacyDebug"' in _jsx, True)
# A SWITCH THAT CAN HIDE SOMETHING HAS TO BE REACHABLE TO BRING IT
# BACK. It used to render only where the dev flag was set, which was
# fine while it governed dev-only buttons and is a trap now that Find
# ascents and Produce ascents are behind it too.
_cb = _jsx[_jsx.index("ALWAYS HERE, unlike the buttons it governs"):]
_cb = _cb[:_cb.index("</label>")]
check("the checkbox itself is not behind the dev flag",
      "produceDebug.enabled" in _cb, False)
# WHAT AN OPERATOR READS. The comment above the switch still mentions
# the old name on purpose -- somebody will find it in localStorage one
# day -- but the control itself must not.
check("neither its label nor its tooltip says legacy",
      "legacy" in _cb.lower(), False)
check("...and the tooltip lists what it governs",
      all(w in _cb for w in ("Debug3", "Swing test", "Find ascents",
                             "Produce ascents")), True)

print()
print("every button on the row is behind it:")
# The block of row actions, from the first diagnostic button to the
# last. Each button's own deployment gate stays ON TOP of the switch:
# the dev-only ones still need produceDebug.enabled, and Scan for ball
# still wants a mirror to pull from.
_blk = _jsx[_jsx.index("🐞 Debug") - 2000:_jsx.index("🛫 Produce ascents") + 200]
for _label, _icon in (("Debug", "🐞 Debug"), ("Debug2", "🔬 Debug2"),
                      ("Debug3", "🧿 Debug3"),
                      ("Swing test", "⛳ Swing test"),
                      ("Scan for ball", "⚪ Scan for ball"),
                      ("Scan & produce", "⚪▶ Scan &amp; produce"),
                      ("Find ascents", "🔎 Find ascents"),
                      ("Produce ascents", "🛫 Produce ascents")):
    _i = _blk.index(_icon)
    # the nearest gate opening above this button
    _gate = _blk.rfind("{showDebug", 0, _i)
    _other = _blk.rfind("{produceDebug.enabled", 0, _i)
    check("%s is hidden with the switch off" % _label,
          _gate > _other and _gate != -1, True)

print()
print("what the switch does NOT govern:")
# The ordinary Produce button is the operator's path and must never be
# hidden -- it runs the ascent pipeline itself, which is why hiding
# Produce ascents costs nothing.
_prod = _jsx[_jsx.index("onClick={() => handleProduce(row)}") - 400:
             _jsx.index("onClick={() => handleProduce(row)}")]
check("Produce is not behind the switch", "showDebug" in _prod, False)
check("...and it is the ascent path, so nothing is lost",
      "Produce is the ascent path now" in _jsx, True)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("all good — one switch, and the row reads cleanly without it")
