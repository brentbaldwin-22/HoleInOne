#!/usr/bin/env python3
"""The tee/green offset must not inherit the two Pis' clock skew.

WHAT THIS IS ABOUT. The dual-camera cut is aligned from
`green_recording_started_at - tee_recording_started_at`, which the
model's own comment described as "requires NTP-synced Pi clocks" --
an assumption nothing checked and which is false on this deployment.
Measured 26 Oct, a quarter of an hour apart:

    tee    offset -0.018s   round trip  205 ms
    green  offset +0.668s   round trip 1518 ms

0.69s of skew went straight into the delta, and D3_LANDING_LEAD_SEC is
0.75s -- very nearly the whole lead the cut takes before the landing.
On upload 826 the same mechanism was worth 2.07s and reported a 6.6s
flight as 8.4s.

Each Pi now ships its own offset with the clip, so the skew can be
subtracted. The rules this pins down:

  * both offsets present -> the skew comes out, source says so
  * ONE offset present   -> change nothing. Correcting one clock and
                            not the other moves the delta by that
                            clock's whole offset, which is worse than
                            leaving it alone.
  * neither              -> exactly the old behaviour, byte for byte
  * an operator's own measurement still outranks all of it

    python3 tools/clock_skew_test.py
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.routers import admin as A  # noqa: E402

FAILED = []
T0 = datetime(2026, 10, 26, 16, 0, 0)


class FakeDB:
    def __init__(self, ev):
        self.ev = ev

    def get(self, _model, _id):
        return self.ev


def db_and_row(green_lag, tee_off=None, tee_err=None,
               green_off=None, green_err=None, edit_metrics=None):
    ev = SimpleNamespace(
        tee_recording_started_at=T0,
        green_recording_started_at=T0 + timedelta(seconds=green_lag),
        tee_clock_offset_sec=tee_off, tee_clock_error_sec=tee_err,
        green_clock_offset_sec=green_off, green_clock_error_sec=green_err,
    )
    row = SimpleNamespace(id=909, camera_event_id=7,
                          edit_metrics=edit_metrics or {})
    # _d3_green_delta_sec takes (db, row), in that order.
    return FakeDB(ev), row


def check(label, got, want, tol=1e-6):
    ok = (abs(got[0] - want[0]) <= tol) and got[1] == want[1]
    print("%-52s %+.3fs %-18s %s"
          % (label, got[0], got[1], "ok" if ok else
             "FAIL (want %+.3fs %s)" % want))
    if not ok:
        FAILED.append(label)


# The real numbers. The green clip genuinely started 1.000s after the
# tee; the clocks make it LOOK like 1.686s.
APPARENT = 1.0 + (0.668 - (-0.018))
check("no offsets at all — old behaviour, skew intact",
      A._d3_green_delta_sec(*db_and_row(APPARENT)),
      (APPARENT, "camera_event"))

check("both offsets — the 0.686s skew comes out",
      A._d3_green_delta_sec(*db_and_row(
          APPARENT, tee_off=-0.018, tee_err=0.103,
          green_off=0.668, green_err=0.759)),
      (1.0, "camera_event+clock"))

check("green offset only — must NOT half-correct",
      A._d3_green_delta_sec(*db_and_row(
          APPARENT, green_off=0.668, green_err=0.759)),
      (APPARENT, "camera_event"))

check("tee offset only — must NOT half-correct",
      A._d3_green_delta_sec(*db_and_row(
          APPARENT, tee_off=-0.018, tee_err=0.103)),
      (APPARENT, "camera_event"))

check("both clocks perfect — correction is a no-op",
      A._d3_green_delta_sec(*db_and_row(
          1.0, tee_off=0.0, tee_err=0.0, green_off=0.0, green_err=0.0)),
      (1.0, "camera_event+clock"))

# upload 826: 2.07s of skew, the one that reported 6.6s of flight as 8.4s
check("upload 826's 2.07s skew",
      A._d3_green_delta_sec(*db_and_row(
          1.0 + 2.07, tee_off=0.0, tee_err=0.05,
          green_off=2.07, green_err=0.40)),
      (1.0, "camera_event+clock"))

# An operator who marked the strike in both views still wins.
check("operator's own measurement still outranks the clocks",
      A._d3_green_delta_sec(*db_and_row(
          APPARENT, tee_off=-0.018, tee_err=0.103,
          green_off=0.668, green_err=0.759,
          edit_metrics={"green_impact_frame": 2421,
                        "tee_green_delta_sec": -6.62})),
      (-6.62, "operator"))

# A zero offset is a real measurement and must not read as "missing".
check("a measured ZERO offset is still a measurement",
      A._d3_green_delta_sec(*db_and_row(
          1.5, tee_off=0.0, tee_err=0.02, green_off=0.0, green_err=0.02)),
      (1.5, "camera_event+clock"))

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("ALL PASS")
