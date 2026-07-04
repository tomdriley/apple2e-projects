#!/usr/bin/env python3
"""Automated round-trip test for the Super Serial Card demo.

The demo has three moving parts: the 6502 program (ssc-serial.c) on the Apple,
the serial link, and the serial_demo.py client on the host. This harness drives
the *real* client and checks that a byte sent from the host is echoed back --
the same round trip you watch by hand in the terminal.

Two peers can sit on the far end of the link:

  fake  (default) -- A local stand-in that reproduces the byte-level behavior of
        ssc-serial.c: on connect it sends the banner, then echoes every received
        byte back with CR->CRLF translation, exactly like the C program's emit().
        Needs no emulator or ROMs, so it always runs. This proves the client and
        the wire protocol are correct.

  mame  -- Boots build/ssc-serial.dsk in headless MAME with a real Super Serial
        Card and runs the identical round trip against the actual 6502 code.
        Requires MAME plus the apple2e and a2ssc ROMs. If the SSC
        ROM is absent the test reports SKIPPED (not FAILED), because the demo
        program itself is fine -- only the emulator's ROM dependency is missing.

Usage:
    python roundtrip_test.py            # protocol test against the fake Apple
    python roundtrip_test.py --mame     # real emulator test (needs a2ssc ROM)
"""
from __future__ import annotations

import argparse
import pathlib
import shutil
import socket
import subprocess
import sys
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
CLIENT = HERE / "serial_demo.py"
DISK = ROOT / "build" / "ssc-serial.dsk"
PORT = 6551
PY = sys.executable
MAME = shutil.which("mame") or r"C:\mame\mame.exe"
ROMPATH = r"C:\mame\roms"

# A phrase unique to the banner, and a token we round-trip through the Apple.
BANNER_MARK = "echo it back"
TEST_LINE = "PING-FROM-CLIENT"

# Byte-for-byte what ssc-serial.c emits at startup (each CR followed by LF).
FAKE_BANNER = (
    b"\r\n"
    b"Super Serial Card demo\r\n"
    b"Type in the client; I echo it back:\r\n"
    b"\r\n"
)


def fake_apple(stop: threading.Event) -> None:
    """Connect to the client's listening socket and behave like ssc-serial.c."""
    sock = None
    for _ in range(50):  # the client needs a moment to start listening
        try:
            sock = socket.create_connection(("127.0.0.1", PORT), timeout=1)
            break
        except OSError:
            time.sleep(0.1)
    if sock is None:
        return
    with sock:
        sock.sendall(FAKE_BANNER)
        sock.settimeout(0.2)
        while not stop.is_set():
            try:
                data = sock.recv(256)
            except socket.timeout:
                continue
            if not data:
                break
            out = bytearray()
            for byte in data:
                c = byte & 0x7F  # strip high bit, like `ch = ACIA_DATA & 0x7F`
                out.append(c)
                if c == 0x0D:  # CR -> CRLF, like emit()
                    out.append(0x0A)
            sock.sendall(bytes(out))


def start_mame() -> subprocess.Popen:
    cmd = [
        MAME, "apple2e",
        "-rompath", ROMPATH,
        "-sl2", "ssc", "-sl2:ssc:rs232", "null_modem",
        "-bitb", f"socket.127.0.0.1:{PORT}",
        "-flop1", str(DISK),
        "-video", "none", "-sound", "none",
        "-skip_gameinfo", "-str", "60",
    ]
    return subprocess.Popen(
        cmd, cwd=str(ROOT),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )


def mame_ssc_rom_present() -> bool:
    try:
        r = subprocess.run(
            [MAME, "-rompath", ROMPATH, "-verifyroms", "a2ssc"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "is good" in (r.stdout + r.stderr)


def run_round_trip(start_peer, stop_peer, banner_timeout: float, echo_timeout: float):
    """Drive the real client against a peer; return (ok, transcript, reason)."""
    client = subprocess.Popen(
        [PY, str(CLIENT), "tcp", "--host", "127.0.0.1", "--port", str(PORT)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1, cwd=str(ROOT),
    )

    transcript: list[str] = []
    banner_seen = threading.Event()
    echo_seen = threading.Event()
    listening = threading.Event()

    def pump_stdout() -> None:
        buf = ""
        while True:
            ch = client.stdout.read(1)
            if ch == "":
                break
            transcript.append(ch)
            buf += ch
            if BANNER_MARK in buf:
                banner_seen.set()
            if banner_seen.is_set() and TEST_LINE in buf.split(BANNER_MARK, 1)[-1]:
                echo_seen.set()

    def pump_stderr() -> None:
        for line in client.stderr:
            if "waiting for" in line:
                listening.set()

    threading.Thread(target=pump_stdout, daemon=True).start()
    threading.Thread(target=pump_stderr, daemon=True).start()

    peer = None
    try:
        if not listening.wait(10):
            return False, "".join(transcript), "client never started listening"
        peer = start_peer()

        if not banner_seen.wait(banner_timeout):
            return False, "".join(transcript), "no banner arrived from the Apple"

        client.stdin.write(TEST_LINE + "\n")
        client.stdin.flush()

        if not echo_seen.wait(echo_timeout):
            return False, "".join(transcript), "banner arrived but echo did not"
        return True, "".join(transcript), ""
    finally:
        if peer is not None:
            stop_peer(peer)
        for closer in (client.stdin.close, client.terminate):
            try:
                closer()
            except Exception:
                pass
        try:
            client.wait(timeout=5)
        except Exception:
            client.kill()


def show(transcript: str) -> None:
    print("    --- client saw " + "-" * 40)
    for line in transcript.splitlines():
        print("    | " + line)
    print("    " + "-" * 52)


def test_fake() -> int:
    print("[fake] driving the real client against a stand-in for ssc-serial.c")
    stop = threading.Event()

    def start():
        t = threading.Thread(target=fake_apple, args=(stop,), daemon=True)
        t.start()
        return t

    def stop_peer(_t):
        stop.set()

    ok, transcript, reason = run_round_trip(start, stop_peer, 5.0, 5.0)
    show(transcript)
    if ok:
        print(f"[fake] PASS -- banner received and {TEST_LINE!r} echoed back\n")
        return 0
    print(f"[fake] FAIL -- {reason}\n")
    return 1


def test_mame() -> int:
    print("[mame] booting build/ssc-serial.dsk on a real Super Serial Card")
    if not pathlib.Path(MAME).exists() and shutil.which("mame") is None:
        print("[mame] SKIP -- MAME executable not found\n")
        return 0
    if not mame_ssc_rom_present():
        print("[mame] SKIP -- the Super Serial Card ROM (a2ssc: 341-0065-a.bin) is")
        print(f"[mame]         missing. Supply it as {ROMPATH}\\a2ssc.zip and this")
        print("[mame]         mode will run the real 6502 program.\n")
        return 0

    def start():
        return start_mame()

    def stop_peer(proc: subprocess.Popen):
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()

    ok, transcript, reason = run_round_trip(start, stop_peer, 60.0, 15.0)
    show(transcript)
    if ok:
        print(f"[mame] PASS -- the Apple sent its banner and echoed {TEST_LINE!r}\n")
        return 0
    print(f"[mame] FAIL -- {reason}\n")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mame", action="store_true",
                    help="run against real headless MAME instead of the fake Apple")
    args = ap.parse_args()
    return test_mame() if args.mame else test_fake()


if __name__ == "__main__":
    raise SystemExit(main())
