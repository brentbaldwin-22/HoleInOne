#!/usr/bin/env bash
# Why the tee rig lost a swing, counted by cause.
#
# THE CARD CANNOT KNOW THIS. A stuck event says "the tee clip never
# arrived", and from the server that is all there is to see: no bytes.
# But the tee registers the event BEFORE it records (tee.py, the
# event_trigger call), so EVERY failure after that point produces the
# same card — a lost camera cut and a dead uplink look identical from
# Production. They are not the same problem and they have different
# fixes. Only the Pi's own log separates them.
#
# Run on the tee:  ssh pi@golfreelz-tee 'bash -s' < tools/tee_losses.sh
set -u
SINCE=${SINCE:-24 hours ago}
LOG=$(journalctl -u golfreelz-agent --since "${SINCE}" --no-pager 2>/dev/null)

echo "=== $(hostname) — lost swings since ${SINCE} ==="
echo
count () {  # count <label> <grep-pattern> <meaning>
  local n
  n=$(printf '%s\n' "${LOG}" | grep -ciE "$2" || true)
  [ "${n:-0}" -eq 0 ] && return
  printf '  %-22s %3d   %s\n' "$1" "$n" "$3"
}
# Recording never produced a file — the uplink is not involved.
count "ring cut failed"   "could not cut session"       "camera/ring: the swing was never cut"
count "buffer empty"      "buffer empty at trigger"     "decode path: no frames at trigger"
count "writer failed"     "VideoWriter failed to open"  "no space, or a bad codec"
count "length cap"        "ran to its length cap"       "stop signal never arrived"
# The backend refused, or the clip had nowhere to land.
count "trigger refused"   "event_trigger refused"       "4xx: token, or camera disabled"
count "orphaned"          "no event to attach it to"    "event deleted while uploading"
count "paused"            "triggering is paused"        "operator paused this camera"
# The uplink really was the problem.
count "spool evicted"     "dropping spooled"            "spool full or >24h old"
count "upload timeouts"   "upload.*(timeout|stalled)"   "link slow or down"
echo
echo "  (nothing listed = that cause did not fire)"
echo
echo "--- the most recent few, with their session ids ---"
printf '%s\n' "${LOG}" \
| grep -iE "could not cut session|buffer empty at trigger|VideoWriter failed|event_trigger refused|no event to attach it to|dropping spooled" \
| tail -8 \
| sed 's/^/  /'
