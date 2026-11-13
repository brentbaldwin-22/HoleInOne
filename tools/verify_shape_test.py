#!/usr/bin/env python3
"""The rig, the backend and the fakes must agree on /verify-swing.

HOW THIS TEST CAME TO EXIST. `tools/pick_best_swing_test.py` carried a
FakeClient pinning the exact signature of `common.Client.verify_swing`:

    def verify_swing(self, image_path, timeout=25.0, frames_path=None)

The real one then grew `shots`, `crop`, `zones`, `geo`, `offsets`,
`ratio`, `attack_ms` and `session_id`. Calls into the fake started
raising TypeError -- and the engine CATCHES that, treats it as "could
not tell", and uploads. So every assertion in that file about holding
a confirmed swing was being made against a run where nothing was ever
confirmed and nothing was ever held.

It did not fail loudly. It stopped testing its subject and reported
the symptom three steps downstream ("nothing shipped yet -- it is
held" against a session id), where it sat on the known-failures list
being explained away.

TWO RULES COME OUT OF THAT, and this file holds both:

  1. EVERY KEYWORD THE RIG SENDS, THE BACKEND TAKES. They are updated
     separately and in either order, so a keyword the rig has and the
     endpoint does not is a swing judged on nothing.
  2. A FAKE MAY NOT PIN A SIGNATURE IT DOES NOT OWN. A stand-in for a
     client takes **kwargs, so the next field to arrive cannot quietly
     disarm the test around it.

    python3 tools/verify_shape_test.py
"""
import ast
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi-agent"))

_fails = []


def ok(label, got, want):
    _ok = got == want
    print("%-4s %s: got %r, want %r" % ("ok" if _ok else "FAIL",
                                        label, got, want))
    if not _ok:
        _fails.append(label)


def _endpoint_params():
    """The backend endpoint's parameter names, read from source.

    Source rather than import: the backend pulls in FastAPI, OpenCV
    and the model stack, and this test has to run anywhere the rig
    code does.
    """
    src = (ROOT / "backend/app/routers/cameras.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "verify_swing"):
            a = node.args
            return {p.arg for p in (a.args + a.posonlyargs + a.kwonlyargs)}
    return set()


def main():
    from agent.common import BackendClient as Client

    rig = set(inspect.signature(Client.verify_swing).parameters) - {"self"}
    api = _endpoint_params()
    ok("the backend endpoint was found", bool(api), True)

    # `image_path`/`frames_path` are local files on the rig; they go up
    # as the multipart fields `image` and `frames`. `timeout` is the
    # rig's own HTTP concern and never crosses.
    LOCAL = {"timeout"}
    RENAMED = {"image_path": "image", "frames_path": "frames"}
    sent = {RENAMED.get(k, k) for k in rig - LOCAL}

    missing = sorted(sent - api - {"token"})
    ok("every field the rig sends, the endpoint accepts", missing, [])
    if missing:
        print("     the endpoint has no: %s" % ", ".join(missing))
    print("     rig sends: %s" % ", ".join(sorted(sent)))

    # The reverse is NOT an error and must not be asserted: the
    # endpoint deliberately accepts fields an older rig does not send,
    # because the two are updated separately and in either order.
    extra = sorted(api - sent - {"token"})
    print("     endpoint also takes (older rigs omit these): %s"
          % (", ".join(extra) or "nothing"))

    # ── AND NO FAKE MAY PIN WHAT IT DOES NOT OWN ────────────────────
    for rel in ("tools/pick_best_swing_test.py",
                "tools/swing_verify_test.py",
                "tools/testcut_verify_test.py"):
        f = ROOT / rel
        if not f.exists():
            continue
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == "verify_swing"):
                _takes_kw = node.args.kwarg is not None
                _named = {p.arg for p in node.args.args} - {"self"}
                # Either it sweeps up everything, or it already names
                # every field the rig sends -- both are honest; what is
                # not is naming some and silently raising on the rest.
                ok("%s's fake cannot be disarmed by a new field" % rel.split("/")[-1],
                   _takes_kw or sent <= {RENAMED.get(k, k) for k in _named},
                   True)

    print()
    if _fails:
        print("%d FAILED: %s" % (len(_fails), ", ".join(_fails)))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
