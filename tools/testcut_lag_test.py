#!/usr/bin/env python3
"""A lag typed into Test Cut must move the pictures, and only this run.

ASKED FOR: "on the long-upload / test cut page, can we add an audio
lag field here, where I can put .8 second lag (for example) and have
the images grab based on that."

WHY IT MOVES THE PICTURES. The microphone hears the strike late, so
Test Cut cuts everything around

    _strike_sec = peak_time_sec - _blag

and every frame it draws — the six tiles, the ball strip, the heat
composite, the ball checks — is taken from that instant. The lag
decides which frames an operator is shown. Until now the only way to
try another was to edit the hole's stored gate, run, and put it back.

THE TWO THINGS THIS MUST NOT DO:

  1. Make a typo read as a lag. An unparseable or out-of-range value
     falls back to the hole's gate; it never becomes 0.0, which is a
     real lag meaning "the sound IS the strike" and would cut every
     picture at the audio peak.

  2. Accept a value in silence when it was refused. Type 0.8, be
     given the hole's 0.6 with nothing said, and every picture below
     is read as evidence about 0.8.

    python3 tools/testcut_lag_test.py
"""
import inspect
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.routers.admin import _test_cut_run, test_cut_long_upload  # noqa

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


SRC = inspect.getsource(_test_cut_run)
EP = inspect.getsource(test_cut_long_upload)

print("\n1. the field exists, all the way down")
check("the endpoint takes it", "audio_lag_sec" in EP, True)
check("as a STRING, so empty can mean the hole's gate",
      'audio_lag_sec: str = Form("")' in EP, True)
check("and hands it to the runner", "audio_lag_sec=audio_lag_sec," in EP,
      True)
check("the runner takes it", "audio_lag_sec=''" in SRC, True)

print("\n2. it is parsed once, not per peak")
check("parsed before the loop over peaks",
      SRC.index("_lag_override = None") < SRC.index("for idx"), True)
check("...so two peaks cannot be cut around different lags",
      SRC.count("_lag_override = float("), 1)

print("\n3. a bad value is the hole's gate, never a zero")
# Re-run the parse the way the function does, over the values an
# operator can actually type.
def parse(v):
    _o, _w = None, None
    if str(v or "").strip():
        try:
            _o = float(str(v).strip())
            if _o < 0 or _o > 3.0:
                _w = "out of range"
                _o = None
        except (TypeError, ValueError):
            _w = "not a number"
            _o = None
    return _o, _w


for _v, _want, _lbl in (
        ("0.8", 0.8, "0.8 is taken"),
        (".8", 0.8, "a bare .8 is taken"),
        (" 0.8 ", 0.8, "spaces are trimmed"),
        ("0", 0.0, "an explicit 0 is a real lag and is taken"),
        ("", None, "empty is the hole's gate"),
        ("   ", None, "whitespace is the hole's gate"),
        ("0.8s", None, "a stray unit is refused"),
        ("abc", None, "nonsense is refused"),
        ("-1", None, "a negative lag is refused"),
        ("30", None, "half a minute of lag is refused"),
):
    check("  %s" % _lbl, parse(_v)[0], _want)
check("and a refusal always carries a reason",
      all(parse(v)[1] for v in ("0.8s", "abc", "-1", "30")), True)
check("...while an accepted value carries none",
      all(parse(v)[1] is None for v in ("0.8", "", "0")), True)

print("\n4. it beats the gate, and touches nothing stored")
check("applied after the hole's gate is resolved",
      SRC.index('_blag = _bgates["audio_lag_sec"]')
      < SRC.index("_blag = _lag_override"), True)
check("...and before the instant everything is cut around",
      SRC.index("_blag = _lag_override")
      < SRC.index("_strike_sec = peak_time_sec - float(_blag)"), True)
check("the gate itself is never written",
      bool(re.search(r"set_gate|save_gate|gates\[.audio_lag_sec.\]\s*=",
                     SRC)), False)
check("the cut says which lag it got",
      'cut_entry["ball_lag_from"]' in SRC, True)

print("\n5. the run reports it, even with the judge off")
check("at the top level, not only on a cut",
      '"audio_lag_sec": _lag_override,' in SRC, True)
check("with the refusal beside it",
      '"audio_lag_warning": _lag_override_why,' in SRC, True)

print("\n6. the page sends it and shows what happened")
API = (ROOT / "frontend/src/api.js").read_text()
JSX = (ROOT / "frontend/src/pages/AdminLongUpload.jsx").read_text()
check("sent only when the box has something in it",
      'if (opts.audioLagSec != null && String(opts.audioLagSec).trim())'
      in API, True)
check("the box is a string field", "const [audioLag, setAudioLag]" in JSX,
      True)
check("empty is labelled as the hole's", 'placeholder="hole"' in JSX, True)
check("sent for every detector, not just the audio ones",
      JSX.index('if ((audioLag[uploadId] || "").trim())')
      > JSX.index("if (detectOnly[uploadId]) opts.cutClips = false;"), True)
check("the result says what the pictures were cut around",
      "(typed, this run only)" in JSX, True)
check("and a refused value is called out, not swallowed",
      "audio_lag_warning" in JSX and "lag not used" in JSX, True)

print()
if FAILED:
    print("FAILED (%d):" % len(FAILED))
    for f in FAILED:
        print("  -", f)
    sys.exit(1)
print("all ok")
