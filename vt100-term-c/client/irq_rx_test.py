#!/usr/bin/env python3
"""Discriminating ROM-backed test for receive during a no-pump CPU stall."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "conformance"))

from target_mame import MameTarget  # noqa: E402


MARKER = b"IRQ-BURST-0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ"
EXPECTED_ROW = 5
EXPECTED_COL = 10
EXPECTED_CURSOR = (5, 57)


def run_once(port: int) -> list[str]:
    target = MameTarget(port=port)
    failures: list[str] = []
    try:
        target.open()
        target.reset()
        target.render(b"\x1b[2J\x1b[5;10H")

        # BEL enters beep(), whose deliberate CPU delay has no serial_pump().
        # Send the marker only after that delay has started, not in the same
        # transport window where it could be buffered before BEL is dispatched.
        target.term.send(b"\x07")
        time.sleep(0.040)
        target.term.send(MARKER)
        time.sleep(0.250)

        screen = target.render(b"")
        got = screen.text[EXPECTED_ROW - 1][
            EXPECTED_COL - 1 : EXPECTED_COL - 1 + len(MARKER)
        ].encode("ascii", errors="replace")
        if got != MARKER:
            failures.append(f"marker expected {MARKER!r}, got {got!r}")
        if screen.cursor != EXPECTED_CURSOR:
            failures.append(
                f"cursor expected {EXPECTED_CURSOR}, got {screen.cursor}"
            )
        for name, want in (
            ("serial_irq_active", 1),
            ("serial_irq_seen", 1),
            ("ring_drop_count", 0),
        ):
            got_state = screen.state.get(name)
            if got_state != want:
                failures.append(f"state {name} expected {want}, got {got_state}")
    finally:
        target.close()
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=1, help="fresh MAME boots")
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("MAME_PORT", "6551"))
    )
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be at least 1")

    failed = 0
    for run in range(1, args.runs + 1):
        failures = run_once(args.port)
        if failures:
            failed += 1
            print(f"FAIL run {run}/{args.runs}")
            for failure in failures:
                print(f"  - {failure}")
        else:
            print(f"PASS run {run}/{args.runs}")

    if failed:
        print(f"irq_rx_test: FAIL ({failed}/{args.runs} boots)")
        return 1
    print(f"irq_rx_test: PASS ({args.runs}/{args.runs} boots)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
