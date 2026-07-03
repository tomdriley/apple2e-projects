#!/usr/bin/env python3
"""Bridge a WSL bash session to the Apple IIe VT100 terminal.

pywinpty runs `wsl.exe` bash on an 80x24 pseudo-terminal (ConPTY) and relays it
to the terminal over tcp (MAME's null_modem socket) or serial (real hardware) --
turning the Apple into a login console for Linux.

    python vt100_shell.py tcp            # with MAME (this listens; then boot MAME)
    python vt100_shell.py serial         # real hardware, auto-detect the port
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time

from serial_link import open_link


def wait_ready(link, timeout: float = 25.0) -> bool:
    """Handshake with the terminal (ESC[6n) so bash only starts once the terminal
    is booted and reading -- otherwise its first output is lost to the 6551."""
    print("[handshaking with the terminal (ESC[6n) ...]", file=sys.stderr)
    deadline = time.time() + timeout
    buf = b""
    while time.time() < deadline:
        link.write(b"\x1b[6n")
        stop = time.time() + 0.5
        while time.time() < stop:
            buf += link.read(64)
            if b"\x1b[" in buf and b"R" in buf:
                print("[terminal ready]", file=sys.stderr)
                return True
            time.sleep(0.02)
    print("[no response; starting bash anyway]", file=sys.stderr)
    return False


def run(link, term: str) -> None:
    import winpty

    env = dict(os.environ)
    env["TERM"] = term
    argv = ["wsl.exe", "-e", "bash", "-lic",
            f"export TERM={term}; stty rows 24 cols 80 ixon 2>/dev/null; exec bash -i"]

    wait_ready(link)
    proc = winpty.PtyProcess.spawn(argv, env=env, dimensions=(24, 80))
    print("[bash started; Ctrl+C to quit]", file=sys.stderr)
    stop = threading.Event()

    def pump_pty():
        while not stop.is_set():
            try:
                data = proc.read(4096)  # str (ConPTY is UTF-8)
            except EOFError:
                break
            if data:
                link.write(data.encode("utf-8", "replace"))
            elif not proc.isalive():
                break
            else:
                time.sleep(0.005)
        stop.set()

    threading.Thread(target=pump_pty, daemon=True).start()
    try:
        while not stop.is_set():
            data = link.read(1024)  # bytes from the Apple keyboard
            if data:
                proc.write(data.decode("latin-1"))
            elif not proc.isalive():
                break
            else:
                time.sleep(0.005)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        try:
            proc.terminate(force=True)
        except Exception:
            pass


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("transport", choices=["tcp", "serial"])
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=6551)
    p.add_argument("--device", help="serial port (auto-detected if omitted)")
    p.add_argument("--baud", type=int, default=9600)
    p.add_argument("--term", default="vt100")
    args = p.parse_args()

    link = open_link(args.transport, args.host, args.port, args.device, args.baud)
    try:
        run(link, args.term)
    finally:
        link.close()


if __name__ == "__main__":
    main()
