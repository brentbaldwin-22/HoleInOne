"""Curfew sleep/wake: halt the Pi outside playing hours, wake by RTC.

Power math that motivates this: the rig draws ~6-8W recording but
essentially nothing halted, so sleeping the ~10 dark hours stretches a
100Ah battery from ~5-6 days to ~10-12 — and battery swaps happen at
night, when the camera is asleep anyway, costing zero footage.

Mechanism (Pi 5): write the next wake time (epoch seconds) into
/sys/class/rtc/rtc0/wakealarm, then power off. The RTC (with its coin
cell) fires the alarm and the PMIC boots the Pi at dawn. For the
lowest halted draw set POWER_OFF_ON_HALT=1 in the EEPROM config
(rpi-eeprom-config); wake-on-alarm works either way.

Safety rails:
  * Clock sanity — never halt on a clock that predates this code
    (unsynced boot clock could put "18:30" anywhere in history).
  * Boot grace — if the Pi boots INSIDE the curfew window (battery
    reconnected early, manual power-up for maintenance), stay awake
    for boot_grace_seconds so the operator can SSH in and disable the
    curfew before it re-halts.
  * Arm-before-halt — the wakealarm write must SUCCEED or the halt is
    skipped; a Pi that can't promise to wake up stays awake.
"""

from __future__ import annotations

import datetime as dt
import logging
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger("golfreelz_agent.curfew")

_WAKEALARM = Path("/sys/class/rtc/rtc0/wakealarm")
_MIN_SANE_YEAR = 2025

def _parse_hhmm(s: str) -> tuple[int, int]:
    h, m = str(s).strip().split(":")
    return int(h), int(m)


def _uptime_seconds() -> float:
    """How long this machine has been up. 0.0 when it cannot be read,
    which makes the boot grace start now -- the forgiving direction."""
    try:
        with open("/proc/uptime", "r") as fh:
            return float(fh.read().split()[0])
    except Exception:  # noqa: BLE001 - not a Linux box, or no procfs
        return 0.0


def _who_is_logged_in() -> list:
    """Interactive sessions on this machine, by name. Empty when none,
    and empty when it cannot be worked out -- a curfew must not be
    blocked forever by a question it cannot answer."""
    try:
        out = subprocess.run(["who"], capture_output=True, text=True,
                             timeout=5)
        return sorted({ln.split()[0] for ln in (out.stdout or "").splitlines()
                       if ln.strip()})
    except Exception:  # noqa: BLE001
        return []


