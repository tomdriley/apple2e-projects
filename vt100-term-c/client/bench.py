#!/usr/bin/env python3
"""End-to-end firmware rendering benchmark for the Apple IIe VT100 terminal.

Boots build/vt100.dsk in headless MAME (Super Serial Card wired to a socket) and
streams deterministic, scroll-heavy payloads to the terminal, measuring how long
the firmware takes to render them. This is the quantitative counterpart to the
pass/fail suites (vt100_test.py, shell_test.py): it exists to compare firmware
variants -- most importantly, before vs. after removing the $7000 screen-shadow
buffer, which copies a second full screen on every scroll/clear.

Timing marker
-------------
The terminal answers ESC[6n (DSR) with ESC[<row>;<col>R only after every byte
before it has been processed, so ESC[6n appended to a payload is an exact,
method-agnostic "everything so far is rendered" marker.

Transport (lossless and non-wedging)
------------------------------------
The firmware feeds its one-byte 6551 receive register into a 256-byte ring. A
busy stream can block the firmware for hundreds of milliseconds per command
(each full scroll copies both video banks and the shadow), so two rules are
needed to stream to it losslessly:
  * A background thread drains the socket continuously. The firmware transmits
    its DSR replies by spin-waiting on the ACIA transmit register; if the host
    stops reading, that register never empties and the firmware wedges
    mid-render. Continuous draining prevents that.
  * The sender is *windowed*: it batches the payload into <=WINDOW-byte groups
    of complete escape sequences / lines and, after each batch, appends an
    ESC[6n and waits for the reply before sending the next batch. That bounds
    the bytes in flight (socket + ring) well under 256, so the ring can never
    overflow (a known-broken full-guard corrupts it at 256) no matter how long
    the firmware blocks. This is what makes scroll-heavy streams lossless;
    honoring XON/XOFF alone does not, because by the time the firmware raises
    XOFF the whole payload is already queued in the socket/MAME pipe.

Two clocks
----------
  * emu  -- emulated seconds from client/bench_probe.lua, which timestamps every
            DSR reply with MAME's emulated clock. Each trial records a baseline
            idle DSR, then the payload is streamed as windows (each ending in a
            DSR); emu is the last window's timestamp minus the baseline.
            Deterministic and independent of host load -- the headline.
  * wall -- host wall-clock at 1x throttle, plus a screen correctness check from
            build/screen.txt (screen_watch.lua): confirms the firmware rendered
            the stream correctly, not merely quickly.

    python bench.py                     # both passes, all workloads
    python bench.py -k scroll           # only workloads whose name matches
    python bench.py --trials 5 --json before.json
    python bench.py --emulated-only     # just the deterministic timing pass
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import statistics
import subprocess
import sys
import threading
import time
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
MAME = os.environ.get("MAME", r"C:\mame\mame.exe")
ROMPATH = os.environ.get("MAME_ROMPATH", r"C:\mame\roms")
DISK = str(ROOT / "build" / "vt100.dsk")
WATCH_LUA = str(HERE / "screen_watch.lua")
PROBE_LUA = str(HERE / "bench_probe.lua")
SCREEN = ROOT / "build" / "screen.txt"
TICKS = ROOT / "build" / "bench_ticks.txt"
PORT = 6551

CPR = re.compile(rb"\x1b\[(\d+);(\d+)R")  # cursor position report
XON, XOFF = 0x11, 0x13
OP_TIMEOUT = 180.0  # generous: an abandoned mid-render op corrupts later ones
WINDOW = 96         # max bytes in flight before we pause for a DSR ack (< ring/2)


# --------------------------------------------------------------------------
# Workloads. Each returns (chunks, checks):
#   chunks -- list of atomic byte groups (complete escape sequences / lines).
#             The sender batches them into <=WINDOW-byte windows; a group is
#             never split across a window, so a DSR probe is only ever injected
#             at a safe parser boundary.
#   checks -- ("has", text) / ("absent", text) applied to the settled screen
#             (from screen_watch.lua) to prove the stream rendered correctly and
#             completely -- catching a "fast but lossy" regression.
# Sizes target a few seconds of render each. A full 24-row scroll costs ~0.35s
# (it copies both video banks *and* the shadow), so scroll counts are modest.
# --------------------------------------------------------------------------
SCROLLS = 10   # ESC[NL + ESC[NM => 2*N full scrolls; headline pure-scroll point
BIGSCROLLS = 20  # second pure-scroll point; the pair isolates per-scroll cost
CATLINES = 44  # lines for the narrow-cat workload (~20 scrolls after fill)
WIDELINES = 30
CLEARS = 24
ALTS = 8


def _scroll_chunks(n, tag):
    return [b"\x1b[2J\x1b[H", f"\x1b[{n}L".encode(), f"\x1b[{n}M".encode(),
            f"\x1b[H{tag}".encode()]


def w_scroll_il():
    """Pure scrolling with no character drawing: home the cursor, then insert
    SCROLLS blank lines (each a full-region scroll) and delete them again. This
    isolates the scroll cost -- exactly what the shadow buffer inflates. Paired
    with scroll_big, the two counts give the per-scroll cost as a slope."""
    return _scroll_chunks(SCROLLS, "SCROLL-IL-DONE"), [("has", "SCROLL-IL-DONE")]


def w_scroll_big():
    """Same as scroll_il with twice the scrolls. Comparing scroll_big vs scroll_il
    (before and after) yields the marginal cost of a single full scroll, which is
    the quantity the shadow removal targets and should roughly halve."""
    return _scroll_chunks(BIGSCROLLS, "SCROLL-BIG-DONE"), [("has", "SCROLL-BIG-DONE")]


def w_cat_narrow():
    """The classic 'long cat': many short numbered lines. The first screenful
    fills without scrolling; the rest scroll one row each. After 44 lines the
    visible rows are L0021..L0043; earlier lines have scrolled off."""
    chunks = [b"\x1b[2J\x1b[H"]
    chunks += [f"L{i:04d} narrow\r\n".encode() for i in range(CATLINES)]
    chunks += [b"CAT-NARROW-DONE"]
    return chunks, [("has", "CAT-NARROW-DONE"), ("has", f"L{CATLINES - 1:04d}"),
                    ("has", "L0035"), ("has", "L0028"), ("absent", "L0000")]


def w_cat_wide():
    """Full-width (80-column) lines: each line is a full row of character writes
    plus a scroll -- exercises scr_put's shadow write as well as region scroll."""
    chunks = [b"\x1b[2J\x1b[H"]
    for i in range(WIDELINES):
        body = (f"L{i:04d}:" + "W" * 74)[:78]
        chunks.append(body.encode() + b"\r\n")
    chunks += [b"CAT-WIDE-DONE"]
    return chunks, [("has", "CAT-WIDE-DONE"), ("has", f"L{WIDELINES - 1:04d}:"),
                    ("has", "L0018:"), ("absent", "L0000:")]


