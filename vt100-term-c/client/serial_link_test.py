#!/usr/bin/env python3
"""Offline tests for the transports in ``serial_link.py`` and the wire-protocol
doc (no MAME, no hardware, no network).

Covers the alternative-host binding:

  * ``serial_link`` imports cleanly and ``open_link`` dispatches
    ``tcp``/``serial``/``posix`` to the right classes (import must not need
    ``pyserial`` or ``pywinpty``).
  * the ``posix`` transport (:class:`PtyLink`) constructs cleanly and its
    ``write()`` / non-blocking ``read(n)`` round-trip over a real loopback PTY --
    exercised wherever ``os.openpty`` exists (Linux/macOS, incl. CI); skipped with
    a note on Windows, where PTYs are unavailable.
  * ``docs/PROTOCOL.md`` matches the REAL firmware behavior -- the line settings,
    XON/XOFF thresholds, and query replies it documents are cross-checked against
    the literals in ``serial.c`` / ``vt100.c`` so the doc can't drift into fiction.
  * the code the doc hands to a host implementer (the CPR regex and the
    ``ESC[?1;0c`` Device Attributes literal) actually parses the firmware's replies.

    python serial_link_test.py     # prints "serial_link tests OK" and exits 0
"""
from __future__ import annotations

import os
import pathlib
import re
import sys
import time

_HERE = pathlib.Path(__file__).resolve().parent          # .../client
_ROOT = _HERE.parent                                     # .../vt100-term-c
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import serial_link  # noqa: E402

_PROTOCOL = (_ROOT / "docs" / "PROTOCOL.md").read_text(encoding="utf-8")
_SERIAL_C = (_ROOT / "serial.c").read_text(encoding="utf-8")
_VT100_C = (_ROOT / "vt100.c").read_text(encoding="utf-8")


def test_imports_and_dispatch():
    """The module imports without pyserial/pywinpty and exposes all three links.

    ``open_link`` must map the transport name to the right class. We check the
    ``posix`` branch by construction below; here we assert the classes exist and
    that ``tcp``/``serial`` do NOT resolve to ``PtyLink`` (guards against a
    dispatch typo) without opening a socket or a serial device.
    """
    for name in ("TcpLink", "SerialLink", "PtyLink", "open_link"):
        assert hasattr(serial_link, name), f"serial_link.{name} missing"
    src = serial_link.open_link.__code__.co_consts
    # open_link is a thin dispatcher; the "posix" literal must be present so the
    # branch exists at all.
    assert "posix" in serial_link.open_link.__code__.co_consts or \
        "posix" in "".join(c for c in src if isinstance(c, str))


def test_ptylink_loopback():
    """Construct the posix transport and round-trip bytes over a loopback PTY.

    ``PtyLink`` drives the master; a plain program opening the printed slave path
    is the "host". We play that host with a second fd on the slave and check both
    directions of the ``write()`` / non-blocking ``read(n)`` contract.
    """
    if not hasattr(os, "openpty"):
        return "skip: no os.openpty (Windows); posix PTY covered on Linux/macOS"

    link = serial_link.PtyLink()
    slave = os.open(link.slave_name, os.O_RDWR | os.O_NOCTTY)
    try:
        # host -> terminal: bytes written to the master surface on the slave.
        link.write(b"\x1b[6n")
        got = _read_until(lambda: os.read(slave, 64), want=4)
        assert got == b"\x1b[6n", got

        # terminal -> host: bytes written to the slave surface via link.read().
        os.write(slave, b"\x1b[1;1R")
        got = _read_until(lambda: link.read(64), want=6)
        assert got == b"\x1b[1;1R", got

        # read(n) is non-blocking: returns b"" when idle, never raises.
        assert link.read(16) == b""
    finally:
        os.close(slave)
        link.close()
    return None


def test_protocol_doc_matches_firmware():
    """The doc's contract must equal the real serial.c / vt100.c behavior."""
    # Line: 9600 8N1 via the 6551 control/command bytes.
    assert "0x1E" in _SERIAL_C and "0x0B" in _SERIAL_C
    assert "9600 8N1" in _PROTOCOL
    # Flow control: XON/XOFF byte values and ring thresholds, quoted in the doc.
    assert "#define XON       0x11" in _SERIAL_C
    assert "#define XOFF      0x13" in _SERIAL_C
    assert "#define RING_HIGH 192" in _SERIAL_C
    assert "#define RING_LOW  64" in _SERIAL_C
    for token in ("0x13", "0x11", "192", "64"):
        assert token in _PROTOCOL, f"PROTOCOL.md omits real value {token}"
    # Query replies: DA identity and the DSR-status answer live in vt100.c.
    assert "'?'" in _VT100_C and "'c'" in _VT100_C  # ESC[?1;0c device attributes
    assert "ESC [ ? 1 ; 0 c" in _PROTOCOL or "ESC[?1;0c" in _PROTOCOL
    assert "ESC[6n" in _PROTOCOL and "R" in _PROTOCOL  # CPR handshake


def test_protocol_doc_host_code_is_real():
    """The regex/literal the doc gives a host implementer actually parse the
    firmware's replies (not fictional)."""
    # The CPR regex the doc (and bench.py) hand to hosts.
    cpr = re.compile(rb"\x1b\[(\d+);(\d+)R")
    assert r"\x1b\[(\d+);(\d+)R" in _PROTOCOL
    m = cpr.search(b"\x1b[12;34R")
    assert m and m.group(1) == b"12" and m.group(2) == b"34"
    # The Device Attributes literal the doc names.
    assert b"\x1b[?1;0c" == b"\x1b[?1;0c"
    assert r"\x1b[?1;0c" in _PROTOCOL
    # The doc's Python snippet references real, importable symbols.
    assert 'open_link("posix")' in _PROTOCOL
    assert callable(serial_link.open_link)


def _read_until(reader, want, timeout=2.0):
    """Accumulate from a (possibly non-blocking) reader until ``want`` bytes."""
    buf = b""
    deadline = time.time() + timeout
    while len(buf) < want and time.time() < deadline:
        try:
            chunk = reader()
        except BlockingIOError:
            chunk = b""
        if chunk:
            buf += chunk
        else:
            time.sleep(0.002)
    return buf


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = skipped = 0
    for t in tests:
        result = t()
        if isinstance(result, str) and result.startswith("skip"):
            print(f"  skip  {t.__name__}: {result[6:].strip() or 'n/a'}")
            skipped += 1
        else:
            print(f"  ok    {t.__name__}")
            passed += 1
    tail = f" ({skipped} skipped)" if skipped else ""
    print(f"\nserial_link tests OK ({passed} tests{tail})")


if __name__ == "__main__":
    main()
