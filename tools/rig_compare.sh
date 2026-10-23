#!/usr/bin/env bash
# Everything about one rig's uplink that could explain a slow upload,
# in a form safe to paste into a chat.
#
# WHY A SCRIPT RATHER THAN A LIST OF COMMANDS. The settings that decide
# upload speed are spread across config.yaml, the modem, the kernel and
# the service, and "same settings on both rigs" is only answerable by
# putting the two outputs side by side. Reading them by hand, one rig
# at a time, is how a difference gets missed.
#
# NOTHING SECRET IS PRINTED. The camera's rtsp:// line carries the
# password, so every value is matched by KEY and the device line is
# reported only as present-or-absent. Run it on both rigs and paste
# both outputs.
#
#   ssh pi@golfreelz-green 'bash -s' < tools/rig_compare.sh
#   ssh pi@golfreelz-tee   'bash -s' < tools/rig_compare.sh
set -u
CFG=${CFG:-/opt/golfreelz-agent/config.yaml}

echo "=== rig: $(hostname) ==="
echo "--- uptime/load ---"
uptime
echo
echo "--- settings that decide upload speed ---"
# Keys only, values printed as given. `device:` is never printed.
for k in capture_engine copy_segment_seconds copy_fallback_preroll_seconds \
         upload_bitrate_kbps upload_chunked upload_chunk_kb \
         upload_settle_seconds upload_fresh_timeout upload_idle_timeout \
         upload_patient_timeout upload_backoff_base upload_backoff_max \
         upload_spool_max_mb buffer_seconds max_clip_seconds \
         record_delay_seconds width height fps mode; do
  v=$(grep -E "^[[:space:]]*#?[[:space:]]*${k}:" "$CFG" 2>/dev/null \
      | head -1 | sed 's/^[[:space:]]*//')
  printf '  %-30s %s\n' "$k" "${v:-<unset — using the default>}"
done
printf '  %-30s %s\n' "camera.device" \
  "$(grep -qE '^[[:space:]]*device:' "$CFG" && echo '<set, not printed>' \
     || echo '<missing>')"
echo
echo "--- modem / uplink ---"
GW=$(ip route 2>/dev/null | awk '/^default/{print $3; exit}')
echo "  default gateway: ${GW:-<none>}"
if [ -n "${GW:-}" ]; then
  echo "  modem says:"
  curl -s --max-time 6 "http://${GW}/api/model" 2>/dev/null | head -c 400
  echo
  # Signal, wherever this firmware hides it.
  curl -s --max-time 6 "http://${GW}/api/modem" 2>/dev/null \
    | tr ',' '\n' | grep -iE 'rsrp|rsrq|sinr|rssi|band|carrier|bars' \
    | head -12
fi
echo
echo "--- usb resets (a modem re-enumerating is NOT a signal problem) ---"
dmesg 2>/dev/null | grep -ciE 'usb .*(reset|disconnect)' \
  || echo "  dmesg not readable without sudo"
echo
echo "--- throughput, three runs against the backend ---"
BASE=$(grep -E '^[[:space:]]*backend_url:' "$CFG" 2>/dev/null \
       | head -1 | sed 's/.*backend_url:[[:space:]]*//; s/"//g')
echo "  backend: ${BASE:-<unset>}"
for i in 1 2 3; do
  # Time-to-first-byte and total, on a tiny endpoint: this measures the
  # LINK and the round trip, not the server's work.
  curl -s -o /dev/null --max-time 20 \
    -w "  run $i: connect %{time_connect}s  ttfb %{time_starttransfer}s  total %{time_total}s\n" \
    "${BASE}/health" 2>/dev/null || echo "  run $i: failed"
done
echo
echo "--- upload speed ---"
# REMOVED, NOT FIXED HERE. This used to POST 2 MB at ${BASE}/health and
# report the rate. /health is GET-only: the server answered 405 without
# reading the body, so the figure was the speed of being refused. It
# looked like a measurement and was not, which is worse than no line at
# all -- a rig was called slow on the strength of it.
echo "  see tools/speed_now.sh — it uploads to a host that accepts the"
echo "  bytes, and says FAILED instead of printing a zero when it cannot."
echo
echo "--- agent service ---"
systemctl is-active golfreelz-agent 2>/dev/null
journalctl -u golfreelz-agent --since '-2 hours' --no-pager 2>/dev/null \
  | grep -ciE 'upload (ok|done|complete)' | sed 's/^/  uploads in 2h: /'
journalctl -u golfreelz-agent --since '-2 hours' --no-pager 2>/dev/null \
  | grep -iE 'upload.*(timeout|retry|failed|stalled)' | tail -5
