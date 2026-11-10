#!/usr/bin/env python3
"""No local is read before it is assigned, in the big produce paths.

THE BUG THIS EXISTS FOR SHIPPED AND BROKE EVERY PRODUCE. A dict
literal built right after the reverse walk referenced `_anchor`, which
is not computed until forty lines further down the same loop, so every
run on that path died with

    cannot access local variable '_anchor' where it is not
    associated with a value

Nothing caught it. `python -c "import ast; ast.parse(...)"` is happy --
it is valid syntax. pyflakes is happy -- the name IS assigned in the
scope, just later. Every test in this repo that touches these
functions reads their SOURCE rather than running them, so a green
suite meant nothing. It took the operator pressing the button.

So: a scope-aware read-before-assign check. It walks each function
body in statement order carrying the set of names bound so far, which
is the one thing the naive version cannot do -- a loop variable is
bound by its own `for`, and reads of it inside that loop are fine even
though the assignment "comes later" in line order.

Branches are merged permissively (a name bound in either arm counts as
bound) so an `if`/`else` that sets a variable both ways does not
register. That makes this an UNDER-reporter: it will miss some real
cases rather than cry wolf, which is the only way a check like this
survives contact with a file this size.

    python3 tools/forward_ref_test.py
"""
import ast
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The functions worth checking: the ones that run on a produce and are
# too big to hold in your head, which is the same list as "the ones
# where this has already happened".
TARGETS = (
    ("backend/app/routers/admin.py", "_ascent_descents"),
    ("backend/app/routers/admin.py", "_ascent_run"),
    ("backend/app/routers/admin.py", "_rest_spot_at"),
    ("backend/app/services/greencrawl.py", "crawl_flight_back"),
    ("backend/app/services/greendescent.py", "track_descent_back"),
)

_fails = []


def ok(label, got, want):
    _ok = got == want
    print("%-4s %s: got %r, want %r" % ("ok" if _ok else "FAIL",
                                        label, got, want))
    if not _ok:
        _fails.append(label)


def _targets(node):
    """Every name a target expression binds."""
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            out.add(n.id)
    return out


def _locals_of(fn):
    """Every name assigned anywhere in the function — its locals."""
    out = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            out.add(n.id)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                out.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, ast.ExceptHandler) and n.name:
            out.add(n.name)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.add(n.name)
    return out


def _reads(node, bound, loc, bad, skip=()):
    """Report Loads of a local that is not yet bound."""
    for n in ast.walk(node):
        if n in skip:
            continue
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            if n.id in loc and n.id not in bound:
                bad.append((n.id, n.lineno))
        # A comprehension binds its own targets; treat anything inside
        # as bound so `[q for q in xs]` does not flag `q`.
        if isinstance(n, (ast.ListComp, ast.SetComp, ast.DictComp,
                          ast.GeneratorExp)):
            for g in n.generators:
                bound |= _targets(g.target)
        if isinstance(n, ast.Lambda):
            bound |= {a.arg for a in n.args.args}


def _walk(body, bound, loc, bad):
    """Statements in order, carrying what is bound so far."""
    for st in body:
        if isinstance(st, (ast.For, ast.AsyncFor)):
            _reads(st.iter, bound, loc, bad)
            bound |= _targets(st.target)
            _walk(st.body, bound, loc, bad)
            _walk(st.orelse, bound, loc, bad)
        elif isinstance(st, ast.While):
            _reads(st.test, bound, loc, bad)
            _walk(st.body, bound, loc, bad)
            _walk(st.orelse, bound, loc, bad)
        elif isinstance(st, ast.If):
            _reads(st.test, bound, loc, bad)
            _a, _b = set(bound), set(bound)
            _walk(st.body, _a, loc, bad)
            _walk(st.orelse, _b, loc, bad)
            # PERMISSIVE: bound in either arm counts as bound.
            bound |= _a | _b
        elif isinstance(st, ast.Try):
            _walk(st.body, bound, loc, bad)
            for h in st.handlers:
                _h = set(bound)
                if h.name:
                    _h.add(h.name)
                _walk(h.body, _h, loc, bad)
                bound |= _h
            _walk(st.orelse, bound, loc, bad)
            _walk(st.finalbody, bound, loc, bad)
        elif isinstance(st, (ast.With, ast.AsyncWith)):
            for it in st.items:
                _reads(it.context_expr, bound, loc, bad)
                if it.optional_vars is not None:
                    bound |= _targets(it.optional_vars)
            _walk(st.body, bound, loc, bad)
        elif isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
            bound.add(st.name)
        elif isinstance(st, (ast.Import, ast.ImportFrom)):
            for a in st.names:
                bound.add((a.asname or a.name).split(".")[0])
        elif isinstance(st, ast.Global) or isinstance(st, ast.Nonlocal):
            bound |= set(st.names)
        else:
            # Value first, then the targets it binds -- `x = x + 1` on
            # a fresh name is a genuine read-before-assign.
            _tg = set()
            if isinstance(st, ast.Assign):
                _reads(st.value, bound, loc, bad)
                for t in st.targets:
                    _reads(t, bound, loc, bad, skip=set(ast.walk(t)))
                    _tg |= _targets(t)
            elif isinstance(st, (ast.AugAssign, ast.AnnAssign)):
                if st.value is not None:
                    _reads(st.value, bound, loc, bad)
                _tg |= _targets(st.target)
                if isinstance(st, ast.AugAssign):
                    _reads(st.target, bound, loc, bad,
                           skip=set(ast.walk(st.target)))
            else:
                _reads(st, bound, loc, bad)
            bound |= _tg
            # A walrus anywhere in the statement binds too.
            for n in ast.walk(st):
                if isinstance(n, ast.NamedExpr):
                    bound |= _targets(n.target)


def main():
    for rel, name in TARGETS:
        path = ROOT / rel
        tree = ast.parse(io.open(path, encoding="utf-8").read())
        fn = next((n for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and n.name == name), None)
        if fn is None:
            ok("%s has %s" % (rel.split("/")[-1], name), False, True)
            continue
        args = fn.args
        bound = {a.arg for a in
                 list(args.posonlyargs) + list(args.args)
                 + list(args.kwonlyargs)}
        if args.vararg:
            bound.add(args.vararg.arg)
        if args.kwarg:
            bound.add(args.kwarg.arg)
        bad = []
        _walk(fn.body, bound, _locals_of(fn), bad)
        _uniq = sorted(set(bad), key=lambda q: q[1])
        if _uniq:
            for _n, _ln in _uniq:
                print("     %s:%d reads %r before it is assigned"
                      % (rel, _ln, _n))
        ok("%s reads nothing before it is assigned" % name, _uniq, [])

    print()
    if _fails:
        print("%d FAILED: %s" % (len(_fails), ", ".join(_fails)))
        return 1
    print("all good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
