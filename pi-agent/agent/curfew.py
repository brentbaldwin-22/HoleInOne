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
    (unsynced boot clock could put "21:00" anywhere in history).
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
import math
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger("golfreelz_agent.curfew")

_WAKEALARM = Path("/sys/class/rtc/rtc0/wakealarm")
_MIN_SANE_YEAR = 2025

# THE SUN IS DOWN WHEN ITS CENTRE IS 0.833 DEGREES BELOW THE HORIZON:
# half a degree of solar disc plus about a third of a degree of
# atmospheric refraction, which lifts the image of a sun that has
# geometrically already set. This is the standard definition every
# almanac's "sunset" uses, and using plain 0 would put bedtime three
# or four minutes early.
_SUNSET_ALTITUDE_DEG = -0.833
# The Earth's axial tilt, which is what makes any of this vary.
_OBLIQUITY_DEG = 23.4397


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


def sunset_local(day: dt.date, lat: float, lon: float) -> dt.datetime | None:
    """Local wall-clock sunset for a date and place. None above the
    Arctic circle in summer, and anywhere else the sun does not set.

    NO NETWORK AND NO LIBRARY. This rig is on an LTE link that is
    sometimes down for an afternoon and it decides when to power
    itself off; a bedtime that depends on an API is a bedtime that
    does not happen. The NOAA sunrise equation is forty lines of
    arithmetic and good to about a minute, which is far finer than the
    hour of margin the curfew leaves anyway.

    `lon` is degrees EAST (so the Americas are negative), matching
    every map and GPS you will copy it from. The result is naive local
    time, in whatever zone the Pi is set to -- the same clock
    `datetime.now()` reads, which is what the window check compares
    against.
    """
    _lat_r = math.radians(float(lat))
    # The equation is written in degrees WEST.
    _lw = -float(lon)
    # Days since 2000-01-01 12:00 TT. `toordinal` counts from year 1,
    # and 730120 is 2000-01-01 on that scale.
    _n = (day.toordinal() - 730120) + 0.0008 + _lw / 360.0
    # Mean solar anomaly, the Earth's position in its own orbit.
    _M = (357.5291 + 0.98560028 * _n) % 360.0
    _Mr = math.radians(_M)
    # The orbit is an ellipse, so the sun runs fast in January and
    # slow in July. This is that correction.
    _C = (1.9148 * math.sin(_Mr) + 0.0200 * math.sin(2 * _Mr)
          + 0.0003 * math.sin(3 * _Mr))
    _lam = math.radians((_M + _C + 180.0 + 102.9372) % 360.0)
    # Solar noon, as a count of days from the same epoch.
    _transit = (2451545.0 + _n + 0.0053 * math.sin(_Mr)
                - 0.0069 * math.sin(2 * _lam))
    _dec = math.asin(math.sin(_lam) * math.sin(math.radians(_OBLIQUITY_DEG)))
    _cos_w = (
        (math.sin(math.radians(_SUNSET_ALTITUDE_DEG))
         - math.sin(_lat_r) * math.sin(_dec))
        / (math.cos(_lat_r) * math.cos(_dec))
    )
    # OUTSIDE [-1, 1] THE SUN NEVER CROSSES THE HORIZON THAT DAY --
    # midnight sun above, polar night below. Not a case any golf course
    # this runs on will meet, and not a reason to crash or to invent a
    # bedtime either.
    if not -1.0 <= _cos_w <= 1.0:
        return None
    _jset = _transit + math.degrees(math.acos(_cos_w)) / 360.0
    # Julian date -> UTC -> whatever clock this Pi is set to. The
    # conversion is where the timezone comes from, and it is the Pi's
    # own: a rig left on UTC gets a UTC answer, which is the same
    # mistake a fixed bedtime makes and is visible in the same log
    # line.
    _utc = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(
        days=_jset - 2440587.5)
    return _utc.astimezone().replace(tzinfo=None)


