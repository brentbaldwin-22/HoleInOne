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

    total = sum(p.stat().st_size for p in segs)
    print("segments  : %d spanning %.0fs, %.1f MB"
          % (len(segs), len(segs) * float(a.get("segment_seconds", 1.0)),
             total / 1e6))
    print("newest    : %s" % segs[-1].name)

    zero = sum(1 for p in segs if p.stat().st_size == 0)
    if zero:
        print("  %d EMPTY segment(s) — ffmpeg is writing nothing" % zero)

    # Feed the real listener, so what it reports is what the agent sees.
    li = SwingListener(sr, highpass_hz=float(a.get("highpass_hz", 1500.0)),
                       min_peak_ratio=ratio,
                       baseline_sec=float(a.get("baseline_sec", 30.0)))
    hits, peaks = [], []
    for p in segs:
        try:
            pcm = p.read_bytes()
        except OSError:
            continue
        ts = _seg_start(p)
        hits += li.feed(pcm, ts)
        # Loudest sample in this second, as a multiple of the median,
        # so a near-miss is visible and not just an absence.
        n = len(pcm) // 2
        if n:
            s = struct.unpack("<%dh" % n, pcm[:n * 2])
            peaks.append((ts, max(abs(v) for v in s) / 32768.0))

    med = li.median
    print()
    print("noise floor (median envelope, after the %.0f Hz high-pass): %.6f"
          % (float(a.get("highpass_hz", 1500.0)), med))
    if med <= 1e-5:
        print("  *** DIGITAL SILENCE. The ring is recording nothing. The")
        print("  *** stream is connected but carries no audio -- check the")
        print("  *** camera's Audio in on the profile the agent pulls.")
    print("threshold : x%.1f of that = %.6f" % (ratio, med * ratio))
    print()
    print("loudest seconds in the window (raw peak sample):")
    for ts, v in sorted(peaks, key=lambda r: -r[1])[:8]:
        import datetime
        print("  %s  peak %.5f  (%.1f dBFS)"
              % (datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S"), v,
                 20 * __import__("math").log10(v) if v > 0 else -99))
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
        print("Re-run with a lower ratio to see how close it came:")
        print("  min_ratio in config is %.1f; the probe uses it directly."
              % ratio)
    return 0


if __name__ == "__main__":
    sys.exit(main())
