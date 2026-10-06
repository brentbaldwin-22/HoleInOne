#!/usr/bin/env python3
"""Does the camera card report BOTH halves of the pipeline?

Everything the STREAM block showed described what the camera SENDS.
Nothing described what the clip is cut down to before it crosses the
link — and the only scaler in that path, `upload_scale_height`, lives in
the Pi's config where nobody looks.

So a tee set to 1920x1080 read 1920x1080 on the card, in its config, and
in the camera's own web UI, while delivering 1280x720 clips. Every
number on the page agreed with every other one and all of them were
about the wrong half. It cost an afternoon.

`downscaled` is the comparison of the two, computed once on the server
so the card does not have to re-derive it. The UI suite stubs the API,
so it can see the card react to this flag but never see the flag being
computed; that is this file's job.

    python3 tools/stream_status_test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.routers.cameras import stream_status  # noqa: E402

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


def info(**over):
    """A heartbeat's stream block, as the agent sends it."""
    base = {
        "open_w": 1920, "open_h": 1080, "open_fps": 60.0,
        "config_fps": 60.0, "delivered_fps": 59.8, "stamped_fps": 59.8,
        "upload_scale_height": None, "upload_bitrate_kbps": 3500,
    }
    base.update(over)
    return base


def main() -> int:
    # ---- the case that cost an afternoon ----------------------------
    s = stream_status(info(upload_scale_height=720))
    check("1080 in, 720 out is flagged", s["downscaled"], True)
    check("and the card is told what it is cut to",
          s["upload_scale_height"], 720)
    check("and at what bitrate", s["upload_bitrate_kbps"], 3500)

    # ---- the control, or the flag means nothing ----------------------
    check("no scale set is not downscaling",
          stream_status(info())["downscaled"], False)
    check("scaling to the height it already is, is not downscaling",
          stream_status(info(upload_scale_height=1080))["downscaled"], False)
    # UPSCALING IS NOT A WARNING EITHER. It wastes bytes rather than
    # throwing detail away, and a card that cries about both teaches
    # the operator to ignore it.
    check("scaling UP is not flagged as throwing detail away",
          stream_status(info(open_h=720, upload_scale_height=1080))["downscaled"],
          False)

    # ---- an agent that predates the field ----------------------------
    # Every rig updates on its own schedule over a link that sometimes
    # cannot carry an update at all, so "the key is missing" is a state
    # this runs in for weeks, not an impossible one. It must read as
    # "not downscaling", never as a warning nobody can act on.
    old = info()
    del old["upload_scale_height"]
    del old["upload_bitrate_kbps"]
    s_old = stream_status(old)
    check("an agent too old to report it does not warn",
          s_old["downscaled"], False)
    check("and reports the field as absent rather than inventing one",
          s_old["upload_scale_height"], None)

    # ---- a camera that has not opened yet ----------------------------
    # open_h is None until OpenCV has a stream. Comparing against it
    # must not raise, and must not warn on a comparison it cannot make.
    check("no opened size yet is not a warning",
          stream_status(info(open_h=None, upload_scale_height=720))["downscaled"],
          False)

    print()
    if FAIL:
        print(f"{len(FAIL)} FAILED: {', '.join(FAIL)}")
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
