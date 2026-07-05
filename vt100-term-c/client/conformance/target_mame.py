#!/usr/bin/env python3
"""MameTarget -- render corpus inputs on the real firmware in headless MAME.

This is the comprehensive, fully-automated render target for the conformance
runner (issue #13). It boots ``build/vt100.dsk`` in MAME with the Super Serial
Card bridged to a socket -- exactly like ``bench.py`` and ``shell_test.py`` --
and drives each case through three machine oracles, with no human in the loop:

  * **glyph plane + inverse plane** -- ``probes/conformance_probe.lua`` reads the
    80-column video RAM every frame and writes ``build/conf_probe.txt`` (a SEQ
    counter, the 24x80 glyph plane, the 24x80 inverse plane, and the probed
    firmware state). We wait for a snapshot taken strictly *after* the input has
    rendered, so the read-back always reflects the settled screen.
  * **wire read-back** -- the windowed-lossless ``bench.Terminal`` sender appends
    an ESC[6n to every window; the reply (and any DA/DSR the case itself emits)
    is drained by ``Terminal``'s background thread and read via ``peek()``. The
    last CPR is the exact post-input cursor; the raw bytes satisfy ``report``.
  * **state probe** -- the firmware's cursor/attribute/scroll-region variables
    are exposed (non-static) and their addresses read from ``build/vt100.lbl``
    (ld65 ``-Ln``); we hand the probe their addresses via ``build/conf_syms.txt``.

The runner asks :attr:`caps` before checking an expectation, so this target
advertises every channel it can observe.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import time

import transport  # noqa: F401  (side effect: puts client/ on sys.path)
from transport import (
    Terminal, listen, chunk_bytes, MAME, ROMPATH, PORT, OP_TIMEOUT, CPR, WINDOW,
)
from target_base import Target, Capabilities
from model import Screen, ROWS, COLS

HERE = pathlib.Path(__file__).resolve().parent      # client/conformance
ROOT = HERE.parent.parent                            # vt100-term-c
BUILD = ROOT / "build"
DISK = BUILD / "vt100.dsk"
LBL = BUILD / "vt100.lbl"
PROBE_LUA = HERE / "probes" / "conformance_probe.lua"
PROBE_OUT = BUILD / "conf_probe.txt"
SYMS_OUT = BUILD / "conf_syms.txt"

# Firmware state variables to expose to the probe (assembly names carry a
# leading underscore; the probe/corpus use the bare C name). saved_screen_* and
# cur_attr are handed over too though no case needs them yet -- they cost
# nothing and round out the state channel.
STATE_SYMS = (
    "cur_col", "cur_row", "scroll_top", "scroll_bot", "cur_attr",
    "app_cursor", "attr_inverse", "saved_screen_col", "saved_screen_row",
)
# VICE label line, e.g.  al 001EDE ._cur_col
_LBL_RE = re.compile(r"^al\s+([0-9A-Fa-f]+)\s+\._(\w+)\s*$")


def write_syms(build: pathlib.Path = BUILD) -> dict:
    """Parse the ld65 ``-Ln`` label file and write ``conf_syms.txt`` for the Lua.

    Returns the resolved ``name -> address`` map (empty if the label file is
    missing, in which case the state channel is simply unavailable and the
    runner degrades those expectations to not-checkable)."""
    lbl = build / "vt100.lbl"
    found: dict[str, int] = {}
    if lbl.exists():
        for line in lbl.read_text(encoding="utf-8", errors="replace").splitlines():
            m = _LBL_RE.match(line.strip())
            if m and m.group(2) in STATE_SYMS:
                found[m.group(2)] = int(m.group(1), 16)
    build.mkdir(parents=True, exist_ok=True)
    (build / "conf_syms.txt").write_text(
        "".join(f"{n}={a:04x}\n" for n, a in found.items()), encoding="utf-8")
    return found


class MameTarget(Target):
    name = "mame"
    caps = Capabilities(glyphs=True, inverse=True, cursor=True,
                        reports=True, state=True)

    def __init__(self, port: int = PORT, boot_timeout: float = 45.0,
                 settle: float = OP_TIMEOUT, ack_to: float = 3.0):
        self.port = port
        self.boot_timeout = boot_timeout
        self.settle = settle
        # Per-window ESC[6n ack timeout. Short on purpose: a well-behaved window
        # acks in milliseconds, so a miss almost always means the case left the
        # parser mid-sequence (it swallowed the probe) -- we detect that fast and
        # fall back rather than stalling the whole run for `settle` seconds.
        self.ack_to = ack_to
        self.proc = None
        self.srv = None
        self.conn = None
        self.term: Terminal | None = None
        self.syms: dict = {}

    # -- lifecycle ---------------------------------------------------------
    def _launch(self):
        cmd = [str(MAME), "apple2e", "-rompath", str(ROMPATH), "-aux", "ext80",
               "-sl2", "ssc", "-sl2:ssc:rs232", "null_modem",
               "-bitb", f"socket.127.0.0.1:{self.port}",
               "-flop1", str(DISK), "-video", "none", "-sound", "none",
               "-skip_gameinfo", "-str", "3600",
               "-autoboot_script", str(PROBE_LUA)]
        return subprocess.Popen(cmd, cwd=str(ROOT),
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)

    def open(self) -> None:
        if not DISK.exists():
            raise FileNotFoundError(
                f"{DISK} not found -- run `make` in vt100-term-c first")
        if not PROBE_LUA.exists():
            raise FileNotFoundError(f"probe script missing: {PROBE_LUA}")
        self.syms = write_syms()
        # Drop any stale snapshot so the freshness wait can't read a prior run.
        try:
            PROBE_OUT.unlink()
        except FileNotFoundError:
            pass
        self.srv = listen(self.port, timeout=self.boot_timeout)
        self.proc = self._launch()
        try:
            self.conn, _ = self.srv.accept()
        except OSError as exc:
            self.close()
            raise RuntimeError(f"MAME never connected on :{self.port}") from exc
        self.term = Terminal(self.conn)
        if not self._wait_ready(self.boot_timeout):
            self.close()
            raise RuntimeError("firmware did not answer DSR after boot")
        # Wait for the probe's first snapshot so state/screen are available.
        self._wait_probe(15.0)

    def _wait_ready(self, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.term.clear_buf()
            self.term.send(b"\x1b[6n")
            if self.term.wait_cpr(1.0):
                self.term.clear_buf()
                return True
        return False

    def _wait_probe(self, timeout: float) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._read_probe() is not None:
                return
            time.sleep(0.05)
        raise RuntimeError(f"probe never wrote a valid {PROBE_OUT.name}")

    # -- per-case ----------------------------------------------------------
    def reset(self) -> None:
        # A prior case can leave the parser mid-CSI / mid-ESC / mid-charset (or
        # collecting a string). ST (ESC \) forces any of those back to S_NORMAL
        # -- worst case it lands as a harmless unknown CSI final -- and then RIS
        # (ESC c) hard-resets the screen, attributes, scroll region, saved
        # cursor and charset, giving each case a clean, isolated slate.
        self.term.send(b"\x1b\\\x1bc")
        self.term.sync(self.settle)  # ESC[6n; returns False (never raises) on miss

    def _send(self, data: bytes) -> None:
        """Stream `data` to the firmware, paced into <=WINDOW-byte windows each
        followed by an ESC[6n we block on -- lossless, the input ring can't
        overflow. Unlike ``Terminal.send_windowed`` this never raises: a case may
        legitimately end mid-sequence/mid-string and swallow the ack, so on the
        first unanswered window we stop pacing and flush the remainder raw."""
        windows: list[bytes] = []
        cur = bytearray()
        for ch in chunk_bytes(data):     # atomic chunks: never split across ESC[6n
            if cur and len(cur) + len(ch) > WINDOW:
                windows.append(bytes(cur))
                cur = bytearray()
            cur.extend(ch)
        if cur:
            windows.append(bytes(cur))

        for i, win in enumerate(windows):
            self.term.clear_buf()
            self.term.send(win + b"\x1b[6n")
            if not self.term.wait_cpr(self.ack_to):
                # Unanswered -> parser is mid-sequence and ate the probe; the
                # firmware is still consuming, so just dump the rest in order.
                for rest in windows[i + 1:]:
                    self.term.send(rest)
                return

    def render(self, data: bytes) -> Screen:
        self.term.clear_buf()
        if data:
            self._send(data)
        else:
            # Nothing to send; still drive one ESC[6n so an input-less case gets
            # a cursor read-back and a settle point.
            self.term.send(b"\x1b[6n")
            self.term.wait_cpr(self.ack_to)
        # Let any bytes trailing the acking CPR (e.g. a DA reply that preceded
        # it, or the case's own report) land before we read the wire.
        time.sleep(0.03)
        reports = self.term.peek()

        # Wait for a snapshot strictly newer than "now" so the planes reflect the
        # fully-rendered screen, not a mid-render frame.
        seq_now = self._current_seq()
        text, inverse, state = self._wait_fresh(seq_now, self.settle)
        state = self._fix_state(state)
        # The wire CPR is the canonical, always-correct cursor report and is read
        # before any harness-induced side effect; prefer it. Fall back to the
        # state probe only when the case suppressed its CPR (e.g. it ended
        # mid-string and swallowed the pacing ESC[6n).
        cursor = self._last_cpr(reports)
        if cursor is None:
            cursor = self._state_cursor(state)
        return Screen(text=text, inverse=inverse, cursor=cursor,
                      reports=reports, state=state)

    @staticmethod
    def _state_cursor(state: dict):
        """The (row, col) cursor from the (already 1-based) state probe, or None
        if those vars weren't probed."""
        if "cur_row" in state and "cur_col" in state:
            return (state["cur_row"], state["cur_col"])
        return None

    @staticmethod
    def _fix_state(state: dict) -> dict:
        """Expose cursor row/col 1-based (matching the DSR ``cursor``); leave the
        scroll-region margins and flags as the firmware's raw values."""
        out = {}
        for k, v in state.items():
            out[k] = v + 1 if k in ("cur_row", "cur_col") else v
        return out

    @staticmethod
    def _last_cpr(buf: bytes):
        last = None
        for m in CPR.finditer(buf):
            last = (int(m.group(1)), int(m.group(2)))
        return last

    # -- probe file parsing ------------------------------------------------
    def _current_seq(self) -> int:
        p = self._read_probe()
        return p[0] if p else -1

    def _wait_fresh(self, after_seq: int, timeout: float):
        deadline = time.time() + timeout
        while time.time() < deadline:
            p = self._read_probe()
            if p and p[0] > after_seq:
                return p[1], p[2], p[3]
            time.sleep(0.02)
        raise TimeoutError("no fresh probe snapshot after render")

    def _read_probe(self):
        """Return (seq, text_rows, inverse_rows, state) or None if the file is
        absent / mid-write / malformed (caller retries)."""
        try:
            raw = PROBE_OUT.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        lines = raw.split("\n")
        if not lines or lines[0][:4] != "SEQ ":
            return None
        try:
            seq = int(lines[0][4:].strip())
        except ValueError:
            return None
        try:
            i_scr = lines.index("SCREEN")
            i_att = lines.index("ATTR")
            i_sta = lines.index("STATE")
            i_end = lines.index("END")
        except ValueError:
            return None  # a marker not yet written -> incomplete
        if not (i_scr < i_att < i_sta < i_end):
            return None
        text = [self._pad(r) for r in lines[i_scr + 1:i_att]]
        inverse = [self._pad(r, fill="0") for r in lines[i_att + 1:i_sta]]
        if len(text) < ROWS or len(inverse) < ROWS:
            return None
        state: dict = {}
        for ln in lines[i_sta + 1:i_end]:
            parts = ln.split()
            if len(parts) == 2:
                try:
                    state[parts[0]] = int(parts[1])
                except ValueError:
                    pass
        return seq, text[:ROWS], inverse[:ROWS], state

    @staticmethod
    def _pad(row: str, fill: str = " ") -> str:
        if len(row) < COLS:
            return row + fill * (COLS - len(row))
        return row[:COLS]

    # -- teardown ----------------------------------------------------------
    def close(self) -> None:
        if self.term is not None:
            try:
                self.term.close()
            except Exception:
                pass
            self.term = None
        if self.conn is not None:
            try:
                self.conn.close()
            except OSError:
                pass
            self.conn = None
        if self.proc is not None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None
        if self.srv is not None:
            try:
                self.srv.close()
            except OSError:
                pass
            self.srv = None


if __name__ == "__main__":
    # Smoke test: boot, render a couple of cases, dump what the probes saw.
    t = MameTarget()
    t.open()
    try:
        for inp in (b"\x1b[2J\x1b[3;5HHELLO", b"\x1b[2J\x1b[7mINV\x1b[0m",
                    b"\x1b[9;9H\x1b[6n", b"\x1b[c"):
            t.reset()
            scr = t.render(inp)
            print(f"\n--- input={inp!r}")
            print("cursor:", scr.cursor, "reports:", scr.reports)
            print("state:", scr.state)
            for r in range(1, 4):
                print(f"  row{r}: {scr.row(r)[:40]!r}")
    finally:
        t.close()
    print("\nMameTarget smoke test done")
    sys.exit(0)
