"""Declared pyte quirks -- where the reference itself is wrong or incomplete (#18).

The oracle uses pyte as an *independent* source of truth, but pyte is not a perfect
VT100. Where pyte is demonstrably wrong or incomplete, we say so here so that:

* a pyte/firmware disagreement in the differential is skipped as a *pyte quirk*
  rather than counted as a firmware regression, and
* a pyte/authored-expect disagreement in the audit is attributed to pyte rather
  than mistaken for a corpus-authoring bug.

These are *only* cases where pyte 0.8.2 is demonstrably wrong or incomplete, excluded
from scoring pending a documented pyte limitation -- each entry below states the specific
defect (what pyte does vs. what the spec requires). Cases where the **firmware**
intentionally diverges from strict spec are deliberately NOT handled here: the case's
``basis`` field (``profile``/``tolerance``/``degenerate`` in ``model.VALID_BASIS``)
carries those, so a genuine firmware non-conformance still surfaces in the audit instead
of being silently suppressed by a hand-maintained allowlist. The audit prints how many
``spec``-basis cases these exclusions remove from the denominator (``spec coverage``), so
the effect on the headline is visible rather than hidden.

Every entry was produced by running ``oracle.py`` over the corpus and probing pyte
0.8.2 directly (see client/requirements.txt). Re-verify when the pin moves -- especially
NEL, HPA and SCOSC/SCORC, which are the pyte-version-sensitive ones.
"""
from __future__ import annotations

# Whole categories pyte 0.8.2 cannot faithfully model for this corpus.
QUIRK_CATEGORIES = {
    "alt-screen":
        "pyte 0.8.2 does not switch or restore the alternate screen buffer "
        "(?47/?1047/?1049): it writes onto the primary buffer and never "
        "restores, so save/restore round-trips cannot be oracled.",
    "charset-linedraw":
        "pyte 0.8.2 does not apply the DEC Special Graphics charset (ESC(0): "
        "it passes j-x through as ASCII letters instead of line-drawing glyphs, "
        "so it cannot judge the firmware's ASCII fold.",
    "dcs-strings":
        "pyte 0.8.2 does not consume DCS device-control strings (ESC P ... ST): "
        "it renders the payload bytes straight to the screen instead of "
        "suppressing them, so DCS payload-absence cannot be oracled by pyte.",
}

# Finer-grained per-case pyte deviations (id -> reason), found by the audit run.
# pyte is the demonstrably-unreliable side for these (specific defect per entry).
QUIRK_IDS: dict[str, str] = {
    "cur-vt100-nel":
        "pyte 0.8.2 ESC E (NEL) indexes down without a carriage return (it "
        "behaves like IND); a real VT100 NEL is CR+LF, so the column should reset.",
    "esc-nel-next-line":
        "same pyte NEL quirk: ESC E moves down but keeps the column instead of "
        "returning to column 1.",
    "cur-vt100-hpa":
        "pyte 0.8.2 does not implement HPA (CSI Pn '`'); it ignores the sequence. "
        "HPA is equivalent to CHA (CSI Pn 'G'), which pyte does implement.",
    "cur-hpa-clamps-right":
        "same pyte HPA quirk: CSI 99 '`' is ignored instead of clamping to column 80.",
    "cur-hpa-default-column-one":
        "same pyte HPA quirk: CSI '`' with no parameter is ignored instead of "
        "moving to column 1.",
    "cur-desc-decrc-csi-s-u":
        "pyte 0.8.2 does not implement SCOSC/SCORC (CSI s / CSI u) cursor "
        "save-restore; it reads CSI s as DECSLRM (set margins) and drops CSI u.",
    "cur-vt100-save-restore":
        "same pyte SCOSC/SCORC quirk (CSI s / CSI u are not honored as "
        "save/restore-cursor).",
    "scroll-su-pan-unsupported":
        "pyte 0.8.2 does not implement SU (CSI S); it leaves the buffer "
        "unchanged, so it cannot model the spec-correct scroll-up this case "
        "expects (both pyte and the firmware fail to scroll, for different "
        "reasons).",
    "scroll-sd-pan-unsupported":
        "pyte 0.8.2 does not implement SD (CSI T); it leaves the buffer "
        "unchanged, so it cannot model the spec-correct scroll-down this case "
        "expects.",
}


def quirk_reason(case) -> str | None:
    """Return why pyte is unreliable for ``case``, or None if pyte is trusted.

    Category-level quirks take precedence over per-id ones (they are broader).
    """
    if case.category in QUIRK_CATEGORIES:
        return QUIRK_CATEGORIES[case.category]
    return QUIRK_IDS.get(case.id)
