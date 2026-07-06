#!/usr/bin/env python3
"""Data model for the spec-driven conformance corpus.

The corpus is *data*: each case is a declarative record authored from the spec
(ECMA-48 / VT100 / xterm ctlseqs), stored as JSON under ``corpus/`` and decoupled
from both the runner and the firmware. A render target turns a case's input bytes
into a :class:`Screen`; the case's ``expect`` block is then checked against that
Screen (plus captured wire reports and probed firmware state) entirely by machine
-- there is no human review and no reference emulator in this workstream.

Nothing here talks to MAME or a serial port; this module is pure data + checking
so it can be unit-tested on any host without the emulator.
"""
from __future__ import annotations

import json
import pathlib
import re
from dataclasses import dataclass, field
from typing import Optional

ROWS, COLS = 24, 80

VALID_STATUS = ("supported", "partial", "unsupported")

# The *basis* of a case's expected value -- what a PASS actually means. `status`
# drives the pass/fail classification; `basis` is orthogonal and drives how the
# pass is *counted*, so a relabel cannot silently inflate spec conformance.
#   spec         strict VT100/ECMA-48 behaviour, hardware-independent. The firmware
#                either matches it (real conformance) or should (a clean XFAIL).
#   profile      an ECMA-permitted, documented Apple IIe degradation (monochrome
#                SGR ignore, DEC line-draw -> ASCII fold). Conformant *as a profile*.
#   tolerance    a sequence safely absorbed as an observable no-op in this context
#                (e.g. SO/SI with the default G1); "does not corrupt", not "implements".
#   degenerate   passes only because the firmware default coincides with the tested
#                direction (e.g. a mode's reset direction while the mode is ignored);
#                does NOT prove the feature. Excluded from spec/profile conformance.
#   unobservable the real effect cannot be probed (e.g. DECTCEM cursor visibility);
#                scored as SKIP so an untested claim is never counted as conformance.
VALID_BASIS = ("spec", "profile", "tolerance", "degenerate", "unobservable")

# Every key the ``expect`` block may contain. The loader validates against this
# set so a typo in a corpus file is caught at load time rather than silently
# skipped (a silently-ignored expectation would inflate the conformance score).
EXPECT_KEYS = ("cursor", "rows", "cells", "has", "absent", "attr", "state", "report")

ATTR_KINDS = ("inverse", "normal")


# --------------------------------------------------------------------------
# input mini-decoder
# --------------------------------------------------------------------------
# Corpus ``input`` (and the ``report`` expectation) are written as readable,
# diffable strings using backslash escapes rather than raw control bytes in the
# JSON. This keeps a case like "\e[2J\e[8;20H" legible in a code review.
_SIMPLE_ESCAPES = {
    "e": 0x1B,  # ESC -- the workhorse; not standard Python, added for readability
    "a": 0x07,  # BEL
    "b": 0x08,  # BS
    "t": 0x09,  # HT
    "n": 0x0A,  # LF
    "v": 0x0B,  # VT
    "f": 0x0C,  # FF
    "r": 0x0D,  # CR
    "0": 0x00,  # NUL
    "\\": 0x5C,
    '"': 0x22,
    "'": 0x27,
}


def decode(s: str) -> bytes:
    """Decode a corpus input/report string to raw bytes.

    Supported escapes: ``\\e`` (ESC), ``\\xNN`` (hex byte), ``\\a \\b \\t \\n
    \\v \\f \\r \\0`` and ``\\\\``. Any other ``\\x`` passes the following
    character through literally. Bytes >= 0x80 in ``\\xNN`` form are preserved
    verbatim (needed for the UTF-8 folding cases).
    """
    out = bytearray()
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n:
            nxt = s[i + 1]
            if nxt == "x":
                if i + 3 >= n:
                    raise ValueError(f"truncated \\x escape in {s!r}")
                out.append(int(s[i + 2:i + 4], 16))
                i += 4
                continue
            if nxt in _SIMPLE_ESCAPES:
                out.append(_SIMPLE_ESCAPES[nxt])
                i += 2
                continue
            # Unknown escape: pass the escaped char through literally.
            out.append(ord(nxt))
            i += 2
            continue
        out.append(ord(c) & 0xFF)
        i += 1
    return bytes(out)


