"""audio1 — measure every audio peak, decide nothing.

WHAT THIS IS FOR. The existing detector asks one question of a peak —
is it loud enough, fast enough, far enough from the last one — and
that question cannot separate a club-on-ball strike from a plosive in
somebody's sentence. The comment in `detect_swings_from_audio` says so
outright, measured on upload 1258: two real swings attacked in 5-9 ms
and two people talking attacked in 7-14 ms. Only amplitude separated
them, and amplitude is a function of how far away the tee is.

So this module measures a peak from every angle anybody has suggested
might help, writes all of it down, and REJECTS NOTHING. The confidence
score it computes is diagnostic: it is not consulted by the cutter, it
cannot keep a clip or drop one, and it must not start doing so until
there are enough labelled examples to say whether it is any good.

THREE THINGS IT IS CAREFUL ABOUT, because getting them wrong would
produce numbers that look like measurements and are not:

  THE SAMPLE RATE IS A FACT ABOUT THE CAMERA, not a constant. The
  existing detector decodes at a fixed 22050 Hz, so everything above
  11 kHz is gone before anything looks at it. A band is only reported
  when the recording can carry it; above Nyquist it is `None`, which
  is a different statement from zero energy.

  THE HIGH-PASS IS NOT APPLIED HERE. `detect_swings_from_audio` runs
  ffmpeg's `highpass=f=1500` BEFORE it builds its envelope, which is
  right for finding transients and ruinous for asking how much energy
  sits below 300 Hz: the answer would always be "almost none",
  measured off a filter rather than off a golf course.

  A NUMBER THAT COULD NOT BE MEASURED IS None, NEVER ZERO. A decay
  that never completes inside the window, an attack whose reference
  sits in the noise, a band above Nyquist: each returns None and says
  why. Zero reads as a finding.
"""
from __future__ import annotations

import json
import logging
import math
import shutil
import subprocess
from dataclasses import dataclass, field, asdict
from pathlib import Path

log = logging.getLogger("golfreelz.audio1")

try:
    import numpy as np
    HAS_NP = True
except Exception:  # noqa: BLE001
    np = None  # type: ignore
    HAS_NP = False


# ── CONFIGURATION ───────────────────────────────────────────────────
#
# EVERYTHING TUNABLE IN ONE PLACE, because every number below is a
# guess until the labelled set says otherwise. The request was
# explicit about it: "Make all weights, thresholds, frequency bands
# and analysis windows configurable."

@dataclass
class Band:
    name: str
    lo_hz: float
    hi_hz: float
    why: str


@dataclass
class Audio1Config:
    # ── FREQUENCY BANDS ─────────────────────────────────────────────
    bands: list = field(default_factory=lambda: [
        Band("low", 0.0, 300.0,
             "wind, rumble and microphone disturbance"),
        Band("mid", 300.0, 1500.0,
             "voices, environmental noise, dull impacts"),
        Band("impact", 1500.0, 3000.0,
             "impact and club acoustics"),
        Band("sharp", 3000.0, 8000.0,
             "the sharp edge of a strike"),
    ])
    # The two "percentage above" cut points the request names.
    pct_above_hz: list = field(default_factory=lambda: [1500.0, 3000.0])

    # ── ANALYSIS WINDOWS, in milliseconds ───────────────────────────
    #
    # SHORTER THAN THE DETECTOR'S. `detect_swings_from_audio` smooths
    # over 10 ms, which is the right scale for finding a peak and the
    # wrong one for timing a 5 ms rise: the smoothing IS most of the
    # answer at that length. 2 ms is still well above a sample at any
    # rate a camera records.
    envelope_ms: float = 2.0
    # The slice the spectrum, crest factor and centroid are taken over.
    impact_window_ms: float = 50.0
    # How far before the onset the background is measured over...
    background_ms: float = 500.0
    # ...minus this much immediately before it, so the rise of the
    # impact itself is not counted as the background it rose from.
    background_guard_ms: float = 50.0
    # ── ENERGY WINDOWS, CENTRED ON THE PEAK ─────────────────────────
    #
    # Nested, so the ratios between them are concentration: how much
    # of the energy in 100 ms is inside the middle 10 ms. A strike is
    # a spike; a voice, a mat and a gust are not.
    energy_windows_ms: list = field(
        default_factory=lambda: [10.0, 20.0, 50.0, 100.0, 200.0])
    # Numerator over denominator, in milliseconds.
    concentration_ratios: list = field(
        default_factory=lambda: [(10.0, 100.0), (20.0, 200.0),
                                 (50.0, 200.0)])
    # Energy AFTER the peak, in [from, to) milliseconds.
    post_intervals_ms: list = field(
        default_factory=lambda: [(0.0, 20.0), (20.0, 50.0),
                                 (50.0, 100.0), (100.0, 200.0)])
    # Where "high frequency" starts, for the HF copy of all of it.
    hf_cut_hz: float = 1500.0
    # A decay longer than this is reported as suspect rather than as a
    # measurement. See `decay_end_reason`: past about this point the
    # number is almost always about a LATER sound, not this one.
    decay_suspect_ms: float = 400.0
    # HOW LONG AN EXCURSION BACK OVER THE FLOOR HAS TO LAST to count
    # as a sound rather than as jitter.
    #
    # Without this the count is useless: an envelope sitting ON its
    # floor crosses it every few samples, and the first version
    # reported ELEVEN resets for a clean strike with nothing after it
    # — then printed "this number times whatever followed", about
    # nothing. 5 ms is far shorter than any real sound and far longer
    # than the jitter.
    decay_reset_min_ms: float = 5.0
    # WHEN THE FLOOR IS INSIDE THE AMBIENCE, as a multiple of the hold.
    #
    # NOT A COUNT OF CROSSINGS. Counting them called a clean strike
    # noisy: an envelope touches its floor a dozen times on the way
    # down and settles immediately, which is fine. What separates the
    # cases is how much QUIET there is to be had anywhere in the
    # window — measured, on four synthetic clips: 1855 ms, 1738 ms and
    # 686 ms for strikes that settled properly against 31 ms for the
    # quiet strike over a noisy floor that reported 1483 ms. A longest
    # quiet stretch barely longer than the hold means there was never
    # any quiet to find.
    decay_quiet_floor_mult: float = 3.0
    # Secondary peaks are looked for this far after the primary.
    secondary_window_ms: float = 200.0
    # ...and must be at least this fraction of it to count.
    secondary_min_frac: float = 0.25
    # Decay ends when the envelope has been under its threshold for
    # this long without coming back.
    decay_hold_ms: float = 20.0
    decay_floor_frac: float = 0.10
    attack_lo_frac: float = 0.10
    attack_hi_frac: float = 0.90
    # STFT for flux and the spectrogram.
    stft_ms: float = 8.0
    stft_hop_ms: float = 2.0

    # ── CLIPPING ────────────────────────────────────────────────────
    clip_level: float = 0.995
    clip_min_samples: int = 3

    # ── SCORING ─────────────────────────────────────────────────────
    #
    # The weights the request asked to start from. They sum to 100 and
    # nothing here has been validated against a labelled set yet.
    weights: dict = field(default_factory=lambda: {
        "frequency": 30.0,
        "peak_to_background": 20.0,
        "attack": 15.0,
        "decay": 15.0,
        "duration": 10.0,
        "isolation": 10.0,
    })
    # Each component is normalised to 0..1 against these before being
    # weighted. Ranges, not thresholds: a value at or past `good`
    # scores 1, at or before `poor` scores 0, and in between it is
    # linear. Nothing is rejected at either end.
    norm: dict = field(default_factory=lambda: {
        # Fraction of energy above 1500 Hz.
        "hf_fraction_poor": 0.10,
        "hf_fraction_good": 0.55,
        # Peak over background, in dB.
        "snr_db_poor": 6.0,
        "snr_db_good": 26.0,
        # Attack, ms — faster is better, so poor > good.
        "attack_ms_poor": 30.0,
        "attack_ms_good": 4.0,
        # Decay, ms. A strike rings briefly: too short is a click, too
        # long is a sustained sound, so this one is a window.
        "decay_ms_lo": 15.0,
        "decay_ms_best_lo": 35.0,
        "decay_ms_best_hi": 180.0,
        "decay_ms_hi": 450.0,
        # Transient duration, ms, same shape.
        "dur_ms_lo": 20.0,
        "dur_ms_best_lo": 45.0,
        "dur_ms_best_hi": 250.0,
        "dur_ms_hi": 600.0,
        # Isolation: how much quieter the 200 ms after is than the
        # peak, in dB, and how few secondary peaks there are.
        "iso_db_poor": 3.0,
        "iso_db_good": 18.0,
    })
    # Where the bands of the verdict fall. Experimental, and named so.
    confidence_high: float = 75.0
    confidence_medium: float = 45.0

    def to_json(self) -> dict:
        d = asdict(self)
        d["bands"] = [asdict(b) if not isinstance(b, dict) else b
                      for b in self.bands]
        return d


