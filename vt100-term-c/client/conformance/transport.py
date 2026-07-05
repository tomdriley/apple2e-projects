#!/usr/bin/env python3
"""Shared transport for the conformance runner.

Reuses the existing client infrastructure rather than reimplementing it:
  * ``open_link`` / ``TcpLink`` / ``SerialLink`` -- ``serial_link.py``
  * ``Terminal`` + tuning constants              -- ``bench.py``
    (the windowed-lossless sender, XON/XOFF-aware drain, ``send_windowed``,
    and the ``sync``/``wait_cpr`` DSR helpers)

so the corpus streams to the firmware exactly like ``bench.py``'s benchmarks do
-- lossless on scroll-heavy input, paced to the render, never overrunning the
6551's single-byte receiver.
"""
from __future__ import annotations

import pathlib
import socket
import sys

_CLIENT = pathlib.Path(__file__).resolve().parent.parent  # .../client
if str(_CLIENT) not in sys.path:
    sys.path.insert(0, str(_CLIENT))

# Re-exported for the targets/runner. These imports have no side effects at
# import time (bench.py guards its CLI behind __main__).
from serial_link import open_link, TcpLink, SerialLink  # noqa: E402,F401
from bench import (  # noqa: E402,F401
    Terminal,
    WINDOW,
    OP_TIMEOUT,
    CPR,
    XON,
    XOFF,
    MAME,
    ROMPATH,
    PORT,
)


def listen(port: int = PORT, host: str = "127.0.0.1", timeout: float = 60.0):
    """Create a listening socket for MAME's ``null_modem`` to connect out to.

    MAME is the TCP *client* (``-bitb socket.host:port``), so the harness must
    already be listening when MAME boots -- mirroring ``bench.py`` /
    ``shell_test.py``.
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(1)
    srv.settimeout(timeout)
    return srv


def chunk_bytes(data: bytes, size: int = 16) -> list[bytes]:
    """Split a payload into atomic chunks for ``Terminal.send_windowed``.

    A conformance case is short, so fixed-size chunking is fine; we only need the
    windowing so a DSR ack paces us and the firmware ring never overflows. Chunks
    are kept small enough that a window boundary never lands mid-escape for the
    tiny inputs the corpus uses (each case is already a handful of sequences).
    """
    return [data[i:i + size] for i in range(0, len(data), size)]