# --------------------------------------------------------------------------
# Screen -- what a render target produces
# --------------------------------------------------------------------------
@dataclass
class Screen:
    """A rendered 80x24 screen plus the side-channels a target can observe.

    ``text``     : ROWS strings, each padded to COLS, the glyph plane.
    ``inverse``  : ROWS strings of '0'/'1' per cell (the inverse-attribute
                   plane); empty list if the target cannot report attributes.
    ``cursor``   : (row, col) 1-based, from the DSR reply and/or state probe.
    ``reports``  : raw bytes the terminal transmitted back over the wire.
    ``state``    : firmware variables probed from RAM, e.g. {"app_cursor": 1}.
    """

    text: list[str] = field(default_factory=list)
    inverse: list[str] = field(default_factory=list)
    cursor: Optional[tuple[int, int]] = None
    reports: bytes = b""
    state: dict = field(default_factory=dict)

    def row(self, n: int) -> str:
        """Row ``n`` (1-based) as a string, or '' if out of range."""
        return self.text[n - 1] if 1 <= n <= len(self.text) else ""

    def inv_row(self, n: int) -> str:
        return self.inverse[n - 1] if 1 <= n <= len(self.inverse) else ""

    @property
    def joined(self) -> str:
        return "\n".join(self.text)

    @classmethod
    def blank(cls) -> "Screen":
        return cls(text=[" " * COLS for _ in range(ROWS)],
                   inverse=["0" * COLS for _ in range(ROWS)])


# --------------------------------------------------------------------------
# Case -- one declarative corpus record
# --------------------------------------------------------------------------
@dataclass
class Case:
    id: str
    category: str
    spec_ref: str
    input: str
    status: str
    expect: dict = field(default_factory=dict)
    notes: str = ""
    source: str = ""  # corpus file the case came from (for diagnostics)
    # `basis` is appended last (with a default) so positional construction in the
    # offline selftest stays valid. See VALID_BASIS for the taxonomy.
    basis: str = "spec"

    @property
    def input_bytes(self) -> bytes:
        return decode(self.input)

    @property
    def is_xfail(self) -> bool:
        """partial/unsupported cases are *expected* to fail against the spec."""
        return self.status in ("partial", "unsupported")


# --------------------------------------------------------------------------
# expectation checking -- returns a list of human-readable failure strings
# --------------------------------------------------------------------------
# A trailing Cursor Position Report (ESC[<row>;<col>R) at the very end of the wire
# read-back -- the reply to a readiness/pacing ESC[6n probe. `check()` strips at
# most one of these before an exact report comparison (see the "report" branch).
_CPR_TAIL = re.compile(rb"\x1b\[\d+;\d+R\Z")


def check(screen: Screen, expect: dict) -> list[str]:
    """Compare a rendered Screen against an ``expect`` block.

    Returns [] when every declared expectation holds, else a list of failure
    descriptions. Only the keys present in ``expect`` are checked, so a case may
    assert just a cursor position, just screen content, just a report, etc.
    """
    fails: list[str] = []

    if "cursor" in expect:
        want = tuple(expect["cursor"])
        if screen.cursor != want:
            fails.append(f"cursor: expected {want} got {screen.cursor}")

    # rows: [[row, col, text], ...] -- text must appear at (row, col), 1-based.
    for row_n, col, text in expect.get("rows", []):
        got = screen.row(row_n)[col - 1: col - 1 + len(text)]
        if got != text:
            fails.append(f"row {row_n}@{col}: expected {text!r} got {got!r}")

    # cells: alias accepting the same shape as rows (kept for authoring clarity).
    for row_n, col, text in expect.get("cells", []):
        got = screen.row(row_n)[col - 1: col - 1 + len(text)]
        if got != text:
            fails.append(f"cell {row_n}@{col}: expected {text!r} got {got!r}")

    joined = screen.joined
    for text in expect.get("has", []):
        if text not in joined:
            fails.append(f"has: {text!r} not on screen")

    for text in expect.get("absent", []):
        if text in joined:
            fails.append(f"absent: {text!r} unexpectedly on screen")

    # attr: [[row, col, len, kind], ...] where kind in ATTR_KINDS.
    for row_n, col, length, kind in expect.get("attr", []):
        span = screen.inv_row(row_n)[col - 1: col - 1 + length]
        if not span:
            fails.append(f"attr {row_n}@{col}: no inverse-plane data "
                         "(target cannot report attributes)")
            continue
        want_bit = "1" if kind == "inverse" else "0"
        if span != want_bit * length:
            fails.append(f"attr {row_n}@{col}: expected {kind} "
                         f"({want_bit * length}) got {span}")

    for key, want in expect.get("state", {}).items():
        if key not in screen.state:
            fails.append(f"state {key}: not probed (target lacks state probe)")
        elif screen.state[key] != want:
            fails.append(f"state {key}: expected {want} got {screen.state[key]}")

    if "report" in expect:
        want_bytes = decode(expect["report"])
        got = screen.reports
        # EXACT match, not containment (issue #31). Containment quietly accepted a
        # doubled or malformed firmware reply (e.g. the reply concatenated with a
        # harness-injected readiness CPR, or the same reply emitted twice), so a
        # missing/garbled reply that merely *contained* the wanted bytes as a
        # substring passed unseen. The MameTarget no longer appends its own ESC[6n
        # probe to a case that already ends in a report query, so `got` should be
        # precisely the firmware's reply. The one documented allowance: a case whose
        # expected reply is NOT itself a CPR may still capture a single trailing
        # readiness CPR (ESC[<row>;<col>R) if a probe was unavoidable -- strip at most
        # one such trailing CPR and retry. Cases whose expected reply *is* a CPR get
        # no leniency, so a doubled CPR is still caught.
        ok = got == want_bytes
        if not ok and not _CPR_TAIL.search(want_bytes):
            ok = _CPR_TAIL.sub(b"", got) == want_bytes
        if not ok:
            fails.append(f"report: expected exactly {want_bytes!r} "
                         f"got {got!r}")

    return fails