class CurfewThread(threading.Thread):
    """Checks the local clock every 30s; inside the sleep window it
    arms the RTC for the wake time and powers the Pi off."""

    def __init__(self, cfg: dict | None, work_pending=None):
        super().__init__(daemon=True, name="curfew")
        cfg = cfg or {}
        self.enabled = bool(cfg.get("enabled", False))
        # 18:30 TO 07:30, which is what the rigs are actually run on.
        # It gets dark at Rivertowne around 17:30 once the clocks go
        # back, so 21:00 was three hours of nothing being filmed on a
        # battery pack, and 06:00 was ninety minutes of the dark on
        # the other end. These are the FALLBACKS -- they apply when a
        # `curfew:` block says `enabled: true` without saying when,
        # which is how a rig ends up sleeping on a schedule nobody
        # typed. Worth them being the right hours.
        self.sleep_hm = _parse_hhmm(cfg.get("sleep", "18:30"))
        self.wake_hm = _parse_hhmm(cfg.get("wake", "07:30"))
        self.boot_grace = int(cfg.get("boot_grace_seconds", 600))
        self.dry_run = bool(cfg.get("dry_run", False))
        # DO NOT POWER OFF OWING SOMEBODY A CLIP. Called with no
        # arguments; returns how many clips are still to reach the
        # backend. None means nobody asked us to care, and the curfew
        # behaves as it always did.
        self.work_pending = work_pending
        # ...BUT NOT FOREVER. This rig runs on a battery, and the curfew
        # is what makes it last a fortnight instead of five days. An
        # upload that cannot complete — the tee's link has gone whole
        # afternoons without finishing one — would otherwise hold the Pi
        # awake all night and flatten it, and a dead camera tomorrow is
        # worse than a clip that arrives at dawn. The spool is on the SD
        # card, so a deferred clip is LATE, never lost.
        self.max_defer = float(cfg.get("upload_defer_minutes", 45)) * 60.0
        self._defer_since = None
        # The shortest sleep worth taking. See the halt path: under
        # this, a halt costs more than it saves and risks the morning.
        self.min_sleep = float(cfg.get("min_sleep_minutes", 60)) * 60.0
        self._held_since = None
        self._early_warned = False
        # WHEN THE BOOT GRACE STARTS COUNTING, and it was never set.
        #
        # `run()` reads this on the first tick that lands inside the
        # sleep window: `self.boot_grace - (monotonic() - _started_at)`.
        # Nothing assigned it, so that line raised AttributeError, the
        # thread died, and the curfew halted nothing -- on a daemon
        # thread, where the traceback goes to the journal and the agent
        # carries on as if nothing happened.
        #
        # THE EXACT WINDOW, because "it has always been broken" was
        # wrong and cost a day of looking in the wrong place. The
        # assignment was there from 07d9cd8 (25 Jul) and was dropped by
        # c7295b4 (3 Oct), which added the upload deferral and took
        # this line with it. So the rigs slept nightly for ten weeks,
        # stopped on 3 Oct, and nobody noticed until a 100Ah pack
        # started lasting five days instead of twelve -- the curfew's
        # own power math, read backwards.
        #
        # FROM BOOT, NOT FROM THE AGENT'S START. I set this to the
        # agent's start first, reasoning that an agent which restarts
        # mid-window was restarted by an operator who should get the
        # full window. That is exactly backwards, and it powered a rig
        # off under its owner: `update.sh` stops and starts the
        # service, so running an update at 07:00 -- inside a window
        # that ends at 08:00 -- reset this clock, and ten minutes
        # later the curfew halted a Pi somebody was in the middle of
        # working on. From boot, an agent restart changes nothing: a
        # rig that has been up all day is past its grace either way,
        # and the "is somebody working on this" question is answered
        # properly below by asking whether anybody is logged in.
        self._started_at = time.monotonic() - _uptime_seconds()
        self.stopping = threading.Event()

    # ── window math ──────────────────────────────────────────────────
    def _in_sleep_window(self, now: dt.datetime) -> bool:
        sh, sm = self.sleep_hm
        wh, wm = self.wake_hm
        sleep_t = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
        wake_t = now.replace(hour=wh, minute=wm, second=0, microsecond=0)
        if sleep_t <= wake_t:
            # window inside one day (e.g. sleep 01:00 wake 06:00)
            return sleep_t <= now < wake_t
        # window spans midnight (the normal case: sleep 18:30 wake 07:30)
        return now >= sleep_t or now < wake_t

    def _next_wake(self, now: dt.datetime) -> dt.datetime:
        wh, wm = self.wake_hm
        wake_t = now.replace(hour=wh, minute=wm, second=0, microsecond=0)
        if wake_t <= now:
            wake_t += dt.timedelta(days=1)
        return wake_t

    # ── halt machinery ───────────────────────────────────────────────
    def _sleep_until(self, wake_at: dt.datetime) -> bool:
        """Arm the RTC and power off. Primary path is `rtcwake -m off`
        (one atomic arm+halt; the agent user gets a NOPASSWD sudoers
        entry for it from install.sh). Fallback: direct sysfs arm +
        shutdown for root-run agents. Returns False only when NOTHING
        could both guarantee a wake-up and halt."""
        epoch = int(wake_at.timestamp())
        # SAY IT IN BOTH CLOCKS, because this is where they can part
        # company. The epoch is unambiguous; what `rtcwake` DOES with
        # it is not. With no -u or -l it decides from /etc/adjtime
        # whether the hardware clock runs UTC or local time, while the
        # sysfs fallback below hands the kernel an epoch, and the
        # kernel always reads the RTC as UTC. On a Pi whose
        # /etc/adjtime says LOCAL the two paths therefore arm
        # DIFFERENT times, and the gap is exactly this machine's UTC
        # offset -- five hours here, which turns an 08:00 wake into an
        # 03:00 one. One line in the journal now says which was meant,
        # instead of it having to be inferred from a boot time the
        # next morning.
        log.info(
            "curfew: arming the RTC for %s local / %s UTC (epoch %d) — "
            "if the rig wakes at a different time than that local one, "
            "the hardware clock's timezone is the thing to check "
            "(timedatectl, /etc/adjtime)",
            wake_at.strftime("%Y-%m-%d %H:%M:%S"),
            dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S"),
            epoch)
        for cmd in (
            ["sudo", "-n", "rtcwake", "-m", "off", "-t", str(epoch)],
            ["rtcwake", "-m", "off", "-t", str(epoch)],
        ):
            try:
                subprocess.run(
                    cmd, timeout=30, check=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                )
                return True  # (normally unreached — the Pi is off)
            except Exception:  # noqa: BLE001
                continue
        if self._arm_rtc(wake_at):
            self._halt()
            return True
        return False

    def _arm_rtc(self, wake_at: dt.datetime) -> bool:
        epoch = int(wake_at.timestamp())
        try:
            _WAKEALARM.write_text("0\n")  # clear any stale alarm
        except OSError:
            pass  # empty/unset alarm errors on some kernels — fine
        try:
            _WAKEALARM.write_text(f"{epoch}\n")
        except OSError as exc:
            log.error("curfew: could not arm RTC wakealarm: %s", exc)
            return False
        # Read back — the kernel silently drops past-times.
        try:
            armed = _WAKEALARM.read_text().strip()
            if not armed or int(armed) != epoch:
                log.error(
                    "curfew: wakealarm readback mismatch (%r != %d)",
                    armed, epoch,
                )
                return False
        except (OSError, ValueError) as exc:
            log.error("curfew: wakealarm readback failed: %s", exc)
            return False
        return True

    def _halt(self) -> None:
        for cmd in (
            ["sudo", "-n", "shutdown", "-h", "now"],
            ["shutdown", "-h", "now"],
            ["systemctl", "poweroff"],
        ):
            try:
                subprocess.run(cmd, timeout=15, check=True)
                return
            except Exception:  # noqa: BLE001
                continue
        log.error(
            "curfew: every shutdown command failed — staying awake "
            "(agent user may need a sudoers entry for shutdown)",
        )

    # ── do we still owe anybody a clip? ──────────────────────────────
    def _uploads_settled(self) -> bool:
        """True when it is fair to power off. Logs why when it is not."""
        if self.work_pending is None:
            return True
        try:
            n = int(self.work_pending() or 0)
        except Exception as exc:  # noqa: BLE001 - never block a halt on a bug
            log.warning("curfew: could not read the upload queue (%s) — "
                        "treating it as empty", exc)
            return True
        if n <= 0:
            if self._defer_since is not None:
                log.info("curfew: uploads finished after %.0f min — halting",
                         (time.monotonic() - self._defer_since) / 60.0)
                self._defer_since = None
            return True
        if self._defer_since is None:
            self._defer_since = time.monotonic()
            log.info(
                "curfew: sleep window reached but %d clip(s) are still to "
                "upload — staying awake for up to %.0f min so today's "
                "shots go out today", n, self.max_defer / 60.0,
            )
            return False
        waited = time.monotonic() - self._defer_since
        if waited < self.max_defer:
            return False
        log.warning(
            "curfew: %d clip(s) STILL pending after %.0f min — halting "
            "anyway to protect the battery. They are spooled on the SD "
            "card and go out at wake-up.", n, waited / 60.0,
        )
        self._defer_since = None
        return True

    # ── main loop ────────────────────────────────────────────────────
    def run(self) -> None:
        """Never dies quietly.

        A daemon thread that raises is gone, and the only trace is a
        traceback on stderr that reads like noise next to a running
        agent. That is how a curfew that could not halt a Pi went
        unnoticed: everything else about the rig kept working. An
        unexpected failure now says so in the agent's own log, every
        time it happens, and the loop carries on -- a curfew that
        cannot work out whether to sleep must leave the rig AWAKE,
        which is the safe direction and is what it will do by looping.
        """
        try:
            self._run()
        except Exception:  # noqa: BLE001
            log.exception("curfew: the sleep/wake thread stopped — this "
                          "rig will NOT power itself off tonight")

    def _run(self) -> None:
        if not self.enabled:
            log.info("curfew: disabled — this rig stays awake "
                     "(set curfew.enabled: true to sleep it)")
            return
        log.info(
            "curfew: enabled — sleep %02d:%02d wake %02d:%02d%s",
            *self.sleep_hm, *self.wake_hm,
            " (DRY RUN)" if self.dry_run else "",
        )
        while not self.stopping.is_set():
            now = dt.datetime.now()
            if now.year < _MIN_SANE_YEAR:
                # Clock not yet synced (no NTP over LTE yet) — a bogus
                # clock must never decide to power off the rig.
                self.stopping.wait(30)
                continue
            if self._in_sleep_window(now):
                # AWAKE INSIDE ITS OWN SLEEP WINDOW IS NEWS.
                #
                # Either somebody powered the rig up on purpose, or the
                # RTC alarm fired at the wrong time, and the second one
                # is otherwise invisible: the rig comes back, behaves
                # normally, and burns a night of battery that nobody is
                # awake to notice. Said once per boot, before the
                # grace check rather than inside it -- a rig that has
                # been up for hours never reaches that branch, and it
                # is the one whose early wake most needs saying.
                if not self._early_warned:
                    self._early_warned = True
                    _gap = (self._next_wake(now) - now).total_seconds()
                    log.warning(
                        "curfew: awake at %s, which is INSIDE this rig's "
                        "own sleep window and %.1fh before its %02d:%02d "
                        "wake time. If nobody powered it up, the RTC alarm "
                        "fired early — a gap of close to a whole number of "
                        "hours means the hardware clock's timezone "
                        "(timedatectl, /etc/adjtime) rather than anything "
                        "about the curfew.",
                        now.strftime("%H:%M"), _gap / 3600.0, *self.wake_hm)
                grace_left = self.boot_grace - (
                    time.monotonic() - self._started_at
                )
                if grace_left > 0:
                    log.info(
                        "curfew: in sleep window but %.0fs of boot grace "
                        "remain (operator window) — halting after",
                        grace_left,
                    )
                    self.stopping.wait(min(grace_left + 1, 60))
                    continue
                if not self._uploads_settled():
                    self.stopping.wait(30)
                    continue
                wake_at = self._next_wake(now)
                # ── TOO LATE TO BE WORTH IT ──────────────────────────
                #
                # A halt is not free: it costs a boot cycle at full
                # power, a reconnect, and the risk that the rig does
                # not come back at all. The curfew earns its keep over
                # ten dark hours; over the last forty minutes of a
                # window it saves a few watt-minutes and stakes the
                # morning on an RTC alarm firing correctly.
                #
                # This is also the second half of what powered a rig
                # off under its owner: an agent restarted at 07:00,
                # fifty minutes before an 08:00 wake, where the right
                # answer is obviously "stay up, you are nearly there".
                _to_wake = (wake_at - now).total_seconds()
                if _to_wake < self.min_sleep:
                    log.info(
                        "curfew: only %.0f min until the %02d:%02d wake — "
                        "not worth a halt and a boot, staying up",
                        _to_wake / 60.0, *self.wake_hm)
                    self.stopping.wait(min(_to_wake + 1, 300))
                    continue
                # ── SOMEBODY IS WORKING ON THIS RIG ──────────────────
                #
                # The boot grace was meant to be this check and could
                # not be: it is a timer, so it expires while an
                # operator is still typing, and it cannot tell a
                # maintenance visit from an ordinary boot. Asking who
                # is logged in answers the actual question, and the
                # answer is good for as long as they stay.
                #
                # Bounded by the same deferral the uploads get, so a
                # forgotten SSH session cannot hold the rig awake all
                # night and flatten the battery.
                _who = _who_is_logged_in()
                if _who:
                    if self._held_since is None:
                        self._held_since = time.monotonic()
                        log.info(
                            "curfew: bedtime, but %s is logged in — "
                            "staying up for up to %.0f min so nobody is "
                            "cut off mid-session",
                            ", ".join(_who), self.max_defer / 60.0)
                    if (time.monotonic() - self._held_since) < self.max_defer:
                        self.stopping.wait(30)
                        continue
                    log.warning(
                        "curfew: %s still logged in after %.0f min — "
                        "halting anyway to protect the battery",
                        ", ".join(_who), self.max_defer / 60.0)
                self._held_since = None
                log.info(
                    "curfew: sleep window reached — arming RTC for %s "
                    "and powering off", wake_at.isoformat(),
                )
                if self.dry_run:
                    log.info(
                        "curfew: DRY RUN — NOT powering off. Would have "
                        "halted now and woken at %s. Remove dry_run (or "
                        "set it false) to arm it for real.",
                        wake_at.isoformat())
                    self.stopping.wait(300)
                    continue
                if not self._sleep_until(wake_at):
                    log.error(
                        "curfew: could not guarantee an RTC wake-up — "
                        "staying awake (will retry)",
                    )
                # Either the halt failed or (dry paths) returned —
                # don't spin-loop the shutdown attempt.
                self.stopping.wait(300)
                continue
            self.stopping.wait(30)

    def stop(self) -> None:
        self.stopping.set()
