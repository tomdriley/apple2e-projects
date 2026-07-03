# The 80-column display

[screen80.c](../screen80.c) drives the Apple IIe's 80-column text mode by writing
the video memory directly, rather than going through the firmware's `COUT`. That
gives exact control over the cursor, scrolling, and clears — which the VT100
parser needs — and lets the terminal track the cursor precisely so it can answer
position reports.

## Soft switches

`scr_init()` puts the machine into 80-column text:

| Switch | Address | Effect |
|--------|---------|--------|
| 80STORE on | `$C001` | `PAGE2` now banks the text page for the CPU |
| 80VID on | `$C00D` | Enable 80-column video |
| ALTCHRSET on | `$C00F` | Alternate character set → real lowercase |
| TEXT | `$C051` | Text mode |
| MIXCLR | `$C052` | Full screen (no mixed graphics) |
| PAGE1 | `$C054` | Display page 1 / main bank resting state |

## The interleaved text page

With 80STORE on, the text page `$0400–$07FF` is split across two banks, 40 bytes
per row in each:

- **Even** columns (0, 2, 4, …) live in **auxiliary** memory.
- **Odd** columns (1, 3, 5, …) live in **main** memory.

The `PAGE2` soft switch steers CPU access between the banks **without changing
what is displayed**:

- `TXTPAGE2` (`$C055`) → CPU sees auxiliary memory.
- `TXTPAGE1` (`$C054`) → CPU sees main memory.

### Address of a cell

Each row's base address is precomputed in the `rowbase[24]` table. The classic
Apple text-page layout is not linear — rows are interleaved in groups of eight:

```
base(row) = $0400 + (row & 7) * $80 + (row >> 3) * $28
byte      = base(row) + (col >> 1)      # 40 bytes per bank per row
bank      = AUX if col is even, MAIN if col is odd
```

`cell_put()` selects the bank for the column, writes the byte, then restores the
main bank as the resting state:

```c
static void cell_put(unsigned char col, unsigned char row, unsigned char ch) {
    unsigned char *p = (unsigned char *)(rowbase[row] + (col >> 1));
    if (col & 1) BANK_MAIN(); else BANK_AUX();
    *p = ch;
    BANK_MAIN();
}
```

### Character encoding

Screen bytes use "high" ASCII: a glyph is `0x80 | ascii`. With the alternate
character set enabled that renders normal upper- and lower-case text. A blank
cell is `0xA0` (high-bit space).

## Scrolling and the scroll region

The cursor position and a scroll region `[scroll_top, scroll_bot]` (default the
whole screen, set by DECSTBM) are tracked in software. Scrolling is done by hand:

- `region_up(top, bot)` copies rows `top+1..bot` up one and blanks row `bot`.
- `region_down(top, bot)` copies rows `top..bot-1` down one and blanks row `top`.

Both run once per bank (aux then main). `scroll_up`/`scroll_down` apply them to
the active region; **LF** scrolls up at the bottom margin, **RI** scrolls down at
the top margin. The same primitives implement **IL/DL** by scrolling the sub-
region from the cursor row to the bottom margin.

Insert/delete/erase **character** operations shift cells within a single row.
They read their source glyphs from the shadow buffer (below), which already holds
display bytes, and write both the video page and the shadow.

## The shadow buffer

Because the text page is bank-split, it cannot be read from outside the machine
without toggling `PAGE2` and racing the CPU. So every screen mutation also writes
a plain, linear, non-banked copy at `$7000`:

```
shadow byte(row, col) = *(unsigned char *)(0x7000 + row * 80 + col)
```

`shadowrow[24]` holds the base pointer for each row (`$7000`, `$7050`, …). The
buffer sits in the free gap between the top of the linked image and the C stack
(see [docs/ARCHITECTURE.md](ARCHITECTURE.md#memory-map)), so nothing else uses
it. The test harness reads it directly — no banking, no side effects — which is
what makes deterministic screen assertions possible. See
[docs/TESTING.md](TESTING.md).

## The overrun hazard

At 9600 baud a byte arrives roughly every millisecond, but the 6551 has only a
one-byte receive register. A slow screen operation (a full-row clear is ~1.5 ms)
can therefore drop the byte that arrives while it runs. Every slow loop here —
row fills, row copies, character shifts — calls `serial_pump()` every 8 cells to
drain the ACIA into the ring buffer in time. This subtlety is documented in
[docs/SERIAL.md](SERIAL.md) and [docs/LESSONS.md](LESSONS.md).