# --------------------------------------------------------------------------
# corpus loading + validation
# --------------------------------------------------------------------------
CORPUS_DIR = pathlib.Path(__file__).resolve().parent / "corpus"


def _validate(case: Case) -> list[str]:
    errs: list[str] = []
    if not case.id:
        errs.append("missing id")
    if case.status not in VALID_STATUS:
        errs.append(f"{case.id}: bad status {case.status!r} "
                    f"(want one of {VALID_STATUS})")
    if case.basis not in VALID_BASIS:
        errs.append(f"{case.id}: bad basis {case.basis!r} "
                    f"(want one of {VALID_BASIS})")
    for k in case.expect:
        if k not in EXPECT_KEYS:
            errs.append(f"{case.id}: unknown expect key {k!r} "
                        f"(want subset of {EXPECT_KEYS})")
    for row_n, col, _length, kind in case.expect.get("attr", []):
        if kind not in ATTR_KINDS:
            errs.append(f"{case.id}: bad attr kind {kind!r} (want {ATTR_KINDS})")
    # Decoding the input validates its escape syntax up front.
    try:
        case.input_bytes
    except ValueError as exc:
        errs.append(f"{case.id}: bad input escape: {exc}")
    if "report" in case.expect:
        try:
            decode(case.expect["report"])
        except ValueError as exc:
            errs.append(f"{case.id}: bad report escape: {exc}")
    return errs


def load_corpus(corpus_dir: pathlib.Path = CORPUS_DIR,
                strict: bool = True) -> list[Case]:
    """Load and validate every ``corpus/*.json`` file.

    Each file is a JSON array of case objects. Raises ValueError on any schema
    problem (bad status, unknown expect key, duplicate id, malformed escape) so
    corpus mistakes fail loudly. Returns the cases sorted by (category, id).
    """
    cases: list[Case] = []
    errors: list[str] = []
    seen: dict[str, str] = {}

    files = sorted(corpus_dir.glob("*.json"))
    for path in files:
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{path.name}: invalid JSON: {exc}")
            continue
        if not isinstance(records, list):
            errors.append(f"{path.name}: top level must be a JSON array")
            continue
        for rec in records:
            try:
                case = Case(
                    id=rec["id"],
                    category=rec.get("category", path.stem),
                    spec_ref=rec.get("spec_ref", ""),
                    input=rec["input"],
                    status=rec["status"],
                    expect=rec.get("expect", {}),
                    notes=rec.get("notes", ""),
                    source=path.name,
                    basis=rec.get("basis", "spec"),
                )
            except KeyError as exc:
                errors.append(f"{path.name}: a record is missing key {exc}")
                continue
            if case.id in seen:
                errors.append(f"duplicate id {case.id!r} in {path.name} "
                              f"and {seen[case.id]}")
            seen[case.id] = path.name
            errors.extend(_validate(case))
            cases.append(case)

    if errors and strict:
        raise ValueError("corpus validation failed:\n  " + "\n  ".join(errors))
    cases.sort(key=lambda c: (c.category, c.id))
    return cases


if __name__ == "__main__":
    # `python model.py` self-checks the decoder and (if present) the corpus.
    assert decode(r"\e[2J") == b"\x1b[2J"
    assert decode(r"\x1b[8;20H") == b"\x1b[8;20H"
    assert decode(r"A\r\n") == b"A\r\n"
    assert decode(r"\xc3\xa9") == b"\xc3\xa9"  # UTF-8 e-acute, preserved
    print("decoder self-check OK")
    if CORPUS_DIR.exists():
        loaded = load_corpus()
        print(f"loaded {len(loaded)} cases from {CORPUS_DIR}")