def w_clear_spam():
    """Repeated full-screen clears (ESC[2J blanks both video banks + shadow)."""
    chunks = [b"\x1b[2J\x1b[HclearspamFILL\r\n" for _ in range(CLEARS)]
    chunks += [b"\x1b[2J\x1b[HCLEAR-SPAM-DONE"]
    return chunks, [("has", "CLEAR-SPAM-DONE"), ("absent", "clearspamFILL")]


def w_altscreen():
    """Enter/leave the alternate screen repeatedly: each cycle saves the whole
    screen into SAVE_BASE and restores it -- both read/write the shadow today."""
    chunks = [b"\x1b[2J\x1b[HMAIN-ALT-START\r\n"]
    chunks += [b"\x1b[?1049h\x1b[2JINALT\x1b[?1049l" for _ in range(ALTS)]
    chunks += [b"MAIN-ALT-END"]
    return chunks, [("has", "MAIN-ALT-START"), ("has", "MAIN-ALT-END"),
                    ("absent", "INALT")]


WORKLOADS = {
    "scroll_il": w_scroll_il,
    "scroll_big": w_scroll_big,
    "cat_narrow": w_cat_narrow,
    "cat_wide": w_cat_wide,
    "clear_spam": w_clear_spam,
    "altscreen": w_altscreen,
}


