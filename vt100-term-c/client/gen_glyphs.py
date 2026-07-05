#!/usr/bin/env python3
"""Generate client/glyphs80.lua: a fingerprint -> ASCII table for the pixel-based
screen reader (client/screen_pixels.lua / screen_watch.lua).

It boots the current firmware in MAME, paints every printable character once in
normal video and once in inverse video, and for each screen cell pairs the 56-bit
pixel fingerprint (what the reader will see) with the glyph the $7000 shadow holds
(decoded exactly as the old shadow reader did -- the ground truth while the shadow
still exists). The resulting map lets the reader recover text from pixels alone,
so the firmware shadow can be removed. Regenerate after any change that affects
glyph rendering:

    python client/gen_glyphs.py
"""
import pathlib
import re
import socket
import subprocess
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
MAME = r"C:\mame\mame.exe"
ROMPATH = r"C:\mame\roms"
DISK = str(ROOT / "build" / "vt100.dsk")
DUMP = ROOT / "build" / "glyphdump.txt"
DUMPER = ROOT / "build" / "_glyphdump.lua"
OUT = HERE / "glyphs80.lua"
PORT = 6551
CPR = re.compile(rb"\x1b\[(\d+);(\d+)R")

# Autoboot Lua: every 4 frames, atomically-ish rewrite build/glyphdump.txt with a
# "FRAME n" header then one "row col fingerprint shadow" line per cell. Python
# only samples a frame that is fully rendered (see wait_stable_valid). Uses the
# same cell_fp as the real reader.
DUMPER_LUA = r'''
local M = dofile("client/screen_pixels.lua")
local SHADOW = 0x7000
local frames = 0
_gd = emu.add_machine_frame_notifier(function()
  frames = frames + 1
  if frames % 4 ~= 0 then return end
  pcall(function()
    local scr = M.screen(); if not scr then return end
    local buf = scr:pixels(); local w = scr.width
    local mem = manager.machine.devices[":maincpu"].spaces["program"]
    local out = { string.format("FRAME %d", frames) }
    for r = 0, M.ROWS - 1 do
      for c = 0, M.COLS - 1 do
        local fp = M.cell_fp(buf, w, r, c)
        local sh = mem:read_u8(SHADOW + r * 80 + c)
        out[#out + 1] = string.format("%d %d %016X %02X", r, c, fp, sh)
      end
    end
    local f = io.open("build/glyphdump.txt", "w")
    if f then f:write(table.concat(out, "\n")); f:close() end
  end)
end)
'''

CELLS = 24 * 80


def decode_shadow(raw):
    """Exactly the mapping the old shadow reader used (screen_watch.lua)."""
    if raw >= 0x80:
        b = raw & 0x7F          # normal high-bit ASCII
    elif raw < 0x20:
        b = raw + 0x40          # inverse upper case ($00-$1F -> @A-Z...)
    else:
        b = raw                 # inverse space / digit / symbol ($20-$3F)
    if b < 0x20 or b == 0x7F:
        b = 0x20
    return b


class Drain:
    def __init__(self, conn):
        self.conn = conn
        self.conn.setblocking(False)
        self.buf = bytearray()
        self.lock = threading.Lock()
        self.stop = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while not self.stop.is_set():
            try:
                d = self.conn.recv(4096)
            except (BlockingIOError, OSError):
                time.sleep(0.003)
                continue
            if d:
                with self.lock:
                    self.buf.extend(d)
            else:
                time.sleep(0.003)

    def send(self, data):
        data = bytes(data)
        i = 0
        while i < len(data):
            try:
                i += self.conn.send(data[i:])
            except (BlockingIOError, OSError):
                time.sleep(0.001)

    def clear(self):
        with self.lock:
            self.buf.clear()

    def wait_cpr(self, timeout=30.0):
        end = time.perf_counter() + timeout
        while time.perf_counter() < end:
            with self.lock:
                if CPR.search(bytes(self.buf)):
                    return True
            time.sleep(0.002)
        return False

    def sync(self):
        self.clear()
        self.send(b"\x1b[6n")
        ok = self.wait_cpr()
        self.clear()
        return ok


def read_dump():
    """Return (frame, cells) for a complete dump, else (None, None) for a torn or
    missing file. cells maps (r, c) -> (fingerprint, shadow_byte)."""
    try:
        txt = DUMP.read_text()
    except OSError:
        return None, None
    lines = txt.splitlines()
    if not lines or not lines[0].startswith("FRAME"):
        return None, None
    try:
        frame = int(lines[0].split()[1])
    except (IndexError, ValueError):
        return None, None
    cells = {}
    for line in lines[1:]:
        parts = line.split()
        if len(parts) != 4:
            return None, None            # caught mid-write; retry
        r, c, fp, sh = int(parts[0]), int(parts[1]), int(parts[2], 16), int(parts[3], 16)
        cells[(r, c)] = (fp, sh)
    if len(cells) != CELLS:
        return None, None                # incomplete
    return frame, cells


