#!/bin/bash
# GolfReelz link + thermal logger.
#
# One CSV line a minute. The point is a TIMELINE: every diagnosis on this
# project so far has come from a snapshot taken after something broke, and
# twice those snapshots misled us (a modem still registering read as 50%
# packet loss; single temperature readings hours apart read as a trend).
#
# With a timeline you can see whether temperature climbs BEFORE loss does
# -- which separates "the enclosure is cooking" from "the signal is bad" --
# and what the last readings were before the Pi went silent.
#
# IN THE REPO AS OF 14 OCT, having lived only on the tee Pi's SD card
# since it was written. It was found in `crontab -l` while hunting for
# something that might be resetting the modem, and for a few minutes it
# was a suspect: an unexplained script running sixty times an hour on the
# one rig with the fault. It is read-only and innocent -- and it turned
# out to be holding the only long timeline we have of when that rig's
# link was actually down. That is worth not losing to a reflash. Read it
# with tools/linklog_report.py.
#
# Install (per rig):
#   install -m 755 gr-linklog.sh /home/pi/gr-linklog.sh
#   ( crontab -l 2>/dev/null | grep -v gr-linklog
#     echo '* * * * * /home/pi/gr-linklog.sh >/dev/null 2>&1' ) | crontab -
OUT="$HOME/golfreelz-link.csv"
GW=$(ip route | awk '/default/{print $3; exit}')
[ -n "$GW" ] || GW="192.168.1.1"

ping_stats() {   # -> "loss_pct avg_ms"  ("100 -" when nothing comes back)
  local out
  out=$(ping -c 5 -i 0.3 -W 2 "$1" 2>/dev/null)
  local loss avg
  loss=$(printf '%s' "$out" | sed -n 's/.*, \([0-9.]*\)% packet loss.*/\1/p' | head -1)
  avg=$(printf '%s' "$out" | sed -n 's#.*= [0-9.]*/\([0-9.]*\)/.*#\1#p' | head -1)
  printf '%s %s' "${loss:-100}" "${avg:--}"
}

[ -f "$OUT" ] || echo "ts,uptime_s,temp_c,throttled,gw_loss_pct,gw_ms,net_loss_pct,net_ms" > "$OUT"

TEMP=$(vcgencmd measure_temp 2>/dev/null | tr -dc '0-9.')
THR=$(vcgencmd get_throttled 2>/dev/null | cut -d= -f2)
UP=$(cut -d. -f1 /proc/uptime)
read -r GWL GWM <<< "$(ping_stats "$GW")"
read -r NL NM <<< "$(ping_stats 1.1.1.1)"

echo "$(date -Is),$UP,${TEMP:--},${THR:--},$GWL,$GWM,$NL,$NM" >> "$OUT"

# Keep roughly 30 days and never let it grow without bound.
if [ "$(wc -l < "$OUT")" -gt 45000 ]; then
  tail -n 43200 "$OUT" > "$OUT.tmp" && mv "$OUT.tmp" "$OUT"
fi
