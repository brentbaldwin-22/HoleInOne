"""Where the Player ID tile's frozen frame gets cut.

The tile and the 9:16 deliverable have to frame the same shot, so the
clamp and the aspect here are the render's own. What is worth pinning is
the arithmetic at the edges: a golfer near the left touchline must not
produce a crop that starts at a negative x and comes back empty, and a
scan that found no tracer must land on centre rather than on a crash.
"""
import os, sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite://")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services import shot_still as S

fails = []


def check(label, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + (f"   {detail}" if detail else ""))
    if not cond:
        fails.append(label)


def main():
    W, H = 1920, 1080
    CW = int(round(H * 9 / 16))          # 608

    print("a 9:16 slice of a 1080p frame")
    x, y, w, h = S.crop_box(W, H, 0.5)
    check("full height, never letterboxed", (y, h) == (0, H), f"y={y} h={h}")
    check("nine-sixteenths of it wide", w == CW, f"{w}")
    check("centred on a centred golfer", x == (W - CW) // 2, f"x={x}")

    print("\nthe golfer stands where the tracer starts")
    xl, _, wl, _ = S.crop_box(W, H, 0.25)
    xr, _, wr, _ = S.crop_box(W, H, 0.75)
    check("left of frame crops left", xl < (W - CW) // 2, f"x={xl}")
    check("right of frame crops right", xr > (W - CW) // 2, f"x={xr}")
    check("and the window keeps its shape either way", wl == wr == CW)

    print("\nthe edges, where an off-by-one shows a blank tile")
    for f in (0.0, 0.05, 0.15, 0.85, 0.95, 1.0):
        x, _, w, _ = S.crop_box(W, H, f)
        ok = x >= 0 and x + w <= W
        check(f"focus {f} stays inside the frame", ok, f"x={x} x+w={x+w} W={W}")

    print("\na scan that found nothing")
    for bad in (None, "", "abc", float("nan")):
        check(f"{bad!r} falls back to centre", S.clamp_focus(bad) == 0.5)
    check("and crops dead centre, like the render does",
          S.crop_box(W, H, None)[0] == (W - CW) // 2)

    print("\nthe clamp is the render's")
    check("below the band", S.clamp_focus(0.01) == S.FOCUS_MIN)
    check("above the band", S.clamp_focus(0.99) == S.FOCUS_MAX)
    check("inside it is untouched", S.clamp_focus(0.42) == 0.42)

    print("\nsources that are already tall")
    check("a 9:16 source is taken whole, not re-cropped",
          S.crop_box(608, 1080, 0.2) == (0, 0, 608, 1080))
    check("so is something squarer than the window",
          S.crop_box(500, 1080, 0.9) == (0, 0, 500, 1080))

    print("\nother frame sizes the pipeline produces")
    for (fw, fh) in ((1280, 720), (3840, 2160), (720, 1280)):
        x, y, w, h = S.crop_box(fw, fh, 0.3)
        check(f"{fw}x{fh} crops inside itself", x >= 0 and x + w <= fw and h == fh,
              f"x={x} w={w}")

    print("\na frame with no size is an error, not a silent empty crop")
    for bad in ((0, 1080), (1920, 0)):
        try:
            S.crop_box(*bad, 0.5)
            check(f"{bad} refused", False, "no error raised")
        except ValueError as e:
            check(f"{bad} refused", True, str(e))

    print("\nzoomed on the tracer's foot, which is where the golfer stands")
    # #1266's reading: tracer 0.704. The tile must be cut around that.
    zx, zy, zw, zh = S.crop_box(W, H, 0.704, 0.72)
    check("the window is a real zoom, not the whole height",
          zh < H and zh == int(round(H / S.ZOOM)), f"h={zh} of {H}")
    check("still 9:16", abs(zw / zh - 9 / 16) < 0.01, f"{zw}x{zh}")
    check("centred on 0.704 horizontally",
          abs((zx + zw / 2) / W - 0.704) < 0.01, f"centre={(zx + zw / 2) / W:.3f}")
    feet = 0.72 * H
    check("the feet sit near the bottom of the cut, not the middle",
          abs((feet - zy) / zh - (1.0 - S.FEET_FROM_BOTTOM)) < 0.02,
          f"feet at {(feet - zy) / zh:.2f} down the crop")
    check("the golfer's head has room above", zy < feet - zh * 0.5)

    print("\na zoomed window still cannot leave the frame")
    for fx in (0.0, 0.15, 0.5, 0.85, 1.0):
        for fy in (0.0, 0.45, 0.72, 1.0):
            x, y, w, h = S.crop_box(W, H, fx, fy)
            ok = x >= 0 and y >= 0 and x + w <= W and y + h <= H
            if not ok:
                check(f"focus ({fx},{fy}) stays inside", False, f"{x},{y},{w},{h}")
    check("every focus pair stays inside the frame", True)

    print("\nno tracer means no vertical anchor, so no guessing")
    check("without a y it frames like the render: full height",
          S.crop_box(W, H, 0.704, None)[3] == H)
    check("and that is what a failed scan gets",
          S.crop_box(W, H, None, None) == S.crop_box(W, H, 0.5, None))

    print("\na frame too small for the zoomed window falls back")
    x, y, w, h = S.crop_box(200, 300, 0.5, 0.7)
    check("rather than cutting bigger than the picture",
          x >= 0 and y >= 0 and x + w <= 200 and y + h <= 300, f"{x},{y},{w},{h}")

    print("\nwhich frame gets frozen")
    FPS, N = 30.0, 360          # a 12s clip
    check("a second or so before the tracer, where the golfer is at address",
          S.frame_index(FPS, 6.0, N) == int(round((6.0 - S.ADDRESS_LEAD_SEC) * FPS)),
          f"{S.frame_index(FPS, 6.0, N)}")
    check("which is BEFORE the swing, not frame zero",
          0 < S.frame_index(FPS, 6.0, N) < int(6.0 * FPS))
    check("a tracer that starts immediately cannot seek before the clip",
          S.frame_index(FPS, 0.2, N) == 0, f"{S.frame_index(FPS, 0.2, N)}")
    check("nor past the end of a short one",
          S.frame_index(FPS, 99.0, N) == N - 1, f"{S.frame_index(FPS, 99.0, N)}")
    check("no tracer takes the middle, not the opening",
          S.frame_index(FPS, None, N) == (N - 1) // 2, f"{S.frame_index(FPS, None, N)}")
    check("and that is the point: frame zero can predate the golfer",
          S.frame_index(FPS, None, N) != 0)
    for bad in ("", "abc", float("nan")):
        check(f"{bad!r} falls back to the middle", S.frame_index(FPS, bad, N) == (N - 1) // 2)
    check("a single-frame clip has only one answer", S.frame_index(FPS, 5.0, 1) == 0)
    check("a nonsense fps does not crash the grab", S.frame_index(0, 6.0, N) >= 0)

    print("\na composite is tee footage then green footage")
    # 12s clip, camera changes at 5s. Everything worth freezing is on
    # the tee side; past the cut is a different camera pointed at the
    # green, which is how a tile came back showing the putting surface.
    CUT = 5.0
    tee_last = S.tee_limit_frames(FPS, N, CUT) - 1
    check("the tee half is the first part of it",
          0 < tee_last < N - 1, f"tee ends at frame {tee_last} of {N}")
    # Not merely "before the cut" -- the frames either side of a join are
    # the two cameras blended, so it has to stop CLEAR of it.
    check("and stops clear of the join, not one frame short of it",
          tee_last <= int((CUT - 0.2) * FPS),
          f"{tee_last} vs cut frame {int(CUT * FPS)}")
    check("no tracer takes the middle of the TEE half, not of the clip",
          S.frame_index(FPS, None, N, CUT) == tee_last // 2,
          f"{S.frame_index(FPS, None, N, CUT)} (whole-clip middle would be {N // 2})")
    check("which is the bug: the clip's own middle is past the cut",
          N // 2 > tee_last)
    check("a tracer found late cannot drag the frame into the green half",
          S.frame_index(FPS, 11.0, N, CUT) == tee_last,
          f"{S.frame_index(FPS, 11.0, N, CUT)} vs tee_last {tee_last}")
    check("a tee-side tracer is unaffected",
          S.frame_index(FPS, 4.0, N, CUT) == int(round((4.0 - S.ADDRESS_LEAD_SEC) * FPS)))

    print("\na single-camera clip is all tee")
    check("no cut means the whole clip", S.tee_limit_frames(FPS, N, None) == N)
    check("and the midpoint is the clip's", S.frame_index(FPS, None, N, None) == (N - 1) // 2)
    for bad in ("", "abc", float("nan"), 0, -3):
        check(f"a cut of {bad!r} is treated as no cut",
              S.tee_limit_frames(FPS, N, bad) == N)

    print("\nthe cache name is per source file")
    check("named off the stem", S.still_name("clip123_composite") == "clip123_composite_shot_still5.jpg")

    print()
    if fails:
        print("FAILURES:", fails); return 1
    print("ALL PASS"); return 0


if __name__ == "__main__":
    raise SystemExit(main())