def wait_stable_valid(timeout=12.0, need=2):
    """Sample a fully-rendered frame. MAME's pixel bitmap briefly lags the shadow
    during a repaint, so we wait for a fresh dump that is both *valid* (every
    non-space shadow cell has a non-zero pixel fingerprint -- i.e. nothing is
    mid-draw) and *stable* (identical fingerprints across `need`+1 consecutive
    fresh dumps). Returns the accepted cells."""
    end = time.time() + timeout
    prev_frame = -1
    last_valid_fp = None
    matches = 0
    while time.time() < end:
        frame, cells = read_dump()
        if cells is None or frame == prev_frame:
            time.sleep(0.02)
            continue
        prev_frame = frame
        valid = all(fp != 0 for (fp, sh) in cells.values()
                    if decode_shadow(sh) != 0x20)
        fpmap = tuple(cells[rc][0] for rc in sorted(cells))
        if valid and fpmap == last_valid_fp:
            matches += 1
            if matches >= need:
                return cells
        else:
            matches = 0
        last_valid_fp = fpmap if valid else None
        time.sleep(0.02)
    raise SystemExit("timed out waiting for a stable, fully-rendered pixel frame")


def paint_and_read(term, prefix, suffix):
    printable = bytes(range(0x20, 0x7F))          # 0x20..0x7E
    term.send(b"\x1b[2J\x1b[H" + prefix + printable + suffix + b"\x1b[6n")
    term.wait_cpr()
    return wait_stable_valid()


def main():
    if not pathlib.Path(DISK).exists():
        raise SystemExit(f"missing {DISK} -- run `make` first")
    DUMPER.write_text(DUMPER_LUA)

    cmd = [MAME, "apple2e", "-rompath", ROMPATH, "-aux", "ext80",
           "-sl2", "ssc", "-sl2:ssc:rs232", "null_modem",
           "-bitb", f"socket.127.0.0.1:{PORT}",
           "-flop1", DISK, "-video", "none", "-sound", "none",
           "-skip_gameinfo", "-str", "120", "-autoboot_script", str(DUMPER)]
    mame = subprocess.Popen(cmd, cwd=str(ROOT),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", PORT))
    srv.listen(1)
    srv.settimeout(60)
    conn, _ = srv.accept()
    srv.close()
    term = Drain(conn)
    glyphs = {}     # fp -> ascii
    charfp = {}     # ascii -> set(fp) (for reporting)
    try:
        # wait for the terminal to answer DSR
        end = time.time() + 40
        ready = False
        while time.time() < end:
            term.clear()
            term.send(b"\x1b[6n")
            if term.wait_cpr(1.0):
                ready = True
                break
        if not ready:
            raise SystemExit("terminal never answered ESC[6n")

        passes = [("normal", b"", b""),
                  ("inverse", b"\x1b[7m", b"\x1b[0m")]
        for label, pre, suf in passes:
            cells = paint_and_read(term, pre, suf)
            got = 0
            for (r, c), (fp, sh) in cells.items():
                ascii_b = decode_shadow(sh)
                if fp in glyphs and glyphs[fp] != ascii_b:
                    raise SystemExit(
                        f"fingerprint collision fp={fp:016X}: "
                        f"{glyphs[fp]:#04x} vs {ascii_b:#04x} ({label} r{r} c{c})")
                glyphs[fp] = ascii_b
                charfp.setdefault(ascii_b, set()).add(fp)
                got += 1
            print(f"[{label}] cells={got} distinct-fp={len(glyphs)}")
    finally:
        term.stop.set()
        try:
            mame.terminate()
            mame.wait(timeout=10)
        except Exception:
            try:
                mame.kill()
            except Exception:
                pass

    # sanity: every printable ASCII must be representable (normal video)
    missing = [ch for ch in range(0x20, 0x7F) if ch not in charfp]
    if missing:
        print("WARNING: no glyph recorded for: "
              + " ".join(f"{m:#04x}" for m in missing))

    lines = [
        "-- Generated by client/gen_glyphs.py -- do not edit by hand.",
        "-- Maps each 56-bit cell fingerprint (client/screen_pixels.lua cell_fp)",
        "-- to the ASCII byte the terminal renders there, so the screen reader can",
        "-- recover text from pixels without the firmware $7000 shadow buffer.",
        "-- Regenerate with: python client/gen_glyphs.py",
        "return {",
    ]
    for fp in sorted(glyphs, key=lambda k: (glyphs[k], k)):
        ch = glyphs[fp]
        disp = chr(ch) if 0x21 <= ch <= 0x7E else "space"
        lines.append(f"    [0x{fp:016X}] = 0x{ch:02X}, -- {disp}")
    lines.append("}")
    OUT.write_text("\n".join(lines) + "\n")
    print(f"[wrote {OUT}]  {len(glyphs)} fingerprints, "
          f"{len(charfp)} distinct characters")
    try:
        DUMPER.unlink()
        DUMP.unlink()
    except OSError:
        pass


if __name__ == "__main__":
    main()
