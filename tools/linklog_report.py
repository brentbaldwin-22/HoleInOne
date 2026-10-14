#!/usr/bin/env python3
"""Read gr-linklog.sh's CSV: when was this rig's link actually down?

THE FILE IS THE ONLY LONG TIMELINE THIS PROJECT HAS. Every other
diagnosis here came from a snapshot taken after something broke, and
this page's history is largely a record of snapshots misleading us. One
row a minute for up to thirty days answers questions a live reading
cannot:

  * how often was the link dead, per day and per hour -- so a change
    can be judged against the week before it rather than against a
    hopeful ten minutes after it
  * has this rig EVER browned out (`throttled`), which we had only ever
    read live, on a rig that was fine at that moment
  * did temperature climb BEFORE loss did, which separates a cooking
    enclosure from a bad signal
  * when did the Pi reboot, off uptime_s going backwards

WHAT THE NUMBERS ARE NOT. The logger pings for about three seconds out
of every sixty, so a ten-second outage every three minutes lands inside
a sample only a fraction of the time. **The absolute percentages
understate outage time, by a lot.** They are a RELATIVE measure:
identical sampling on both sides of a change makes before-and-after
comparisons sound, and that is what this is for. A single bad minute
means almost nothing; a day's worth means something.

    python3 tools/linklog_report.py ~/golfreelz-link.csv
    python3 tools/linklog_report.py --day 2026-10-14 link.csv
    python3 tools/linklog_report.py --since 2026-10-10 link.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# A minute counts as DEAD at total loss, not at "some loss". Five pings
# 0.3s apart span ~1.5s; a modem that re-enumerates takes ~9s, so a
# sample that overlaps one comes back 100%. Partial loss is a different
# animal (a congested link, which this project has plenty of) and
# lumping the two together is how "the link is bad" stayed unfalsifiable
# for weeks.
DEAD_LOSS_PCT = 100.0

COLUMNS = ("ts", "uptime_s", "temp_c", "throttled",
           "gw_loss_pct", "gw_ms", "net_loss_pct", "net_ms")


def _num(raw):
    """A CSV field as a float, or None. The logger writes '-' for a
    reading it could not take, and a truncated last line is normal on a
    file being appended to while it is read."""
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def parse(lines):
    """CSV lines -> list of row dicts. Unparseable rows are skipped.

    Tolerant on purpose: this file is appended to by cron while being
    read, is trimmed by `tail` at 45000 lines, and has survived at
    least one agent version. A reader that dies on one short line is a
    reader that cannot answer the question on the day it matters.
    """
    rows = []
    for line in lines:
        line = line.strip()
        # The header check states intent; it is not what actually
        # filters the header out. The timestamp guard below does that
        # too (the literal "ts" has no capital T), so removing one does
        # not make the other load-bearing -- mutating this line away
        # changes no output. Both stay: a reader of this code should
        # not have to notice the coincidence.
        if not line or line.startswith("ts,"):
            continue
        parts = line.split(",")
        if len(parts) < len(COLUMNS):
            continue
        r = dict(zip(COLUMNS, parts))
        # .get() throughout, not r[...]. The length guard above already
        # drops a short row, so this is belt and braces -- but the file
        # is appended to by cron WHILE being read, and a parser that can
        # raise KeyError on a half-written line is a parser that dies on
        # the one day somebody needs the report.
        if "T" not in r.get("ts", ""):
            continue
        r["_day"] = r["ts"].split("T", 1)[0]
        # 'HH' out of '...THH:MM:SS+00:00'. Guarded because a row whose
        # timestamp is mangled should be dropped, not crash the report.
        _t = r["ts"].split("T", 1)[1]
        if len(_t) < 2 or not _t[:2].isdigit():
            continue
        r["_hour"] = _t[:2]
        r["_gw_loss"] = _num(r.get("gw_loss_pct"))
        r["_net_loss"] = _num(r.get("net_loss_pct"))
        r["_temp"] = _num(r.get("temp_c"))
        r["_uptime"] = _num(r.get("uptime_s"))
        rows.append(r)
    return rows


def is_dead(row) -> bool:
    """Was the GATEWAY unreachable this minute?

    The gateway, not 1.1.1.1, because the gateway IS the modem: losing
    it means the USB device or its stack went away, which is the fault
    under investigation. Losing 1.1.1.1 while the gateway answers is
    the carrier's problem and a different report.

    A missing reading is NOT dead. The logger writes 100 when ping
    returns nothing, so a real outage is always a number; a '-' means
    the measurement itself did not happen, and counting that as an
    outage would make a broken logger look like a broken link.
    """
    return row["_gw_loss"] is not None and row["_gw_loss"] >= DEAD_LOSS_PCT


def bucket(rows, key):
    """-> {bucket: (dead_minutes, total_minutes)}, in sorted order."""
    out: dict = {}
    for r in rows:
        d, t = out.get(r[key], (0, 0))
        out[r[key]] = (d + (1 if is_dead(r) else 0), t + 1)
    return dict(sorted(out.items()))


def reboots(rows):
    """Timestamps where uptime_s went BACKWARDS — i.e. the Pi restarted.

    Reads the reboot off the data rather than off a log, because the
    log is what goes missing when a rig power-cycles.
    """
    out = []
    prev = None
    for r in rows:
        up = r["_uptime"]
        if up is None:
            continue
        if prev is not None and up < prev:
            out.append((r["ts"], prev, up))
        prev = up
    return out


def throttle_counts(rows):
    """Every distinct `throttled` value seen, with its count and when it
    was first and last seen. 0x0 is healthy; anything else is a
    brownout this rig has ALREADY had, which no live reading can show."""
    out: dict = {}
    for r in rows:
        v = r["throttled"]
        if v in out:
            n, first, _ = out[v]
            out[v] = (n + 1, first, r["ts"])
        else:
            out[v] = (1, r["ts"], r["ts"])
    return dict(sorted(out.items(), key=lambda kv: -kv[1][0]))


def temp_split(rows):
    """Mean temperature in dead minutes vs live ones -> (dead, live).

    If the enclosure were cooking the modem, dead minutes would be the
    hot ones. Either side may be None when there are no samples.
    """
    acc = {True: [0.0, 0], False: [0.0, 0]}
    for r in rows:
        if r["_temp"] is None:
            continue
        a = acc[is_dead(r)]
        a[0] += r["_temp"]
        a[1] += 1
    return tuple(
        (a[0] / a[1]) if a[1] else None for a in (acc[True], acc[False])
    )


def _bar(frac, width=28):
    n = int(round(frac * width))
    return "#" * n + "." * (width - n)


def report(rows, day=None, out=print):
    if not rows:
        out("no usable rows")
        return
    out(f"{len(rows)} minutes, {rows[0]['ts']} .. {rows[-1]['ts']}")
    out("")
    out("DEAD MINUTES PER DAY  (gateway unreachable; understates real "
        "outage time -- see the module docstring)")
    for k, (d, t) in bucket(rows, "_day").items():
        out(f"  {k}  {d:5d} / {t:5d}  {100.0 * d / t:5.1f}%  {_bar(d / t)}")

    pick = day or rows[-1]["_day"]
    hourly = bucket([r for r in rows if r["_day"] == pick], "_hour")
    out("")
    out(f"HOUR BY HOUR, {pick}")
    if not hourly:
        out(f"  no rows for {pick}")
    for k, (d, t) in hourly.items():
        out(f"  {k}:00  {d:3d} / {t:3d}  {100.0 * d / t:5.1f}%  {_bar(d / t)}")

    out("")
    out("THROTTLED, every value seen  (0x0 = healthy; anything else is a "
        "brownout this rig has already had)")
    for v, (n, first, last) in throttle_counts(rows).items():
        out(f"  {v:>12}  {n:6d} min   first {first}   last {last}")

    hot, cool = temp_split(rows)
    out("")
    out("TEMPERATURE")
    out(f"  dead minutes: {'n/a' if hot is None else f'{hot:.1f}C'}")
    out(f"  live minutes: {'n/a' if cool is None else f'{cool:.1f}C'}")
    if hot is not None and cool is not None:
        out(f"  difference:   {hot - cool:+.1f}C"
            "   (a cooking enclosure makes dead minutes the HOT ones)")

    rb = reboots(rows)
    out("")
    out(f"REBOOTS: {len(rb)}")
    for ts, prev, now in rb[-15:]:
        out(f"  {ts}   uptime {prev:.0f}s -> {now:.0f}s")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("csv", nargs="?", default="~/golfreelz-link.csv")
    ap.add_argument("--day", help="which day to break down by hour "
                                  "(default: the last day in the file)")
    ap.add_argument("--since", help="ignore rows before this YYYY-MM-DD")
    a = ap.parse_args(argv)

    path = Path(a.csv).expanduser()
    if not path.exists():
        print(f"no such file: {path}", file=sys.stderr)
        print("On a rig, the logger writes ~/golfreelz-link.csv. If it is "
              "missing, gr-linklog.sh is not installed there — see "
              "pi-agent/tools/gr-linklog.sh.", file=sys.stderr)
        return 2
    rows = parse(path.read_text(errors="replace").splitlines())
    if a.since:
        rows = [r for r in rows if r["_day"] >= a.since]
    report(rows, day=a.day)
    return 0


if __name__ == "__main__":
    sys.exit(main())
