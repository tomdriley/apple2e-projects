#!/usr/bin/env python3
"""Automated VT100 terminal tests driven through MAME.

Boots build/vt100.dsk in headless MAME with the Super Serial Card wired to a
socket, then exercises the terminal over serial and checks the results.

  cursor tests  - send an operation, then ask the terminal where its cursor is
                  (ESC[6n -> ESC[row;colR) and compare to the expected position.
  keyboard tests- inject key presses in MAME and check the bytes the Apple sends
                  back over serial (run with --keys; uses client/keys.lua).

Requires the a2ssc ROM (see README) and -aux ext80. Exit status is nonzero if
any test fails, so it is CI-friendly.

    python vt100_test.py            # cursor tests
    python vt100_test.py --keys     # keyboard-input tests
"""
from __future__ import annotations

import argparse
import os
import re
import socket
import subprocess
import sys
import time
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
MAME = r"C:\mame\mame.exe"
ROMPATH = r"C:\mame\roms"
DISK = str(ROOT / "build" / "vt100.dsk")
KEYS_LUA = str(HERE / "keys.lua")
SSC_IRQ_LUA = str(HERE / "ssc_irq.lua")
PORT = int(os.environ.get("MAME_PORT", "6551"))

CPR = re.compile(rb"\x1b\[(\d+);(\d+)R")

# name, bytes to send (clear + setup + operation), expected (row, col) 1-based
CURSOR_TESTS = [
    ("home",        b"\x1b[2J\x1b[H",                (1, 1)),
    ("cup",         b"\x1b[2J\x1b[8;20H",            (8, 20)),
    ("cr",          b"\x1b[2J\x1b[5;10H\r",          (5, 1)),
    ("lf",          b"\x1b[2J\x1b[5;10H\n",          (6, 10)),
    ("backspace",   b"\x1b[2J\x1b[5;10H\b",          (5, 9)),
    ("cursor_fwd",  b"\x1b[2J\x1b[5;10H\x1b[3C",     (5, 13)),
    ("cursor_back", b"\x1b[2J\x1b[5;10H\x1b[4D",     (5, 6)),
    ("cursor_up",   b"\x1b[2J\x1b[5;10H\x1b[2A",     (3, 10)),
    ("cursor_down", b"\x1b[2J\x1b[5;10H\x1b[2B",     (7, 10)),
    ("tab",         b"\x1b[2J\x1b[1;1H\t",           (1, 9)),
    ("print_text",  b"\x1b[2J\x1b[3;5HHELLO",        (3, 10)),
    ("autowrap",    b"\x1b[2J\x1b[1;79HABC",         (2, 2)),
    ("ed2_preserves", b"\x1b[5;10H\x1b[2J",           (5, 10)),
    ("back_clamp",  b"\x1b[2J\x1b[1;3H\x1b[9D",      (1, 1)),
    ("fwd_clamp",   b"\x1b[2J\x1b[1;78H\x1b[9C",     (1, 80)),
    ("up_clamp",    b"\x1b[2J\x1b[1;5H\x1b[9A",      (1, 5)),
    ("save_restore", b"\x1b[2J\x1b[5;10H\x1b[s\x1b[1;1H\x1b[u", (5, 10)),
    ("cha",         b"\x1b[2J\x1b[5;10H\x1b[3G",      (5, 3)),
    ("hpa",         b"\x1b[2J\x1b[5;10H\x1b[7`",      (5, 7)),
    ("vpa",         b"\x1b[2J\x1b[5;10H\x1b[12d",     (12, 10)),
    ("cnl",         b"\x1b[2J\x1b[5;10H\x1b[3E",      (8, 1)),
    ("cpl",         b"\x1b[2J\x1b[5;10H\x1b[2F",      (3, 1)),
    ("ind",         b"\x1b[2J\x1b[5;10H\x1bD",        (6, 10)),
    ("ri",          b"\x1b[2J\x1b[5;10H\x1bM",        (4, 10)),
    ("nel",         b"\x1b[2J\x1b[5;10H\x1bE",        (6, 1)),
    ("ri_top",      b"\x1b[2J\x1b[1;5H\x1bM",         (1, 5)),
    ("ind_bottom",  b"\x1b[2J\x1b[24;5H\x1bD",        (24, 5)),
    ("decstbm_home", b"\x1b[2J\x1b[10;15H\x1b[5;20r", (1, 1)),
    ("region_reset", b"\x1b[r\x1b[2J\x1b[8;8H",       (8, 8)),
    ("ris",         b"\x1b[12;30H\x1bc",              (1, 1)),
]


