#!/usr/bin/env python3
"""pyte reference oracle -- independent cross-check of the conformance corpus.

The conformance runner grades the firmware against **hand-authored** expectations. Those goldens encode
our *own* reading of the VT100/ECMA-48 spec, so a shared misreading by the people who
wrote both the firmware and the expectations is invisible. This oracle adds an
*independent* second opinion: pyte, a pure-Python ECMA-48 screen model that knows
nothing about our firmware or our authored ``expect`` blocks.

For a given case there are up to three screens:

    E  -- authored ``expect``      (the human truth from the corpus)
    F  -- firmware screen          (MameTarget; needs a build + MAME)
    P  -- pyte reference screen     (PyteTarget; pure Python)

and this driver runs two comparisons plus a self-test:

  * ``--audit`` (default, **MAME-free**) -- P vs E. Does an independent spec-follower
    satisfy our authored expectations? Graded by each case's ``basis`` (model.VALID_BASIS):
    a ``spec``-basis case where pyte *disagrees* is a **spec-suspect** -- either a
    corpus-authoring bug or a genuine firmware/spec question worth escalating. A
    ``profile``-basis disagreement is *expected* (pyte does not model the documented
    Apple IIe degradations), so it is reported but never a suspect.

  * ``--differential`` -- F vs P. The firmware against the independent reference over
    the *whole* screen (stronger than ``check()``'s declared-key comparison). This is
    literally "the conformance runner, graded against pyte instead of the authored expect": the
    per-case results are shaped exactly like ``runner.run_case`` and fed straight through
    ``runner.summarize`` / ``runner.print_report``, so the differential yields the same
    basis-graded metrics (behavioral / spec / profile conformance) computed against an
    independent oracle. Needs a built firmware disk + MAME.

  * ``--selftest`` -- P vs P. Renders every case through pyte twice and asserts the diff
    pipeline reports zero diffs (determinism + a smoke test of ``diff_screens``). No MAME.

pyte is not treated as infallible: where pyte 0.8.2 is itself wrong or incomplete, the
case is declared in :mod:`oracle_quirks` and excluded from both the audit's spec figures
and the differential (skipped, never counted as a firmware regression).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import oracle_quirks  # noqa: E402
import runner  # noqa: E402  -- reuse checkable_expect / classify / summarize / print_report
from model import COLS, ROWS, Screen, check, load_corpus, CORPUS_DIR  # noqa: E402
from target_pyte import PyteTarget  # noqa: E402

BUILD_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "build"
DEFAULT_JSON = BUILD_DIR / "oracle.json"


# --------------------------------------------------------------------------- #
# Screen comparison core
# --------------------------------------------------------------------------- #
def _norm_glyph_row(s: str) -> str:
    """Canonicalize a glyph row to exactly COLS chars.

    The firmware right-trims trailing whitespace per row; pyte pads to COLS. Padding
    both to COLS makes them comparable and gives a stable 1-based column index.
    """
    return (s or "")[:COLS].ljust(COLS)


def _norm_inv_row(s: str) -> str:
    """Canonicalize an inverse-plane row to COLS chars of '0'/'1'."""
    return (s or "")[:COLS].ljust(COLS, "0")


def _first_diff_col(a: str, b: str) -> int:
    for i in range(min(len(a), len(b))):
        if a[i] != b[i]:
            return i + 1
    return min(len(a), len(b)) + 1


def diff_screens(fw: Screen, ref: Screen) -> list[str]:
    """Human-readable diffs between two screens, over the channels *both* carry.

    Only the glyph plane, the inverse (reverse-video) plane and the cursor are
    compared -- pyte has no wire reports and no firmware state, so those channels are
    left to the firmware-only runner. Returns one string per differing row/field;
    an empty list means the two screens agree on every comparable channel.
    """
    diffs: list[str] = []

    for i in range(1, ROWS + 1):
        a, b = _norm_glyph_row(fw.row(i)), _norm_glyph_row(ref.row(i))
        if a != b:
            col = _first_diff_col(a, b)
            diffs.append(
                f"glyph row {i} @col {col}: firmware {a.rstrip()!r} "
                f"vs reference {b.rstrip()!r}")

    if fw.inverse and ref.inverse:
        for i in range(1, ROWS + 1):
            a, b = _norm_inv_row(fw.inv_row(i)), _norm_inv_row(ref.inv_row(i))
            if a != b:
                col = _first_diff_col(a, b)
                diffs.append(
                    f"inverse row {i} @col {col}: firmware {a} vs reference {b}")

    if fw.cursor is not None and ref.cursor is not None and fw.cursor != ref.cursor:
        diffs.append(f"cursor: firmware {fw.cursor} vs reference {ref.cursor}")

    return diffs


# --------------------------------------------------------------------------- #
# Audit: pyte (P) vs authored expect (E), MAME-free, graded by basis
# --------------------------------------------------------------------------- #
# verdict -> one-line meaning (also the print order).
AUDIT_VERDICTS = {
    "spec-confirmed":
        "basis=spec and pyte satisfies our authored expect (independently confirmed)",
    "spec-suspect":
        "basis=spec but pyte DISAGREES -- corpus bug or firmware/spec question",
    "profile-coincides":
        "basis=profile and pyte happens to satisfy expect too",
    "profile-divergence":
        "basis=profile and pyte diverges (expected: pyte omits the IIe degradation)",
    "tolerance-ok":
        "basis=tolerance and pyte agrees",
    "tolerance-divergence":
        "basis=tolerance and pyte diverges (a no-op absorbed differently)",
    "degenerate-ok":
        "basis=degenerate and pyte agrees",
    "degenerate-divergence":
        "basis=degenerate and pyte diverges (passed only by default coincidence)",
    "pyte-quirk":
        "pyte 0.8.2 is itself wrong/incomplete here (see oracle_quirks)",
    "skip-unobservable":
        "no pyte-observable expectation to audit",
}


def classify_audit(basis: str, agrees: bool) -> str:
    table = {
        "spec": ("spec-confirmed", "spec-suspect"),
        "profile": ("profile-coincides", "profile-divergence"),
        "tolerance": ("tolerance-ok", "tolerance-divergence"),
        "degenerate": ("degenerate-ok", "degenerate-divergence"),
    }
    ok, bad = table.get(basis, ("spec-confirmed", "spec-suspect"))
    return ok if agrees else bad


def run_audit(cases, ref: PyteTarget) -> list[dict]:
    results = []
    for c in cases:
        base = {"id": c.id, "category": c.category,
                "status": c.status, "basis": c.basis}
        if c.basis == "unobservable":
            results.append({**base, "verdict": "skip-unobservable",
                            "reason": "case basis is unobservable", "fails": []})
            continue
        quirk = oracle_quirks.quirk_reason(c)
        if quirk:
            results.append({**base, "verdict": "pyte-quirk",
                            "reason": quirk, "fails": []})
            continue
        checkable, _skipped = runner.checkable_expect(c.expect, ref)
        if not checkable:
            results.append({**base, "verdict": "skip-unobservable",
                            "reason": "no pyte-observable expect keys", "fails": []})
            continue
        fails = check(ref.render(c.input_bytes), checkable)
        results.append({**base, "verdict": classify_audit(c.basis, not fails),
                        "reason": None, "fails": fails})
    return results


def summarize_audit(results: list[dict]) -> dict:
    counts = Counter(r["verdict"] for r in results)
    confirmed = counts.get("spec-confirmed", 0)
    suspect = counts.get("spec-suspect", 0)
    spec_checked = confirmed + suspect
    spec_total = sum(1 for r in results if r["basis"] == "spec")
    spec_quirked = sum(1 for r in results
                       if r["basis"] == "spec" and r["verdict"] == "pyte-quirk")
    spec_unobservable = sum(1 for r in results
                            if r["basis"] == "spec"
                            and r["verdict"] == "skip-unobservable")
    return {
        "total": len(results),
        "counts": dict(counts),
        "spec_confirmed": confirmed,
        "spec_suspect": suspect,
        "spec_checked": spec_checked,
        "spec_total": spec_total,
        "spec_quirked": spec_quirked,
        "spec_unobservable": spec_unobservable,
        "reference_agreement_pct":
            round(100.0 * confirmed / spec_checked, 1) if spec_checked else None,
        "spec_coverage_pct":
            round(100.0 * spec_checked / spec_total, 1) if spec_total else None,
        "suspects": [r["id"] for r in results if r["verdict"] == "spec-suspect"],
    }


def print_audit(summary: dict, results: list[dict], verbose: bool) -> None:
    counts = summary["counts"]
    print("\n== Reference audit (pyte vs authored expect) ==")
    print(f"  cases                 {summary['total']}")
    for verdict in AUDIT_VERDICTS:
        n = counts.get(verdict, 0)
        if n:
            print(f"  {verdict:<21} {n}")

    agree = summary["reference_agreement_pct"]
    agree_s = "n/a" if agree is None else f"{agree}%"
    print(f"\n  reference agreement   {summary['spec_confirmed']}/"
          f"{summary['spec_checked']}  {agree_s}"
          "   (spec-confirmed / spec-checked)")
    cov = summary.get("spec_coverage_pct")
    cov_s = "n/a" if cov is None else f"{cov}%"
    print(f"  spec coverage         {summary['spec_checked']}/"
          f"{summary['spec_total']}  {cov_s}"
          f"   (pyte-oracled spec cases; {summary['spec_quirked']} pyte-quirk + "
          f"{summary['spec_unobservable']} unobservable excluded)")

    suspects = [r for r in results if r["verdict"] == "spec-suspect"]
    if suspects:
        print("\n!! SPEC-SUSPECT -- an independent spec-follower disagrees with our")
        print("   spec-basis authored expect (corpus bug OR firmware/spec question):")
        for r in suspects:
            print(f"  {r['id']}: " + "; ".join(r["fails"]))

    if verbose:
        divs = [r for r in results if r["verdict"] == "profile-divergence"]
        if divs:
            print("\n-- profile-divergence (expected: pyte omits the IIe degradation) --")
            for r in divs:
                print(f"  {r['id']}: " + "; ".join(r["fails"]))
        quirks = [r for r in results if r["verdict"] == "pyte-quirk"]
        if quirks:
            print("\n-- pyte-quirk (pyte is the unreliable side; not audited) --")
            for r in quirks:
                print(f"  {r['id']}: {r['reason']}")


# --------------------------------------------------------------------------- #
# Differential: firmware (F) vs pyte (P) -- runner-shaped, runner-graded
# --------------------------------------------------------------------------- #
def run_differential_case(firmware, ref: PyteTarget, case) -> dict:
    """One F-vs-P case, shaped exactly like ``runner.run_case`` so that
    ``runner.summarize`` / ``runner.print_report`` can grade it unchanged."""
    base = {"id": case.id, "category": case.category, "status": case.status,
            "basis": case.basis, "spec_ref": case.spec_ref}
    if case.basis == "unobservable":
        return {**base, "outcome": runner.SKIP, "fails": [],
                "skipped_keys": sorted(case.expect)}
    quirk = oracle_quirks.quirk_reason(case)
    if quirk:
        return {**base, "outcome": runner.SKIP, "fails": [],
                "skipped_keys": [f"pyte-quirk: {quirk}"]}
    # The wire `report` channel (DSR/DA replies) is invisible to pyte
    # (caps.reports=False), so pyte can never oracle a case's *report* assertion;
    # `checkable_expect` below drops it. Historically we skipped the whole case,
    # because MameTarget also appended its own ESC[6n cursor probe to every render
    # and, back-to-back with a case's own DSR, the doubled query could leave a
    # stray report-final byte on the firmware glyph plane -- an artifact the case's
    # real (single-query) bytes never produce and that pyte, never seeing the
    # probe, cannot match. That harness contamination is fixed (issue #31: the
    # probe is no longer appended after a case's own trailing query), so a report
    # case that ALSO asserts a pyte-observable plane -- e.g. the cursor of a CPR
    # case -- now yields a valid glyph/inverse/cursor diff and is graded on those
    # planes (its report key is simply dropped as un-oracleable). Pure wire-only
    # report cases have nothing pyte can see and fall through to the `not
    # checkable` skip just below.
    # pyte can only oracle the glyph/inverse/cursor planes. A case whose entire
    # `expect` lives on channels pyte cannot see (firmware `state`) gives a screen
    # diff nothing to prove -- firmware and pyte would "agree" on a blank screen for
    # reasons unrelated to the tested behaviour -- so scoring it as a PASS is vacuous.
    # Skip it (the F-vs-E runner still covers it via the state probe).
    checkable, _skipped = runner.checkable_expect(case.expect, ref)
    if not checkable:
        return {**base, "outcome": runner.SKIP, "fails": [],
                "skipped_keys": sorted(case.expect)}
    try:
        firmware.reset()
        fw = firmware.render(case.input_bytes)
        p = ref.render(case.input_bytes)
    except Exception as exc:  # noqa: BLE001 -- isolate a bad case, never abort the run
        return {**base, "outcome": runner.ERROR,
                "fails": [f"{type(exc).__name__}: {exc}"], "skipped_keys": []}
    diffs = diff_screens(fw, p)
    # The case has >=1 pyte-observable assertion, so the full-screen glyph/inverse/
    # cursor diff is a valid oracle. n_checkable = len(checkable) (>0) makes classify
    # score it, and any divergence -- even on a plane the partial `expect` never
    # asserted -- is surfaced (that is how the ED cursor-homing finding caught two
    # erase cases whose `expect` did not assert the cursor).
    outcome = runner.classify(case.status, diffs, len(checkable))
    return {**base, "outcome": outcome, "fails": diffs, "skipped_keys": []}


# --------------------------------------------------------------------------- #
# Self-test: pyte vs pyte -- diff pipeline + determinism, no MAME
# --------------------------------------------------------------------------- #
def _diff_detection_checks() -> list[str]:
    """Positive controls: ``diff_screens`` must DETECT a known difference in each
    channel it compares, and must NOT report one between identical screens.

    ``--selftest``'s pyte-vs-pyte pass only proves determinism; on its own it would
    still pass if ``diff_screens`` were silently a no-op (e.g. the inverse-plane
    comparison never firing). These synthetic controls prove the pipeline bites.
    """
    def scr(**kw) -> Screen:
        base = dict(text=[" " * COLS for _ in range(ROWS)],
                    inverse=["0" * COLS for _ in range(ROWS)],
                    cursor=(1, 1), reports=b"", state={})
        base.update(kw)
        return Screen(**base)

    fails: list[str] = []
    glyph = [" " * COLS for _ in range(ROWS)]
    glyph[2] = "X".ljust(COLS)
    if not diff_screens(scr(), scr(text=glyph)):
        fails.append("missed a glyph-plane difference")
    inv = ["0" * COLS for _ in range(ROWS)]
    inv[2] = "1" + "0" * (COLS - 1)
    if not diff_screens(scr(), scr(inverse=inv)):
        fails.append("missed an inverse-plane difference")
    if not diff_screens(scr(), scr(cursor=(5, 10))):
        fails.append("missed a cursor difference")
    if diff_screens(scr(), scr()):
        fails.append("reported a difference between identical screens")
    return fails


def run_selftest(cases, ref: PyteTarget) -> int:
    print("\n== Oracle self-test ==")
    detect = _diff_detection_checks()
    if detect:
        print("  !! diff_screens positive-control FAILED:")
        for f in detect:
            print(f"    diff_screens {f}")
        return 1
    print("  diff_screens detects glyph/inverse/cursor differences. OK")

    bad = []
    for c in cases:
        diffs = diff_screens(ref.render(c.input_bytes), ref.render(c.input_bytes))
        if diffs:
            bad.append((c.id, diffs))
    print(f"  pyte-vs-pyte over {len(cases)} cases (determinism)")
    if bad:
        print(f"  !! {len(bad)} case(s) produced non-empty self-diffs:")
        for cid, diffs in bad:
            print(f"    {cid}: " + "; ".join(diffs))
        return 1
    print("  all renders deterministic; diff pipeline reports zero diffs. OK")
    return 0


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def _write_json(path: str, summary: dict, results: list) -> None:
    out = pathlib.Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "results": results}, indent=2),
                   encoding="utf-8")
    print(f"\nwrote {out}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="pyte reference oracle: audit (default) / differential / selftest")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--audit", action="store_const", const="audit", dest="mode",
                      help="(default) pyte vs authored expect -- MAME-free corpus audit")
    mode.add_argument("--differential", action="store_const", const="differential",
                      dest="mode", help="firmware vs pyte -- needs a built firmware + MAME")
    mode.add_argument("--selftest", action="store_const", const="selftest", dest="mode",
                      help="pyte vs pyte -- diff-pipeline sanity check (no MAME)")
    ap.set_defaults(mode="audit")
    ap.add_argument("--firmware", default="mame",
                    help="differential firmware target: mame | fake (default mame)")
    ap.add_argument("--corpus", default=str(CORPUS_DIR), help="corpus dir")
    ap.add_argument("-k", dest="select", default="",
                    help="only cases whose id or category contains this")
    ap.add_argument("--strict", action="store_true",
                    help="audit: fail on any spec-suspect; "
                         "differential: also fail on UNEXPECTED_PASS")
    ap.add_argument("--json", default=str(DEFAULT_JSON),
                    help="write the machine report here")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    cases = load_corpus(pathlib.Path(args.corpus))
    if args.select:
        s = args.select.lower()
        cases = [c for c in cases if s in c.id.lower() or s in c.category.lower()]
    if not cases:
        print("no cases selected")
        return 2

    ref = PyteTarget()

    if args.mode == "selftest":
        return run_selftest(cases, ref)

    if args.mode == "audit":
        results = run_audit(cases, ref)
        summary = summarize_audit(results)
        summary["mode"] = "audit"
        summary["reference"] = ref.name
        print_audit(summary, results, args.verbose)
        _write_json(args.json, summary, results)
        if args.strict and summary["counts"].get("spec-suspect"):
            return 1
        return 0

    # differential (F vs P)
    firmware = runner.make_target(args.firmware)
    results = []
    try:
        with firmware:
            for c in cases:
                results.append(run_differential_case(firmware, ref, c))
    except Exception as exc:  # noqa: BLE001 -- boot/build failures get a clear message
        print(f"\n!! could not run the differential against firmware target "
              f"{args.firmware!r}: {type(exc).__name__}: {exc}")
        print("   The differential needs a built firmware disk (run `make` in "
              "vt100-term-c/ to produce build/vt100.dsk) and MAME on PATH; the "
              "MAME-free `--audit` mode needs neither.")
        return 1

    summary = runner.summarize(results)
    summary["mode"] = "differential"
    summary["target"] = f"{args.firmware}-vs-pyte"
    print("\nNOTE: the metrics below are firmware-vs-pyte *visible* agreement -- a"
          " glyph/inverse/\ncursor diff against an independent reference, bucketed by"
          " the runner's spec/basis\nlabels. It is strong evidence for spec conformance"
          " but is mediated by pyte's own\ncorrectness; cases whose only assertions are"
          " pyte-unobservable (state, report) are\nSKIPped, not scored.")
    runner.print_report(summary, results, args.verbose)
    _write_json(args.json, summary, results)

    o = summary["by_outcome"]
    if o.get(runner.REGRESSION) or o.get(runner.ERROR):
        return 1
    if args.strict and o.get(runner.UPASS):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
