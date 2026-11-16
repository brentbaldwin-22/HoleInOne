#!/usr/bin/env python3
"""A picture the backend cuts and nothing renders is a picture nobody sees.

`_ascent_ai_launch` cut a six-frame strip around the impact and a
frame showing the ball measured at rest, wrote both to CLIPS_DIR, and
put their URLs in the report. Nothing in the frontend ever read either
key. So the stage did its work, the files sat on disk, and the operator
reported "I'm not seeing any of the images that we discussed" -- twice.

Grepping for the name is what hid it: `frames_image_url` IS referenced
in the frontend, by CutVerdicts, on its own per-cut object. Same key,
different dict, and the stage's own picture still went nowhere.

So this does not grep for the bare key. For every URL key the backend
puts on the ai_launch report it requires the frontend to read it off
`ai_launch` specifically.

    python3 tools/pictures_rendered_test.py
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-58s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


adm = (ROOT / "backend" / "app" / "routers" / "admin.py").read_text()
fe = "\n".join(p.read_text()
               for p in (ROOT / "frontend" / "src").rglob("*.jsx"))

# THE STAGE'S OWN BODY, not the whole file: `out[...]` inside
# _ascent_ai_launch is the ai_launch report and nothing else is.
_i = adm.index("def _ascent_ai_launch")
_j = adm.index("\ndef ", _i + 10)
body = adm[_i:_j]

keys = sorted(set(re.findall(r'out\[["\'](\w*_url)["\']\]\s*=', body)))
print("URL keys _ascent_ai_launch puts on the report: %s" % ", ".join(keys))
check("it sets some at all", len(keys) > 0)

print()
print("every one of them is read off ai_launch in the frontend:")
for k in keys:
    # ON THE ai_launch OBJECT. `rep.ai_launch.frames_image_url`, or
    # destructured from it -- not merely the key appearing somewhere
    # on some other object, which is exactly how this hid.
    hit = (re.search(r'ai_launch(?:\s*\??\.\s*|\.)' + re.escape(k), fe)
           or re.search(r'ai_launch\s*\|\|\s*\{\}[^;]{0,200}' + re.escape(k),
                        fe))
    check("ai_launch.%s is rendered" % k, bool(hit))

# AND THE REASONS, same rule: a stage that declines must be able to say
# so on the screen, or it reads exactly like a stage that found nothing.
print()
print("and the fields that explain an absence:")
for k, where in [("reason", "ai_launch"), ("frames_why", "ai_launch")]:
    check("%s.%s is rendered" % (where, k),
          bool(re.search(r'ai_launch(?:\s*\??\.\s*|\.)' + re.escape(k), fe)))

print()
print("the per-cut panel explains a missing zoom too:")
cv = (ROOT / "frontend" / "src" / "components"
      / "CutVerdicts.jsx").read_text()
check("ball_box_why is rendered", "ball_box_why" in cv)
check("and only when there is no picture", "!c.ball_box_url" in cv)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("all ok — every picture the stage cuts has somewhere to appear")
