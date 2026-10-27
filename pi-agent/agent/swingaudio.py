"""Find club-on-ball impacts in a stream of PCM, as it arrives.

A PORT OF THE SERVER'S detect_swings_from_audio, restructured to be
fed a second at a time instead of handed a finished file. The numbers
are deliberately the same ones, because they were tuned against real
clips and re-tuning them here would throw that away:

    high-pass        1500 Hz
    envelope         10 ms moving average of |x|
    threshold        median x min_peak_ratio
    attack           <= 30 ms, measured from max(0.1*peak, 2*median)
    separation       6 s

WHAT THE TUNING IS WORTH, measured on upload 1258 (back tees, two real
swings) and 1259:

    swing          x19 - x30     <- after the high-pass
    talking        x5 - x7
    HF chirps      x14 - x21     <- rejected on SHAPE, not level

So ratio 8 separates swings from talking on that course with about a
1.3x margin, which is thin, and the attack filter does NOT help with
voices -- after the high-pass speech is all consonants and attacks in
7-14 ms, same as a strike. It earns its place against rumble and wind,
which stay sustained in any band.

TWO THINGS THIS CANNOT DO, written down so they are not rediscovered:

  * A STRIKE AT FRONT-TEE RANGE IS INVISIBLE HERE. Air absorbs high
    frequencies fastest, so a far strike arrives as a dull thump with
    ~1% of its energy above 1.5 kHz -- the high-pass removes exactly
    what survived the trip. Measured on event-1257: x13.4 full-band
    and x1.02 after the filter.
  * IT CANNOT TELL WHOSE SWING IT IS. Nothing in the sound says which
    tee it came from. That is what the caller's zone check is for.

THE BASELINE IS ROLLING, which is the one real difference from the
server. A finished clip has a median over the whole of itself; a ring
has to decide now, so the median is taken over the last
`baseline_sec`. Too short and a loud passage raises the bar until a
swing inside it cannot clear it; too long and the detector is slow to
notice the course going quiet. 30 s covers a group playing through.
"""

from __future__ import annotations

import logging
import math
from collections import deque
from typing import Optional

log = logging.getLogger("golfreelz_agent.swingaudio")

DEFAULT_HIGHPASS_HZ = 1500.0
DEFAULT_MIN_RATIO = 8.0
DEFAULT_MAX_ATTACK_SEC = 0.030
DEFAULT_MIN_SEPARATION_SEC = 6.0
DEFAULT_BASELINE_SEC = 30.0
ENVELOPE_MS = 10.0
# The envelope is kept at this rate, not at the sample rate: 1 ms is
# far finer than the 30 ms the attack filter asks about, and it makes
# the median and the peak scan cheap enough to run on every segment.
ENVELOPE_HZ = 1000
# Same floor as the server's: at 10% of the peak a quiet strike's
# attack reference lands AT the noise floor, the walk-back finds no
# crossing, and the peak is scored "sustained" for being quiet rather
# than slow. See ai_tracer.detect_swings_from_audio.
ATTACK_NOISE_MULT = 2.0
# A peak this close to the floor has no rise left to time.
ATTACK_MIN_HEADROOM = 4.0


class _HighPass:
    """One RBJ biquad, matching ffmpeg's `highpass=f=N` default (Q=0.707)."""

    def __init__(self, fs: float, f0: float, q: float = 0.7071):
        w0 = 2.0 * math.pi * f0 / fs
        cw, sw = math.cos(w0), math.sin(w0)
        alpha = sw / (2.0 * q)
        a0 = 1.0 + alpha
        self.b0 = ((1.0 + cw) / 2.0) / a0
        self.b1 = (-(1.0 + cw)) / a0
        self.b2 = self.b0
        self.a1 = (-2.0 * cw) / a0
        self.a2 = (1.0 - alpha) / a0
        self.x1 = self.x2 = self.y1 = self.y2 = 0.0

    def run(self, xs):
        b0, b1, b2, a1, a2 = self.b0, self.b1, self.b2, self.a1, self.a2
        x1, x2, y1, y2 = self.x1, self.x2, self.y1, self.y2
        out = []
        ap = out.append
        for x in xs:
            y = b0 * x + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            x2, x1 = x1, x
            y2, y1 = y1, y
            ap(y)
        self.x1, self.x2, self.y1, self.y2 = x1, x2, y1, y2
        return out