# --------------------------------------------------------------------------
# MAME + socket plumbing (host is the TCP server; MAME connects as client).
# --------------------------------------------------------------------------
def launch_mame(autoboot):
    # 1x throttle (no -nothrottle): under -nothrottle MAME advances emulated time
    # while idle-spinning for host bytes, which inflates the emulated clock. At 1x
    # the emulated-time tap and the wall clock agree for render-bound work.
    cmd = [MAME, "apple2e", "-rompath", ROMPATH, "-aux", "ext80",
           "-sl2", "ssc", "-sl2:ssc:rs232", "null_modem",
           "-bitb", f"socket.127.0.0.1:{PORT}",
           "-flop1", DISK, "-video", "none", "-sound", "none",
           "-skip_gameinfo", "-str", "3600", "-autoboot_script", autoboot]
    return subprocess.Popen(cmd, cwd=str(ROOT),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def accept_conn():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", PORT))
    srv.listen(1)
    srv.settimeout(60)
    return srv


class Terminal:
    """The MAME end of the socket. A background thread drains everything the
    terminal sends (so its transmitter never blocks), tracks XON/XOFF, and keeps
    the recent bytes so we can spot the DSR reply. Sends honor XON/XOFF."""

    def __init__(self, conn):
        self.conn = conn
        self.conn.setblocking(False)
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._paused = threading.Event()
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._drain, daemon=True)
        self._t.start()

    def _drain(self):
        while not self._stop.is_set():
            try:
                d = self.conn.recv(4096)
            except (BlockingIOError, OSError):
                time.sleep(0.002)
                continue
            if not d:
                time.sleep(0.002)
                continue
            for b in d:
                if b == XOFF:
                    self._paused.set()
                elif b == XON:
                    self._paused.clear()
            with self._lock:
                self._buf.extend(d)

    def send(self, data, chunk=16):
        data = bytes(data)
        i = 0
        while i < len(data):
            while self._paused.is_set():
                time.sleep(0.001)
            part = data[i:i + chunk]
            j = 0
            while j < len(part):
                try:
                    j += self.conn.send(part[j:])
                except (BlockingIOError, OSError):
                    time.sleep(0.001)
            i += chunk

    def clear_buf(self):
        with self._lock:
            self._buf.clear()

    def peek(self):
        """Return a copy of the bytes drained from the terminal so far.

        The conformance MameTarget uses this to capture wire read-back (the DA
        reply, the DSR/CPR) after a windowed send, without disturbing the drain
        thread. Read-only; the benchmark path does not use it."""
        with self._lock:
            return bytes(self._buf)

    def wait_cpr(self, timeout=OP_TIMEOUT):
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            with self._lock:
                if CPR.search(bytes(self._buf)):
                    return True
            time.sleep(0.001)
        return False

    def sync(self, timeout=OP_TIMEOUT):
        """Block until the terminal is idle (answers a fresh ESC[6n); clear buf."""
        self.clear_buf()
        self.send(b"\x1b[6n")
        ok = self.wait_cpr(timeout)
        self.clear_buf()
        return ok

    def send_windowed(self, chunks, window=WINDOW, timeout=OP_TIMEOUT):
        """Stream atomic chunks, batching them into <=window-byte groups. After
        each group we append ESC[6n and block on the reply, so no more than
        ~window bytes are ever in flight and the firmware ring can't overflow --
        lossless on arbitrarily long streams while pacing us to the render.
        Returns the number of windows (DSR acks) emitted."""
        nwin = 0
        batch = bytearray()

        def flush():
            nonlocal nwin
            self.clear_buf()
            self.send(bytes(batch) + b"\x1b[6n")
            if not self.wait_cpr(timeout):
                raise TimeoutError(f"no DSR after {len(batch)}-byte window")
            batch.clear()
            nwin += 1

        for ch in chunks:
            if batch and len(batch) + len(ch) > window:
                flush()
            batch.extend(ch)
        if batch:
            flush()
        return nwin

    def clear_screen(self):
        # ESC[r resets the scroll region so it can't leak between trials.
        self.send(b"\x1b[r\x1b[2J\x1b[H")

    def close(self):
        self._stop.set()


