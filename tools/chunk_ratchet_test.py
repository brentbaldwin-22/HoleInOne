#!/usr/bin/env python3
"""Can the upload chunk size climb back, or only fall?

THE BUG THIS EXISTS FOR. The uploader halves its chunk on a timeout,
down to a 64 KB floor, and saves the result to learned_kbps.json. The
grow-back check compared against the size the CALLER handed in -- which
is the size it had already been beaten down to. So the ceiling fell
with the floor, the saved value survived restarts, and nothing in the
system could ever raise it again.

On 15 Oct the tee's SIM expired mid-upload. Every chunk timed out, the
size walked to 64 KB, and refilling the SIM did not help: 23.7 MB in
64 KB slices is 371 round trips. The owner's words were that uploads
"slow down with each successive upload to the point where it's
unusable" -- which is a ratchet, described exactly.

The assertions below are about the ratchet, not about ffmpeg or the
network: a fake session answers every chunk, so the only thing under
test is how _chunk moves.

    python3 tools/chunk_ratchet_test.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi-agent"))

import time as _time

_time.sleep = lambda *_a, **_k: None

from agent.common import BackendClient, CHUNK_MIN_BYTES  # noqa: E402

FAIL: list[str] = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


class Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self.text = json.dumps(payload or {})
        self.content = self.text.encode()

    def json(self):
        return json.loads(self.text)


class Wire:
    """Banks whatever it is sent. `fail_first` chunks time out first.

    Models the server faithfully enough for the ratchet: it reports how
    much it holds, and accepts appended bytes.
    """

    def __init__(self, fail_first=0):
        self.received = 0
        self.fail_first = fail_first
        self.chunk_sizes: list[int] = []

    def request(self, method, url, **kw):
        if "upload-status" in url:
            return Resp(200, {"received": self.received})
        body = kw.get("data")
        n = len(body) if body else 0
        files = kw.get("files") or {}
        for v in files.values():
            fh = v[1] if isinstance(v, (tuple, list)) else v
            n = len(fh.read())
        self.chunk_sizes.append(n)
        if self.fail_first > 0:
            self.fail_first -= 1
            import requests
            raise requests.Timeout("chunk timed out")
        self.received += n
        done = self.received >= self.total
        return Resp(200, {"received": self.received,
                          "ok": True,
                          **({"event_id": 1, "status": "ok"} if done else {})})


def run_upload(path, start_chunk, chunk_max, fail_first=0):
    c = BackendClient("http://x", "tok")
    w = Wire(fail_first=fail_first)
    w.total = path.stat().st_size
    c.session = w
    sizes = []
    try:
        c.upload_event_chunked(
            "sess", path, chunk_bytes=start_chunk, chunk_max=chunk_max,
            chunk_timeout=1, deadline_seconds=120,
            on_chunk_size=sizes.append,
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}", "sizes": sizes, "wire": w}
    return {"error": None, "sizes": sizes, "wire": w}


with tempfile.TemporaryDirectory() as td:
    clip = Path(td) / "c.mp4"
    # Big enough that a 64 KB start needs many chunks, so a grow-back
    # has room to happen several times.
    clip.write_bytes(b"\0" * (4 * 1024 * 1024))

    # THE FIX. Started at the floor because a dead link beat it there,
    # but allowed to climb back to the configured 512 KB.
    r = run_upload(clip, CHUNK_MIN_BYTES, 512 * 1024)
    check("a floored chunk size climbs back when the link is clean",
          max(r["sizes"]) if r["sizes"] else CHUNK_MIN_BYTES, 512 * 1024)
    check("...and the upload still completes", r["error"], None)

    # CONTROL: the ratchet reproduced. chunk_max equal to the start is
    # exactly what the old code did, and it must NOT grow.
    r = run_upload(clip, CHUNK_MIN_BYTES, CHUNK_MIN_BYTES)
    check("the OLD behaviour: ceiling == start means it never grows",
          max(r["sizes"]) if r["sizes"] else CHUNK_MIN_BYTES,
          CHUNK_MIN_BYTES)

    # CONTROL: it must never exceed the ceiling it is given.
    r = run_upload(clip, CHUNK_MIN_BYTES, 128 * 1024)
    check("it never climbs past the configured ceiling",
          max(r["sizes"]) if r["sizes"] else CHUNK_MIN_BYTES, 128 * 1024)

    # CONTROL: a healthy start is not dragged DOWN by the new ceiling.
    # Stated as "the size callback never fires", because that is what
    # "unchanged" means here -- on_chunk_size is called only when the
    # size MOVES. Measuring the POST bodies instead would also catch the
    # final completion call, which carries a few bytes of form fields
    # and is not a chunk.
    r = run_upload(clip, 512 * 1024, 512 * 1024)
    check("a healthy chunk size never moves at all", r["sizes"], [])
    check("...and every chunk it sent was the full size",
          [n for n in r["wire"].chunk_sizes if n > 1024],
          [512 * 1024] * 8)

    # Timeouts still halve it -- the fix must not disable the backoff
    # that makes a bad link survivable in the first place.
    r = run_upload(clip, 512 * 1024, 512 * 1024, fail_first=3)
    check("timeouts still halve the chunk",
          min(r["sizes"]) < 512 * 1024 if r["sizes"] else False, True)
    check("...but never below the floor",
          min(r["sizes"]) >= CHUNK_MIN_BYTES if r["sizes"] else True, True)

    # A ceiling of 0 means "use the start size", so an older caller that
    # passes no chunk_max behaves exactly as before.
    r = run_upload(clip, 256 * 1024, 0)
    check("chunk_max=0 falls back to the start size (old callers)",
          max(r["sizes"]) if r["sizes"] else 256 * 1024, 256 * 1024)

    # A ceiling that is NOT a power-of-two multiple of the floor. Every
    # other case here doubles neatly onto its ceiling, so an unclamped
    # `_chunk * 2` would pass them all and overshoot this one. A config
    # of chunk_kb: 300 reaches exactly this.
    r = run_upload(clip, CHUNK_MIN_BYTES, 300 * 1024)
    check("growth is clamped to an awkward ceiling, not overshot",
          max(r["sizes"]) if r["sizes"] else CHUNK_MIN_BYTES, 300 * 1024)

    # The floor has to hold under a long run of failures, not just a
    # run that happens to land on it. 512 KB halves onto 64 KB in
    # exactly three steps, so three timeouts cannot tell a floor from
    # its absence.
    r = run_upload(clip, 512 * 1024, 512 * 1024, fail_first=8)
    check("a long run of timeouts still stops at the floor",
          min(r["sizes"]) if r["sizes"] else CHUNK_MIN_BYTES,
          CHUNK_MIN_BYTES)

    # AND FROM AN AWKWARD SIZE, which is the case that actually needs
    # the clamp. Halving a power of two lands exactly ON the floor, so
    # the `_chunk > CHUNK_MIN_BYTES` guard alone is enough and the
    # max() looks redundant -- a mutation run showed it surviving.
    # 300 KB halves 150 -> 75 -> 37, and 75 KB is above the floor, so
    # without the clamp the next step goes under it.
    r = run_upload(clip, 300 * 1024, 300 * 1024, fail_first=8)
    check("an awkward chunk size is clamped AT the floor, not through it",
          min(r["sizes"]) if r["sizes"] else CHUNK_MIN_BYTES,
          CHUNK_MIN_BYTES)


# ---------------------------------------------------------------------
# The wiring. The fix is worthless if the uploader does not hand its
# configured ceiling down -- and nothing above would notice, because
# everything above calls upload_event_chunked directly.
# ---------------------------------------------------------------------

from agent.common import BackgroundUploader  # noqa: E402


class CapturingClient:
    def __init__(self):
        self.kwargs = None

    def upload_event_chunked(self, session_id, video_path, **kw):
        self.kwargs = kw
        return {"event_id": 1}


def deliver_kwargs(chunk_now, configured):
    u = BackgroundUploader.__new__(BackgroundUploader)
    u.client = CapturingClient()
    u.chunked = True
    u._chunk_now = chunk_now
    u.chunk_bytes = configured
    u._kbps_now = 0
    u._last_progress = 0
    u._deliver("s", Path("/nonexistent.mp4"), None, 1, 60)
    return u.client.kwargs


_kw = deliver_kwargs(CHUNK_MIN_BYTES, 512 * 1024)
check("the uploader starts where the link left it",
      _kw.get("chunk_bytes"), CHUNK_MIN_BYTES)
check("...and hands down the CONFIGURED ceiling to climb back to",
      _kw.get("chunk_max"), 512 * 1024)

print()
if FAIL:
    print(f"{len(FAIL)} FAILED: {', '.join(FAIL)}")
    sys.exit(1)
print("all checks passed")
