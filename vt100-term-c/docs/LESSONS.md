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

## The 6551 overrun, again and again

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

The polling-era lesson was: **any** code path that can hold the CPU for a byte
time must keep the receiver drained. Both fixes were just that, in different
places.

A third instance showed up later under sustained scrolling: blanking the new
bottom row of the shadow buffer after each scroll is a ~0.9 ms loop, and the byte
that arrived during it was lost — so the last lines of a fast `seq 1 30` into a
scroll region went missing. The fix was the same: pump inside that loop too. The
rule that emerged is simply **no memory loop over more than ~40 bytes without a
`serial_pump()`**. A region-scroll status-bar test is what exposed it.

Removing the shadow buffer (below) brought two more instances — a good reminder
that a rendering change can re-expose this class of bug somewhere new:

1. **Saving the alternate screen.** `read_row_glyphs` reads a whole 80-cell row
   back from the video page (~3 ms in cc65), and a `DECSET ?1049h` save reads all
   24 rows back-to-back while the host keeps streaming the rest of its payload.
   With no drain inside the read, the tail bytes overran. Fix: `serial_pump()`
   every 8 cells during the read-back.
2. **Short erases.** The per-cell erase/shift/redraw loops (`blank_to`,
   `row_blank_from`, `scr_erase_chars`, insert/delete, and the alternate-screen
   restore) flip `PAGE2` **per cell** via `cell_put`, and
   in cc65 each cell is a sizeable fraction of a byte time — so even a 6-cell
   erase-to-BOL spanned more than one byte time, and the byte that followed the
   escape sequence (e.g. the `X` in `ESC[1KX`) was overwritten in the RX register
   and lost. Fix: `serial_pump()` after **every** cell in those loops.

Both were invisible while the shadow existed: the old shadow path wrote a linear,
non-banked buffer with no per-cell bank switch, so the same loops ran fast enough
to stay under a byte time. Removing the shadow shifted that compiled timing just
enough to cross the line — the render change and the serial bug were coupled
through raw cycle count.

Those pump additions were useful mitigations, but they also exposed the
architectural problem: correctness depended on finding every one-millisecond
gap forever. Report TX later needed three more narrow fixes (pre-drain before
TDRE, absolute TDR stores to avoid the NMOS dummy read, and division-free CPR
formatting). They made back-to-back `ESC[6n` deterministic, but a deliberate
no-pump delay such as BEL still dropped an incoming burst.

The durable fix is **receiver interrupt, transmitter polling**:

- command `$09` enables only the 6551 RX interrupt; physical SSC SW2:6 must be On;
- a pure-assembly Monitor handler reads RDR before doing ring bookkeeping;
- it calls only the assembly ring producer, never C under `-Cl`;
- status polling masks IRQ and captures RDR, because any status read clears the
  interrupt latch;
- foreign IRQs restore exact Monitor-entry A/P/X/Y and chain to the prior vector;
- Ctrl-Reset and normal exit disable the card and restore IRQLOC, SOFTEV/PWREDUP,
  and the prior ACIA configuration.

The screen-bank interaction turned out simpler than toggling `PAGE2` in the ISR.
With 80STORE, `PAGE2` affects only `$0400-$07FF`; the program and RX state live
above `$0800`. Linker assertions enforce that invariant, so an interrupt can run
while AUX is selected without touching the switch or disturbing the interrupted
screen operation.

## The ring's "full" guard that never fired

The overrun fixes above lean on the 256-byte RX ring to soak up bursts. But the
ring had its own latent bug: the occupancy counter `r_count` was an
`unsigned char`, while `RING_SIZE` is 256. The full guards were written
`r_count != RING_SIZE` — and since an `unsigned char` can only hold 0..255, that
comparison is **always true**. cc65 even said so: *"Result of comparison is always
true"* at both guard sites. The ring therefore never detected "full": on overflow
`serial_pump()`/`serial_put()` kept writing, `r_head` lapped `r_tail` (silently
overwriting unread bytes), and `++r_count` wrapped `255 -> 0`, corrupting the
count. XON/XOFF at 192 bytes normally keeps the ring from ever reaching 256, which
is why this stayed hidden — but a host that ignores or lags XOFF walks straight
into it.

The obvious quick fix is to widen `r_count` to `unsigned` and compare
`r_count < RING_SIZE` — that detects full and silences the warnings. But it leaves
a 16-bit occupancy counter that is *redundant* with the head/tail pointers and,
being multi-byte, is not read or written atomically — a wart for the
interrupt-driven RX path. The shipped fix instead **drops the counter entirely**
and derives the state from the two pointers, the way a hardware FIFO does:

- `r_head`/`r_tail` stay `unsigned char`, so they wrap mod 256 for free.
- One slot is reserved as a sentinel, so `r_head == r_tail` is unambiguously
  *empty* and `(unsigned char)(r_head + 1) == r_tail` is *full* (255 usable).
- Occupancy for the XON/XOFF thresholds is `(unsigned char)(r_head - r_tail)` — a
  single-byte subtract, with no counter that can overflow.
- `serial_rx_ready()` returns that pointer distance directly (0..255), so the old
  clamp is gone too.

