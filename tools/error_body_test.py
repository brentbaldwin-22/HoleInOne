#!/usr/bin/env python3
"""Does a failed backend call say something a person can act on?

WHAT THE OPERATOR SAW on upload 1310:

    AUDIO x13.6 · JUDGE: COULD NOT TELL — ASK FAILED: BACKEND POST
    /verify-swing FAILED: 502: <!DOCTYPE html> <HTML LANG="EN"> <HEAD>
    <META CHARSET="UTF-8"> <META NAME="VIEWPORT" CONTENT="WIDTH=DEVICE-WID
    @ 5.3S

The rig pasted the first 120 characters of whatever came back. The
host's error page says nothing whatever about this request: it fills
the card, cuts off mid-attribute, and carries exactly one useful fact,
which is the number in front of it.

A JSON body is the opposite case -- the backend's own `detail` is
written for this reader and beats the status code. So the rule is:
the detail when there is one, the page title when it is a web page
("502 Bad Gateway" in four words), the text when it is short and
plain, and a shape note when it is none of those.

    python3 tools/error_body_test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi-agent"))

from agent.common import short_body  # noqa: E402

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


class Resp:
    """Only what short_body touches."""

    def __init__(self, text, ctype="", raises=False):
        self.text = text
        self.headers = {"content-type": ctype}
        self._raises = raises

    def json(self):
        import json as _j
        if self._raises:
            raise ValueError("not json")
        return _j.loads(self.text)


# The real page, as Render serves it.
RENDER_502 = (
    '<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    '<title>502 Bad Gateway</title>\n</head>\n<body>\n'
    '<h1>Bad Gateway</h1>\n' + ("<p>padding</p>\n" * 60) + '</body>\n</html>'
)


def main() -> int:
    # ---- the page that caused this ---------------------------------
    got = short_body(Resp(RENDER_502, "text/html; charset=utf-8"))
    print(f"\n      502 page -> {got!r}\n")
    check("an HTML error page is summarised, not pasted",
          "<!DOCTYPE" not in got and "<meta" not in got.lower(), True)
    check("  and the title, which is the useful part, survives",
          "502 Bad Gateway" in got, True)
    check("  it says whose page it is", "from the host" in got, True)
    check("  and how big it was", str(len(RENDER_502)) in got, True)
    check("  while staying short enough for a card", len(got) < 120, True)
    # The exact string the operator could not read.
    check("  the viewport meta is gone",
          "WIDTH=DEVICE-WID" in got.upper(), False)

    # DETECTED BY SHAPE, not only by the header. A host that sends an
    # HTML page as text/plain -- or with no content-type at all -- is
    # the same page and the same uselessness.
    check("an HTML body with no content-type is still recognised",
          "502 Bad Gateway" in short_body(Resp(RENDER_502)), True)
    check("  and one mislabelled as text/plain",
          "502 Bad Gateway" in short_body(Resp(RENDER_502, "text/plain")),
          True)
    check("  a page with no title says so rather than guessing",
          "no title" in short_body(Resp("<html><body>nope</body></html>",
                                        "text/html")), True)

    # ---- JSON is the case where the BODY beats the status ----------
    check("the backend's own detail is kept",
          short_body(Resp('{"detail": "camera token not found"}',
                          "application/json")),
          "camera token not found")
    check("  under any of the keys it uses",
          short_body(Resp('{"error": "clip already attached"}',
                          "application/json")),
          "clip already attached")
    check("  recognised without a content-type too",
          short_body(Resp('{"detail": "no key"}')), "no key")
    check("  a JSON body with nothing useful falls back to its text",
          short_body(Resp('{"x": 1}', "application/json")), '{"x": 1}')
    check("  and a content-type that lies does not throw",
          short_body(Resp("not json at all", "application/json")),
          "not json at all")

    # ---- plain text is passed through ------------------------------
    check("a short plain message is left alone",
          short_body(Resp("upstream timed out", "text/plain")),
          "upstream timed out")
    check("  whitespace is flattened so it fits one line",
          short_body(Resp("upstream\n\n  timed   out", "text/plain")),
          "upstream timed out")
    check("  an empty body says so rather than printing nothing",
          short_body(Resp("", "text/plain")), "<empty body>")

    # ---- it never raises, whatever it is handed --------------------
    # This runs inside the error path of a rig that has already lost
    # one request; an exception here loses the swing as well.
    class Broken:
        headers = {}

        @property
        def text(self):
            raise RuntimeError("socket gone")

    check("an unreadable body does not raise", short_body(Broken()),
          "<unreadable body>")

    class NoHeaders:
        text = "plain words"
        headers = None

        def json(self):
            raise ValueError

    check("  nor does a response with no headers at all",
          short_body(NoHeaders()), "plain words")

    # ---- and a long plain body is still bounded --------------------
    check("a long plain body is truncated", len(short_body(
        Resp("x" * 5000, "text/plain"))), 160)

    # ---- the callers use it ----------------------------------------
    src = (ROOT / "pi-agent" / "agent" / "common.py").read_text()
    check("the retry loop reports through it",
          'last_err = f"{resp.status_code}: {short_body(resp)}"' in src, True)
    check("  and the hard-fail path too", "_body = short_body(resp)" in src,
          True)
    # THE ORPHAN CHECK STILL READS THE RAW BODY. It matches on the
    # backend's own words, and handing it a summary would silently
    # stop a clip being recognised as unattachable.
    check("  while the orphan check keeps the real body",
          "_clip_is_orphaned(_body)" in src
          and "_body = resp.text[:200]" in src, True)

    print()
    if FAIL:
        print(f"FAILED {len(FAIL)}: " + ", ".join(FAIL))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