class CurfewThread(threading.Thread):
    """Checks the local clock every 30s; inside the sleep window it
    arms the RTC for the wake time and powers the Pi off."""

    def __init__(self, cfg: dict | None, work_pending=None):
        super().__init__(daemon=True, name="curfew")
        cfg = cfg or {}
        self.enabled = bool(cfg.get("enabled", False))
        self.sleep_hm = _parse_hhmm(cfg.get("sleep", "21:00"))
        self.wake_hm = _parse_hhmm(cfg.get("wake", "06:00"))
        # ── BEDTIME THAT FOLLOWS THE SUN ─────────────────────────────
        #
        # A fixed bedtime is wrong for most of the year. On this course
        # the sun sets at 17:29 in early November and at 20:31 in late
        # June: one clock time cannot serve both, so it either burns an
        # hour of battery filming the dark in December or stops
        # recording while people are still playing in May. Set
        # `sleep_after_sunset_minutes` and bedtime is worked out from
        # the sun each day instead.
        #
        # `sleep` stays, and is what runs when this is not configured
        # or cannot be computed -- an upgrade changes nothing for a rig
        # that does not opt in.
        _after = cfg.get("sleep_after_sunset_minutes")
        self.after_sunset_min = (None if _after is None
                                 else float(_after))
        self.lat = cfg.get("latitude")
        self.lon = cfg.get("longitude")
        self.lat = None if self.lat is None else float(self.lat)
        self.lon = None if self.lon is None else float(self.lon)
        # RAILS, BECAUSE A MISTYPED COORDINATE IS A SILENT ONE. A
        # longitude with the sign flipped moves sunset by ten hours and
        # the rig would halt at breakfast; nothing downstream would
        # question it. These bound the computed answer -- never the
        # fixed one, which is whatever the operator typed on purpose.
        self.sleep_not_before = _parse_hhmm(
            cfg.get("sleep_not_before", "16:00"))
        self.sleep_not_after = _parse_hhmm(
            cfg.get("sleep_not_after", "22:30"))
        self._sun_cache: tuple | None = None
        self._sun_warned = False
        self._early_warned = False
        # The shortest sleep worth taking. See the halt path: under
        # this, a halt costs more than it saves and risks the morning.
        self.min_sleep = float(cfg.get("min_sleep_minutes", 60)) * 60.0
        self._held_since = None
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
        # WHEN THE BOOT GRACE STARTS COUNTING, and it was never set.
        #
        # `run()` reads this on the first tick that lands inside the
        # sleep window: `self.boot_grace - (monotonic() - _started_at)`.
        # Nothing assigned it, so that line raised AttributeError, the
        # thread died, and the curfew never halted anything -- on a
        # daemon thread, where the traceback goes to the journal and
        # the agent carries on as if nothing happened.
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
    def sleep_hm_for(self, day: dt.date) -> tuple[int, int]:
        """Tonight's bedtime, as (hour, minute).

        The fixed `sleep` unless this rig was given a sunset offset and
        a position, in which case it is sunset plus that offset, worked
        out here with no network and cached for the day. Falls back to
        the fixed time, loudly and once, if the sun cannot be placed.
        """
        if self.after_sunset_min is None:
            return self.sleep_hm
        if self.lat is None or self.lon is None:
            if not self._sun_warned:
                self._sun_warned = True
                log.warning(
                    "curfew: sleep_after_sunset_minutes is set but this "
                    "rig has no latitude/longitude — falling back to the "
                    "fixed bedtime %02d:%02d", *self.sleep_hm)
            return self.sleep_hm
        if self._sun_cache and self._sun_cache[0] == day:
            return self._sun_cache[1]
        try:
            _set = sunset_local(day, self.lat, self.lon)
        except Exception as exc:  # noqa: BLE001
            log.warning("curfew: could not work out sunset (%s) — using "
                        "the fixed bedtime %02d:%02d", exc, *self.sleep_hm)
            return self.sleep_hm
        if _set is None:
            log.warning("curfew: the sun does not set here today — using "
                        "the fixed bedtime %02d:%02d", *self.sleep_hm)
            return self.sleep_hm
        _bed = _set + dt.timedelta(minutes=self.after_sunset_min)
        _hm = (_bed.hour, _bed.minute)
        # THE TWO RAILS FAIL IN OPPOSITE DIRECTIONS, so they are not
        # the same rule.
        #
        # TOO LATE is a battery question and a real policy: "whatever
        # the sun does, be asleep by half ten". Clamping is exactly
        # what is wanted, and the clamped time is still a safe one --
        # it is dark by then wherever this runs.
        #
        # TOO EARLY is almost always a WRONG COORDINATE -- a longitude
        # with its sign the wrong way round moves sunset by ten hours
        # -- and clamping it would still halt the rig in the middle of
        # the afternoon, which costs the footage this whole system
        # exists to capture. So the computation is thrown away and the
        # operator's own fixed bedtime is used: a number a person
        # typed on purpose beats a number derived from one they
        # probably mistyped.
        _mins = _hm[0] * 60 + _hm[1]
        _lo = self.sleep_not_before[0] * 60 + self.sleep_not_before[1]
        _hi = self.sleep_not_after[0] * 60 + self.sleep_not_after[1]
        if _mins < _lo:
            log.warning(
                "curfew: sunset %02d:%02d + %.0f min is %02d:%02d, EARLIER "
                "than the %02d:%02d floor — ignoring it and using the "
                "fixed bedtime %02d:%02d. CHECK THE LATITUDE AND "
                "LONGITUDE (%.4f, %.4f): a sign the wrong way round moves "
                "sunset by hours, and halting in the afternoon costs the "
                "shots.",
                _set.hour, _set.minute, self.after_sunset_min, *_hm,
                *self.sleep_not_before, *self.sleep_hm, self.lat, self.lon)
            self._sun_cache = (day, self.sleep_hm)
            return self.sleep_hm
        _clamped = (_hi // 60, _hi % 60) if _mins > _hi else _hm
        if _clamped != _hm:
            log.info(
                "curfew: sunset %02d:%02d + %.0f min is %02d:%02d, past the "
                "%02d:%02d ceiling — sleeping at %02d:%02d to protect the "
                "battery. It is dark by then anyway.",
                _set.hour, _set.minute, self.after_sunset_min, *_hm,
                *self.sleep_not_after, *_clamped)
        if not self._sun_cache or self._sun_cache[0] != day:
            log.info(
                "curfew: tonight the sun sets at %02d:%02d, so bedtime is "
                "%02d:%02d (sunset + %.0f min); wake %02d:%02d",
                _set.hour, _set.minute, *_clamped, self.after_sunset_min,
                *self.wake_hm)
        self._sun_cache = (day, _clamped)
        return _clamped

    def _in_sleep_window(self, now: dt.datetime) -> bool:
        sh, sm = self.sleep_hm_for(now.date())
        wh, wm = self.wake_hm
        sleep_t = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
        wake_t = now.replace(hour=wh, minute=wm, second=0, microsecond=0)
        if sleep_t <= wake_t:
            # window inside one day (e.g. sleep 01:00 wake 06:00)
            return sleep_t <= now < wake_t
        # window spans midnight (the normal case: sleep 21:00 wake 06:00)
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
            "curfew: enabled — sleep %s wake %02d:%02d%s",
            ("%.0f min after sunset (%.4f, %.4f), not before %02d:%02d "
             "and not after %02d:%02d"
             % (self.after_sunset_min, self.lat or 0.0, self.lon or 0.0,
                *self.sleep_not_before, *self.sleep_not_after))
            if self.after_sunset_min is not None
            else ("%02d:%02d" % self.sleep_hm),
            *self.wake_hm,
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
