#!/usr/bin/env python3
"""Render-target interface: ``render(bytes) -> Screen``.

A *target* turns a case's input bytes into a rendered :class:`Screen`. The runner
is target-agnostic: it asks each target for its :class:`Capabilities` so an
expectation the target physically cannot observe (e.g. the inverse plane on a
serial-only connection) is reported as *not checkable* rather than failed.

Targets in this workstream:
  * ``MameTarget``   -- firmware in headless MAME (the comprehensive target).
  * ``SerialTarget`` -- a real Apple IIe; only the report/cursor subset.
  * ``PyteTarget``   -- the pyte reference oracle.
"""
from __future__ import annotations

import pathlib
import sys
from dataclasses import dataclass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from model import Screen  # noqa: E402


@dataclass(frozen=True)
class Capabilities:
    """Which observation channels a target supports.

    The runner uses this to decide, per expectation, whether the target can
    check it. A case asserting ``attr`` against a target with ``inverse=False``
    is skipped on that target (counted as not-checkable), never failed.
    """

    glyphs: bool = True    # read the glyph plane
    inverse: bool = False  # read the inverse-attribute plane
    cursor: bool = True    # report cursor position (DSR and/or state probe)
    reports: bool = True   # capture wire read-back (DSR/DA/...)
    state: bool = False    # probe firmware state variables from RAM


# Map each expect key to the capability it needs. Keys absent here (content
# checks that only need the glyph plane) require ``glyphs``.
EXPECT_CAPABILITY = {
    "cursor": "cursor",
    "rows": "glyphs",
    "cells": "glyphs",
    "has": "glyphs",
    "absent": "glyphs",
    "attr": "inverse",
    "state": "state",
    "report": "reports",
    "report_absent": "reports",
}


class Target:
    """Abstract render target. Subclasses implement :meth:`render`."""

    name = "base"
    caps = Capabilities()

    def open(self) -> None:
        """Start up and block until the terminal is ready to accept input."""

    def reset(self) -> None:
        """Return the screen to a known blank state between cases."""

    def render(self, data: bytes) -> Screen:
        """Send ``data`` to the terminal, let it settle, and return the Screen."""
        raise NotImplementedError

    def close(self) -> None:
        """Tear down (terminate MAME, close the socket, ...)."""

    def supports(self, expect_key: str) -> bool:
        need = EXPECT_CAPABILITY.get(expect_key, "glyphs")
        return bool(getattr(self.caps, need))

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()
