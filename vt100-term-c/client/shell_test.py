#!/usr/bin/env python3
"""End-to-end shell-rendering tests: real WSL bash <-> MAME Apple IIe terminal.

Boots build/vt100.dsk in headless MAME (Super Serial Card wired to a socket) with
client/screen_watch.lua mirroring the Apple's 80x24 screen to build/screen.txt,
then, for each case, runs a real shell command in WSL (`wsl.exe -e bash -c ...`)
under a pseudo-terminal (pywinpty/ConPTY) and streams its output over the socket
to the terminal. It waits for the Apple screen to stop changing and asserts what
rendered. This proves the whole pipeline end to end: bash -> pty -> socket ->
6551 -> the VT100 parser -> the 80-column screen driver.

Each command runs in a fresh shell (so tests are independent and deterministic);
the MAME terminal stays booted for the whole suite and is cleared between cases.

    python shell_test.py         # run the suite
    python shell_test.py -v      # also print each settled screen
    python shell_test.py -k ls   # only cases whose label matches "ls"

Requires the a2ssc ROM (see README), -aux ext80, a working `wsl.exe` default
distro, and pywinpty in the venv. Exit status is nonzero if any case fails.
"""
from __future__ import annotations

import argparse
import os
import re
import socket
import subprocess
import sys
import threading
import time
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
MAME = r"C:\mame\mame.exe"
ROMPATH = r"C:\mame\roms"
DISK = str(ROOT / "build" / "vt100.dsk")
WATCH_LUA = str(HERE / "screen_watch.lua")
SCREEN = ROOT / "build" / "screen.txt"
PORT = 6551

CPR = re.compile(rb"\x1b\[(\d+);(\d+)R")  # cursor-position report from the terminal

# Each case: (label, shell command, [checks]). A check is one of:
#   ("has", text)        text appears somewhere on the screen
#   ("row", n, text)     row n (1-based) contains text
#   ("absent", text)     text does NOT appear anywhere
# Outputs are chosen so they differ from the typed command where it matters, so
# a match proves the command's OUTPUT rendered (not merely the echoed keystrokes).
SHELL_TESTS = [
    ("echo",      "echo READY-XYZ",
     [("has", "READY-XYZ")]),
    ("arith",     "echo $((6*7))",
     [("has", "42")]),
    ("seq",       "seq 1 5 | paste -sd' ' -",
     [("has", "1 2 3 4 5")]),
    ("printf_nl", r"printf 'ALPHA\nBETA\n'",
     [("has", "ALPHA"), ("has", "BETA")]),
    ("ls_root",   "ls /",
     [("has", "bin"), ("has", "etc"), ("has", "usr")]),
    ("cursor",    r"printf '\033[8;30HPOSMARK\r\n'",
     [("row", 8, "POSMARK")]),
    ("wrap",      r"printf 'Q%.0s' $(seq 1 90); printf '\r\n'",
     [("row", 1, "Q" * 80), ("row", 2, "Q" * 10)]),
    ("sgr",       r"printf '\033[7mINVERSE\033[0m TEXT\r\n'",
     [("has", "INVERSE"), ("has", "TEXT")]),
    ("sgr_row",   r"printf '\033[12;1HNORMAL \033[7mHILITE\033[0m DONE\r\n'",
     [("row", 12, "NORMAL HILITE DONE")]),
    ("el_bol",    r"printf '\033[3;1HABCDEFGHIJ\033[3;5H\033[1KX\r\n'",
     [("row", 3, "XFGHIJ"), ("absent", "ABCDE")]),
    ("el_line",   r"printf '\033[4;1HZZZZZZ\033[4;3H\033[2KQ\r\n'",
     [("row", 4, "Q"), ("absent", "ZZZ")]),
    ("ed_bop",    r"printf '\033[2;1HTOPLINE\033[5;1HMIDLINE\033[6;3H\033[1J\r\n'",
     [("absent", "TOPLINE"), ("absent", "MIDLINE")]),
    ("ri_scroll", r"printf '\033[2;1HLINE-TWO\033[1;1H\033M'",
     [("row", 3, "LINE-TWO"), ("row", 1, "")]),
    ("ind_scroll", r"printf '\033[23;1HNEARBOT\033[24;1H\033D'",
     [("row", 22, "NEARBOT")]),
    ("region_scroll", r"printf '\033[3;5r\033[3;1HRR3\033[4;1HRR4\033[5;1HRR5\033[5;1H\n\033[r'",
     [("row", 3, "RR4"), ("row", 5, "")]),
    ("ich",       r"printf '\033[6;1HABCDEFGH\033[6;3H\033[3@\r\n'",
     [("row", 6, "AB   CDEFGH")]),
    ("dch",       r"printf '\033[7;1HABCDEFGH\033[7;3H\033[3P\r\n'",
     [("row", 7, "ABFGH"), ("absent", "CDE")]),
    ("ech",       r"printf '\033[8;1HABCDEFGH\033[8;3H\033[3X\r\n'",
     [("row", 8, "AB   FGH")]),
    ("il",        r"printf '\033[10;1HLINE-A\033[11;1HLINE-B\033[10;1H\033[1L\r\n'",
     [("row", 11, "LINE-A"), ("row", 10, "")]),
    ("dl",        r"printf '\033[14;1HKEEP1\033[15;1HGONE1\033[16;1HKEEP2\033[15;1H\033[1M\r\n'",
     [("row", 15, "KEEP2"), ("absent", "GONE1")]),
    ("altscreen", r"printf 'MAINSCREEN\033[?1049h\033[2J\033[5;1HALTBUFFER\033[?1049l'",
     [("has", "MAINSCREEN"), ("absent", "ALTBUFFER")]),
    ("clear",     "clear; echo AFTER-CLEAR",
     [("has", "AFTER-CLEAR"), ("absent", "READY-XYZ")]),
]


