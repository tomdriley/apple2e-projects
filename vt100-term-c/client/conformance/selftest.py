#!/usr/bin/env python3
"""Offline self-test for the conformance machinery (no MAME required).

Exercises the input decoder, the expectation checker for every ``expect`` key,
the pure ``classify`` progress logic across all five outcomes, and one full
runner loop through :class:`FakeTarget` -- then loads the real committed corpus
to prove it still validates. Runs anywhere, so CI can guard the framework
itself independently of the emulator end-to-end run.

    python selftest.py     # prints "selftest OK" and exits 0 on success
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from model import decode, check, Screen, Case, load_corpus  # noqa: E402
from target_base import Capabilities  # noqa: E402
from target_fake import FakeTarget  # noqa: E402
from runner import run_case, summarize, classify, PASS, REGRESSION, XFAIL, UPASS, SKIP  # noqa: E402


def _screen(text=None, inverse=None, cursor=None, reports=b"", state=None):
    s = Screen.blank()
    for (r, c, t) in (text or []):
        row = list(s.text[r - 1])
        for i, ch in enumerate(t):
            row[c - 1 + i] = ch
        s.text[r - 1] = "".join(row)
    for (r, c, length) in (inverse or []):
        row = list(s.inverse[r - 1])
        for i in range(length):
            row[c - 1 + i] = "1"
        s.inverse[r - 1] = "".join(row)
    s.cursor = cursor
    s.reports = reports
    s.state = state or {}
    return s


def test_decode():
    assert decode(r"\e[2J") == b"\x1b[2J"
    assert decode(r"\x1b[8;20H") == b"\x1b[8;20H"
    assert decode(r"A\r\nB") == b"A\r\nB"
    assert decode(r"\xc3\xa9") == b"\xc3\xa9"
    assert decode(r"\a\b\t\v\f\0") == b"\x07\x08\x09\x0b\x0c\x00"


def test_check_each_key():
    scr = _screen(text=[(3, 5, "HELLO")], inverse=[(12, 1, 6)],
                  cursor=(8, 20), reports=b"\x1b[?1;0c\x1b[8;20R",
                  state={"app_cursor": 1, "scroll_top": 2})
    assert check(scr, {"rows": [[3, 5, "HELLO"]]}) == []
    assert check(scr, {"cells": [[3, 5, "HELLO"]]}) == []
    assert check(scr, {"has": ["HELLO"]}) == []
    assert check(scr, {"absent": ["WORLD"]}) == []
    assert check(scr, {"cursor": [8, 20]}) == []
    assert check(scr, {"attr": [[12, 1, 6, "inverse"]]}) == []
    assert check(scr, {"attr": [[3, 5, 5, "normal"]]}) == []
    assert check(scr, {"state": {"app_cursor": 1, "scroll_top": 2}}) == []
    assert check(scr, {"report": "\\e[?1;0c"}) == []
    # Negatives must be reported.
    assert check(scr, {"rows": [[3, 5, "WORLD"]]})
    assert check(scr, {"cursor": [1, 1]})
    assert check(scr, {"attr": [[3, 5, 5, "inverse"]]})
    assert check(scr, {"state": {"app_cursor": 0}})
    assert check(scr, {"report": "\\e[c"})
    assert check(scr, {"absent": ["HELLO"]})


def test_classify():
    assert classify("supported", [], 1) == PASS
    assert classify("supported", ["boom"], 1) == REGRESSION
    assert classify("unsupported", ["boom"], 1) == XFAIL
    assert classify("partial", ["boom"], 1) == XFAIL
    assert classify("unsupported", [], 1) == UPASS
    assert classify("partial", [], 1) == UPASS
    assert classify("supported", [], 0) == SKIP
    assert classify("unsupported", [], 0) == SKIP


def test_runner_loop():
    # A responder that always renders "HI" at row 1, col 1, regardless of input.
    fixed = _screen(text=[(1, 1, "HI")])
    target = FakeTarget(responder=lambda data: fixed)
    cases = [
        Case("a", "cat", "", "", "supported", {"has": ["HI"]}),     # PASS
        Case("b", "cat", "", "", "supported", {"has": ["NO"]}),     # REGRESSION
        Case("c", "cat", "", "", "unsupported", {"has": ["NO"]}),   # XFAIL
        Case("d", "cat", "", "", "partial", {"has": ["HI"]}),       # UPASS
    ]
    with target:
        results = [run_case(target, c) for c in cases]
    got = {r["id"]: r["outcome"] for r in results}
    assert got == {"a": PASS, "b": REGRESSION, "c": XFAIL, "d": UPASS}, got

    summary = summarize(results)
    assert summary["by_outcome"] == {PASS: 1, REGRESSION: 1, XFAIL: 1, UPASS: 1}
    assert summary["regressions"] == ["b"]
    assert summary["unexpected_passes"] == ["d"]
    # All four cases default to basis="spec" (none unobservable, none SKIP), so
    # conf == 4. spec: 1 PASS / 4 = 25%. profile: same set = 25%. behavioral:
    # PASS+UPASS = 2 / 4 = 50%. completeness: 2 supported / 4 = 50%. correctness:
    # 1 PASS / 2 supported-checkable = 50%.
    assert summary["spec_conformance_pct"] == 25.0
    assert summary["profile_conformance_pct"] == 25.0
    assert summary["behavioral_compat_pct"] == 50.0
    assert summary["completeness_pct"] == 50.0
    assert summary["correctness_pct"] == 50.0
    assert summary["basis_counts"] == {"spec": 4}


def test_basis_buckets_and_unobservable_skip():
    # One case per basis; the responder always renders "HI" at (1,1).
    fixed = _screen(text=[(1, 1, "HI")])
    target = FakeTarget(responder=lambda d: fixed)
    cases = [
        Case("s1", "cat", "", "", "supported", {"has": ["HI"]}, basis="spec"),
        Case("s2", "cat", "", "", "unsupported", {"has": ["NO"]}, basis="spec"),
        Case("p1", "cat", "", "", "supported", {"has": ["HI"]}, basis="profile"),
        Case("d1", "cat", "", "", "supported", {"has": ["HI"]}, basis="degenerate"),
        Case("t1", "cat", "", "", "supported", {"has": ["HI"]}, basis="tolerance"),
        Case("u1", "cat", "", "", "supported", {"has": ["HI"]}, basis="unobservable"),
    ]
    with target:
        results = [run_case(target, c) for c in cases]
    got = {r["id"]: r["outcome"] for r in results}
    # unobservable is never rendered or checked -- it is SKIP so it can never be
    # counted as conformance, even though its (vacuous) check would have "passed".
    assert got["u1"] == SKIP, got
    assert got == {"s1": PASS, "s2": XFAIL, "p1": PASS,
                   "d1": PASS, "t1": PASS, "u1": SKIP}, got

    summary = summarize(results)
    # conf excludes the SKIP/unobservable case -> 5 cases score.
    # spec = {s1,s2}: 1 PASS / 2 = 50%. profile = {s1,s2,p1}: 2 PASS / 3 = 66.7%.
    # degenerate + tolerance count toward neither spec nor profile, only behavioural.
    assert summary["spec_conformance_pct"] == 50.0
    assert summary["profile_conformance_pct"] == 66.7
    # behavioural compat = PASS+UPASS over conf = {s1,p1,d1,t1} / 5 = 80%.
    assert summary["behavioral_compat_pct"] == 80.0
    assert summary["basis_counts"] == {
        "spec": 2, "profile": 1, "degenerate": 1, "tolerance": 1, "unobservable": 1}


def test_behavioral_compat_is_relabel_invariant():
    # The point of the basis split: promoting a passing case from unsupported to
    # supported must NOT move the behavioural headline (the firmware's observable
    # behaviour did not change), even though strict spec conformance does move.
    fixed = _screen(text=[(1, 1, "HI")])
    target = FakeTarget(responder=lambda d: fixed)

    def metrics(status):
        case = Case("x", "cat", "", "", status, {"has": ["HI"]})  # basis=spec
        with target:
            return summarize([run_case(target, case)])

    before = metrics("unsupported")  # UNEXPECTED_PASS
    after = metrics("supported")     # PASS
    assert before["behavioral_compat_pct"] == after["behavioral_compat_pct"] == 100.0
    # ...but the relabel makes the strict figure honest: 0% -> 100%.
    assert before["spec_conformance_pct"] == 0.0
    assert after["spec_conformance_pct"] == 100.0


def test_skip_when_uncheckable():
    # A target that cannot read the inverse plane must SKIP an attr-only case,
    # never fail it.
    caps = Capabilities(glyphs=True, inverse=False, cursor=True,
                        reports=True, state=True)
    target = FakeTarget(responder=lambda d: Screen.blank(), caps=caps)
    case = Case("x", "cat", "", "", "supported",
                {"attr": [[1, 1, 3, "inverse"]]})
    with target:
        r = run_case(target, case)
    assert r["outcome"] == SKIP, r
    assert r["skipped_keys"] == ["attr"]


def test_real_corpus_loads():
    cases = load_corpus()  # strict validation; raises on any schema problem
    assert len(cases) > 100, f"only {len(cases)} cases loaded"
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids)), "duplicate ids in corpus"


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\nselftest OK ({len(tests)} tests)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
