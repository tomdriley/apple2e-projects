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
3. **Answer queries** (if it's a report) by building the whole reply and calling
   `serial_write()`. It publishes a complete short reply before arming an idle
   transmitter while the assembly ISR independently keeps RX drained.
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

Screen operations never service the ACIA. The RX ISR continues filling the ring
during long clears, scrolls, bank switches, and alternate-screen copies. When
you shift cells within a row, read the source glyphs from the video page with
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

- **Memory-mapped I/O must be `volatile`.** The 6551 pointer is
  `volatile unsigned char *`; without it, `-O` caches reads of the status
  register and the driver hangs or misbehaves. Same for any soft switch you read
  in a loop. Do not write the 6551 data register through that dynamic pointer:
  cc65 emits `STA (zp),Y`, whose NMOS 6502 dummy read consumes RDR before writing
  TDR. The serial ISR must use a slot-specific absolute store.
- **`-Cl` static locals.** C functions are not reentrant. The serial ISR is pure
  assembly, calls no C, and uses dedicated zero-page scratch. Do not add
  recursion or call C from an interrupt handler.
- **Interrupt-shared state is `volatile`.** The ring indices written by the ISR
  must be re-read by C. Keep shared byte writes atomic; protect multi-step
  invariants (the TX capacity check/store/publish) with a brief `SEI`/`CLI`.
- **Apple IRQ ABI.** The ROM saves A at `$45` and jumps through `$03FE`; an ISR
  must preserve X/Y, restore A, avoid cc65 runtime temporaries, and finish with
  `RTI`. Unclaimed IRQs must restore the same entry contract before chaining.
  The serial ISR must not touch `PAGE2`.
- **6502 stores can read first.** `STA (zp),Y` performs a dummy read before its
  write. Never use it for ACIA DATA: the read clears RDRF. The serial ISR uses an
  install-time-patched absolute `STA` for exactly this reason.
- **C89 declarations.** cc65 wants declarations at the top of a block. Declaring a
  variable mid-block, or after a statement, is a compile error.
- **Definition order.** A `static` function must be defined (or forward-declared)
  before it is called, or you get an implicit-declaration / conflicting-type
  error.
- **Watch the byte budget.** The image loads at `$0800` and must stay below the
  alternate-screen save area at `$6800` and the C stack. `make` prints the size;
  the linker map is `build/vt100.map`.
- **Inspect the generated assembly.** `build/<name>.s` is the compiler's 6502
  output. Check critical sections and hot loops there, and inspect
  `build/vt100.map` for zero-page/BSS growth.

## Change the baud rate

Set the control-register value in [serial.c](../serial.c) (`CTRL_9600_8N1 =
0x1E`) to the 6551 code for the new rate, and match it on the host side (the
Python clients default to 9600). Faster rates improve throughput but increase
ISR frequency and fill the software ring faster during long renders — keep flow
control on and re-run the ROM-backed stress cases.

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
