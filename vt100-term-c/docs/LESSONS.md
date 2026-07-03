# Lessons and design decisions

The non-obvious choices in this project, and the bugs that led to them. If you
maintain or port the terminal, this is the context that will save you time.

## Direct auxiliary memory, not the firmware

Going through the 80-column firmware's `COUT` would have been simpler, but it
does not give exact control over the cursor, scrolling, or partial clears — and
it does not let the terminal know where the cursor is. Driving the interleaved
text page directly ([docs/80COLUMN.md](80COLUMN.md)) makes cursor-position
reports exact, which in turn makes the terminal testable via `ESC[6n` with no
screen scraping at all.

## The greeting-program auto-boot

The terminal is installed as the DOS 3.3 **greeting program**: `hello.bas` `BRUN`s
the binary, so booting the disk runs the terminal with no keystroke timing. This
works identically on MAME and real hardware — unlike MAME `-autoboot_command`,
which is emulator-only and timing-sensitive. (AppleCommander tokenizes the
Applesoft greeting with `-bas`, not `-as`.)

## The 6551 overrun, twice

The 6551 holds exactly one received byte. At 9600 baud it must be read within
about a millisecond, and two things kept violating that:

1. **Slow screen operations.** A full-row clear takes ~1.5 ms, longer than a byte
   time, so the terminal dropped every other byte during clears and scrolls. Fix:
   call `serial_pump()` every 8 cells inside every slow loop. This was found by
   reading the compiler's assembly (`build/screen80.s`) to estimate the cycle
   cost per cell.
2. **Transmitting replies.** When the host sent a Device Attributes request
   (`ESC[c`, which ConPTY does at startup), the terminal transmitted its 7-byte
   answer with a blocking `serial_put()` that did **not** drain RX — so the host's
   next bytes overran the register, leaving escape-sequence fragments like `04h`
   on screen. Fix: drain RX inside `serial_put()`'s transmit-wait loop.

The lesson: **any** code path that can hold the CPU for a byte time must keep the
receiver drained. Both fixes are just that, in different places.

## Memory-mapped I/O must be `volatile`

An early version cached the 6551 status register because the pointer wasn't
`volatile`; with `-O`, cc65 read it once and spun forever. MMIO pointers and
soft-switch reads in loops must be `volatile` so every access is a real bus
cycle.

## The shadow buffer

The first screen dumper toggled `PAGE2` from MAME's Lua to read both memory banks
of the text page. Done asynchronously on a frame notifier, it raced the running
terminal and corrupted the display (a doubled, garbled boot screen). There is no
way to read the bank-split page from outside without touching `PAGE2`.

The fix was to stop reading the video page at all: the firmware mirrors every
glyph into a plain, linear, non-banked buffer at `$7000`, and the dumper reads
**that**. No banking, no race, no side effects. It costs ~2 KB of otherwise-
unused RAM in the gap below the C stack, and it is the single thing that makes
the shell-render test suite possible.

## Windows file semantics bit the dumper

Publishing the snapshot went through two wrong versions before the right one:

- `os.rename(tmp, dst)` **fails if `dst` exists on Windows** (POSIX would
  overwrite). Symptom: `screen.txt` froze at the very first, pre-boot, blank
  snapshot forever — which looked exactly like a broken shadow buffer and cost
  hours to diagnose.
- Removing `dst` first, or writing `dst` directly, can then fail while the Python
  reader holds the file open (a sharing violation).

Final design: build the whole 24-line snapshot in memory, write it in one
`io.open("w")`, and wrap the frame-notifier body in `pcall` so a transient lock
skips one frame instead of killing the dumper. A rare partial read is harmless
because the harness waits for the screen to settle.

## MAME `-nothrottle` versus `-str`

`-str N` stops MAME after **N emulated seconds**. With `-nothrottle` the emulator
runs ~27× real time, so `-str 600` expired in ~22 s of wall-clock and MAME quit
in the middle of the test suite — which presented as the terminal mysteriously
"freezing" after a few cases. Wall-clock-long test drivers run MAME at 1× and
terminate it explicitly.

## Interactive bash under ConPTY

A fully interactive `bash -i` bridged through ConPTY + WSL interop stalls on job
control — it echoes typed input but never executes it, then exits. The automated
suite sidesteps this entirely by running each command in a fresh
`wsl.exe -e bash -c "…"`, which is also more deterministic for testing. The
interactive `vt100_shell.py` bridge is meant for a human at a real keyboard.

## Arrow keys

The IIe delivers arrow keys as raw control codes (`$08/$15/$0B/$0A`), which a host
reads as backspace and friends, not cursor motion. `term.c` translates them to
`ESC[A..D`, and to `ESC O A..D` when application cursor keys (DECCKM) are enabled
— which is what `vi` and other full-screen programs ask for.

## Testing philosophy

Prefer assertions the machine can make unambiguously: cursor-position reports for
motion, and a settled snapshot of the shadow buffer for rendering. Choose test
commands whose **output** differs from the typed text, so a pass proves the
terminal rendered the result rather than merely echoing input.