def launch_mame(extra=None):
    cmd = [MAME, "apple2e", "-rompath", ROMPATH, "-aux", "ext80",
           "-sl2", "ssc", "-sl2:ssc:rs232", "null_modem",
           "-bitb", f"socket.127.0.0.1:{PORT}",
           "-flop1", DISK, "-video", "none", "-sound", "none",
           "-skip_gameinfo", "-str", "120"] + (extra or [])
    return subprocess.Popen(cmd, cwd=str(ROOT),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def drain(conn):
    conn.settimeout(0.05)
    try:
        while conn.recv(256):
            pass
    except (socket.timeout, OSError):
        pass


def query_cursor(conn, timeout=3.0):
    conn.sendall(b"\x1b[6n")
    conn.settimeout(0.2)
    buf = b""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            d = conn.recv(64)
        except socket.timeout:
            continue
        if not d:
            break
        buf += d
        m = CPR.search(buf)
        if m:
            return int(m.group(1)), int(m.group(2))
    return None


def wait_ready(conn, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if query_cursor(conn, timeout=1.0):
            return True
    return False


def run_cursor_tests(conn):
    fails = 0
    for name, seq, expect in CURSOR_TESTS:
        drain(conn)
        conn.sendall(seq)
        got = query_cursor(conn)
        ok = got == expect
        fails += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  {name:12} expected {expect} got {got}")
    total = len(CURSOR_TESTS)
    print(f"\ncursor: {total - fails}/{total} passed")
    return fails


class SerialConn:
    """Adapt a pyserial port to the tiny socket-like interface the cursor tests
    use (settimeout / sendall / recv), so they can run against a real Apple IIe
    over the Super Serial Card instead of MAME."""

    def __init__(self, device, baud):
        import serial
        self.ser = serial.Serial(device, baud, timeout=0.2)

    def settimeout(self, t):
        self.ser.timeout = t

    def sendall(self, data):
        self.ser.write(data)

    def recv(self, n):
        d = self.ser.read(n)
        if not d:
            raise socket.timeout()  # match the socket's "no data" behaviour
        return d

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass


def autodetect_port():
    from serial.tools import list_ports
    ports = [p for p in list_ports.comports() if getattr(p, "vid", None)]
    ports = ports or list(list_ports.comports())
    if not ports:
        sys.exit("No serial ports found - is the USB/RS-232 adapter plugged in?")
    return ports[0].device


def run_serial(args):
    """Real-hardware verification: run the DSR cursor tests over a serial port."""
    if args.keys:
        print("--keys uses MAME key injection and cannot run over serial.")
        return 2
    device = args.device or autodetect_port()
    print(f"[serial {device} @ {args.baud} 8N1]")
    print("[boot the vt100 disk on the Apple; waiting for the terminal ...]")
    conn = SerialConn(device, args.baud)
    try:
        if not wait_ready(conn):
            print("FAIL: no cursor report. Check the cable (TX<->RX crossed), the "
                  "baud rate, and that the terminal booted on the Apple.")
            return 1
        print("[terminal is responding -- running cursor tests]\n")
        fails = run_cursor_tests(conn)
    finally:
        conn.close()
    return 1 if fails else 0


# What the Apple should transmit for the injected keys: letter, Enter (CR), then
# the four arrows as ANSI cursor sequences. In application-cursor-keys mode
# (DECCKM, ESC[?1h) the arrows switch from ESC [ x to ESC O x.
KEY_EXPECT = b"A\r\x1b[D\x1b[C\x1b[A\x1b[B"
KEY_EXPECT_APP = b"A\r\x1bOD\x1bOC\x1bOA\x1bOB"


def run_key_tests(conn, app=False):
    drain(conn)
    if app:
        # DECCKM on. Sent after draining the boot marker but well before the
        # arrows are injected (frame 780), so the mode is active in time.
        conn.sendall(b"\x1b[?1h")
    print("[waiting for keys.lua to inject keys ...]")
    conn.settimeout(0.3)
    buf = bytearray()
    deadline = time.time() + 30
    last_change = time.time()
    while time.time() < deadline:
        try:
            d = conn.recv(64)
        except socket.timeout:
            d = b""
        if d:
            buf += d
            last_change = time.time()
        elif buf and (time.time() - last_change) > 2.0:
            break  # stream settled
    got = bytes(buf)
    expect = KEY_EXPECT_APP if app else KEY_EXPECT
    ok = got == expect
    print(f"  expected: {expect!r}")
    print(f"  got:      {got!r}")
    print(f"\nkeyboard{'(app)' if app else ''}: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keys", action="store_true", help="run keyboard-input tests")
    ap.add_argument("--app", action="store_true",
                    help="with --keys: test application cursor keys (DECCKM)")
    ap.add_argument("--serial", action="store_true",
                    help="run the cursor tests against real hardware over a serial port")
    ap.add_argument("--device", help="serial port for --serial (auto-detected if omitted)")
    ap.add_argument("--baud", type=int, default=9600, help="serial baud for --serial")
    args = ap.parse_args()

    if args.serial:
        return run_serial(args)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", PORT))
    srv.listen(1)
    srv.settimeout(60)
    script = KEYS_LUA if args.keys else SSC_IRQ_LUA
    mame = launch_mame(["-autoboot_script", script])
    fails = 1
    try:
        conn, _ = srv.accept()
        if not wait_ready(conn):
            print("FAIL: terminal never answered ESC[6n")
            return 1
        fails = run_key_tests(conn, args.app) if args.keys else run_cursor_tests(conn)
    finally:
        srv.close()
        mame.terminate()
        try:
            mame.wait(timeout=10)
        except Exception:
            mame.kill()
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