def wait_ready(term, timeout=40.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        term.clear_buf()
        term.send(b"\x1b[6n")
        if term.wait_cpr(1.0):
            term.clear_buf()
            return True
    return False


# --------------------------------------------------------------------------
# Screen correctness (wall pass): read the settled screen and apply checks.
# --------------------------------------------------------------------------
def read_screen():
    for _ in range(5):
        try:
            return SCREEN.read_text(errors="replace")
        except OSError:
            time.sleep(0.01)
    return ""


def check_screen(checks, settle=2.5):
    """screen_watch.lua snapshots at ~4 Hz; poll briefly for the expected state."""
    deadline = time.time() + settle
    fails = ["init"]
    while time.time() < deadline:
        joined = read_screen()
        fails = []
        for kind, text in checks:
            if kind == "has" and text not in joined:
                fails.append(f"missing {text!r}")
            elif kind == "absent" and text in joined:
                fails.append(f"unexpected {text!r}")
        if not fails:
            return []
        time.sleep(0.1)
    return fails


# --------------------------------------------------------------------------
# Emulated-time reader (bench_probe.lua appends a timestamp per DSR reply).
# --------------------------------------------------------------------------
def read_ticks():
    try:
        return [float(x) for x in TICKS.read_text().split()]
    except OSError:
        return []


def reset_ticks():
    try:
        TICKS.write_text("")
    except OSError:
        pass


def wait_ticks(n, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = read_ticks()
        if len(t) >= n:
            return t
        time.sleep(0.02)
    return read_ticks()


# --------------------------------------------------------------------------
def fmt_stats(samples):
    lo = min(samples)
    med = statistics.median(samples)
    mean = statistics.mean(samples)
    sd = statistics.pstdev(samples) if len(samples) > 1 else 0.0
    return lo, med, mean, sd


def selected_workloads(select):
    return [(n, g) for n, g in WORKLOADS.items() if not select or select in n]


def run_emu(select, trials, verbose):
    print("[emulated-time pass: 1x throttle, bench_probe.lua]")
    mame = launch_mame(PROBE_LUA)
    results = {}
    srv = accept_conn()
    conn, _ = srv.accept()
    srv.close()
    term = Terminal(conn)
    try:
        if not wait_ready(term):
            print("FAIL: terminal never answered ESC[6n")
            return None
        for name, gen in selected_workloads(select):
            chunks, _checks = gen()
            nbytes = sum(len(c) for c in chunks)
            samples = []
            for _ in range(trials):
                term.clear_screen()
                term.sync()                      # settle the clear, terminal idle
                reset_ticks()
                term.sync()                      # baseline DSR -> ticks[0]
                try:
                    nwin = term.send_windowed(chunks)   # +nwin window DSRs
                except TimeoutError as e:
                    print(f"  {name:12} TIMEOUT ({e})")
                    samples = []
                    break
                t = wait_ticks(1 + nwin, 10.0)
                if len(t) >= 2:
                    d = t[-1] - t[0]
                    samples.append(d)
                    if verbose:
                        print(f"        emu={d:7.3f}s  windows={nwin}")
            if not samples:
                print(f"  {name:12} (no samples)")
                continue
            lo, med, mean, sd = fmt_stats(samples)
            results[name] = {"bytes": nbytes, "emu_min": lo, "emu_med": med,
                             "emu_mean": mean, "emu_sd": sd}
            print(f"  {name:12} {nbytes:6}B  emu_min={lo:7.3f}s "
                  f"emu_med={med:7.3f}s sd={sd:5.3f}")
    finally:
        term.close()
        _shutdown(mame)
    return results


def run_wall(select, trials, verbose):
    print("[wall-clock pass: 1x throttle, screen_watch.lua]")
    mame = launch_mame(WATCH_LUA)
    results = {}
    srv = accept_conn()
    conn, _ = srv.accept()
    srv.close()
    term = Terminal(conn)
    try:
        if not wait_ready(term):
            print("FAIL: terminal never answered ESC[6n")
            return None
        for name, gen in selected_workloads(select):
            chunks, checks = gen()
            nbytes = sum(len(c) for c in chunks)
            samples, bad = [], []
            for _ in range(trials):
                term.clear_screen()
                term.sync()
                t0 = time.perf_counter()
                try:
                    term.send_windowed(chunks)
                except TimeoutError as e:
                    print(f"  {name:12} TIMEOUT ({e})")
                    samples = []
                    break
                samples.append(time.perf_counter() - t0)
                bad = check_screen(checks)
            if not samples:
                continue
            lo, med, mean, sd = fmt_stats(samples)
            results[name] = {"bytes": nbytes, "wall_min": lo, "wall_med": med,
                             "wall_mean": mean, "wall_sd": sd, "ok": not bad}
            tag = "ok" if not bad else "LOSSY:" + ";".join(bad)
            print(f"  {name:12} {nbytes:6}B  wall_min={lo:7.3f}s "
                  f"wall_med={med:7.3f}s  {tag}")
            if verbose:
                print(f"        samples={['%.3f' % s for s in samples]}")
    finally:
        term.close()
        _shutdown(mame)
    return results


def _shutdown(mame):
    try:
        mame.terminate()
        mame.wait(timeout=10)
    except Exception:
        try:
            mame.kill()
        except Exception:
            pass


def print_summary(out):
    emu = out.get("emu", {})
    wall = out.get("wall", {})
    names = list(dict.fromkeys(list(emu) + list(wall)))
    if not names:
        return
    print("\n=== summary (seconds; lower is faster) ===")
    print(f"{'workload':12} {'bytes':>6} {'emu_min':>8} {'emu_med':>8} "
          f"{'wall_min':>8} {'wall_med':>8}  correct")
    for n in names:
        e, w = emu.get(n, {}), wall.get(n, {})
        b = e.get("bytes", w.get("bytes", 0))
        em = f"{e['emu_min']:8.3f}" if e else f"{'-':>8}"
        emd = f"{e['emu_med']:8.3f}" if e else f"{'-':>8}"
        wm = f"{w['wall_min']:8.3f}" if w else f"{'-':>8}"
        wmd = f"{w['wall_med']:8.3f}" if w else f"{'-':>8}"
        ok = ("yes" if w.get("ok") else "NO") if w else "-"
        print(f"{n:12} {b:6} {em} {emd} {wm} {wmd}  {ok}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-k", dest="select", default="",
                    help="only workloads whose name contains this text")
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--wall-only", action="store_true",
                    help="only the wall-clock/correctness pass")
    ap.add_argument("--emulated-only", action="store_true",
                    help="only the deterministic emulated-time pass")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--json", default="", help="write results to this JSON file")
    args = ap.parse_args()

    if not pathlib.Path(DISK).exists():
        sys.exit(f"missing {DISK} -- run `make` first")

    out = {}
    if not args.wall_only:
        e = run_emu(args.select, args.trials, args.verbose)
        if e:
            out["emu"] = e
    if not args.emulated_only:
        w = run_wall(args.select, args.trials, args.verbose)
        if w:
            out["wall"] = w

    print_summary(out)
    if args.json and out:
        pathlib.Path(args.json).write_text(json.dumps(out, indent=2))
        print(f"[wrote {args.json}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
