# Swing detection on real tee footage — what the signal actually looks like

Run 11 Oct against Rivertowne footage, to answer one question before
building anything: **can the Pi decide which clips contain a swing, so
it uploads 13 s instead of 126 s?**

Short answer: **the signal is there, the existing detector does not
find it, and the noise floor is a foursome rather than the weather.**

---

## What was measured

Four clips, two tee/green pairs with confirmed swings (events 736 and
859), plus two tee clips with no golfer at all (914, 1001) and one
indoor test (1029).

Ground truth came from watching contact sheets frame by frame, not
from the detector.

---

## 1. The existing detector picks the wrong peaks

`detect_swings_combined` on event-736 tee (120 s, foursome, three
visible swings):

| time | what is actually there | ROI ratio | detector reported it? |
|---|---|---|---|
| ~0 s | **swing**, club overhead | 5.16 | no |
| ~50 s | **swing** | **7.24** | no |
| ~60 s | **swing** | 6.41 | no |
| 15.0 s | people milling | 3.72 | **yes** |
| 24.8 s | people milling | 3.73 | **yes** |

It reported the two weakest candidates in the clip and missed the
largest motion event in the first 90 seconds. Every detection across
all four clips came back `confidence: low`.

**The physics works; the selection logic on top of it does not.** That
is the thing to fix, and it is a much smaller job than building a
detector.

---

## 2. The noise floor is other golfers, not weather

```
swing             5 - 7
people milling    2 - 4      <- four players walking, pulling clubs,
quiet             ~1            practice swings
```

About **2:1 separation**, not the 10:1 the detector's own docstring
assumes when it says "the player stands almost still during setup."
With a foursome on the tee that premise is simply false: three other
people moving generate motion comparable to the swing.

This is the real obstacle. It is also why audio matters more than a
video-first design suggests -- a club strike is acoustically unique,
whereas walking looks much like swinging.

---

## 3. ROI scoping helps 10-30%, not 10x

A prediction made before measuring was that scoping the motion metric
to the tee-box ROI would raise a swing's ratio by an order of
magnitude. It does not:

| | whole frame | ROI (0.25-0.68 h) | gain |
|---|---|---|---|
| t=0 s | 3.91 | 5.16 | +32% |
| t=50 s | 6.55 | 7.24 | +11% |
| t=60 s | 4.94 | 6.41 | +30% |

In this footage the golfers are large in frame, so the whole-frame
average was never being swamped. Worth doing, not a fix.

**Where it WOULD matter: a camera aimed down the hole.** See below.

---

## 4. The aim problem, found by accident

Events 914 and 1001 are 21 s and 36 s of tee footage containing **no
golfer at all** -- confirmed on 20-frame contact sheets and a
full-resolution crop of the bottom strip. The camera is pointed down
the fairway at houses and sky; roughly two thirds of the frame is
cloud.

The detector's one "detection" on 1001 (t=30.5 s, ratio 2.79) is
cloud movement.

So two separate events recorded ~57 s of empty golf course and
uploaded it over a failing LTE link. Before tuning any detector,
check what the camera is aimed at and what the trigger zones cover:
no amount of signal processing rescues a frame the golfer is not in.

---

## 5. Where this leaves the "upload only swings" plan

**Viable, with the order changed.**

1. **Fix the peak-picking** in `detect_swings_combined`, and validate
   against the ground truth above rather than against its own
   confidence labels. Cheapest step, biggest gain.
2. **Fix the camera aim / trigger zones.** Free, and currently
   costing whole clips of nothing.
3. **Scope the motion metric to the ROI.** Modest on good framing,
   large on bad.
4. **Add audio as the confirming signal.** With 2:1 video separation,
   video alone will not reliably separate a swing from a practice
   swing or a player walking past. `detect_swings_from_audio` already
   exists, with a 1500 Hz highpass and a 30 ms attack gate.
5. Only then move it to the Pi.

`tools/swing_motion_profile.py` prints the signal for any clip so this
can be re-measured rather than re-argued.