Why a sentinel slot rather than `RING_SIZE = 255`? Two same-width pointers can
represent only 256 distinct differences but a 256-slot ring has 257 occupancy
states (0..256), so `head == tail` would mean *both* empty and completely full —
ambiguous. Reserving one slot (or, alternatively, an extra wrap bit) is what breaks
that tie. And `RING_SIZE = 255` specifically is the worst option: `r_head`/`r_tail`
rely on **free mod-256 wrap** (`ring[r_head]`, no explicit `% RING_SIZE`), so
dropping to 255 would force a modulo-255 or a wrap branch on every push and pop —
more hot-path code than the one unused byte a sentinel costs.

The pointer-compare ring is also smaller and faster than the counter version at the
6502 level: no `inc low / bne / inc high` counter upkeep, an absolute-indexed
store instead of building a zero-page pointer, and a single-byte threshold
compare. The ISR owns head and main owns tail. Assembly push stores data before
publishing head; pop reads data before publishing tail. A full ring drops the
newest byte, preserves all queued data, and increments a saturating telemetry
byte.

The lesson: **an occupancy counter is redundant with the pointers, and redundant
state is somewhere for a bug to hide** — here, a counter too narrow to represent a
full ring, which cc65 flagged as an always-true comparison. Trust those "always
true/false" warnings; this one marked a real data-corruption path. Prefer the
two-pointer FIFO that hardware uses: empty when the pointers meet, full when the
writer sits one slot behind the reader.

**Why there is no corpus/MAME regression test for this.** The overflow can't be
provoked through the conformance harness: its transport is *windowed-lossless* by
construction. The sender batches the payload into `WINDOW`-byte groups and blocks
for a cursor-report (DSR/CPR) ack before releasing the next window, with
`WINDOW = 96` — deliberately `< ring/2` (see `client/bench.py`) — and it honors
XOFF. So a conformance case can never put more than ~96 bytes in flight, and the
ring never approaches full; the very thing that makes the harness deterministic
also makes it structurally incapable of flooding the ring. A real overflow test
would need to bypass flow control and race the drain loop, which the harness will
not do. The full/empty logic is therefore covered instead by (1) the cc65 warning's
disappearance (the old always-true comparison is gone), (2) the existing ROM-backed
MAME conformance gate (proves the ring rework didn't regress normal receive), and
(3) a ROM-free host unit test (`make test`, see [TESTING.md](TESTING.md)) that
drives the ring past capacity and asserts full detection, retained-byte FIFO
integrity, wraparound, drop-newest counting/saturation, and reset. The test links
the production `ring.c` storage and `ring_io.s` operations rather than mirroring
them. Accepting the absent corpus overflow test is deliberate: the timed
ROM-backed IRQ test covers actual ISR publication, while sim65 covers the
otherwise hard-to-force full/drop boundary.

## Memory-mapped I/O must be `volatile`

An early version cached the 6551 status register because the pointer wasn't
`volatile`; with `-O`, cc65 read it once and spun forever. MMIO pointers and
soft-switch reads in loops must be `volatile` so every access is a real bus
cycle.

Interrupt RX adds a second rule: **a real bus cycle can itself be destructive**.
Reading 6551 status clears the IRQ latch, and an indirect-indexed TDR store
dummy-reads RDR first on the NMOS 6502. Runtime status access therefore lives in
IRQ-masked assembly, and TX uses slot-specific absolute stores. `volatile` is
necessary, but it is not enough to make the wrong addressing mode or an
unserialized status read safe.

## The shadow buffer, and why it is gone

The first screen dumper toggled `PAGE2` from MAME's Lua to read both memory banks
of the text page, but it did so naively — without putting the terminal's prior
`PAGE2` state back — so the CPU resumed banked into the wrong half of the text
page. That raced the running terminal and corrupted the display (a doubled,
garbled boot screen).

The original reaction was to stop reading the video page at all: the firmware
mirrored every glyph into a plain, linear, non-banked buffer at `$7000`, and the
dumper read **that**. No banking, no race, no side effects — and it made the
shell-render test suite possible. But it was not free: the mirror had to be
updated on every screen mutation, and on a scroll that linear copy moves as many
bytes as both video banks combined, so it **roughly doubled the cost of every
scroll** (see [docs/PERFORMANCE.md](PERFORMANCE.md)).

So the shadow was later **removed**. The insight that made that safe: you *can*
read the bank-split page from outside — you just have to toggle `PAGE2` **from the
frame notifier, which fires with the CPU paused between frames, and put `PAGE2`
back exactly as you found it** before the CPU resumes. The reader samples the
current state from the `RDPAGE2` soft switch (`$C01C`, bit 7), reads MAIN and AUX,
then restores it. Between-frames access plus an exact restore is the whole
difference from the first naive attempt; it does not race the terminal at all.
With the reader no longer needing plain RAM, the firmware writes only the real
video page, and scrolling is ~47% faster.

## Windows file semantics bit the dumper

Publishing the snapshot went through two wrong versions before the right one:

- `os.rename(tmp, dst)` **fails if `dst` exists on Windows** (POSIX would
  overwrite). Symptom: `screen.txt` froze at the very first, pre-boot, blank
  snapshot forever — which looked exactly like a broken screen reader and cost
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
motion, and a settled snapshot of the rendered video page for rendering. Choose test
commands whose **output** differs from the typed text, so a pass proves the
terminal rendered the result rather than merely echoing input.
