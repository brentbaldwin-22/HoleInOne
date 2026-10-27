#!/usr/bin/env python3
"""What is the audio ring actually hearing, and how close did it come?

WHY. The detector only logs when it FINDS something, so a ring full of
silence and a course with no swings in it produce the same journal:
nothing. This reads the ring directly and says what is in it -- the
noise floor, the loudest peaks, and what ratio each would have scored
against the live threshold -- so "not listening" and "nothing to hear"
stop looking alike.

Runs against the live ring without touching the agent: it only reads
finished segments, which the agent has already closed.

    sudo -u golfreelz /opt/golfreelz-agent/venv/bin/python3 \
        /opt/golfreelz-agent/audioring_probe.py [seconds]
"""
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import yaml
except ImportError:
    yaml = None

from agent.audiocap import _NAME_GLOB, _seg_start  # noqa: E402
from agent.swingaudio import SwingListener  # noqa: E402

WINDOW = float(sys.argv[1]) if len(sys.argv) > 1 else 120.0
CFG = Path("/opt/golfreelz-agent/config.yaml")


def main() -> int:
    cfg = {}
    if yaml is not None and CFG.exists():
        cfg = yaml.safe_load(CFG.read_text()) or {}
    a = cfg.get("audio_trigger") or {}
    work = Path(cfg.get("work_dir") or "/var/lib/golfreelz-agent")
    d = work / "audioring"
    sr = int(a.get("sample_rate", 16000))
    ratio = float(a.get("min_ratio", 8.0))

    print("ring      : %s" % d)
    if not d.is_dir():
        print("  NOT A DIRECTORY — the ring never started. Check the agent "
              "log for 'audio ring:' at startup.")
        return 1
    segs = sorted(p for p in d.glob(_NAME_GLOB) if _seg_start(p) is not None)
    if not segs:
        print("  NO SEGMENTS. ffmpeg is not writing. Check %s" % (d / "audiocap.log"))
        return 1
    segs = segs[:-1][-int(WINDOW):]        # drop the in-progress one
    if not segs:
        print("  only one segment so far — wait a few seconds and re-run")
        return 1

    # THE WHOLE DIRECTORY, not just the slice this probe reads. The
    # ring is swept by AudioRing.tick, which for a long time had no
    # caller at all: window_seconds bounded nothing, the directory grew
    # at 32 KB/s forever, and `segments()` globs and sorts every file
    # in it on every tick -- so an unswept ring costs CPU that grows
    # without bound, five times a second, on a Pi that is also
    # capturing video. Reading only the recent slice would show a
    # perfectly healthy ring sitting on top of that.
    all_segs = sorted(d.glob(_NAME_GLOB))
    all_bytes = 0
    for q in all_segs:
        try:
            all_bytes += q.stat().st_size
        except OSError:
            pass
    want = float(a.get("window_seconds", 185.0))
    print("on disk   : %d files, %.1f MB (the window asks for %.0f files)"
          % (len(all_segs), all_bytes / 1e6, want))
    if len(all_segs) > want * 2:
        print("  *** THE RING IS NOT BEING SWEPT. Every tick globs and")
        print("  *** sorts all %d of these, five times a second. Update"
              % len(all_segs))
        print("  *** the agent; the next tick clears it.")
    _age = time.time() - (_seg_start(all_segs[-1]) or 0) if all_segs else None
    if _age is not None:
        print("newest    : %s (%.0fs ago)%s"
              % (all_segs[-1].name, _age,
                 "  *** STALLED: ffmpeg is not writing" if _age > 10 else ""))

    total = sum(p.stat().st_size for p in segs)
    print("read here : %d segments spanning %.0fs, %.1f MB"
          % (len(segs), len(segs) * float(a.get("segment_seconds", 1.0)),
             total / 1e6))

    zero = sum(1 for p in segs if p.stat().st_size == 0)
    if zero:
        print("  %d EMPTY segment(s) — ffmpeg is writing nothing" % zero)

    # Feed the real listener, so what it reports is what the agent sees.
    li = SwingListener(sr, highpass_hz=float(a.get("highpass_hz", 1500.0)),
                       min_peak_ratio=ratio,
                       baseline_sec=float(a.get("baseline_sec", 30.0)))
    seg_len = float(a.get("segment_seconds", 1.0))
    hits, rows = [], []
    for p in segs:
        try:
            pcm = p.read_bytes()
        except OSError:
            continue
        ts = _seg_start(p)
        hits += li.feed(pcm, ts)
        # TWO numbers per second, because they disagree and the
        # disagreement is the whole story. The raw peak is what a
        # person would hear; the envelope is what the threshold is
        # measured against, AFTER the high-pass has thrown away
        # everything below highpass_hz. A mower pins the first and
        # leaves the second alone.
        n = len(pcm) // 2
        raw = 0.0
        if n:
            raw = max(abs(v) for v in struct.unpack("<%dh" % n, pcm[:n * 2])) / 32768.0
        rows.append((ts, raw, li.envelope_max(ts, ts + seg_len)))

    med = li.median
    print()
    print("noise floor (median envelope, after the %.0f Hz high-pass): %.6f"
          % (float(a.get("highpass_hz", 1500.0)), med))
    if med <= 1e-5:
        print("  *** DIGITAL SILENCE. The ring is recording nothing. The")
        print("  *** stream is connected but carries no audio -- check the")
        print("  *** camera's Audio in on the profile the agent pulls.")
    print("gate      : x%.1f (min_ratio) = %.6f" % (ratio, med * ratio))
    print()
    import datetime
    import math
    print("closest the window came, by FILTERED ratio (what the gate sees).")
    print("'raw' is the unfiltered peak -- a loud raw with a low ratio is")
    print("energy the high-pass discarded, i.e. not a nearby strike.")
    print()
    print("    time        ratio   raw peak")
    best_ratio = 0.0
    for ts, raw, env in sorted(rows, key=lambda r: -r[2])[:8]:
        r = (env / med) if med > 0 else 0.0
        best_ratio = max(best_ratio, r)
        print("  %s  x%6.2f   %.5f (%.1f dBFS)"
              % (datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S"), r,
                 raw, 20 * math.log10(raw) if raw > 0 else -99))
    print()
    print("loudest ratio over the noise floor: x%.2f against a x%.1f gate"
          % (best_ratio, ratio))
    if med > 0 and best_ratio < ratio:
        print("  -> nothing came within %.0f%% of firing. At front-tee range a"
              % (100.0 * best_ratio / ratio))
        print("     real strike measures about x1 here (measured on 1257), so")
        print("     this is the expected reading for those tees, not a fault.")
    print()
    if hits:
        print("IMPACTS the live settings would have fired on:")
        for h in hits:
            import datetime
            print("  %s  x%.1f  attack %.0f ms"
                  % (datetime.datetime.fromtimestamp(h["at"]).strftime("%H:%M:%S"),
                     h["ratio"], h["attack_sec"] * 1000))
    else:
        print("NO IMPACTS at x%.1f over this window." % ratio)
        print("Ratio is only the first gate; a second above it can still be")
        print("declined on attack time. The table above is the ratio gate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