DEFAULT_CONFIG = Audio1Config()


# ── THE RECORDING ITSELF ────────────────────────────────────────────

def probe_audio(path: Path) -> dict:
    """What the camera actually recorded, before anything resamples it.

    THE FIRST QUESTION THE REQUEST ASKS, and the one everything else
    depends on: a band above Nyquist cannot be measured, and reporting
    0 for it would be a lie with a number on it.

    Never raises. Returns {ok, reason, sample_rate, channels, codec,
    duration_sec, nyquist_hz}.
    """
    out = {"ok": False, "reason": None, "sample_rate": None,
           "channels": None, "codec": None, "duration_sec": None,
           "nyquist_hz": None, "bit_depth": None}
    if shutil.which("ffprobe") is None:
        out["reason"] = "ffprobe is not installed"
        return out
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries",
             "stream=sample_rate,channels,codec_name,bits_per_raw_sample,"
             "duration",
             "-of", "json", str(path)],
            capture_output=True, timeout=60, check=False)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        out["reason"] = f"ffprobe failed: {exc}"
        return out
    if proc.returncode != 0:
        out["reason"] = ("ffprobe: "
                         + proc.stderr.decode("utf-8", "ignore")[:160])
        return out
    try:
        streams = (json.loads(proc.stdout or "{}") or {}).get("streams") or []
    except ValueError as exc:
        out["reason"] = f"ffprobe returned nothing usable: {exc}"
        return out
    if not streams:
        out["reason"] = "this file has no audio stream"
        return out
    st = streams[0]
    try:
        out["sample_rate"] = int(st.get("sample_rate") or 0) or None
    except (TypeError, ValueError):
        out["sample_rate"] = None
    out["channels"] = st.get("channels")
    out["codec"] = st.get("codec_name")
    out["bit_depth"] = st.get("bits_per_raw_sample")
    try:
        out["duration_sec"] = round(float(st.get("duration")), 3)
    except (TypeError, ValueError):
        out["duration_sec"] = None
    if not out["sample_rate"]:
        out["reason"] = "the audio stream does not report a sample rate"
        return out
    out["nyquist_hz"] = out["sample_rate"] / 2.0
    out["ok"] = True
    return out


def decode_audio(path: Path, sample_rate: int, timeout: int = 180):
    """Mono float32 at `sample_rate`, UNFILTERED. (samples, reason)."""
    if not HAS_NP:
        return None, "numpy is not installed"
    if shutil.which("ffmpeg") is None:
        return None, "ffmpeg is not installed"
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(path),
             "-vn", "-ac", "1", "-ar", str(int(sample_rate)),
             "-f", "s16le", "-"],
            capture_output=True, timeout=timeout, check=False)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return None, f"ffmpeg failed: {exc}"
    if proc.returncode != 0 or not proc.stdout:
        return None, ("no audio decoded: "
                      + proc.stderr.decode("utf-8", "ignore")[:160])
    buf = np.frombuffer(proc.stdout, dtype=np.int16)
    if buf.size == 0:
        return None, "the decoded audio was empty"
    return buf.astype(np.float32) / 32768.0, None


def usable_bands(cfg: Audio1Config, nyquist_hz: float) -> list:
    """The configured bands, clipped to what this recording can carry.

    A band wholly above Nyquist is kept in the list and marked
    unsupported rather than dropped: "we did not measure 3-8 kHz
    because this camera records at 8 kHz" is the finding, and a row
    that silently disappears cannot say it.
    """
    out = []
    for b in cfg.bands:
        lo, hi = float(b.lo_hz), float(b.hi_hz)
        if lo >= nyquist_hz:
            out.append({"name": b.name, "lo_hz": lo, "hi_hz": hi,
                        "why": b.why, "supported": False,
                        "clipped": False,
                        "note": "entirely above this recording's Nyquist "
                                "limit of %.0f Hz" % nyquist_hz})
            continue
        _hi = min(hi, nyquist_hz)
        out.append({"name": b.name, "lo_hz": lo, "hi_hz": _hi,
                    "why": b.why, "supported": True,
                    "clipped": _hi < hi,
                    "note": ("clipped to Nyquist (%.0f Hz); the band asked "
                             "for %.0f Hz" % (nyquist_hz, hi))
                            if _hi < hi else None})
    return out


# ── ENVELOPE AND TIMING ─────────────────────────────────────────────

def envelope_of(samples, sr: int, ms: float):
    """Moving-average magnitude envelope at a millisecond scale."""
    win = max(1, int(round(sr * ms / 1000.0)))
    if samples.size <= win:
        return None
    k = np.ones(win, dtype=np.float32) / float(win)
    return np.convolve(np.abs(samples), k, mode="same")


def _robust_floor(x):
    """A noise floor that one loud thing cannot drag upwards.

    The median is already robust to a single transient; the 25th
    percentile is robust to a background that is itself half-full of
    them, which is what a driving range sounds like. Both are
    reported so the gap between them can be read as a tell: a quiet
    tee has them nearly equal.
    """
    if x is None or x.size == 0:
        return None, None
    return float(np.median(x)), float(np.percentile(x, 25))


def highpass_slice(seg, sr: int, cut_hz: float):
    """`seg` with everything under `cut_hz` removed, same length.

    FFT, ZERO THE LOW BINS, BACK AGAIN. Done per slice rather than
    over the whole clip: the windows this feeds are at most 200 ms
    wide, a slice a few times that costs nothing, and filtering eight
    million samples to read 200 ms of them would be the expensive way
    to get the same answer.

    WHY FILTER AT ALL, when the band energies above come from one
    FFT of one window: because the concentration ratios compare
    DIFFERENT window lengths, and an FFT of each length has its own
    bin spacing and its own normalisation. Energies taken in the time
    domain off one filtered signal are directly comparable; energies
    summed out of five different spectra are not, quite, and the
    error would sit exactly where the ratio is read.
    """
    if seg.size < 8:
        return None
    sp = np.fft.rfft(seg)
    fr = np.fft.rfftfreq(seg.size, 1.0 / sr)
    sp[fr < cut_hz] = 0.0
    return np.fft.irfft(sp, n=seg.size).astype(np.float32)


