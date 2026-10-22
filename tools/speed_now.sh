#!/usr/bin/env bash
# What this rig's uplink will do RIGHT NOW.
#
# WHY NOT THE OTHER SCRIPT. tools/rig_throughput.sh reads what past
# uploads achieved, which is the truest measure but needs uploads to
# have happened -- useless before a round. This measures the link on
# demand.
#
# WHY NOT POST TO OUR OWN BACKEND. rig_compare.sh used to push 2 MB at
# /health and report the rate. /health is GET-only, so the server
# answered 405 without reading the body and the "upload speed" was the
# speed of being refused -- a number that looked plausible and meant
# nothing. A real measurement needs something that actually accepts
# the bytes.
#
# UP IS THE DIRECTION THAT MATTERS. Cellular links are wildly
# asymmetric; a fast download says nothing about whether a 20 MB clip
# will land. Every test here is an upload.
#
#   ssh pi@golfreelz-green 'bash -s' < tools/speed_now.sh
#   ssh pi@golfreelz-tee   'bash -s' < tools/speed_now.sh
set -u
SIZES_MB=${SIZES_MB:-"1 3"}
UP_URL=${UP_URL:-https://speed.cloudflare.com/__up}
CFG=${CFG:-/opt/golfreelz-agent/config.yaml}

echo "=== $(hostname) — uplink, $(date '+%H:%M:%S %Z') ==="
echo
echo "--- link ---"
GW=$(ip route 2>/dev/null | awk '/^default/{print $3; exit}')
IF=$(ip route 2>/dev/null | awk '/^default/{print $5; exit}')
echo "  gateway ${GW:-<none>} via ${IF:-<none>}"

BASE=$(grep -E '^[[:space:]]*backend_url:' "$CFG" 2>/dev/null \
       | head -1 | sed 's/.*backend_url:[[:space:]]*//; s/"//g')
if [ -n "${BASE:-}" ]; then
  curl -s -o /dev/null --max-time 20 \
    -w "  backend: connect %{time_connect}s  ttfb %{time_starttransfer}s\n" \
    "${BASE}/health" 2>/dev/null || echo "  backend: unreachable"
fi
echo
echo "--- upload, the direction that matters ---"
for MB in $SIZES_MB; do
  dd if=/dev/urandom of=/tmp/spd.bin bs=1M count="$MB" status=none 2>/dev/null
  OUT=$(curl -sS -o /dev/null --max-time 180 \
        -w '%{http_code} %{size_upload} %{time_total} %{speed_upload}' \
        --data-binary @/tmp/spd.bin \
        -H 'Content-Type: application/octet-stream' \
        "$UP_URL" 2>/dev/null)
  rm -f /tmp/spd.bin
  set -- $OUT
  CODE=${1:-000}; SENT=${2:-0}; SECS=${3:-0}; BPS=${4:-0}
  if [ "$CODE" = "000" ] || [ "${SENT:-0}" = "0" ]; then
    # SAY IT FAILED RATHER THAN PRINTING A ZERO. A 0 kbps line reads
    # like a dead link when it usually means the test target was
    # unreachable, which is a completely different problem.
    echo "  ${MB} MB: FAILED (http ${CODE}) — target unreachable, not"
    echo "           necessarily a slow link. Try UP_URL=<other host>."
  else
    KBPS=$(awk -v b="$BPS" 'BEGIN{printf "%.0f", b*8/1000}')
    echo "  ${MB} MB in ${SECS}s = ${KBPS} kbps  (http ${CODE})"
  fi
done
echo
echo "--- what real uploads actually got today ---"
# The measurement that counts: full clips over this link, through the
# agent's own retry and bitrate ladder.
journalctl -u golfreelz-agent --since today --no-pager 2>/dev/null \
  | grep -oE '~[0-9]+ kbps' | sed 's/[~]//; s/ kbps//' \
  | awk '{n++; s+=$1; if(!m||$1<m)m=$1; if($1>x)x=$1}
         END {if(n) printf "  %d upload(s): mean %d, slowest %d, fastest %d kbps\n", n, s/n, m, x;
              else print "  no completed uploads yet today"}'
