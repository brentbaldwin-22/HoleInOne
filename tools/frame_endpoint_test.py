#!/usr/bin/env python3
"""A frame that could not be written must say so.

`cv2.imwrite` RETURNS FALSE, IT DOES NOT RAISE. The frame endpoint
ignored that, and the failure surfaced two lines later as
`out_path.stat()` throwing FileNotFoundError -- an unhandled
exception, so FastAPI answered a bare "500: Internal Server Error"
with no detail. On the skyline modal, whose entire job is to show a
picture to draw on, that is the least useful thing it could have said.

Three properties, all source-level, because the failure is a shape of
code rather than a value: the write is checked, the cache-buster is
not taken inline off a file that may not exist, and a cached frame of
zero bytes (a write that ran out of disk) is not served for ever.

    python3 tools/frame_endpoint_test.py
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


_src = (ROOT / "backend" / "app" / "routers"
        / "admin.py").read_text(encoding="utf-8")
# Just the frame endpoint.
_i = _src.index('@router.get("/long-uploads/{upload_id}/frame")')
_body = _src[_i:_src.index("@router.post", _i)]

print("the one endpoint the skyline modal depends on:")
check("the write is checked",
      bool(re.search(r"if not cv2\.imwrite\(", _body)), True)
check("...and says what went wrong",
      "most likely full or not writable" in _body, True)
check("the cache-buster is not taken inline",
      "?v={int(out_path.stat().st_mtime)}" in _body, False)
check("...it is taken where it can be caught",
      bool(re.search(r"_stamp = int\(out_path\.stat\(\)\.st_mtime\)\s*\n"
                     r"\s*except OSError", _body)), True)
check("a zero-byte cached frame is rewritten",
      "out_path.stat().st_size > 0" in _body, True)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
