#!/usr/bin/env python3
"""Does the agent's startup actually PRINT the config it loaded?

The capture-mode line, the mode-overrides-fps warning and the pre-roll
RAM figure are the three lines an operator reads to confirm a config
edit took effect. They are emitted from inside load_config(), so they
only survive if logging is configured BEFORE it runs. It was the other
way round, and all three INFO lines went to the root logger's default
level and were dropped — a verification step that silently printed
nothing. Run from the repo root:

    python3 tools/startup_log_test.py
"""
from __future__ import annotations

import io
import logging
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi-agent"))

FAIL: list[str] = []


def check(name: str, got, want) -> None:
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'}  {name}: got {got!r}, want {want!r}")
    if not ok:
        FAIL.append(name)


def reset_logging() -> io.StringIO:
    """A root logger as bare as the one a fresh process starts with."""
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(logging.WARNING)
    return io.StringIO()


def startup(cfg_text: str, cap: io.StringIO) -> dict:
    """Run the real main() startup sequence up to the first network
    call, with stderr captured."""
    import golfreelz_agent
    from agent import common

    cfg_path = ROOT / "tools" / "_startup_test_config.yaml"
    cfg_path.write_text(cfg_text)
    handler = logging.StreamHandler(cap)
    try:
        # Mirror main()'s order exactly by calling the real functions in
        # the order the real file calls them — read from the source so
        # this test fails if someone swaps them back.
        src = (ROOT / "pi-agent" / "golfreelz_agent.py").read_text()
        body = src.split("def main(", 1)[1]
        i_log = body.index("setup_logging(")
        i_cfg = body.index("load_config(")
        # THE LOAD-BEARING ASSERTION. The capture checks below pass
        # either way, because this harness configures logging itself
        # before calling load_config — only reading the real file's
        # order catches a swap back, which is the regression that
        # actually cost an afternoon of "the journal says nothing".
        check("setup_logging precedes load_config in main()", i_log < i_cfg, True)

        golfreelz_agent.setup_logging()
        logging.getLogger().addHandler(handler)
        cfg = common.load_config(cfg_path)
        lvl = str(cfg.get("log_level", "INFO")).upper()
        logging.getLogger().setLevel(getattr(logging, lvl, logging.INFO))
        return cfg
    finally:
        logging.getLogger().removeHandler(handler)
        cfg_path.unlink(missing_ok=True)


def main() -> int:
    BASE = (
        "backend_url: http://example.invalid\n"
        "auth_token: t\n"
        "buffer_seconds: 5\n"
        "camera:\n"
        "  device: \"rtsp://example.invalid/profile2/media.smp\"\n"
    )

    cap = reset_logging()
    cfg = startup(BASE + '  mode: "1080p60"\n  fps: 60\n', cap)
    out = cap.getvalue()
    check("mode resolved", cfg["camera"]["fps"], 60)
    check("capture-mode line is printed",
          bool(re.search(r"capture mode '1080p60' -> 1920x1080@60", out)), True)
    check("pre-roll RAM line is printed",
          bool(re.search(r"pre-roll buffer: ~\d+MB RAM", out)), True)

    # The trap this was built to expose: a mode that silently overrules
    # the fps somebody just edited. The warning is useless without the
    # INFO lines around it, which is what the old ordering dropped.
    cap = reset_logging()
    startup(BASE + '  mode: "1080p60"\n  fps: 30\n', cap)
    out = cap.getvalue()
    check("override warning is printed",
          "overrides camera.fps=30 with 60" in out, True)

    # And an explicit log_level must still win after the re-level.
    cap = reset_logging()
    startup("log_level: WARNING\n" + BASE + '  mode: "1080p60"\n  fps: 60\n', cap)
    check("log_level: WARNING is honoured",
          logging.getLogger().level, logging.WARNING)

    print()
    if FAIL:
        print(f"{len(FAIL)} FAILED: {', '.join(FAIL)}")
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
