#!/usr/bin/env python3
"""FakeTarget -- an in-process render target for testing the runner offline.

It does not emulate a terminal; it returns whatever :class:`Screen` a supplied
``responder`` produces for the input bytes (default: a blank screen). This lets
``selftest.py`` drive every classification branch (PASS / REGRESSION / XFAIL /
UNEXPECTED_PASS / SKIP) deterministically, with no MAME, so the runner and the
xfail/progress accounting are verifiable in CI on any host.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from model import Screen  # noqa: E402
from target_base import Target, Capabilities  # noqa: E402

# Full-capability by default so the offline tests can exercise every expect key.
FULL_CAPS = Capabilities(glyphs=True, inverse=True, cursor=True,
                         reports=True, state=True)


class FakeTarget(Target):
    name = "fake"
    caps = FULL_CAPS

    def __init__(self, responder=None, caps: Capabilities = FULL_CAPS):
        self.responder = responder or (lambda data: Screen.blank())
        self.caps = caps

    def render(self, data: bytes) -> Screen:
        return self.responder(bytes(data))
