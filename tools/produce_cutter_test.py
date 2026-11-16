#!/usr/bin/env python3
"""The cutter an operator verified is the cutter production runs.

Test Cut can run five detectors and show exactly what each would cut.
Production could run none of them: it swept the whole clip unless the
RIG had heard an impact, and the one narrowing it did have -- the
clip's own audio -- was hard-wired ON for manual uploads and hard-wired
OFF for dwell clips. So the detector somebody had just verified on a
hole could not be the one used on it.

THE RULE THIS SITS UNDER, from `_ascent_windows`: narrowing onto the
wrong two seconds returns a clip with no ball in it and reads exactly
like one, while scanning too much only costs time. So a cutter may
narrow and must never hide: one that finds nothing, or throws, falls
back to the full sweep.

    python3 tools/produce_cutter_test.py
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "backend" / "app" / "routers" / "admin.py"
TEXT = SRC.read_text(encoding="utf-8")
TREE = ast.parse(TEXT)

FAILED = []


def check(label, got, want):
    ok = got == want
    print("  %-62s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


def fn(name):
    return next((n for n in ast.walk(TREE)
                 if isinstance(n, ast.FunctionDef) and n.name == name), None)


def body(name):
    f = fn(name)
    return ast.get_source_segment(TEXT, f) if f else ""


print("\n-- the menu is one definition, and auto is the default")
check("the cutters are declared in one place",
      "PRODUCE_CUTTERS = {" in TEXT, True)
for name in ("auto", "none", "audio", "motion", "combined", "ballgone",
             "pi"):
    check("  %s is offered" % name, '"%s":' % name in TEXT, True)
_cf = body("_cutter_for")
check("nothing stored means auto", 'return "auto", ' in _cf, True)
check("  and an unrecognised name means auto too",
      "not in PRODUCE_CUTTERS" in _cf, True)


print("\n-- it runs the SAME functions Test Cut runs")
# A second copy of the arithmetic would make "what you verified is what
# runs" quietly false.
_ct = body("_cutter_times")
for f in ("detect_swings_from_audio", "detect_swings_from_motion",
          "detect_swings_combined", "find_ball_vanish",
          "detect_swings_pi"):
    check("  %s" % f, f in _ct, True)
check("the audio cutter uses the hole's gates",
      "_gate_tuning_for(db, row)" in _ct and "resolve_gates" in _ct, True)


print("\n-- it may narrow; it may never hide")
_aw = body("_ascent_windows")
check("a chosen cutter is consulted", "_cutter_for(db, row)" in _aw, True)
# THE ORDER THAT MATTERS is where the cutter BRANCH sits, not where
# the lookup does -- the rig's confirmed impact must still return
# before any search of the clip is run.
check("  after the rig's own impact, which stays first",
      _aw.index("_rig_impact_sec") < _aw.index('if _cut != "auto":'),
      True)
check("finding nothing sweeps the whole clip",
      "sweeping the \\nwhole clip" in _aw or "sweeping the whole clip" in _aw,
      True)
check("  and a cutter that THROWS also sweeps",
      "the %s cutter failed" in _aw, True)
check("  'none' sweeps on purpose",
      'if _cut == "none":' in _aw, True)
# CONTROL: the default path is untouched. "auto" must still reach the
# dwell branch that was argued for earlier.
check("control, auto still falls through to the dwell rule",
      'if _cut != "auto":' in _aw and '_kind == "dwell"' in _aw, True)


print("\n-- saving one does not clear the gates, or vice versa")
_sv = body("set_gate_tuning")
check("an unknown cutter is refused", "unknown cutter" in _sv, True)
check("the stored cutter survives a gate-only save",
      '_prev_cut = (store.get(key) or {}).get("cutter")' in _sv, True)
check("  and auto clears it rather than storing a word",
      'None if _cutter == "auto" else _cutter' in _sv, True)
check("the audit line says which cutter", "cutter=" in _sv, True)


print("\n-- and the editor is told what exists")
_gt = body("get_gate_tuning") or TEXT
check("the current choice is served", '"cutter": _cutter_for' in TEXT, True)
check("  with the menu beside it", '"cutters": [{"name": k' in TEXT, True)

print("\n-- every path NAMES the cutter, including the ones that ignore it")
# A cutter that ran and one that was never consulted looked identical:
# the only tell was the clip coming out different, and on a sweep that
# found the same swing anyway it did not. "auto, and the rig had
# already heard it" is an answer; silence is not.
check("the lookup happens once, for every path",
      '_cw = {"cutter": _cut, "cutter_why": _cut_why}' in _aw, True)
check("  and the window record carries the extras",
      'dict({"detail": msg, "spans": spans}, **extra)' in _aw, True)
for path, needle in (
        ("the rig's impact", 'ran="the rig\'s own impact"'),
        ("a chosen cutter that found cuts", 'ran="the %s cutter" % _cut'),
        ("one that found nothing", "which found nothing"),
        ("one that threw", "which failed"),
        ("auto skipping a dwell clip", "auto skips a dwell clip"),
        ("auto's own audio pass", 'ran="auto\'s audio pass"'),
        ("set to sweep on purpose", 'ran="nothing, by choice"')):
    check("  %s" % path, needle in _aw, True)
check("the times it cut at travel too", "times=[round(t, 2)" in _aw, True)


print("\n-- was there a SWING where it cut")
_vc = body("_verify_cut")
_vr = body("_verify_crop")
check("one definition of where to look", bool(_vr), True)
check("  and Test cut uses it",
      "_crop, _zspan, _zfloor, _zbox = _verify_crop(db, row)" in TEXT, True)
check("  with no second copy of the zone arithmetic",
      TEXT.count("_x0 - _w, _y0 - 3 * _h, 3 * _w, 4 * _h"), 1)
check("the judge is the same one", "swing_heat_check" in _vc, True)
check("  asked about the golfer", 'style="golfer"' in _vc, True)
check("  and it never takes the run down",
      "return {}" in _vc, True)
_ar = body("_ascent_run")
check("a FIND judges its cuts", "_verify_cut(" in _ar, True)
# A PRODUCE MUST NOT PAY FOR IT: each is a vision call on a clip the
# produce is about to search anyway.
check("  and a produce does not", "if not produce and isinstance" in _ar,
      True)
check("  bounded, so a noisy clip cannot fan out",
      "_times[:6]" in _ar, True)
# THE CLOSURE THAT WOULD HAVE BEEN A NameError: `_named_url` is
# defined inside another endpoint, not at module level.
check("it builds urls with the module-level helper",
      "_clip_url_for(_v.get" in _ar, True)
check("  not the closure from another endpoint",
      "_named_url(_v.get" in _ar, False)

print("\n-- and the operator can actually choose one")
JSX = (ROOT / "frontend" / "src" / "components"
       / "GateTuning.jsx").read_text(encoding="utf-8")
check("the editor renders the menu the backend serves",
      "data.cutters" in JSX, True)
check("  rather than a list of its own",
      '"audio"' in JSX and '"ballgone"' in JSX, False)
check("  and shows where the choice came from",
      "data.cutter_why" in JSX, True)
check("choosing one saves it", "saveCutter" in JSX, True)
# A CUTTER SAVE MUST NOT DISCARD TYPED GATES. The backend keeps the
# half it is not told about, but the editor holds unsaved numbers.
check("  and carries the pending gates with it",
      "api.setGateTuning(\n        adminPassword, uploadId, pending(), name)"
      in JSX, True)
PROD = (ROOT / "frontend" / "src" / "pages"
        / "AdminProduction.jsx").read_text(encoding="utf-8")
check("Find ascents shows which cutter ran",
      "rep.window.cutter" in PROD and "rep.window.ran" in PROD, True)
check("  the times it cut at", "rep.window.times" in PROD, True)
check("  and says plainly when it found nothing",
      "cannot have hidden a swing" in PROD, True)
check("Find ascents shows the verdict per cut",
      "rep.window.cuts" in PROD, True)
check("  with the frame strip", "c.frames_image" in PROD, True)
check("  and the judge's reason", "c.reason" in PROD, True)

print("\n-- the cutter's WORKING is shown, not just its conclusion")
# "Cut at 24.78s" is a conclusion. The evidence -- the noise floor, the
# gate that came of it, every peak and which test each one failed -- is
# what an operator needs when the answer is wrong, and Test Cut was the
# only screen with it even though production can now be SET to that
# same cutter.
COMP = ROOT / "frontend" / "src" / "components" / "AudioCutDebug.jsx"
check("there is one audio-debug renderer", COMP.exists(), True)
_c = COMP.read_text(encoding="utf-8")
for field in ("median_envelope", "min_peak_ratio_used", "top_peaks",
              "n_after_attack", "below_gate_peaks", "loudest_ratio"):
    check("  it shows %s" % field, field in _c, True)
check("  and it reads a plain `debug` prop, not a screen's state",
      "testCutResults" in _c, False)
LONG = (ROOT / "frontend" / "src" / "pages"
        / "AdminLongUpload.jsx").read_text(encoding="utf-8")
check("Test cut renders through it", "<AudioCutDebug" in LONG, True)
check("Find ascents renders through it", "<AudioCutDebug" in PROD, True)
# THE WHOLE POINT OF EXTRACTING IT: two copies drift, and the one that
# drifts is the one nobody was reading at the time.
check("  and Test cut no longer has its own copy",
      "noise floor={testCutResults" in LONG, False)
API = (ROOT / "frontend" / "src" / "api.js").read_text(encoding="utf-8")
check("the api sends the cutter only when given one",
      "cutter === undefined ? { gates } : { gates, cutter }" in API, True)

print()
if FAILED:
    print("%d FAILED: %s" % (len(FAILED), ", ".join(FAILED)))
    sys.exit(1)
print("all good")
