#!/usr/bin/env python3
"""Find/Produce ascents is four steps, and they run per swing.

THE ORDER WAS INVERTED. The run used to ask the cutter for a WINDOW,
sweep it, chain what it found, and work backwards from the chains to
how many balls had left. On the clip that prompted this, one strike
produced ELEVEN chains "seen leaving" -- eleven candidate clips for
one swing -- and which one produced came down to a list position.

The cutter is the thing that knows where the swings are, so it is now
asked that directly:

  1. the cutter finds the swings, and sets each impact frame
  2. per swing: the best chain above the skyline inside the cone,
     graded by the gates; failing that, the cone accumulator below it
  3. per swing: the descent
  4. per swing: the tee spot, the tracer and the clip

Everything else is parked, not deleted.

    python3 tools/ascent_four_steps_test.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "backend" / "app" / "routers" / "admin.py").read_text()
JSX = (ROOT / "frontend" / "src" / "pages"
       / "AdminProduction.jsx").read_text()
D3 = (ROOT / "backend" / "app" / "services" / "debug3.py").read_text()

FAILED = []


def check(label, got, want=True):
    ok = got == want
    print("  %-58s %s" % (label, "ok" if ok else "FAIL got %r" % (got,)))
    if not ok:
        FAILED.append(label)


def _live(text):
    """Only the code that RUNS.

    The removed logic is parked as comments rather than deleted, at the
    operator's request, so a plain substring search over this file
    finds `_fill_climb_to_ball(` in a line that begins with a `#` and
    concludes the run still calls it. Every assertion below is about
    what executes.
    """
    return "\n".join(ln for ln in text.split("\n")
                      if not ln.lstrip().startswith("#"))


_run_all = SRC[SRC.index("def _ascent_run("):]
_run_all = _run_all[:_run_all.index("\ndef ", 10)]
_run = _live(_run_all)

# STEP 3 LIVES IN ITS OWN FUNCTION. `_ascent_run` calls it and nothing
# more, so assertions about the window and the matcher belong here;
# scoped to `_run` they pass vacuously or fail for the wrong reason.
_desc_all = SRC[SRC.index("def _ascent_descents("):]
_desc_all = _desc_all[:_desc_all.index("\ndef ", 10)]
_desc = _live(_desc_all)

print("the run is four steps, numbered as the operator numbered them:")
_titles = re.findall(r'_stage\((\d), "([^"]+)"', _run)
# ONE STEP MAY HAVE TWO SITES, and step 2 now does: the sweep, and the
# early return that reports it was NOT run because step 1 found no
# swing. They are mutually exclusive -- the second returns -- so a run
# still emits one stage 2. What must hold is that the four steps exist
# in order and that no FIFTH number appears.
_nums = [n for n, _ in _titles]
check("the four steps are there", sorted(set(_nums)), ["1", "2", "3", "4"])
check("...in order", _nums, sorted(_nums))
_first = {}
for _n, _t in _titles:
    _first.setdefault(_n, _t)
check("  1 is the cutter", "cutter" in _first["1"].lower(), True)
check("  2 is the ascent", "ascent" in _first["2"].lower(), True)
check("  3 is the descent", "coming down" in _first["3"].lower(), True)
check("  4 is the tracer", "tracer" in _first["4"].lower(), True)
# AND THE SECOND SITE IS THE NOT-RUN ONE, which must say so rather
# than reporting an empty sweep as a sweep that found nothing.
_twos = [t for n, t in _titles if n == "2"]
check("  step 2 has a second, not-run site", len(_twos), 2)
check("  ...and both name the ascent",
      all("ascent" in t.lower() for t in _twos), True)
# EVERY STEP REPORTS ITS OWN TIME, and a step that passes none reads
# as instant. Checked by walking each `_stage(` call to its closing
# paren rather than matching "...- _t)" as a literal: the seconds are
# no longer the last argument now that `work=` follows them, and an
# assertion that depends on argument ORDER goes quiet the moment one
# is added rather than failing honestly.
def _calls(text, name):
    for m in re.finditer(name + r"\((\d),", text):
        d = 0
        for j in range(m.start(), len(text)):
            if text[j] == "(":
                d += 1
            elif text[j] == ")":
                d -= 1
                if d == 0:
                    yield m.group(1), text[m.start():j + 1]
                    break


_timed = [n for n, c in _calls(_run, "_stage")
          if "time.perf_counter() - _t" in c]
# EVERY SITE IS TIMED, including step 2's not-run one: a step that
# reports no seconds reads as instant, and "the sweep was skipped"
# still took however long step 1 and the window decision took to get
# there.
check("every step is timed", sorted(set(_timed)), ["1", "2", "3", "4"])
check("...and so is step 2's not-run site",
      len([n for n in _timed if n == "2"]), 2)

print()
print("STEP 1 — the cutter finds the swings, not a window:")
check("it asks the cutter for swings",
      "_ascent_cutter_swings(" in _run, True)
_s1 = _live(SRC[SRC.index("def _ascent_cutter_swings("):
                SRC.index("def _ascent_run(")])
# A POSITIVE CLAIM, not the absence of the old one: `_ct[0]` appears
# in this function's own docstring, explaining what it replaced, and
# an assertion that the string is gone fails on the explanation.
check("  it loops over every strike the cutter reported",
      bool(re.search(r"for _i, _t in enumerate\(sorted\(", _s1)), True)
check("  appending one swing for each",
      'out["swings"].append(sw)' in _s1, True)
# THE PEAK IS NOT THE IMPACT, and the whole reason step 1 is more than
# a call to the cutter is that the peak runs ~19 frames late at 60fps.
check("the measured frame is preferred",
      'measured_impact_frame' in _s1, True)
check("  and the peak less the audio lag is the fallback",
      "_heard - _lag" in _s1, True)
check("  with the swing saying which of the two it got",
      '"impact_from"' in _s1 and '"measured"' in _s1, True)

print()
print("STEP 2 — one ascent per swing, graded by the gates:")
check("each swing gets its own span",
      "_spans.append((_f0, (_f0 + _span_max if _f1 is None" in _run, True)
# ...AND THAT SPAN HAS A CEILING. Without one the last swing's span
# ran to the end of the footage — see tools/ascent_span_test.py.
check("  bounded after the strike, not just before it",
      "_ascent_span_verdict(" in _run and "_span_max" in _run, True)
check("  chains are graded fewest-refusals then longest",
      bool(re.search(r'len\(c\.get\("why"\) or \[\]\), '
                     r'-int\(c\.get\("n_points"\)', _run)), True)
check("  and exactly one survives", "_best = sorted(_cands, key=_grade)[0]"
      in _run, True)
# A SWING THAT KEEPS NOTHING IS NOT A SWING WITH NO ANSWER.
check("nothing above the skyline falls to the accumulator",
      "_ascent_cone_accumulate(" in _run, True)
check("  and the losers are reported, not dropped silently",
      '_sw["considered"] = sorted(' in _run, True)

print()
print("STEP 4 — the tee spot is the ball that was watched leaving:")
# THE RED RING ON THE TILES. An observation of the pixel the ball left
# from beats any extrapolation of it.
check("the measured ball is preferred",
      '"measured — the ball was watched sitting at this spot"' in _run
      or "measured — the ball was watched sitting" in _run, True)
check("  the chain run back is the fallback",
      "_ascent_tee_spot(" in _run, True)
check("  and the card is told which one it got",
      '_entry["ball_from"]' in _run, True)
check("step 1's frame beats the chain's own back-run",
      '_entry["impact_frame"] = int(_sw["impact_frame"])' in _run, True)
check("  keeping the estimate beside it",
      '_entry["impact_frame_est"]' in _run, True)

print()
print("and what was removed is PARKED, not deleted:")
# The operator asked for it commented out rather than gone, so the
# reasoning in it survives for whoever needs it next.
for _fn in ("_fill_climb_to_ball", "_ascent_apply_ai_launch",
            "_ascent_pin_rig_impact", "_collapse_one_ball"):
    check("  %s is still defined" % _fn, ("def %s(" % _fn) in SRC, True)
    check("    ...but the run no longer calls it",
          ("    %s(" % _fn) in _run or ("= %s(" % _fn) in _run, False)
check("the old stage 1 is kept as comment",
      "PARKED: THE OLD STAGE 1" in SRC, True)
check("  and so are the stage-3 extras",
      "PARKED: the stage-3 extras" in SRC, True)

print()
print("THE WORK IS SHOWN UNDER THE STEP THAT DID IT:")
# IT USED TO LIVE IN CARDS BESIDE THE STEP LIST, so the row saying a
# step took 24 seconds and the picture showing what it did with them
# were in different parts of the page with nothing tying them
# together. Every step now carries its own `work`, and the panel
# renders it behind a toggle on that step's own row.
check("every step can carry its work",
      "def _stage(n, title, detail, count=None, units=None, secs=None,"
      in SRC and "work=None):" in SRC, True)
check("  and so can every sub-step",
      SRC.count("work=None):") >= 2, True)
check("step 1 hands over the cutter's pictures and numbers",
      "work=_work(_ascent_step1_work," in _run, True)
check("  including the motion heat the swing judge reads",
      '(ai.get("swing_check") or {}).get("image_url")' in SRC, True)
# EVERY PEAK IT WEIGHED, not only the ones it kept. On this course the
# attack is what separates a strike from range noise, so a peak
# refused for a slow attack is the most useful row on the page when a
# swing goes missing.
check("  and every peak it weighed, with why each was kept or refused",
      '"attack ok?"' in SRC and '"refused"' in SRC, True)
# NOTHING AT THE ROOT OF STEP 2. The two searches are 2a and 2b, and
# the work belongs to whichever of them did it; a summary on the step
# repeats one and contradicts the other the moment they disagree.
_step2 = next(c for n, c in _calls(_run, "_stage") if n == "2")
check("step 2 itself carries no work", "work=" in _step2, False)
check("  2a carries the sweep above the skyline",
      '_sub("2a", "Sweeping for chains above the skyline"' in _run, True)
check("  with every chain that was weighed, and each gate's reading",
      "_ascent_chain_table(sweep.get(\"considered\"))" in _run, True)
check("  2b is the cone, and only when 2a found nothing",
      '_sub("2b", "The cone search, below the skyline"' in _run
      and "skipping the cone search" in _run, True)
check("  2c is the detections around the best chain",
      '_sub("2c", "Every detection around the best chain"' in _run, True)
check("step 3 hands over the green's window views",
      "work=_work(_ascent_step3_work," in _run, True)
# `image_url`, NOT `image`: this read the wrong key on its first
# writing and rendered nothing, which from outside looks exactly like
# a descent that drew no picture.
check("  reading image_url, the key the view actually has",
      'v.get("image_url")' in SRC, True)
# AND THE TEST THAT NARROWED IT, beside what it narrowed: the
# before/after frames the green's rest check drew.
check("  with the rest test's own before and after",
      '_rest_imgs(rep, v, "before")' in SRC, True)
check("  with the frames searched and the chains weighed",
      '"frames searched"' in SRC and '"chains weighed"' in SRC, True)
check("step 4 hands over the tee spot and the clip",
      '"tee spot — measured"' in _run, True)
check("and the sweep's pictures hang off 2a, the search that drew them",
      'sweep.get("considered_image_url")' in _run, True)

print()
print("STEP 3 LOOKS AT THE WHOLE WINDOW THE WIZARD DOES:")
# FIVE-TO-EIGHT WAS A GUESS ABOUT THE SHOT, AND IT MISSED ONE. On
# #1780 the band placed f1326-f1626 and reported no descent among 237
# tracks, while the edit wizard scanning f1283-f1763 drew the arc
# plainly at f1572-f1763. A hundred and thirty-seven frames of the
# landing were never looked at.
check("the window is the wizard's, not a flight-time guess",
      "_green_scan_window(" in _desc, True)
check("  padded past the end of the cut, as the wizard pads it",
      "GREEN_SCAN_PAD_SEC" in SRC, True)
check("  with the old band kept as the fallback",
      "FLIGHT_LO_SEC" in _desc and "no wide window could" in _desc, True)
check("  and the step says which of the two it used",
      '"landing_window_from"' in SRC, True)

print()
print("...and the ball at rest narrows it back down:")
# A WIDE WINDOW ADMITS MORE RUBBISH, and this is what pays for it.
# 3b already measures the one thing on the green that was not there
# before the shot; a descent is the path that ARRIVES AT IT. Used
# only as a gate, that measurement said whether to look and never
# where, so a wind-shaken branch in the right seconds competed with
# the real ball on equal terms.
check("a chain must finish near where the ball came to rest",
      "DESCENT_NEAR_REST_PX" in _desc, True)
check("  and the refused ones are counted, not dropped silently",
      '"rest_narrowing"' in _desc, True)
# NO REST SPOT, NO NARROWING. The test fails honestly on a ball that
# lands off the green, and the whole window is then searched as
# before. What it must never do is narrow onto a spot it did not find.
check("  no rest spot means no narrowing at all",
      "if _rs:" in _desc, True)
# Matched on the half that is not split across a source line: the
# sentence is written as two adjacent literals, so the contiguous
# string never appears in the file.
check("  which the step says out loud",
      "whole window was searched" in SRC
      and "nothing was found at rest" in SRC, True)

print()
print("the descent table asks a DESCENT's questions:")
# IT REUSED THE ASCENT TABLE AND PRINTED A WALL OF None. They are
# different measurements: an ascent is judged on how fast and how
# straight it RISES out of a known spot, so it carries rate, bend,
# density and a traced-back origin; a descent is judged on how far it
# FELL and how much survived trimming, and has no origin to trace back
# to. Twelve columns of None reads as a detector that measured
# nothing, rather than a table asking the wrong questions.
check("there is a table of its own", "def _descent_chain_table(" in SRC,
      True)
check("  and step 3 uses it, not the ascent one",
      "_descent_chain_table(v.get(\"chains\"))" in SRC, True)
for _col in ("fall", "drop", "off line", "ends at"):
    check("  it has a %r column" % _col, '"%s"' % _col in SRC, True)
check("  and does not ask a descent for its rise rate",
      '"rate"' in SRC.split("def _descent_chain_table(")[1]
      .split("def ")[0], False)

print()
print("THE SEARCH AREA NARROWS, not just the results:")
# "The ball landed on the right of the green" means this swing's
# descent came down the right of the FRAME, so every detection on the
# left is a branch, a shadow or a mower. Filtering finished chains
# still pays to link all of them, and on a windy green most of what
# gets linked is foliage -- narrowing before linking is what makes
# the wide window affordable.
_fd = D3[D3.index("def find_descents("):]
_fd = _fd[:_fd.index("\ndef ", 10)]
check("find_descents takes where the ball came to rest",
      "near_xy: tuple | None = None" in _fd, True)
check("  and drops detections before linking, not chains after",
      _fd.index("dets = [_d for _d in dets") < _fd.index("tracks = build_tracks("),
      True)
check("  a column, not a circle — the sideways constraint is the one"
      " that means anything",
      'abs(float(_d.get("x", 1e9)) - _nx)' in _fd, True)
check("  it reports what it dropped", '"near_why"' in _fd, True)
# A MERGED WINDOW CAN BELONG TO MORE THAN ONE SWING, and narrowing it
# onto one swing's rest spot would hide another's descent.
check("a shared window only narrows when the swings agree",
      "_window_covers(" in SRC, True)
check("  and never narrows onto a spot nobody measured",
      "if near_xy and dets:" in _fd, True)

print()
print("and the green's detections are drawn on the frame:")
# THE BARE FRAME WAS NEVER THE PICTURE. What makes the wizard's view
# readable is the dots, numbered, and the server has carried them all
# along with nothing drawing them.
check("the view's dots are handed over", '"dotmap"' in SRC, True)
check("  with the ball at rest marked on it", '"mark"' in SRC, True)
check("  and the page draws them over the frame",
      "const DotMap = ({ m })" in JSX, True)
check("  labelling every nth, so a busy green stays readable",
      "m.dots.length || 1) / 60" in JSX, True)
check("  and the bare frame is dropped when the dots carry it",
      "(None if (v.get(\"dots\") or []) else v.get(\"image_url\"))" in SRC,
      True)

print()
print("the reverse walk from the ball is reported under step 3:")
# ON A CLEAN GREEN THE DOT MAP MAKES THE DESCENT OBVIOUS TO A PERSON:
# a short near-vertical run arriving at the ring, with the treeline's
# foliage everywhere else. `track_descent_back` is the machine reading
# it the same way -- start ON the ball at rest and climb, zooming each
# window so a three-pixel ball is something a detector can see.
#
# Its report lived in a card beside the step list, which is the one
# place an operator reading step 3 does not look.
check("step 3 carries the walk's result",
      'desc.get("descents_back")' in SRC, True)
check("  how many points it got, and how many it missed",
      '"%s point(s), %s missed, fell %spx "' in SRC, True)
check("  the zoom it climbed at", '"label": "zoom"' in SRC, True)
check("  where the ball first moved and where it was lost",
      '"ball first moved"' in SRC and '"lost it at"' in SRC, True)
check("  and the picture of the windows it walked",
      '(_wk or {}).get("image_url")' in SRC, True)
# "1 WALKED" AND A NUMBER OF SECONDS SAYS A WALK HAPPENED. It does not
# say where it looked, how far it was willing to match, or why it
# stopped — and a walk that stops early is the whole question on a
# green where the descent is plain to the eye.
check("  and one row per frame it climbed",
      "def _walk_log_table(" in SRC, True)
for _col in ("looked at", "window", "zoom", "within", "cands",
             "nearest", "took"):
    check("    with its %r column" % _col, '"%s"' % _col in SRC, True)
# THE LAST ROWS BEFORE IT WAS LOST ARE THE INTERESTING ONES: an empty
# window means the detector saw nothing there at all, while candidates
# present and none taken means something was there and was refused for
# being too far from where the ball had to be.
check("    showing the steps nearest where it stopped",
      "_rows = _log[-cap:]" in SRC, True)
check("    and saying when it is only part of the walk",
      '"the last %d of %d steps"' in SRC, True)
check("    which the page prints above the table",
      "{t.note && (" in JSX, True)
# The chain table is not displaced by it — both are shown.
check("  without displacing the chains table",
      '"table2"' in SRC and "<Table t={g.table2} />" in JSX, True)
# IT IS MATCHED BY SWING, not by position: `descents_back` only holds
# rows for swings that had a rest spot, so index i is not swing i.
check("matched to its swing by number, not by position",
      'int(w.get("swing") or 0) == int(v.get("swing") or -1)' in SRC, True)

print()
print("A CHAIN THAT FINISHES ON THE BALL IS THE DESCENT:")
# THE WALK WAS SWITCHED OFF ON EVERY DWELL CLIP, which on a rig with
# the audio trigger off is every clip there is: `_old_way = (_kind ==
# "dwell")` and the walk looped over `_clips if not _old_way else []`.
# The reason given was that a dwell's impact frame is FITTED, and "the
# walk and the crawl are anchored on that frame all the way down".
#
# They are not. The walk starts at `_rf`, which comes from the rest
# WINDOW — the rung of the after-ladder the before/after test actually
# found the ball on — and the impact frame cancels out of that
# arithmetic. Its anchor is a measurement of the ball.
check("the walk runs for every swing with a ball at rest",
      "for i, c in enumerate(_clips):" in _desc, True)
check("  not only on non-dwell clips",
      "_clips if not _old_way else []" in _desc, False)
check("  still skipping a swing with no ball to walk from",
      'if not c.get("rest_spot") or c.get("impact_frame") is None:'
      in _desc, True)

print()
print("...and then 3g is skipped, because there is nothing to find:")
# Searching anyway is expensive — 82% of this step — and actively
# harmful: it puts branch chains in front of a matcher that has
# already been handed the right answer.
check("the old search runs only for swings the walk did not solve",
      "_unsolved = [c for c in _clips" in _desc, True)
check("  keyed on the walk having succeeded",
      'if w.get("ok")' in _desc, True)
check("  and on no track already being there",
      'not c.get("green_track")' in _desc, True)
check("  nothing unsolved means nothing searched",
      "_do_descent = bool(_unsolved)" in _desc, True)
check("the step says whether it ran, and why",
      '"the 3g search"' in SRC, True)
# AND THE LABELS REPORT THE OUTCOME, NOT THE FLAG. 3c's units read
# "walked — SKIPPED, this is a dwell clip and gets the older
# whole-window search instead" beside a count of 1 and 57.6 seconds of
# work: the walk had started running on dwell clips and only the label
# still believed otherwise. A label keyed on a flag rather than on
# what happened lies the moment the flag stops deciding anything.
check("3c's label is keyed on what it walked, not the trigger kind",
      '"walked" if out.get("n_descent") else' in _desc, True)
check("  with no trigger-kind text left on it",
      "this is a dwell clip and gets the older" in _desc, False)
check("the method prose is rewritten from what happened",
      "swing(s) were walked back from their own ball at rest" in _desc,
      True)
# The provisional values set at the top must still exist, because an
# early return can happen before the rewrite.
check("  but a provisional one survives the early returns",
      '"search" if _old_way else "walk+crawl"' in _desc, True)
check("  and which swings were walked instead",
      '"descent_skipped_for"' in SRC, True)

print()
print("and the page renders it, collapsed, on the step's own row:")
check("there is one generic renderer",
      "function StepWork({ work })" in JSX, True)
check("  behind a toggle", "function WorkToggle({ work, label })" in JSX,
      True)
check("  on each step", "<WorkToggle work={r.work} />" in JSX, True)
check("  and on each sub-step",
      "{sub.work && <WorkToggle work={sub.work} />}" in JSX, True)
check("  collapsed by default", "useState(false)" in JSX, True)
# ONE PLACE, NOT TWO. The per-swing card that used to sit beside the
# step list said the same things in a different order; two panels
# that must agree are one more thing to keep in agreement.
check("the duplicate per-swing card is gone",
      "AscentSwingSteps" in JSX, False)
# AND SO IS EVERY OTHER PANEL BELOW THE STEPS. They showed the same
# tiles, cone and per-swing narrative in a different order; two places
# saying one thing is one more pair to keep in agreement.
check("the legacy panels are gated off behind one flag",
      "const SHOW_LEGACY_PANELS = false;" in JSX, True)
check("  and every one of them is behind it",
      JSX.count("SHOW_LEGACY_PANELS && ") >= 5, True)

print()
print("A SAVE THAT REFUSES THIS RUN'S NUMBER MUST SAY SO:")
# THE PANEL AND THE WIZARD DISAGREED AND NOTHING EXPLAINED IT. The
# step list shows what this run measured; the wizard reads what was
# SAVED, and `_d3_save_swing` keeps a prior hand-set impact frame or
# ball over anything a produce measured. That is right — an operator
# placed it by eye — and it was silent, so the report showed f1286 and
# chain #1 while the wizard showed f14xx and chain #2.
check("the save reports what it kept from a prior record",
      "kept_out: dict | None = None" in SRC, True)
check("  for a hand-set impact frame",
      'kept_out["impact_frame"] = {' in SRC, True)
check("  and a hand-placed ball", 'kept_out["ball"] = {' in SRC, True)
# ONLY WHEN THEY DIFFER. Reporting a prior value that agrees with the
# measurement is noise on every clip anyone has ever touched.
check("  only when it differs from what was measured",
      '!= int(rec["impact_frame"])' in SRC, True)
check("and step 4 prints it beside the number it measured",
      '"SAVED %s" % k' in SRC, True)
# THE SUB-STEP TIMINGS FOLLOW THE STEP THEY BELONG TO. The descent's
# own breakdown was keyed to stage 4; the descent is step 3 now, and a
# stale key does not error -- the breakdown just stops appearing.
check("the descent's sub-steps are keyed to step 3",
      "3: rep?.descent_stages" in JSX, True)

print()
if FAILED:
    print("FAILED: %s" % ", ".join(FAILED))
    sys.exit(1)
print("all ok — four steps, per swing, each one timed")