def _energy(x):
    """INTEGRATED energy, not average power.

    Asked for by name, and the distinction is the whole point of the
    ratios: mean power over 10 ms against mean power over 100 ms is
    dominated by the window length and says almost nothing, where the
    sums say what fraction of the sound is in the middle.
    """
    if x is None or x.size == 0:
        return 0.0
    return float(np.sum(x.astype(np.float64) ** 2))


def _band_energy(mag, freqs, lo, hi):
    sel = (freqs >= lo) & (freqs < hi)
    if not np.any(sel):
        return 0.0
    return float(np.sum(mag[sel] ** 2))


def measure_peak(samples, sr: int, peak_sec: float, cfg: Audio1Config,
                 bands: list, env=None) -> dict:
    """Every number this module knows how to take, for one peak.

    Nothing here rejects anything. A measurement that cannot be taken
    comes back None with a line in `notes` saying which and why.
    """
    m: dict = {"peak_time_sec": round(float(peak_sec), 4), "notes": []}
    n = samples.size
    i = int(round(peak_sec * sr))
    if i < 0 or i >= n:
        m["notes"].append("the peak is outside the decoded audio")
        return m
    if env is None:
        env = envelope_of(samples, sr, cfg.envelope_ms)
    if env is None:
        m["notes"].append("the clip is shorter than one envelope window")
        return m

    ms = lambda v: int(round(sr * v / 1000.0))  # noqa: E731

    # ── THE BACKGROUND THIS PEAK ROSE OUT OF ────────────────────────
    #
    # Ending a guard before the peak, so the impact's own rise is not
    # counted as the quiet it rose from.
    bg_hi = max(0, i - ms(cfg.background_guard_ms))
    bg_lo = max(0, bg_hi - ms(cfg.background_ms))
    bg_env = env[bg_lo:bg_hi]
    bg_med, bg_p25 = _robust_floor(bg_env)
    m["background_window_sec"] = [round(bg_lo / sr, 4), round(bg_hi / sr, 4)]
    m["background_level"] = (round(bg_med, 6) if bg_med is not None else None)
    m["background_level_p25"] = (round(bg_p25, 6) if bg_p25 is not None
                                 else None)
    if bg_med is None or bg_env.size < ms(20.0):
        m["notes"].append(
            "less than 20 ms of audio before this peak to measure a "
            "background from")
    # SUSTAINED OR ISOLATED. A background whose own peaks come close to
    # its median is a steady noise; one with a big spread is quiet with
    # events in it.
    if bg_env.size:
        _bmax = float(np.max(bg_env))
        m["background_peak"] = round(_bmax, 6)
        m["background_sustained"] = bool(
            bg_med is not None and bg_med > 0 and _bmax < 3.0 * bg_med)
    else:
        m["background_peak"] = None
        m["background_sustained"] = None

    peak_amp = float(np.max(np.abs(samples[max(0, i - ms(5)):i + ms(5)]))
                     ) if n else 0.0
    env_peak = float(env[i])
    m["peak_amplitude"] = round(peak_amp, 6)
    m["peak_envelope"] = round(env_peak, 6)
    m["peak_ratio"] = (round(env_peak / bg_med, 3)
                       if bg_med and bg_med > 0 else None)
    m["snr_db"] = (round(20.0 * math.log10(env_peak / bg_med), 2)
                   if bg_med and bg_med > 0 and env_peak > 0 else None)

    # BACKGROUND-ADJUSTED, as the request asks: the rise the impact
    # itself contributed, not the absolute level it reached.
    base = bg_med if bg_med is not None else 0.0
    span = env_peak - base
    if span <= 0:
        m["notes"].append(
            "this peak does not rise above its own background — nothing "
            "to time an attack or a decay against")
        span = 0.0

    # ── ATTACK: 10% to 90% of the adjusted peak ─────────────────────
    m["attack_ms"] = None
    if span > 0:
        lo_v = base + cfg.attack_lo_frac * span
        hi_v = base + cfg.attack_hi_frac * span
        j = i
        t_hi = t_lo = None
        look = max(1, i - ms(cfg.background_ms))
        while j > look:
            if t_hi is None and env[j] <= hi_v:
                t_hi = j
            if env[j] <= lo_v:
                t_lo = j
                break
            j -= 1
        if t_lo is not None and t_hi is not None and t_hi >= t_lo:
            m["attack_ms"] = round(1000.0 * (t_hi - t_lo) / sr, 2)
        else:
            m["notes"].append(
                "the envelope never drops to 10%% of this peak within "
                "%.0f ms before it — a sustained sound, or one riding on "
                "another" % cfg.background_ms)

    # ── DECAY: back under 10% and staying there ─────────────────────
    #
    # AND WHY IT ENDED WHERE IT DID, which the first version of this
    # did not say and should have. Measured on four synthetic cases:
    #
    #   clean strike, nothing after it ............ 145 ms, correct
    #   second sound 150 ms later ................. 262 ms, WRONG —
    #       the later sound lifts the envelope back over the floor and
    #       resets the hold, so the number times that sound, not this
    #   unrelated sound 600 ms later .............. 145 ms, correct —
    #       the hold had already completed, so contamination is bounded
    #       by how SOON the next sound lands, not by whether one does
    #   quiet strike on a noisy floor ............ 1483 ms, WRONG —
    #       10% of a small span sits inside the ambience, so the
    #       envelope keeps being re-lifted by noise and it takes a
    #       second and a half to find 20 ms of quiet
    #
    # The last one is the ">1000 ms" case, and the first version
    # reported it with NO note at all: a failure wearing a
    # measurement's clothes, which is the same shape as the 90.0 ms
    # attack sentinel this repository has already been bitten by once.
    #
    # Nothing is rejected here — the numbers are all reported — but
    # each one now says which of the four it is.
    m["decay_ms"] = None
    m["transient_duration_ms"] = None
    m["decay_first_cross_ms"] = None
    m["decay_resets"] = None
    m["decay_reset_last_ms"] = None
    m["decay_end_reason"] = None
    m["decay_longest_quiet_ms"] = None
    m["decay_crossings"] = None
    if span > 0:
        floor_v = base + cfg.decay_floor_frac * span
        hold = ms(cfg.decay_hold_ms)
        reset_min = max(1, ms(cfg.decay_reset_min_ms))
        end_i = min(n, i + ms(2000.0))
        under = (env[i:end_i] <= floor_v)
        # RUN-LENGTH ENCODE IT. Walking sample by sample invites the
        # jitter bug back; the question is about RUNS, so ask about
        # runs.
        runs = []
        if under.size:
            edges = np.flatnonzero(np.diff(under.astype(np.int8))) + 1
            bounds = np.concatenate(([0], edges, [under.size]))
            for a, b in zip(bounds[:-1], bounds[1:]):
                runs.append((bool(under[a]), int(a), int(b)))
        quiet = [(a, b) for u, a, b in runs if u]
        if quiet:
            m["decay_first_cross_ms"] = round(1000.0 * quiet[0][0] / sr, 2)
            m["decay_longest_quiet_ms"] = round(
                1000.0 * max(b - a for a, b in quiet) / sr, 2)
        settle = next(((a, b) for a, b in quiet if b - a >= hold), None)
        # A RESET IS A SOUND, NOT A SAMPLE. Only a stretch back over
        # the floor long enough to be a sound counts, and only one
        # that happens after the first crossing and before it settles.
        cut_at = settle[0] if settle else under.size
        resets = [(a, b) for u, a, b in runs
                  if not u and b - a >= reset_min
                  and quiet and a > quiet[0][0] and a < cut_at]
        m["decay_resets"] = len(resets)
        # ── AND THE CROSSINGS TOO SHORT TO BE SOUNDS ────────────────
        #
        # THE THIRD FAILURE MODE, and the one that produced 1483 ms on
        # a quiet strike over a noisy floor. Every excursion there was
        # a handful of samples long, so none counted as a sound and
        # the reason read "fell and stayed down, first time" about an
        # envelope that had churned on its own floor for a second and
        # a half. The count of crossings says what the count of
        # sounds cannot.
        m["decay_crossings"] = sum(
            1 for a, _b in quiet if a < cut_at)
        if resets:
            m["decay_reset_last_ms"] = round(
                1000.0 * resets[-1][0] / sr, 2)
        if settle:
            m["decay_ms"] = round(1000.0 * settle[0] / sr, 2)
            if m["attack_ms"] is not None:
                m["transient_duration_ms"] = round(
                    m["attack_ms"] + m["decay_ms"], 2)
            _churn = ((m["decay_longest_quiet_ms"] or 0.0)
                      < cfg.decay_quiet_floor_mult * cfg.decay_hold_ms)
            if not resets and not _churn:
                m["decay_end_reason"] = "fell and stayed down, first time"
            elif not resets:
                m["decay_end_reason"] = (
                    "the floor is inside the ambience: it crossed %d "
                    "times and the longest quiet stretch anywhere in 2 s "
                    "was only %.0f ms, barely over the %.0f ms hold. "
                    "This is a noise measurement, not a decay."
                    % (m["decay_crossings"] or 0,
                       m["decay_longest_quiet_ms"] or 0.0,
                       cfg.decay_hold_ms))
                m["notes"].append(m["decay_end_reason"])
            else:
                m["decay_end_reason"] = (
                    "crossed at %.0f ms, then %d later sound(s) lifted it "
                    "back over the floor — the last at %.0f ms. This "
                    "number times the last of those, not this sound."
                    % (m["decay_first_cross_ms"] or 0.0, len(resets),
                       m["decay_reset_last_ms"] or 0.0))
                m["notes"].append(m["decay_end_reason"])
            if m["decay_ms"] > cfg.decay_suspect_ms:
                m["notes"].append(
                    "a %.0f ms decay is longer than a struck ball rings — "
                    "read decay_resets before trusting it"
                    % m["decay_ms"])
        else:
            m["decay_crossings"] = len(quiet)
            if not quiet:
                m["decay_end_reason"] = (
                    "never fell under 10%% of its own rise at all within "
                    "2 s, so the floor is inside the ambience and there "
                    "is no decay to time")
            else:
                m["decay_end_reason"] = (
                    "crossed at %.0f ms but never stayed under for "
                    "%.0f ms within 2 s — the longest quiet stretch was "
                    "%.0f ms, broken by %d sound(s)"
                    % (m["decay_first_cross_ms"], cfg.decay_hold_ms,
                       m["decay_longest_quiet_ms"] or 0.0, len(resets)))
            m["notes"].append(m["decay_end_reason"])

    # ── CREST FACTOR, over one consistent window ────────────────────
    w0 = max(0, i - ms(cfg.impact_window_ms / 2.0))
    w1 = min(n, i + ms(cfg.impact_window_ms / 2.0))
    seg = samples[w0:w1]
    m["impact_window_sec"] = [round(w0 / sr, 4), round(w1 / sr, 4)]
    if seg.size:
        rms = float(np.sqrt(np.mean(seg.astype(np.float64) ** 2)))
        pk = float(np.max(np.abs(seg)))
        m["rms"] = round(rms, 6)
        m["crest_factor"] = round(pk / rms, 3) if rms > 0 else None
    else:
        m["rms"] = m["crest_factor"] = None

    # ── CLIPPING ────────────────────────────────────────────────────
    n_clipped = int(np.sum(np.abs(seg) >= cfg.clip_level)) if seg.size else 0
    m["n_clipped_samples"] = n_clipped
    m["clipped"] = bool(n_clipped >= cfg.clip_min_samples)
    if m["clipped"]:
        m["notes"].append(
            "the waveform is clipped here (%d samples at full scale), so "
            "amplitude, crest factor and the spectrum are all understated"
            % n_clipped)

    # ── SECONDARY PEAKS ─────────────────────────────────────────────
    s0, s1 = i + ms(10.0), min(n, i + ms(cfg.secondary_window_ms))
    sec = []
    if s1 - s0 > 3:
        seg_e = env[s0:s1]
        thr = base + cfg.secondary_min_frac * span if span > 0 else None
        if thr is not None:
            k = 1
            while k < seg_e.size - 1:
                if (seg_e[k] >= thr and seg_e[k] >= seg_e[k - 1]
                        and seg_e[k] >= seg_e[k + 1]):
                    sec.append({
                        "at_ms": round(1000.0 * (s0 + k - i) / sr, 1),
                        "strength": round(
                            float(seg_e[k] - base) / span, 3)
                                   if span > 0 else None})
                    k += max(1, ms(5.0))
                else:
                    k += 1
    m["secondary_peaks"] = sec
    m["n_secondary_peaks"] = len(sec)
    # ISOLATION, in dB: how far the loudest thing in the window after
    # the impact sits below the impact.
    if s1 - s0 > 3 and env_peak > 0:
        after_max = float(np.max(env[s0:s1]))
        m["isolation_db"] = (round(20.0 * math.log10(env_peak / after_max), 2)
                             if after_max > 0 else None)
    else:
        m["isolation_db"] = None

    # ── THE SPECTRUM OVER THE IMPACT WINDOW ─────────────────────────
    m["bands"] = []
    m["pct_above"] = {}
    m["spectral_centroid_hz"] = None
    m["hf_snr_db"] = None
    if seg.size >= 16:
        w = np.hanning(seg.size).astype(np.float32)
        mag = np.abs(np.fft.rfft(seg * w))
        freqs = np.fft.rfftfreq(seg.size, 1.0 / sr)
        total = float(np.sum(mag ** 2))
        for b in bands:
            if not b["supported"]:
                m["bands"].append({**b, "energy": None, "pct": None})
                continue
            e = _band_energy(mag, freqs, b["lo_hz"], b["hi_hz"])
            m["bands"].append({
                **b, "energy": e,
                "pct": round(100.0 * e / total, 2) if total > 0 else None})
        for cut in cfg.pct_above_hz:
            key = "pct_above_%d" % int(cut)
            if cut >= sr / 2.0:
                m["pct_above"][key] = None
                m["notes"].append(
                    "%.0f Hz is at or above this recording's Nyquist limit "
                    "of %.0f Hz, so nothing above it was recorded"
                    % (cut, sr / 2.0))
                continue
            e = _band_energy(mag, freqs, cut, sr / 2.0)
            m["pct_above"][key] = (round(100.0 * e / total, 2)
                                   if total > 0 else None)
        if total > 0:
            m["spectral_centroid_hz"] = round(
                float(np.sum(freqs * (mag ** 2)) / total), 1)
        # HIGH-FREQUENCY SNR, against the same band in the background.
        if bg_env.size >= 16 and bg_hi - bg_lo >= seg.size:
            bseg = samples[bg_hi - seg.size:bg_hi]
            if bseg.size == seg.size:
                bmag = np.abs(np.fft.rfft(bseg * w))
                hf_cut = min(1500.0, sr / 2.0 - 1.0)
                e_sig = _band_energy(mag, freqs, hf_cut, sr / 2.0)
                e_bg = _band_energy(bmag, freqs, hf_cut, sr / 2.0)
                m["hf_snr_db"] = (round(10.0 * math.log10(e_sig / e_bg), 2)
                                  if e_bg > 0 and e_sig > 0 else None)
    else:
        m["notes"].append(
            "the impact window holds fewer than 16 samples — no spectrum")

    # ── SPECTRAL FLUX across the onset ──────────────────────────────
    m["spectral_flux"] = None
    nfft = max(16, ms(cfg.stft_ms))
    hop = max(1, ms(cfg.stft_hop_ms))
    f0 = max(0, i - 4 * hop)
    f1 = min(n, i + 4 * hop + nfft)
    if f1 - f0 >= 2 * nfft:
        wf = np.hanning(nfft).astype(np.float32)
        prev = None
        best = 0.0
        for st in range(f0, f1 - nfft, hop):
            fr = np.abs(np.fft.rfft(samples[st:st + nfft] * wf))
            if prev is not None:
                d = fr - prev
                best = max(best, float(np.sum(d[d > 0])))
            prev = fr
        m["spectral_flux"] = round(best, 4)

    # ── ENERGY IN NESTED WINDOWS, CENTRED ON THE PEAK ───────────────
    #
    # BACKGROUND-ADJUSTED, so a loud room and a quiet one give the
    # same answer about the same strike: the background's own energy
    # density times the window length is taken off each one. Floored
    # at zero — a window with less energy than the ambience around it
    # has none of its own, and a negative energy is not a reading.
    #
    # INTEGRATED, NOT AVERAGED, which is the whole point of the
    # ratios below. Mean power over 10 ms against mean power over
    # 100 ms is mostly a statement about window lengths; the sums say
    # what fraction of the sound is in the middle, which is what
    # separates a spike from a voice.
    m["energy"] = {}
    m["energy_hf"] = {}
    m["concentration"] = {}
    m["concentration_hf"] = {}
    m["post"] = {}
    m["post_hf"] = {}
    _maxw = max(list(cfg.energy_windows_ms)
                + [b for _a, b in cfg.post_intervals_ms] + [200.0])
    _pad = ms(_maxw) + ms(50.0)
    e0, e1 = max(0, i - _pad), min(n, i + _pad)
    wide = samples[e0:e1]
    wide_hf = highpass_slice(wide, sr, cfg.hf_cut_hz)
    # The background's energy PER SAMPLE, full band and high band, so
    # the adjustment scales with however long a window is.
    bg_seg = samples[bg_lo:bg_hi]
    bg_den = bg_den_hf = None
    if bg_seg.size >= 16:
        bg_den = _energy(bg_seg) / float(bg_seg.size)
        _bhf = highpass_slice(bg_seg, sr, cfg.hf_cut_hz)
        if _bhf is not None:
            bg_den_hf = _energy(_bhf) / float(_bhf.size)
    m["background_energy_per_sample"] = (
        None if bg_den is None else float(bg_den))
    m["background_energy_per_sample_hf"] = (
        None if bg_den_hf is None else float(bg_den_hf))

    def _adj(x, den):
        if x is None or x.size == 0:
            return None
        e = _energy(x)
        if den is None:
            return e
        return max(0.0, e - den * float(x.size))

    def _slice(lo_ms, hi_ms, arr, off):
        a = i + ms(lo_ms) - off
        b = i + ms(hi_ms) - off
        a, b = max(0, a), min(arr.size, b)
        return arr[a:b] if b > a else None

    for wms in cfg.energy_windows_ms:
        half = wms / 2.0
        m["energy"]["w%d" % int(wms)] = _adj(
            _slice(-half, half, wide, e0), bg_den)
        m["energy_hf"]["w%d" % int(wms)] = (
            None if wide_hf is None
            else _adj(_slice(-half, half, wide_hf, e0), bg_den_hf))

    def _ratio(d, num, den):
        a, b = d.get("w%d" % int(num)), d.get("w%d" % int(den))
        if a is None or b is None or b <= 0:
            return None
        return round(a / b, 4)

    for num, den in cfg.concentration_ratios:
        key = "e%d_over_e%d" % (int(num), int(den))
        m["concentration"][key] = _ratio(m["energy"], num, den)
        m["concentration_hf"][key] = _ratio(m["energy_hf"], num, den)

    # ── ENERGY AFTER THE PEAK, interval by interval ─────────────────
    for a_ms, b_ms in cfg.post_intervals_ms:
        key = "ms%d_%d" % (int(a_ms), int(b_ms))
        m["post"][key] = _adj(_slice(a_ms, b_ms, wide, e0), bg_den)
        m["post_hf"][key] = (None if wide_hf is None
                             else _adj(_slice(a_ms, b_ms, wide_hf, e0),
                                       bg_den_hf))
    # AND AS A SHAPE, not just four numbers: each interval against the
    # first one. A strike empties out; a voice or a gust does not.
    _first = m["post"].get("ms0_20")
    m["post_shape"] = {
        k: (None if (v is None or not _first) else round(v / _first, 4))
        for k, v in m["post"].items()}

    # ── LOW-FREQUENCY (WIND) ENERGY IN THE BACKGROUND ───────────────
    m["wind_pct"] = None
    if bg_env.size >= 64:
        bseg = samples[bg_lo:bg_hi]
        if bseg.size >= 64:
            wb = np.hanning(bseg.size).astype(np.float32)
            bmag = np.abs(np.fft.rfft(bseg * wb))
            bfreqs = np.fft.rfftfreq(bseg.size, 1.0 / sr)
            btot = float(np.sum(bmag ** 2))
            if btot > 0:
                m["wind_pct"] = round(
                    100.0 * _band_energy(bmag, bfreqs, 0.0,
                                         min(300.0, sr / 2.0)) / btot, 2)
    return m