class SwingListener:
    """Feed it PCM; it tells you when a club met a ball."""

    def __init__(
        self,
        sample_rate: int = 16000,
        *,
        highpass_hz: float = DEFAULT_HIGHPASS_HZ,
        min_peak_ratio: float = DEFAULT_MIN_RATIO,
        max_attack_sec: float = DEFAULT_MAX_ATTACK_SEC,
        min_separation_sec: float = DEFAULT_MIN_SEPARATION_SEC,
        baseline_sec: float = DEFAULT_BASELINE_SEC,
    ) -> None:
        self.sample_rate = int(sample_rate)
        self.min_peak_ratio = float(min_peak_ratio)
        self.max_attack_sec = float(max_attack_sec)
        self.min_separation_sec = float(min_separation_sec)
        self._hp = _HighPass(self.sample_rate, float(highpass_hz)) \
            if highpass_hz and highpass_hz > 0 else None
        self._win = max(1, int(self.sample_rate * ENVELOPE_MS / 1000.0))
        self._decim = max(1, int(round(self.sample_rate / ENVELOPE_HZ)))
        self._sum = 0.0
        self._recent: deque = deque(maxlen=self._win)
        self._since = 0
        # (time, envelope) at ENVELOPE_HZ, over the baseline window.
        self._env: deque = deque(maxlen=int(baseline_sec * ENVELOPE_HZ))
        self._t_next: Optional[float] = None
        self._scanned_to = 0
        self._last_hit: Optional[float] = None
        self.median = 0.0

    # ---- feeding ------------------------------------------------------

    def feed(self, pcm: bytes, start_ts: float) -> list:
        """Add `pcm` (s16le mono) that began at `start_ts`. Returns hits."""
        import struct
        n = len(pcm) // 2
        if n <= 0:
            return []
        xs = struct.unpack("<%dh" % n, pcm[:n * 2])
        # A GAP RESETS THE CLOCK, never the samples. If the caller skips
        # ahead -- a hole in the ring, a restart -- the envelope must
        # not pretend the new audio butts onto the old, or every time
        # it reports afterwards is wrong by the length of the hole.
        if self._t_next is None or abs(start_ts - self._t_next) > 0.05:
            self._t_next = start_ts
        ys = self._hp.run(xs) if self._hp is not None else xs
        step = 1.0 / self.sample_rate
        t = self._t_next
        for i, y in enumerate(ys):
            a = abs(y) / 32768.0
            self._sum += a
            if len(self._recent) == self._recent.maxlen:
                self._sum -= self._recent[0]
            self._recent.append(a)
            self._since += 1
            if self._since >= self._decim:
                self._since = 0
                self._env.append((t + i * step,
                                  self._sum / len(self._recent)))
        self._t_next = t + n * step
        return self._scan()

    def envelope_max(self, t0: float, t1: float) -> float:
        """Loudest post-high-pass envelope value in [t0, t1).

        The envelope -- not the raw sample -- is what the threshold is
        measured against, so a caller asking "how close did that noise
        come?" has to ask in these units. A raw peak says nothing: a
        truck can clip the input and leave the envelope flat, because
        the high-pass took its energy away.

        Only the baseline window is kept, so a t0 older than that
        returns what survives of it.
        """
        best = 0.0
        for t, v in reversed(self._env):
            if t < t0:
                break
            if t < t1 and v > best:
                best = v
        return best

    # ---- detection ----------------------------------------------------

    def _scan(self) -> list:
        env = self._env
        if len(env) < int(0.5 * ENVELOPE_HZ):
            return []
        vals = sorted(v for _, v in env)
        self.median = vals[len(vals) // 2]
        if self.median <= 0:
            return []
        thresh = self.median * self.min_peak_ratio
        # Leave a tail unscanned: a local maximum needs neighbours on
        # both sides, and the attack walk-back needs history. Anything
        # inside the tail is scanned on the next feed.
        tail = int(0.12 * ENVELOPE_HZ)
        lo = max(1, self._scanned_to)
        hi = len(env) - tail
        hits = []
        if hi <= lo:
            self._scanned_to = max(0, len(env) - tail)
            return hits
        for i in range(lo, hi):
            t, v = env[i]
            if v < thresh:
                continue
            if not (v >= env[i - 1][1] and v >= env[i + 1][1]):
                continue
            if self._last_hit is not None and \
                    t - self._last_hit < self.min_separation_sec:
                continue
            atk = self._attack(i, v)
            if atk is None or atk > self.max_attack_sec:
                continue
            self._last_hit = t
            hits.append({"at": t, "ratio": v / self.median,
                         "attack_sec": atk})
            log.info("audio: impact at %.3f — x%.1f, attack %.0f ms",
                     t, v / self.median, atk * 1000)
        # The deque has been trimmed from the left by whatever it
        # dropped, so the cursor has to move with it rather than with
        # the raw index, or a trimmed window rescans old audio.
        self._scanned_to = max(0, len(env) - tail)
        return hits

    def _attack(self, i: int, v: float) -> Optional[float]:
        """Seconds from the ambience to the peak, or None if unmeasurable."""
        ref = max(0.10 * v, ATTACK_NOISE_MULT * self.median)
        if ref >= 0.5 * v or v < ATTACK_MIN_HEADROOM * self.median:
            return None
        env = self._env
        back = int(3 * self.max_attack_sec * ENVELOPE_HZ)
        t_pk = env[i][0]
        for k in range(i - 1, max(0, i - back) - 1, -1):
            if env[k][1] < ref:
                return t_pk - env[k][0]
        return None
