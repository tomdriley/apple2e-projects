# Hacking guide

How to extend and modify the terminal, plus the cc65 and hardware conventions
that will bite you if you don't know them.

## Add an escape sequence

Most sequences are a few lines in the CSI dispatcher.

1. **Implement the screen effect** (if new) in [screen80.c](../screen80.c) and
   declare it in [screen.h](../screen.h). Keep the interface Apple-agnostic — the
   parser must not know about banks or addresses. Write the video page through
   `cell_put()` or the existing screen80 helpers.
2. **Dispatch it** in `csi_dispatch()` in [vt100.c](../vt100.c). Read parameters
   with `getp(n)` (returns 0 if absent; apply the sequence's default). The
   `priv` flag is set when the CSI had a `?`.
3. **Answer queries** (if it's a report) by calling `serial_put()`. Replies are
   several bytes — RX interrupts remain active while polled TX waits, and the
   status helper safely captures any byte already in RDR.
4. **Add a test** ([docs/TESTING.md](TESTING.md)): a cursor test if the effect is
   observable via `ESC[6n`, or a shell/`printf` render test otherwise.
5. `make` and run the suites.

Two-byte escapes (`ESC x`) are handled in the `S_ESC` case of `vt100_feed()`.

Example — the reverse index (`ESC M`) added in `S_ESC`:

```c
case 'M': /* RI: reverse index -- up one line, scroll at the top margin */
    scr_ri();
    break;
```

## Add a screen operation

The reusable primitives in `screen80.c`:

- `cell_put(col, row, ch)` — one glyph, handling bank selection.
- `blank_to(row, to)` / `row_blank_from(row, from)` — blank a span of a row.
- `region_up(top, bot)` / `region_down(top, bot)` — scroll a row range one line.

The existing slow loops retain `serial_pump()` calls. RX IRQ now protects the
one-byte 6551 register, but pumping during long work still provides the
IRQ-disabled fallback and gives main context a chance to send XOFF before the
software ring fills. New long-running loops should service `serial_pump()`
periodically; they no longer need a fragile per-byte-time cadence. When you shift
cells within a row, read the source glyphs from the video page with
`read_row_glyphs(row, buf)` into a local buffer, then shift.

## The visible cursor is an overlay — keep it off-screen during work

The text cursor ([screen80.c](../screen80.c): `scr_cursor_paint` /
`scr_cursor_erase`) is painted by inverting one cell and is only ever on the
screen while the terminal loop is idle. [term.c](../term.c) calls
`scr_cursor_erase()` before feeding any received byte to the parser and
`scr_cursor_paint()` only when the receive ring is empty. **Every `scr_*`
operation therefore runs on a screen with no cursor painted**, so it must not try
to preserve or special-case the cursor cell — the loop guarantees the stored
glyph is the real one. If you add a code path that renders bytes outside the main
loop, erase the cursor first (an inline draw/erase between bytes is exactly what
corrupted the first attempt). `cursor_visible` (DECTCEM), `cursor_shown`, and the
saved-glyph state are non-static so the conformance probe can verify visibility
and strip the overlay from its read-back.

## cc65 conventions that matter

- **Memory-mapped I/O must be `volatile`.** The slot-detection pointer and soft
  switches are volatile. After IRQ installation, all 6551 status reads go through
  `serial_irq.s`: a status read clears the IRQ latch, so C must not read it behind
  the handler's back. Do not write the data register through the dynamic pointer:
  cc65 emits `STA (zp),Y`, whose NMOS 6502 dummy read consumes RDR before writing
  TDR. Use `serial.c`'s slot-specific volatile absolute `write_tdr()` stores.
- **`-Cl` static locals.** The build uses statically allocated locals for smaller,
  faster code. Main-line C is non-reentrant; the RX ISR is pure assembly and
  calls no C or cc65 runtime helper. Do **not** add recursion or call C from an
  interrupt handler.
- **C89 declarations.** cc65 wants declarations at the top of a block. Declaring a
  variable mid-block, or after a statement, is a compile error.
- **Definition order.** A `static` function must be defined (or forward-declared)
  before it is called, or you get an implicit-declaration / conflicting-type
  error.
- **Watch the byte budget.** The image loads at `$0800` and must stay below the
  alternate-screen save area at `$6800` and the C stack. `make` prints the size;
  the linker map is `build/vt100.map`.
- **Inspect the generated assembly.** `build/<name>.s` is the compiler's 6502
  output. Reading it is the reliable way to judge a hot loop's cycle cost — that's
  how the every-8-cells pump interval was chosen.

## IRQ handler conventions

- Apple Monitor IRQ dispatch saves interrupted A at `$45` and jumps through
  `IRQLOC` (`$03FE/$03FF`). Preserve the Monitor-dispatch A/P and interrupted X/Y
  exactly before chaining a foreign IRQ; handled IRQs reload A from `$45` before
  `RTI`.
- The ISR must stay in assembly, avoid cc65 zero-page temporaries, and never call
  C under `-Cl`. `ring_io.s` is the only callable producer seam.
- Status reads are destructive to the 6551 IRQ latch. Main context must use
  `serial_irq_status()`, which masks IRQ around status/RDR/enqueue.
- The ISR must not toggle `PAGE2`. With 80STORE, only `$0400-$07FF` is banked;
  linker assertions keep every IRQ-touched object at or above `$0800`.
- Install the Ctrl-Reset cleanup hook before `IRQLOC`, enable the 6551 interrupt
  last, and restore ACIA/vectors before returning to DOS. Do not bypass
  `serial_irq_shutdown()` from a new exit path.

## Change the baud rate

Set `ACIA_CONTROL_9600` in [serial_irq.s](../serial_irq.s) to the 6551 code for
the new rate, and match it on the host side (the Python clients default to
9600). Faster rates fill the software ring sooner — keep flow control on and
retest the timed IRQ receive case.

## Change the screen size

`SCR_COLS`/`SCR_ROWS` in [screen.h](../screen.h) parameterize most of the code,
but the `rowbase[]` table in `screen80.c` is hand-built for 80×24. Changing
geometry means regenerating it and re-checking the memory map. 80×24 is the
natural fit for the IIe's hardware text page.

## Performance notes

- The parser's hot path is printable characters; they take the first branch in
  `vt100_feed()`.
- `cell_put()` flips `PAGE2` per character. For long runs this dominates; a
  bulk-write path that groups same-bank cells is a possible optimization (measure
  first via `build/screen80.s`).
- Scrolling copies 1920 bytes across both banks. It is the most expensive routine
  and the main reason flow control exists.

See [docs/LESSONS.md](LESSONS.md) for the reasoning behind these choices.