# ── THE SCORE ───────────────────────────────────────────────────────

def _ramp(v, poor, good):
    """0 at `poor`, 1 at `good`, linear between, either direction."""
    if v is None:
        return None
    if good == poor:
        return None
    t = (float(v) - poor) / (good - poor)
    return max(0.0, min(1.0, t))


def _window_score(v, lo, best_lo, best_hi, hi):
    """1 inside [best_lo, best_hi], tapering to 0 at lo and hi."""
    if v is None:
        return None
    v = float(v)
    if best_lo <= v <= best_hi:
        return 1.0
    if v < best_lo:
        return max(0.0, (v - lo) / (best_lo - lo)) if best_lo > lo else 0.0
    return max(0.0, (hi - v) / (hi - best_hi)) if hi > best_hi else 0.0


def confidence(m: dict, cfg: Audio1Config) -> dict:
    """An experimental 0-100 score, and every part of how it got there.

    WHAT IT IS NOT: a decision. It does not reach the cutter, it keeps
    no clip and drops none, and it will not until there are labelled
    examples enough to say whether any of these six components
    actually separates a strike from a voice.

    A COMPONENT THAT COULD NOT BE MEASURED IS DROPPED, not scored
    zero, and the weights of what remains are renormalised so the
    total stays comparable. Scoring an unmeasurable attack as 0 would
    punish a quiet, distant strike for being quiet -- which the
    request explicitly asks us not to do -- and it would do it
    invisibly, inside a single number.
    """
    nrm = cfg.norm
    hf = (m.get("pct_above") or {}).get("pct_above_1500")
    parts = {
        "frequency": _ramp(None if hf is None else hf / 100.0,
                           nrm["hf_fraction_poor"], nrm["hf_fraction_good"]),
        "peak_to_background": _ramp(m.get("snr_db"), nrm["snr_db_poor"],
                                    nrm["snr_db_good"]),
        "attack": _ramp(m.get("attack_ms"), nrm["attack_ms_poor"],
                        nrm["attack_ms_good"]),
        "decay": _window_score(m.get("decay_ms"), nrm["decay_ms_lo"],
                               nrm["decay_ms_best_lo"],
                               nrm["decay_ms_best_hi"], nrm["decay_ms_hi"]),
        "duration": _window_score(
            m.get("transient_duration_ms"), nrm["dur_ms_lo"],
            nrm["dur_ms_best_lo"], nrm["dur_ms_best_hi"], nrm["dur_ms_hi"]),
        "isolation": _ramp(m.get("isolation_db"), nrm["iso_db_poor"],
                           nrm["iso_db_good"]),
    }
    used = {k: v for k, v in parts.items() if v is not None}
    w_used = sum(cfg.weights.get(k, 0.0) for k in used)
    missing = [k for k, v in parts.items() if v is None]
    if not used or w_used <= 0:
        return {"score": None, "band": None, "components": parts,
                "weights": dict(cfg.weights), "missing": missing,
                "why": "none of the six components could be measured"}
    score = sum(cfg.weights.get(k, 0.0) * v for k, v in used.items())
    score = 100.0 * score / w_used
    band = ("high" if score >= cfg.confidence_high else
            "medium" if score >= cfg.confidence_medium else "low")
    return {
        "score": round(score, 1),
        "band": band,
        "components": {k: (None if v is None else round(v, 3))
                       for k, v in parts.items()},
        "contributions": {k: round(cfg.weights.get(k, 0.0) * v / w_used
                                   * 100.0, 1)
                          for k, v in used.items()},
        "weights": dict(cfg.weights),
        "missing": missing,
        "why": (None if not missing else
                "%d of 6 components could not be measured (%s); the rest "
                "were renormalised rather than scored zero"
                % (len(missing), ", ".join(missing))),
    }