def launch_mame():
    # Run at real speed (no -nothrottle): with -nothrottle MAME races through the
    # emulated seconds of -str in a fraction of the wall-clock time and would
    # quit in the middle of the suite. -str is a safety cap; we terminate MAME
    # ourselves when the suite finishes.
    cmd = [MAME, "apple2e", "-rompath", ROMPATH, "-aux", "ext80",
           "-sl2", "ssc", "-sl2:ssc:rs232", "null_modem",
           "-bitb", f"socket.127.0.0.1:{PORT}",
           "-flop1", DISK, "-video", "none", "-sound", "none",
           "-skip_gameinfo", "-str", "600",
           "-autoboot_script", WATCH_LUA]
    return subprocess.Popen(cmd, cwd=str(ROOT),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_ready(conn, timeout=40.0):
    """Drain the boot marker and confirm the terminal answers ESC[6n, so bash
    only starts once the terminal is up and listening."""
    conn.setblocking(False)
    deadline = time.time() + timeout
    buf = b""
    while time.time() < deadline:
        try:
            conn.sendall(b"\x1b[6n")
        except OSError:
            pass
        stop = time.time() + 0.5
        while time.time() < stop:
            try:
                d = conn.recv(256)
                if d:
                    buf += d
            except (BlockingIOError, OSError):
                pass
            if CPR.search(buf):
                return True
            time.sleep(0.02)
    return False


def read_screen():
    """Read build/screen.txt, tolerating the brief remove+rename window."""
    for _ in range(5):
        try:
            return SCREEN.read_text(errors="replace")
        except OSError:
            time.sleep(0.01)
    return ""


def wait_settle(min_stable=1.2, timeout=12.0):
    """Wait until build/screen.txt stops changing; return (lines, raw)."""
    deadline = time.time() + timeout
    last = read_screen()
    stable_since = time.time()
    while time.time() < deadline:
        time.sleep(0.2)
        cur = read_screen()
        if cur != last:
            last = cur
            stable_since = time.time()
        elif time.time() - stable_since >= min_stable:
            break
    return last.splitlines(), last


def apply_checks(lines, checks):
    joined = "\n".join(lines)
    fails = []
    for chk in checks:
        if chk[0] == "has":
            if chk[1] not in joined:
                fails.append(f"missing {chk[1]!r}")
        elif chk[0] == "absent":
            if chk[1] in joined:
                fails.append(f"unexpected {chk[1]!r}")
        elif chk[0] == "row":
            _, n, text = chk
            row = lines[n - 1] if 0 < n <= len(lines) else ""
            if text not in row:
                fails.append(f"row {n} {row!r} lacks {text!r}")
    return fails


class Terminal:
    """The MAME terminal end of the socket: send bytes to it, and drain what it
    sends back (DSR/DA replies, XON/XOFF) so the socket buffer never backs up."""

    def __init__(self, conn):
        self.conn = conn
        self.stop = threading.Event()
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        while not self.stop.is_set():
            try:
                if not self.conn.recv(256):
                    time.sleep(0.02)
            except (BlockingIOError, OSError):
                time.sleep(0.02)

    def send(self, data: bytes):
        try:
            self.conn.sendall(data)
        except OSError:
            pass

    def clear(self):
        # ESC[r resets the scroll region so it can't leak between cases.
        self.send(b"\x1b[r\x1b[2J\x1b[H")

    def close(self):
        self.stop.set()


def run_command(term, command, term_env="vt100"):
    """Run one shell command in WSL under a pty and stream its output to the
    terminal. Returns when the command finishes (pty EOF)."""
    import winpty
    env = dict(os.environ)
    env["TERM"] = term_env
    proc = winpty.PtyProcess.spawn(["wsl.exe", "-e", "bash", "-c", command],
                                   env=env, dimensions=(24, 80))
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            data = proc.read(4096)
        except EOFError:
            break
        if data:
            term.send(data.encode("utf-8", "replace"))
        elif not proc.isalive():
            break
        else:
            time.sleep(0.005)
    try:
        proc.terminate(force=True)
    except Exception:
        pass


def run_suite(term, selected, verbose, term_env, mame=None):
    fails = 0
    ran = 0
    for label, cmd, checks in SHELL_TESTS:
        if selected and selected not in label:
            continue
        ran += 1
        term.clear()
        wait_settle(min_stable=0.6, timeout=6)
        run_command(term, cmd, term_env)
        lines, raw = wait_settle()
        bad = apply_checks(lines, checks)
        ok = not bad
        fails += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  {label:10} {cmd}")
        if bad:
            for b in bad:
                print(f"          - {b}")
        if verbose or not ok:
            for i, ln in enumerate(lines):
                if ln.strip():
                    print(f"        {i + 1:2}|{ln}")
        if mame and mame.poll() is not None:
            print("  ABORT: MAME exited mid-suite (raise -str?)")
            fails += sum(1 for t in SHELL_TESTS[ran:]
                         if not selected or selected in t[0])
            break
    return fails, ran


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="print each settled screen")
    ap.add_argument("-k", dest="select", default="",
                    help="only run cases whose label contains this text")
    ap.add_argument("--term", default="vt100")
    args = ap.parse_args()

    try:
        import winpty  # noqa: F401
    except ImportError:
        sys.exit("pywinpty is required: pip install pywinpty (in the venv)")

    # Warm up WSL so the first case doesn't race a cold start.
    print("[warming up WSL ...]")
    subprocess.run(["wsl.exe", "-e", "bash", "-c", "true"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if SCREEN.exists():
        SCREEN.unlink()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", PORT))
    srv.listen(1)
    srv.settimeout(60)

    mame = launch_mame()
    term = None
    conn = None
    fails, ran = 1, 1
    try:
        conn, _ = srv.accept()
        srv.close()
        print("[connected; waiting for terminal ...]")
        if not wait_ready(conn):
            print("FAIL: terminal never answered ESC[6n")
            return 1
        print("[terminal ready; running shell cases ...]")
        term = Terminal(conn)
        fails, ran = run_suite(term, args.select, args.verbose, args.term, mame)
        print(f"\nshell: {ran - fails}/{ran} passed")
    finally:
        if term:
            term.close()
        try:
            if conn:
                conn.close()
        except Exception:
            pass
        mame.terminate()
        try:
            mame.wait(timeout=10)
        except Exception:
            mame.kill()
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
