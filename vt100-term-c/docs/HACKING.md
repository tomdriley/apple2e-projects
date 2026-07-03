# Hacking guide

How to extend and modify the terminal, plus the cc65 and hardware conventions
that will bite you if you don't know them.

## Add an escape sequence

Most sequences are a few lines in the CSI dispatcher.

1. **Implement the screen effect** (if new) in [screen80.c](../screen80.c) and
   declare it in [screen.h](../screen.h). Keep the interface Apple-agnostic — the
   parser must not know about banks or addresses. Update the **shadow buffer**
   wherever you change the video page, or the tests will read stale content.
2. **Dispatch it** in `csi_dispatch()` in [vt100.c](../vt100.c). Read parameters
   with `getp(n)` (returns 0 if absent; apply the sequence's default). The
   `priv` flag is set when the CSI had a `?`.
3. **Answer queries** (if it's a report) by calling `serial_put()`. Replies are
   several bytes — that's fine, `serial_put()` keeps RX drained while it sends.
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

Any loop that can run longer than ~1 ms must call `serial_pump()` periodically
(the convention here is every 8 cells) so the 6551 doesn't overrun. When you
shift cells within a row, read the source glyphs from `shadowrow[row]` — the
shadow already holds display bytes.

## cc65 conventions that matter

- **Memory-mapped I/O must be `volatile`.** The 6551 pointer is
  `volatile unsigned char *`; without it, `-O` caches reads of the status
  register and the driver hangs or misbehaves. Same for any soft switch you read
  in a loop.
- **`-Cl` static locals.** The build uses statically allocated locals for smaller,
  faster code. This is only safe because nothing is reentrant. Do **not** add
  recursion or interrupt handlers without revisiting this.
- **C89 declarations.** cc65 wants declarations at the top of a block. Declaring a
  variable mid-block, or after a statement, is a compile error.
- **Definition order.** A `static` function must be defined (or forward-declared)
  before it is called, or you get an implicit-declaration / conflicting-type
  error.
- **Watch the byte budget.** The image loads at `$0800` and must stay below the
  shadow buffer at `$7000` (and below the C stack). `make` prints the size; the
  linker map is `build/vt100.map`.
- **Inspect the generated assembly.** `build/<name>.s` is the compiler's 6502
  output. Reading it is the reliable way to judge a hot loop's cycle cost — that's
  how the every-8-cells pump interval was chosen.

## Change the baud rate

Set the control-register value in [serial.c](../serial.c) (`CTRL_9600_8N1 =
0x1E`) to the 6551 code for the new rate, and match it on the host side (the
Python clients default to 9600). Faster rates make full-screen redraws snappier
but tighten the overrun margins — keep flow control on.

## Change the screen size

`SCR_COLS`/`SCR_ROWS` in [screen.h](../screen.h) parameterize most of the code,
but the `rowbase[]` and `shadowrow[]` tables in `screen80.c` are hand-built for
80×24 and the shadow's fixed address. Changing geometry means regenerating both
tables and re-checking the memory map. 80×24 is the natural fit for the IIe's
hardware text page.

## Performance notes

- The parser's hot path is printable characters; they take the first branch in
  `vt100_feed()`.
- `cell_put()` flips `PAGE2` per character. For long runs this dominates; a
  bulk-write path that groups same-bank cells is a possible optimization (measure
  first via `build/screen80.s`).
- Scrolling copies 1920 bytes across both banks. It is the most expensive routine
  and the main reason flow control exists.

See [docs/LESSONS.md](LESSONS.md) for the reasoning behind these choices.