# ── PICTURES ────────────────────────────────────────────────────────
#
# DRAWN WITH cv2, because matplotlib is not installed and every other
# diagnostic in this repository is drawn the same way. One image per
# peak holds both views, so the waveform and the spectrogram cannot
# drift apart in time: they are rendered from one slice, over one
# axis, and a reader comparing them is comparing the same samples.

def render_peak_png(samples, sr: int, peak_sec: float, out_path: Path,
                    cfg: Audio1Config = DEFAULT_CONFIG,
                    pad_sec: float = 0.5, width: int = 900,
                    wave_h: int = 170, spec_h: int = 230) -> str | None:
    """Waveform over spectrogram for ±`pad_sec` around a peak.

    Returns the path written, or None with nothing written. Never
    raises: this is the explanation, and a peak must still be
    reported when its picture cannot be drawn.
    """
    if not HAS_NP:
        return None
    try:
        import cv2  # type: ignore
    except Exception:  # noqa: BLE001
        return None
    try:
        i = int(round(peak_sec * sr))
        lo = max(0, i - int(pad_sec * sr))
        hi = min(samples.size, i + int(pad_sec * sr))
        seg = samples[lo:hi]
        if seg.size < 64:
            return None
        img = np.zeros((wave_h + spec_h + 26, width, 3), np.uint8)
        img[:] = (22, 22, 26)

        # ── THE WAVEFORM, as min/max per column ─────────────────────
        #
        # NOT EVERY nth SAMPLE. Decimation on a transient draws
        # whatever happened to land on the stride and can miss the
        # peak entirely -- the one feature the picture exists to
        # show. Column extremes cannot.
        step = max(1, seg.size // width)
        peak_abs = float(np.max(np.abs(seg))) or 1.0
        mid = wave_h // 2
        for x in range(width):
            a = x * seg.size // width
            b = max(a + 1, (x + 1) * seg.size // width)
            chunk = seg[a:b:max(1, (b - a) // 64 or 1)]
            if chunk.size == 0:
                continue
            y0 = int(mid - float(np.max(chunk)) / peak_abs * (mid - 4))
            y1 = int(mid - float(np.min(chunk)) / peak_abs * (mid - 4))
            cv2.line(img, (x, y0), (x, y1), (120, 220, 140), 1)
        cv2.line(img, (0, mid), (width, mid), (60, 60, 68), 1)

        # ── THE SPECTROGRAM ─────────────────────────────────────────
        nfft = 1 << max(6, int(math.log2(max(32, sr * 0.012))))
        hop = max(1, nfft // 4)
        frames = max(1, (seg.size - nfft) // hop)
        if frames < 2:
            return None
        w = np.hanning(nfft).astype(np.float32)
        spec = np.empty((nfft // 2 + 1, frames), np.float32)
        for k in range(frames):
            st = k * hop
            spec[:, k] = np.abs(np.fft.rfft(seg[st:st + nfft] * w))
        db = 20.0 * np.log10(spec + 1e-8)
        top = float(np.max(db))
        db = np.clip(db, top - 72.0, top)
        norm = ((db - (top - 72.0)) / 72.0 * 255.0).astype(np.uint8)
        norm = np.flipud(norm)                      # 0 Hz at the bottom
        heat = cv2.applyColorMap(
            cv2.resize(norm, (width, spec_h),
                       interpolation=cv2.INTER_LINEAR), cv2.COLORMAP_MAGMA)
        img[wave_h:wave_h + spec_h, :, :] = heat

        # ── THE PEAK, AND THE BANDS, MARKED ─────────────────────────
        px = int((i - lo) / max(1, seg.size) * width)
        cv2.line(img, (px, 0), (px, wave_h + spec_h), (80, 120, 255), 1)
        nyq = sr / 2.0
        for b in cfg.bands:
            if b.hi_hz >= nyq:
                continue
            y = wave_h + spec_h - int(b.hi_hz / nyq * spec_h)
            cv2.line(img, (0, y), (width, y), (90, 90, 110), 1)
            cv2.putText(img, "%dHz" % int(b.hi_hz), (4, max(10, y - 3)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.32, (190, 190, 210), 1,
                        cv2.LINE_AA)
        cv2.putText(img,
                    "%.0f ms/div  -  peak %.3fs  -  %d Hz, Nyquist %.0f Hz"
                    % (pad_sec * 1000 / 5.0, peak_sec, sr, nyq),
                    (6, wave_h + spec_h + 17), cv2.FONT_HERSHEY_SIMPLEX,
                    0.38, (200, 200, 215), 1, cv2.LINE_AA)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(out_path), img):
            return None
        return str(out_path)
    except Exception as exc:  # noqa: BLE001
        log.warning("audio1: could not draw the peak picture: %s", exc)
        return None


# ── THE WHOLE RUN ───────────────────────────────────────────────────

def analyse(path: Path, peaks: list, cfg: Audio1Config = DEFAULT_CONFIG,
            images_dir: Path | None = None, image_prefix: str = "a1",
            url_of=None, max_images: int = 24) -> dict:
    """Measure every peak in `peaks` on the audio of `path`.

    `peaks` is a list of {time_sec, passed_gate, ...} — whatever the
    existing detector found, including the ones it refused. THE
    REFUSED ONES ARE THE POINT as much as the kept ones: a missed
    strike only ever appears among them, and a diagnostic that shows
    only what was kept can never show a false negative.

    Decides nothing, changes nothing, and never raises.
    """
    out: dict = {"ok": False, "reason": None, "probe": None,
                 "config": cfg.to_json(), "bands": None, "peaks": [],
                 # SERVED FROM THE CODE, so the page cannot describe a
                 # measurement the running build does not take.
                 "how_measured": HOW_MEASURED}
    probe = probe_audio(path)
    out["probe"] = probe
    if not probe["ok"]:
        out["reason"] = probe["reason"]
        return out
    sr = int(probe["sample_rate"])
    samples, why = decode_audio(path, sr)
    if samples is None:
        out["reason"] = why
        return out
    out["bands"] = usable_bands(cfg, sr / 2.0)
    env = envelope_of(samples, sr, cfg.envelope_ms)
    if env is None:
        out["reason"] = "the clip is shorter than one envelope window"
        return out
    n_img = 0
    for p in (peaks or []):
        try:
            t = float(p.get("time_sec"))
        except (TypeError, ValueError, AttributeError):
            continue
        m = measure_peak(samples, sr, t, cfg, out["bands"], env=env)
        m["passed_gate"] = bool(p.get("passed_gate"))
        m["source"] = p.get("source") or "detector"
        for k in ("ratio", "attack_ms"):
            if p.get(k) is not None:
                m["detector_" + k] = p[k]
        m["confidence"] = confidence(m, cfg)
        if images_dir is not None and n_img < max_images:
            fn = images_dir / ("%s-%s.png" % (image_prefix,
                                              str(round(t, 3)).replace(
                                                  ".", "_")))
            got = render_peak_png(samples, sr, t, fn, cfg)
            if got:
                m["image_url"] = url_of(Path(got).name) if url_of else got
                n_img += 1
        out["peaks"].append(m)
    out["n_peaks"] = len(out["peaks"])
    out["ok"] = True
    return out


# ── HOW EACH NUMBER IS ACTUALLY ARRIVED AT ──────────────────────────
#
# ASKED FOR DIRECTLY: "Document exactly how the existing isolation,
# original attack, SNR and HF SNR measurements are calculated."
#
# It lives here, next to the code, and is served to the page — because
# a description kept anywhere else drifts from the thing it describes,
# and a wrong description of a measurement is worse than none: it gets
# believed.
#
# THE ONE THAT CATCHES PEOPLE OUT is that these are not all taken off
# the same signal. The original attack is measured on the DETECTOR's
# audio, which is resampled to 22050 Hz and HIGH-PASSED AT 1500 Hz
# before its envelope is built. Everything audio1 adds is measured on
# the camera's own rate, unfiltered. Two attack figures in the same
# row are therefore two different measurements of two different
# signals, which is why both are shown.

HOW_MEASURED = [
    {
        "name": "original attack (attack¹)",
        "where": "ai_tracer.detect_swings_from_audio -> _attack_at",
        "signal": "22050 Hz mono, HIGH-PASSED at 1500 Hz by ffmpeg "
                  "before anything else runs",
        "envelope": "moving average of |x| over 10 ms",
        "how": "A reference level is set at max(0.10 x peak, 2.0 x the "
               "clip's median envelope). Walking back from the peak up "
               "to max(10 ms, 3 x max_attack) samples, the attack is "
               "the time since the most recent sample BELOW that "
               "reference.",
        "cannot": "When the reference reaches half the peak there is no "
                  "rise left to time and it returns a sentinel "
                  "((lookback+1)/rate) rather than a number. That is "
                  "'could not measure', not 'slow' — the two were once "
                  "indistinguishable and a filter was silently dropping "
                  "quiet strikes because of it.",
    },
    {
        "name": "attack (10-90%)",
        "where": "audio1.measure_peak",
        "signal": "the camera's own sample rate, UNFILTERED",
        "envelope": "moving average of |x| over 2 ms",
        "how": "Background-adjusted: span = peak envelope - background "
               "median. Walking back from the peak, the time between "
               "the last crossing of background + 10% of span and the "
               "last crossing of background + 90% of span.",
        "cannot": "None when the envelope never drops to 10% within "
                  "500 ms before the peak — a sustained sound, or one "
                  "riding on another.",
    },
    {
        "name": "SNR",
        "where": "audio1.measure_peak",
        "signal": "the camera's own rate, unfiltered; 2 ms envelope",
        "how": "20 x log10(peak envelope / background median). An "
               "AMPLITUDE ratio, hence 20x.",
        "background": "The median of the envelope over the 500 ms "
                      "ending 50 ms before the peak. The guard matters: "
                      "without it the impact's own rise is counted as "
                      "part of the quiet it rose out of. The median is "
                      "used rather than the mean so one earlier bang "
                      "cannot lift the floor; the 25th percentile is "
                      "reported beside it, and a big gap between the "
                      "two means a busy background.",
        "cannot": "None when the background measures zero.",
    },
    {
        "name": "HF SNR",
        "where": "audio1.measure_peak",
        "signal": "the camera's own rate, unfiltered",
        "how": "10 x log10(E_peak / E_background), where E is the sum "
               "of |rfft|^2 from 1500 Hz up to Nyquist. An ENERGY "
               "ratio, hence 10x — so it is on the same dB scale as "
               "the SNR above and measures a different thing: band "
               "energy in a 50 ms window against broadband envelope "
               "amplitude.",
        "background": "A slice of exactly the same length, ending where "
                      "the background window ends, Hann-windowed the "
                      "same way. Equal lengths matter: two FFTs of "
                      "different lengths are not directly comparable.",
        "cannot": "None when either side has no energy above 1500 Hz, "
                  "or when there is less background than one impact "
                  "window to compare against.",
    },
    {
        "name": "isolation",
        "where": "audio1.measure_peak",
        "signal": "the 2 ms envelope",
        "how": "20 x log10(peak envelope / the loudest envelope value "
               "between 10 ms and 200 ms AFTER the peak). High means "
               "nothing followed it; low means something did.",
        "cannot": "None when there is no audio after the peak, or when "
                  "the loudest thing after it measures zero.",
        "note": "It looks only FORWARD. A sound 100 ms BEFORE the peak "
                "does not reduce isolation; it shows up in the "
                "background figures instead.",
    },
    {
        "name": "decay, and why it is sometimes huge or absent",
        "where": "audio1.measure_peak",
        "how": "Time from the peak until the envelope has been under "
               "background + 10% of span for 20 ms without coming back, "
               "searched up to 2 s.",
        "cannot": "Measured on four synthetic cases: a clean strike "
                  "gives 145 ms; a second sound 150 ms later gives "
                  "262 ms, because that sound lifts the envelope back "
                  "over the floor and resets the hold; an unrelated "
                  "sound 600 ms later changes nothing, because the hold "
                  "had already completed; and a quiet strike over a "
                  "noisy floor gives 1483 ms, because 10% of a small "
                  "span sits inside the ambience. Every row now says "
                  "which of those it is in decay_end_reason, with the "
                  "crossing count and the longest quiet stretch beside "
                  "it.",
    },
    {
        "name": "energy windows and concentration ratios",
        "where": "audio1.measure_peak",
        "how": "INTEGRATED energy (sum of x^2), not average power, in "
               "windows of 10/20/50/100/200 ms CENTRED on the peak, "
               "with the background's energy-per-sample x the window "
               "length subtracted and the result floored at zero. The "
               "ratios are E10/E100, E20/E200 and E50/E200. The "
               "high-frequency copy is the same arithmetic on the same "
               "windows after an FFT high-pass at 1500 Hz — filtered "
               "in the time domain rather than summed out of five "
               "different spectra, because windows of different "
               "lengths have different bin spacings and the error "
               "would land exactly where the ratio is read.",
    },
]


# ── THE FLAT VIEW ───────────────────────────────────────────────────
#
# ONE DEFINITION OF THE COLUMNS, used by the CSV export and by the
# on-screen table. Two lists would drift, and then an operator would
# be reading one set of numbers and exporting another under the same
# headings — which is the worst possible failure for a table whose
# entire purpose is to be analysed later.

CSV_COLUMNS = [
    ("upload_id", "upload"),
    ("peak_time_sec", "peak time (s)"),
    ("kind", "kind"),
    ("label", "label"),
    ("passed_gate", "cut by detector"),
    ("peak_amplitude", "peak amplitude"),
    ("peak_ratio", "peak ratio"),
    ("detector_attack_ms", "attack, detector (ms)"),
    ("attack_ms", "attack 10-90% (ms)"),
    ("decay_ms", "decay (ms)"),
    ("transient_duration_ms", "transient (ms)"),
    ("pct_low", "low 0-300Hz (%)"),
    ("pct_mid", "mid 300-1500Hz (%)"),
    ("pct_impact", "impact 1500-3000Hz (%)"),
    ("pct_sharp", "sharp 3000-8000Hz (%)"),
    ("pct_above_1500", "above 1500Hz (%)"),
    ("pct_above_3000", "above 3000Hz (%)"),
    ("hf_snr_db", "HF SNR (dB)"),
    ("snr_db", "SNR (dB)"),
    ("spectral_centroid_hz", "centroid (Hz)"),
    ("spectral_flux", "flux"),
    ("crest_factor", "crest"),
    ("n_secondary_peaks", "secondary peaks"),
    ("decay_end_reason", "decay reason"),
    ("decay_first_cross_ms", "decay first cross (ms)"),
    ("decay_resets", "decay resets"),
    ("decay_crossings", "decay crossings"),
    ("decay_longest_quiet_ms", "longest quiet (ms)"),
    ("e10", "E 10ms"), ("e20", "E 20ms"), ("e50", "E 50ms"),
    ("e100", "E 100ms"), ("e200", "E 200ms"),
    ("e10hf", "E 10ms HF"), ("e20hf", "E 20ms HF"),
    ("e50hf", "E 50ms HF"), ("e100hf", "E 100ms HF"),
    ("e200hf", "E 200ms HF"),
    ("c10_100", "E10/E100"), ("c20_200", "E20/E200"),
    ("c50_200", "E50/E200"),
    ("c10_100hf", "E10/E100 HF"), ("c20_200hf", "E20/E200 HF"),
    ("c50_200hf", "E50/E200 HF"),
    ("p0_20", "post 0-20ms"), ("p20_50", "post 20-50ms"),
    ("p50_100", "post 50-100ms"), ("p100_200", "post 100-200ms"),
    ("p0_20hf", "post 0-20ms HF"), ("p20_50hf", "post 20-50ms HF"),
    ("p50_100hf", "post 50-100ms HF"),
    ("p100_200hf", "post 100-200ms HF"),
    ("isolation_db", "isolation (dB)"),
    ("background_level", "background"),
    ("background_sustained", "background sustained"),
    ("wind_pct", "wind, low-freq (%)"),
    ("clipped", "clipped"),
    ("confidence_score", "confidence"),
    ("confidence_band", "confidence band"),
    ("c_frequency", "c: frequency"),
    ("c_peak_to_background", "c: peak/bg"),
    ("c_attack", "c: attack"),
    ("c_decay", "c: decay"),
    ("c_duration", "c: duration"),
    ("c_isolation", "c: isolation"),
    ("sample_rate", "sample rate"),
    ("note", "note"),
]


def flat_row(m: dict, *, upload_id=None, kind="detected", label=None,
             note=None, sample_rate=None) -> dict:
    """One measurement dict, flattened to the CSV's columns.

    A missing measurement stays MISSING — an empty cell, never a 0.
    The whole table exists to be correlated against labels, and a
    column of zeros standing in for "could not measure" would correlate
    beautifully with nothing at all.
    """
    m = m or {}
    pct = m.get("pct_above") or {}
    bands = {b.get("name"): b for b in (m.get("bands") or [])}
    conf = m.get("confidence") or {}
    comps = conf.get("components") or {}
    row = {
        "upload_id": upload_id,
        "peak_time_sec": m.get("peak_time_sec"),
        "kind": kind,
        "label": label,
        "passed_gate": m.get("passed_gate"),
        "peak_amplitude": m.get("peak_amplitude"),
        "peak_ratio": m.get("peak_ratio"),
        "detector_attack_ms": m.get("detector_attack_ms"),
        "attack_ms": m.get("attack_ms"),
        "decay_ms": m.get("decay_ms"),
        "transient_duration_ms": m.get("transient_duration_ms"),
        "pct_low": (bands.get("low") or {}).get("pct"),
        "pct_mid": (bands.get("mid") or {}).get("pct"),
        "pct_impact": (bands.get("impact") or {}).get("pct"),
        "pct_sharp": (bands.get("sharp") or {}).get("pct"),
        "pct_above_1500": pct.get("pct_above_1500"),
        "pct_above_3000": pct.get("pct_above_3000"),
        "hf_snr_db": m.get("hf_snr_db"),
        "snr_db": m.get("snr_db"),
        "spectral_centroid_hz": m.get("spectral_centroid_hz"),
        "spectral_flux": m.get("spectral_flux"),
        "crest_factor": m.get("crest_factor"),
        "n_secondary_peaks": m.get("n_secondary_peaks"),
        "isolation_db": m.get("isolation_db"),
        "background_level": m.get("background_level"),
        "background_sustained": m.get("background_sustained"),
        "wind_pct": m.get("wind_pct"),
        "clipped": m.get("clipped"),
        "confidence_score": conf.get("score"),
        "confidence_band": conf.get("band"),
        "sample_rate": sample_rate,
        "note": note,
    }
    for k in ("frequency", "peak_to_background", "attack", "decay",
              "duration", "isolation"):
        row["c_" + k] = comps.get(k)
    # ── THE WINDOWED ENERGIES AND WHAT THEY MAKE ────────────────────
    for k in ("decay_end_reason", "decay_first_cross_ms", "decay_resets",
              "decay_crossings", "decay_longest_quiet_ms"):
        row[k] = m.get(k)
    _e, _ehf = m.get("energy") or {}, m.get("energy_hf") or {}
    _c, _chf = m.get("concentration") or {}, m.get("concentration_hf") or {}
    _p, _phf = m.get("post") or {}, m.get("post_hf") or {}
    for w in (10, 20, 50, 100, 200):
        row["e%d" % w] = _e.get("w%d" % w)
        row["e%dhf" % w] = _ehf.get("w%d" % w)
    for num, den in ((10, 100), (20, 200), (50, 200)):
        key = "e%d_over_e%d" % (num, den)
        row["c%d_%d" % (num, den)] = _c.get(key)
        row["c%d_%dhf" % (num, den)] = _chf.get(key)
    for a, b in ((0, 20), (20, 50), (50, 100), (100, 200)):
        key = "ms%d_%d" % (a, b)
        row["p%d_%d" % (a, b)] = _p.get(key)
        row["p%d_%dhf" % (a, b)] = _phf.get(key)
    return row
