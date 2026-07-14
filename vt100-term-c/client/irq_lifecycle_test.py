#!/usr/bin/env python3
"""Exercise IRQ chaining and Ctrl-Reset cleanup in real-ROM MAME."""

from __future__ import annotations

import os
import socket
import subprocess
import time

from bench import MAME, PORT, ROMPATH, ROOT, Terminal


LUA = ROOT / "client" / "irq_lifecycle.lua"
DISK = ROOT / "build" / "vt100.dsk"
LABELS = ROOT / "build" / "vt100.lbl"
OUTPUT = ROOT / "build" / "irq_lifecycle.txt"


def main() -> int:
    if not DISK.exists() or not LABELS.exists():
        raise SystemExit("build/vt100.dsk and build/vt100.lbl are required; run make first")

    OUTPUT.unlink(missing_ok=True)
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", PORT))
    server.listen(1)
    env = os.environ.copy()
    env["IRQ_LIFECYCLE_OUT"] = str(OUTPUT)
    env["IRQ_LIFECYCLE_LABELS"] = str(LABELS)
    cmd = [
        MAME,
        "apple2e",
        "-rompath",
        str(ROMPATH),
        "-flop1",
        str(DISK),
        "-sl1",
        "mockingboard",
        "-sl2",
        "ssc",
        "-sl2:ssc:rs232",
        "null_modem",
        "-bitb",
        f"socket.127.0.0.1:{PORT}",
        "-aux",
        "ext80",
        "-skip_gameinfo",
        "-video",
        "none",
        "-sound",
        "none",
        "-autoboot_script",
        str(LUA),
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    terminal = None
    try:
        server.settimeout(30)
        terminal = Terminal(server.accept()[0])
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not OUTPUT.exists():
            if proc.poll() is not None:
                break
            time.sleep(0.05)
        if not OUTPUT.exists():
            raise RuntimeError("MAME lifecycle probe produced no result")
        result = OUTPUT.read_text(encoding="ascii").strip()
        print(result)
        return 0 if result.startswith("PASS ") else 1
    finally:
        if terminal is not None:
            terminal.close()
        server.close()
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
