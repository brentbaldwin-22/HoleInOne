#!/usr/bin/env bash
# One rig's upload throughput, broken out by hour of the day.
#
# WHY BY HOUR, NOT A SINGLE AVERAGE. A one-day average said green ran
# 15x slower than tee (median 256 vs 3811 kbps), and then green got
# faster on its own. An average cannot tell those two apart:
#
#   slow at the same hours every day  -> tower congestion; nothing on
#                                        the rig will fix it, but the
#                                        shoot can be timed around it.
#   slow at every hour, every day     -> signal or plan: antenna,
#                                        placement, or a throttled SIM.
#   slow in bursts with no pattern    -> the modem dropping to a worse
#                                        band, or a USB re-enumeration.
#
# Run it on BOTH rigs over the same window and read the two together.
# The tee rig is the control: an hour that is slow on both is the
# backend or the internet, not the green uplink.
#
#   ssh pi@golfreelz-green 'bash -s' < tools/rig_throughput.sh
#   ssh pi@golfreelz-tee   'bash -s' < tools/rig_throughput.sh
#
# SINCE sets the window (default 3 days). Nothing secret is printed:
# only the timestamp and the rate are read out of each line.
set -u
SINCE=${SINCE:-3 days ago}

echo "=== $(hostname) — upload throughput by hour, since ${SINCE} ==="
journalctl -u golfreelz-agent --since "${SINCE}" --no-pager 2>/dev/null \
| grep -oE '^[A-Z][a-z]{2} +[0-9]+ [0-9]{2}:.*~[0-9]+ kbps' \
| sed -E 's/^([A-Z][a-z]{2}) +([0-9]+) ([0-9]{2}):.*~([0-9]+) kbps$/\1 \2 \3 \4/' \
| awk '{k=$1" "$2" "$3; n[k]++; s[k]+=$4
        if (!(k in lo) || $4 < lo[k]) lo[k] = $4
        if ($4 > hi[k]) hi[k] = $4
        tn++; ts += $4}
       END {
         if (tn == 0) {
           # One line, because this output goes through sort: three
           # lines would come back shuffled.
           print "Z no completed uploads in this window — the rig was idle, or journald rotated past it; widen with SINCE=\"7 days ago\"."
           exit
         }
         # Column 1 is a sort tag, stripped below: without it the
         # footer sorts above the rows it summarises.
         for (k in s)
           printf "A %s:00  n=%-3d mean=%-6d min=%-6d max=%d\n",
                  k, n[k], s[k]/n[k], lo[k], hi[k]
         printf "Z ----\nZ all hours: n=%d  mean=%d kbps\n", tn, ts/tn
       }' \
| sort -k1,1 -k2,2M -k3,3n -k4,4 \
| sed -E 's/^[AZ] /  /'

echo
echo "--- hour of day, averaged across the whole window ---"
# Collapsing the days answers "is it always 2pm?" directly.
journalctl -u golfreelz-agent --since "${SINCE}" --no-pager 2>/dev/null \
| grep -oE '^[A-Z][a-z]{2} +[0-9]+ [0-9]{2}:.*~[0-9]+ kbps' \
| sed -E 's/^.* ([0-9]{2}):.*~([0-9]+) kbps$/\1 \2/' \
| awk '{n[$1]++; s[$1]+=$2}
       END {for (h in s) printf "  %s:00  n=%-3d mean=%d kbps\n", h, n[h], s[h]/n[h]}' \
| sort
