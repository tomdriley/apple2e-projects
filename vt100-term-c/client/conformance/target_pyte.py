#!/usr/bin/env python3
"""``PyteTarget`` -- render corpus bytes through the pyte reference terminal.

pyte (https://github.com/selectel/pyte) is a pure-Python ECMA-48 screen model.
It knows nothing about this firmware or the hand-authored ``expect`` blocks, so
its rendering is an *independent* second opinion on "what should this byte stream
display" -- the reference oracle for issue #18.

pyte is a screen *model*, not an interactive terminal: it has no transmit channel
(so it cannot answer DSR/DA reports) and no firmware state variables. Those
channels are therefore marked unsupported in :class:`Capabilities`; the runner and
oracle treat them as *not checkable* on this target rather than failed.

Verified against pyte 0.8.2 (see client/requirements.txt). Known modelling gaps
in that version (alt-screen buffer switch, DEC Special Graphics charset) are
declared in ``oracle_quirks`` so pyte is never treated as infallible.
"""
from __future__ import annotations

import pathlib
import sys

import pyte

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from model import COLS, ROWS, Screen  # noqa: E402
from target_base import Capabilities, Target  # noqa: E402


class PyteTarget(Target):
    """Render bytes with pyte and adapt its screen to our :class:`Screen`."""

    name = "pyte"
    # Glyphs, the inverse (reverse-video) plane and the cursor are observable;
    # pyte has no wire reports and no firmware state to probe.
    caps = Capabilities(glyphs=True, inverse=True, cursor=True,
                        reports=False, state=False)

    def render(self, data: bytes) -> Screen:
        screen = pyte.Screen(COLS, ROWS)
        stream = pyte.ByteStream(screen)
        stream.feed(data)

        # Glyph plane: pyte's display is ROWS strings already padded to COLS.
        text = list(screen.display)

        # Inverse plane: pyte carries reverse video as a per-cell attribute.
        # buffer is a sparse defaultdict; a missing cell yields the default
        # (non-reverse) Char, so indexing every column is safe.
        inverse = []
        for y in range(ROWS):
            row = screen.buffer[y]
            inverse.append("".join("1" if row[x].reverse else "0"
                                   for x in range(COLS)))

        # pyte's cursor is 0-based (x=col, y=row); ours is 1-based (row, col).
        cursor = (screen.cursor.y + 1, screen.cursor.x + 1)

        return Screen(text=text, inverse=inverse, cursor=cursor,
                      reports=b"", state={})


if __name__ == "__main__":
    # `python target_pyte.py` renders a couple of sequences as a smoke test.
    t = PyteTarget()
    s = t.render(b"\x1b[2J\x1b[3;5HHI\x1b[7mX")
    assert s.row(3)[4:7] == "HIX", s.row(3)      # HI at cols 5,6; X at col 7
    assert s.cursor == (3, 8), s.cursor          # cursor advances past the X
    assert s.inv_row(3)[6] == "1", s.inv_row(3)  # col 7 (the X) is reverse video
    assert s.inv_row(3)[4] == "0", s.inv_row(3)  # col 5 (the H) is normal
    print("PyteTarget smoke test OK:", s.cursor, repr(s.row(3)[:8]))
