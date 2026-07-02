#!/usr/bin/env python3
"""Host-side driver / live demo for the Apple IIe VT100 terminal.

Transports (same idea as ssc-serial-c/client/serial_demo.py):
  tcp    - listen for MAME's null_modem socket (start this first, then MAME)
  serial - talk to real hardware via a USB/RS-232 adapter (auto-detects the port)

Modes:
  demo   - draw an 80x24 screen (title bar, a box, a live uptime counter) using
           VT100/ANSI escape codes, echo the Apple's keystrokes back onto its
           screen, and print them here too. Shows rendering + the round trip.
  relay  - dumb relay: forward typed lines to the Apple, print what comes back.

Examples:
  python vt100_host.py demo tcp                # with MAME (then `make run`)
  python vt100_host.py demo serial             # real hardware, auto-detect port
  python vt100_host.py relay serial --device COM3 --baud 9600
"""
from __future__ import annotations

import argparse
import socket
import sys
import time


def cup(row: int, col: int) -> bytes:
    """ESC[row;colH - move the cursor (1-based)."""
    return b"\x1b[" + str(row).encode() + b";" + str(col).encode() + b"H"


def center(text: str, width: int) -> bytes:
    text = text[:width]
    pad = width - len(text)
    left = pad // 2
    return b" " * left + text.encode("ascii", "replace") + b" " * (pad - left)


class TcpLink:
    """Listen for MAME's null_modem to connect out to us."""

    def __init__(self, host: str, port: int) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((host, port))
        srv.listen(1)
        print(f"[waiting for MAME on {host}:{port} ...]", file=sys.stderr)
        self.conn, addr = srv.accept()
        srv.close()
        print(f"[connected: {addr[0]}:{addr[1]}]", file=sys.stderr)
        self.conn.setblocking(False)

    def write(self, data: bytes) -> None:
        self.conn.sendall(data)

    def read(self, n: int = 64) -> bytes:
        try:
            return self.conn.recv(n)
        except BlockingIOError:
            return b""
        except OSError:
            return b""

    def close(self) -> None:
        try:
            self.conn.close()
        except OSError:
            pass


class SerialLink:
    """Talk to a USB/RS-232 adapter wired to the Super Serial Card."""

    def __init__(self, device: str | None, baud: int) -> None:
        try:
            import serial
            from serial.tools import list_ports
        except ImportError:
            sys.exit("pyserial is not installed. Run: pip install pyserial")
        if not device:
            ports = [p for p in list_ports.comports() if getattr(p, "vid", None)]
            ports = ports or list(list_ports.comports())
            if not ports:
                sys.exit("No serial ports found - is the USB/RS-232 adapter in?")
            device = ports[0].device
            print(f"[auto-detected {device}]", file=sys.stderr)
        self.ser = serial.Serial(device, baud, timeout=0)
        print(f"[open {device} @ {baud} 8N1 - Ctrl+C to quit]", file=sys.stderr)

    def write(self, data: bytes) -> None:
        self.ser.write(data)

    def read(self, n: int = 64) -> bytes:
        return self.ser.read(n)

    def close(self) -> None:
        self.ser.close()


def wait_ready(link, timeout: float = 20.0) -> bool:
    """Poll the terminal with a cursor-position request until it answers, so we
    only start drawing once it is actually booted and reading."""
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
    print("[no response - drawing anyway]", file=sys.stderr)
    return False


ECHO_ROW = 15
ECHO_COL = 5


def draw_screen(link) -> None:
    bar = b"+" + b"-" * 78 + b"+"
    link.write(b"\x1b[2J")
    link.write(cup(1, 1) + bar)
    link.write(cup(2, 1) + b"|" + center("APPLE IIe VT100 TERMINAL  --  LIVE DEMO", 78) + b"|")
    link.write(cup(3, 1) + bar)
    link.write(cup(5, 3) + b"This 80-column screen is drawn by the PC using VT100/ANSI codes:")
    link.write(cup(7, 6) + b"ESC[2J     clear the screen")
    link.write(cup(8, 6) + b"ESC[r;cH   move the cursor to row r, column c")
    link.write(cup(9, 6) + b"ESC[K      erase to end of line")
    link.write(cup(11, 3) + b"Uptime:")
    link.write(cup(13, 3) + b"Type on the Apple keyboard - it echoes here and on the PC:")
    link.write(cup(ECHO_ROW, 3) + b"> ")


def run_demo(link) -> None:
    wait_ready(link)
    draw_screen(link)
    link.write(cup(ECHO_ROW, ECHO_COL))  # park the cursor after the "> " prompt
    start = time.time()
    last = -1
    try:
        while True:
            secs = int(time.time() - start)
            if secs != last:
                last = secs
                # Update the counter without disturbing the echo cursor: save
                # cursor, jump to the field, write, restore cursor (ESC[s / ESC[u).
                link.write(b"\x1b[s" + cup(11, 11)
                           + f"{secs} second(s)   ".encode() + b"\x1b[u")
            data = link.read(64)
            if data:
                # Echo verbatim to BOTH the Apple and the PC so they stay in
                # lock-step: arrow keys move the cursor on both, and Enter starts
                # a new line on both (CR -> CRLF).
                out = data.replace(b"\r", b"\r\n")
                sys.stdout.buffer.write(out)
                sys.stdout.buffer.flush()
                link.write(out)
            time.sleep(0.02)
    except KeyboardInterrupt:
        pass


def run_relay(link) -> None:
    print("[relay: type a line + Enter to send; received bytes print below]",
          file=sys.stderr)
    import threading

    def reader():
        while True:
            data = link.read(128)
            if data:
                sys.stdout.write(data.decode("ascii", "replace"))
                sys.stdout.flush()
            else:
                time.sleep(0.02)

    threading.Thread(target=reader, daemon=True).start()
    try:
        for line in sys.stdin:
            link.write(line.rstrip("\n").encode("ascii", "replace") + b"\r")
    except KeyboardInterrupt:
        pass


def make_link(args):
    if args.transport == "tcp":
        return TcpLink(args.host, args.port)
    return SerialLink(args.device, args.baud)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mode", choices=["demo", "relay"])
    p.add_argument("transport", choices=["tcp", "serial"])
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=6551)
    p.add_argument("--device", help="serial port (auto-detected if omitted)")
    p.add_argument("--baud", type=int, default=9600)
    args = p.parse_args()

    link = make_link(args)
    try:
        if args.mode == "demo":
            run_demo(link)
        else:
            run_relay(link)
    finally:
        link.close()


if __name__ == "__main__":
    main()
