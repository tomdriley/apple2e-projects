#!/usr/bin/env python3
"""Conformance runner: corpus x target -> classify -> metrics + JSON report.

Implements the esctest ``knownBug`` progress model. Each case
declares a ``status`` (supported / partial / unsupported) and spec-authored
``expect``. After a target renders the case, the declared expectations that the
target can actually observe are checked and the result is classified:

    declared status     expectations met     outcome
    ---------------     ----------------     ----------------
    supported           yes                  PASS
    supported           no                   REGRESSION   (fails CI)
    partial/unsupported no                   XFAIL        (expected)
    partial/unsupported yes                  UNEXPECTED_PASS (progress!)
    (no checkable expectation on this target) SKIP

Snapshot regression *is* the committed spec-authored ``expect`` data: a diff
against it on a ``supported`` case is a real regression; on an ``unsupported``
case it is a still-expected xfail that flips to UNEXPECTED_PASS the day the
feature lands. The run exits nonzero on any REGRESSION; ``--strict`` also fails
on UNEXPECTED_PASS to force the author to flip the case to ``supported``.

Orthogonal to ``status`` (which drives pass/fail) is each case's ``basis`` (see
``model.VALID_BASIS``), which drives how a pass is *counted*. This separates
three questions a single percentage used to conflate:

  * **behavioral_compat_pct** -- did the firmware do the right *observable* thing
    (PASS+UPASS), regardless of label? Relabel-invariant; the honest headline.
  * **spec_conformance_pct** -- PASS among ``basis=spec`` cases: strict
    VT100/ECMA-48 conformance, hardware-independent.
  * **profile_conformance_pct** -- PASS among ``basis in {spec, profile}``: spec
    plus the ECMA-permitted, documented Apple IIe degradations.

``tolerance``/``degenerate`` passes are excluded from the spec/profile figures
(they do not prove a feature) but still count as behavioural compatibility;
``unobservable`` cases are scored SKIP so an untestable claim is never counted.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from collections import Counter, defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from model import load_corpus, check, CORPUS_DIR  # noqa: E402
from target_base import Target  # noqa: E402

PASS = "PASS"
REGRESSION = "REGRESSION"
XFAIL = "XFAIL"
UPASS = "UNEXPECTED_PASS"
SKIP = "SKIP"
ERROR = "ERROR"
OUTCOMES = (PASS, REGRESSION, XFAIL, UPASS, SKIP, ERROR)

DEFAULT_JSON = pathlib.Path(__file__).resolve().parent.parent.parent / "build" / "conformance.json"


def checkable_expect(expect: dict, target: Target):
    """Split an ``expect`` block into the keys this target can observe and the
    keys it cannot (skipped, never failed)."""
    checkable, skipped = {}, []
    for key, val in expect.items():
        if target.supports(key):
            checkable[key] = val
        else:
            skipped.append(key)
    return checkable, skipped


def classify(status: str, fails: list, n_checkable: int) -> str:
    """The whole xfail/progress decision, as a pure function (unit-tested)."""
    if n_checkable == 0:
        return SKIP
    met = not fails
    if status == "supported":
        return PASS if met else REGRESSION
    return UPASS if met else XFAIL


def run_case(target: Target, case) -> dict:
    base = {
        "id": case.id,
        "category": case.category,
        "status": case.status,
        "basis": case.basis,
        "spec_ref": case.spec_ref,
    }
    # `unobservable` cases (e.g. DECTCEM cursor visibility) have no machine-probeable
    # effect on this target, so a "pass" would only prove the bytes were consumed --
    # not that the feature works. Score them SKIP so they are never counted as
    # conformance, rather than letting a vacuous check inflate the numbers.
    if case.basis == "unobservable":
        return {**base, "outcome": SKIP, "fails": [],
                "skipped_keys": sorted(case.expect)}
    try:
        target.reset()
        screen = target.render(case.input_bytes)
    except Exception as exc:  # noqa: BLE001 -- isolate a bad case, never abort the run
        return {**base, "outcome": ERROR,
                "fails": [f"{type(exc).__name__}: {exc}"], "skipped_keys": []}
    checkable, skipped = checkable_expect(case.expect, target)
    fails = check(screen, checkable)
    outcome = classify(case.status, fails, len(checkable))
    return {**base, "outcome": outcome, "fails": fails, "skipped_keys": skipped}


def summarize(results: list) -> dict:
    total = len(results)
    by_outcome = Counter(r["outcome"] for r in results)
    basis_counts = Counter(r.get("basis", "spec") for r in results)

    def pct(a, b):
        return round(100.0 * a / b, 1) if b else 0.0

    def n_pass(rows):
        return sum(1 for r in rows if r["outcome"] == PASS)

    # `conf` = the set of cases that actually contribute to a conformance figure:
    # everything that was checked (not SKIP) and whose basis is not `unobservable`
    # (those are SKIP anyway, but filter defensively). All percentages below are
    # computed over explicit subsets of `conf`, never over raw by_outcome, so the
    # meaning of each number is unambiguous.
    conf = [r for r in results
            if r["outcome"] != SKIP and r.get("basis", "spec") != "unobservable"]
    spec = [r for r in conf if r.get("basis", "spec") == "spec"]
    profile = [r for r in conf if r.get("basis", "spec") in ("spec", "profile")]
    supported_checkable = [r for r in conf if r["status"] == "supported"]

    # Observed compatibility: the firmware produced the correct *observable*
    # behaviour, regardless of how the case is labelled (PASS + UNEXPECTED_PASS).
    # This is the honest headline: it is relabel-invariant -- promoting a case from
    # unsupported to supported moves it from UPASS to PASS but does not change this
    # number, so the metric tracks behaviour, not bookkeeping.
    compat_ok = sum(1 for r in conf if r["outcome"] in (PASS, UPASS))

    cats = defaultdict(Counter)
    for r in results:
        cats[r["category"]][r["outcome"]] += 1

    return {
        "total": total,
        "by_outcome": dict(by_outcome),
        "basis_counts": dict(basis_counts),
        # Observed behavioural compatibility (relabel-invariant headline).
        "behavioral_compat_pct": pct(compat_ok, len(conf)),
        "compat_ok": compat_ok,
        "compat_total": len(conf),
        # Strict VT100/ECMA-48 conformance: PASS among spec-basis cases only.
        "spec_conformance_pct": pct(n_pass(spec), len(spec)),
        "spec_pass": n_pass(spec),
        "spec_total": len(spec),
        # Apple IIe profile conformance: spec + documented hardware degradations.
        "profile_conformance_pct": pct(n_pass(profile), len(profile)),
        "profile_pass": n_pass(profile),
        "profile_total": len(profile),
        # Completeness = declared-supported / conformance-scored cases.
        "completeness_pct": pct(len(supported_checkable), len(conf)),
        # Correctness = PASS / supported-checkable (are our 'supported' claims true).
        "correctness_pct": pct(n_pass(supported_checkable), len(supported_checkable)),
        "categories": {c: dict(v) for c, v in sorted(cats.items())},
        "regressions": [r["id"] for r in results if r["outcome"] == REGRESSION],
        "unexpected_passes": [r["id"] for r in results if r["outcome"] == UPASS],
        "errors": [r["id"] for r in results if r["outcome"] == ERROR],
    }


def print_report(summary: dict, results: list, verbose: bool) -> None:
    o = summary["by_outcome"]
    print("\n== Conformance ==")
    print(f"  cases          {summary['total']}")
    print(f"  PASS           {o.get(PASS, 0)}")
    print(f"  REGRESSION     {o.get(REGRESSION, 0)}")
    print(f"  XFAIL          {o.get(XFAIL, 0)}")
    print(f"  UNEXPECTED     {o.get(UPASS, 0)}")
    print(f"  SKIP           {o.get(SKIP, 0)}")
    print(f"  ERROR          {o.get(ERROR, 0)}")

    print("\n== Metrics ==")
    print(f"  behavioral compat    {summary['compat_ok']}/{summary['compat_total']}"
          f"  {summary['behavioral_compat_pct']}%   (PASS+UPASS, relabel-invariant)")
    print(f"  spec conformance     {summary['spec_pass']}/{summary['spec_total']}"
          f"  {summary['spec_conformance_pct']}%   (strict VT100/ECMA-48)")
    print(f"  profile conformance  {summary['profile_pass']}/{summary['profile_total']}"
          f"  {summary['profile_conformance_pct']}%   (+ documented IIe degradations)")
    print(f"  completeness {summary['completeness_pct']}%   "
          f"correctness {summary['correctness_pct']}%")
    bc = summary["basis_counts"]
    print("  basis          " + "  ".join(f"{k}={bc[k]}" for k in
          ("spec", "profile", "tolerance", "degenerate", "unobservable") if bc.get(k)))

    print("\n== By category ==")
    for cat, counts in summary["categories"].items():
        parts = " ".join(f"{k}={counts[k]}" for k in OUTCOMES if counts.get(k))
        print(f"  {cat:<20} {parts}")

    if verbose:
        print("\n== Cases ==")
        for r in results:
            mark = {PASS: "ok", REGRESSION: "XX", XFAIL: "xf",
                    UPASS: "!!", SKIP: "--", ERROR: "ER"}[r["outcome"]]
            print(f"  [{mark}] {r['id']:<28} {r['outcome']}")
            for f in r["fails"]:
                print(f"        - {f}")

    if summary["regressions"]:
        print("\n!! REGRESSIONS (supported cases that failed):")
        for r in results:
            if r["outcome"] == REGRESSION:
                print(f"  {r['id']}: " + "; ".join(r["fails"]))
    if summary["unexpected_passes"]:
        print("\n:) UNEXPECTED PASSES (review required -- do NOT auto-promote):")
        print("  A pass here may be real progress OR a weak/degenerate check that")
        print("  passes for the wrong reason. Before flipping status to supported,")
        print("  add a discriminating companion test and set an honest `basis`")
        print("  (see docs/CONFORMANCE.md).")
        print("  " + ", ".join(summary["unexpected_passes"]))
    if summary["errors"]:
        print("\n?? ERRORS (target/probe failure -- not a spec verdict):")
        for r in results:
            if r["outcome"] == ERROR:
                print(f"  {r['id']}: " + "; ".join(r["fails"]))


def make_target(name: str) -> Target:
    if name == "mame":
        from target_mame import MameTarget
        return MameTarget()
    if name == "fake":
        from target_fake import FakeTarget
        return FakeTarget()
    raise SystemExit(f"unknown target {name!r} (want: mame, fake)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="VT100 conformance runner")
    ap.add_argument("--target", default="mame", help="mame | fake")
    ap.add_argument("--corpus", default=str(CORPUS_DIR), help="corpus dir")
    ap.add_argument("-k", dest="select", default="",
                    help="only cases whose id or category contains this")
    ap.add_argument("--strict", action="store_true",
                    help="also fail on UNEXPECTED_PASS (force the flip)")
    ap.add_argument("--json", default=str(DEFAULT_JSON),
                    help="write the machine report here")
    ap.add_argument("--list", action="store_true", help="list cases and exit")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    cases = load_corpus(pathlib.Path(args.corpus))
    if args.select:
        s = args.select.lower()
        cases = [c for c in cases if s in c.id.lower() or s in c.category.lower()]
    if not cases:
        print("no cases selected")
        return 2
    if args.list:
        for c in cases:
            print(f"{c.status:<11} {c.category:<18} {c.id}")
        print(f"\n{len(cases)} cases")
        return 0

    target = make_target(args.target)
    results = []
    t0 = time.time()
    with target:
        for i, c in enumerate(cases, 1):
            r = run_case(target, c)
            results.append(r)
            if args.verbose:
                print(f"[{i:>3}/{len(cases)}] {r['outcome']:<15} {c.id}",
                      flush=True)
    elapsed = time.time() - t0

    summary = summarize(results)
    summary["elapsed_s"] = round(elapsed, 1)
    summary["target"] = args.target
    print_report(summary, results, args.verbose)

    out = pathlib.Path(args.json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "results": results},
                              indent=2), encoding="utf-8")
    print(f"\nwrote {out}")

    if summary["by_outcome"].get(REGRESSION) or summary["by_outcome"].get(ERROR):
        return 1
    if args.strict and summary["by_outcome"].get(UPASS):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
