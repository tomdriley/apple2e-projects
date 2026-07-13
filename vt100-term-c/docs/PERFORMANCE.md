# Rendering performance

This documents where the firmware spends its time on busy output, why the
**screen shadow** (`$7000`) was expensive (it has since been removed), and how the
measured benchmark numbers
line up with the compiled 6502 assembly. It exists so the reasoning behind the
"remove the shadow" optimization is recoverable later.

The load-bearing result: a full 80×24 scroll costs **~345K cycles** by static
analysis of the compiled code, which matches the **~350K cycles** measured by the
benchmark (`client/bench.py`) to within ~1–2%. The shadow was ~160K of those
cycles, so removing it should make scrolling roughly **twice as fast**. (Confirmed
below — scrolling came out ~47% faster.)

## What a scroll does

Scrolling one line up (`region_up(top, bot)` in `screen80.c`) moves every row up
by one and blanks the new bottom row, in three separate passes:

| Pass | Where | Bytes per full (0..23) scroll |
|------|-------|------------------------------|
| AUX video (even columns) | `row_copy` ×23 + `row_blank_bank` | 23×40 + 40 = 960 |
| MAIN video (odd columns) | `row_copy` ×23 + `row_blank_bank` | 23×40 + 40 = 960 |
| Shadow buffer at `$7000` | `shadow_region_up` + `shadow_blank_from` | 23×80 + 80 = 1920 |

So the **shadow pass copies as many bytes as both video banks combined**. It
exists only so the MAME Lua test harness can read the screen out of plain RAM
instead of toggling the PAGE2-banked video RAM — it does nothing for the actual
display.

## The copy loop costs ~80 cycles per byte

cc65 compiles both the video `row_copy` inner loop and the `shadow_region_up`
inner loop to the *same* shape, and it is strikingly inefficient: it recomputes a
16-bit destination pointer and reloads the source base through zero-page
indirection **every iteration** instead of walking a pointer. The listing below
is the historical polled-RX build used for the shadow-buffer measurements; it
also shows the now-removed pump cadence. Counted instruction-by-instruction
(cycles for the 65C02):

```
L000E:  lda   i            ; 4    loop counter
        cmp   #$28         ; 2    i < 40 ?
        bcs   done         ; 2    (fall through)
        lda   dstlo        ; 4    recompute dst = base + i ...
        ldx   dsthi        ; 4
        clc                ; 2
        adc   i            ; 4
        bcc   +            ; 3
        inx                ;      (carry, ~1/256)
+       sta   sreg         ; 3
        stx   sreg+1       ; 3
        lda   srclo        ; 4    reload src base ...
        ldx   srchi        ; 4
        ldy   i            ; 4
        sta   ptr1         ; 3
        stx   ptr1+1       ; 3
        lda   (ptr1),y     ; 5    read source[i]
        ldy   #$00         ; 2
        sta   (sreg),y     ; 6    write dest[i]
        lda   i            ; 4
        and   #$07         ; 2    serial_pump every 8th byte
        bne   +            ; 3
        jsr   _serial_pump ;      (counted separately)
+       inc   i            ; 6
        jmp   L000E        ; 3
                           ; ---- 80 cycles / byte ----
```

For comparison, a hand-written `lda src,y / sta dst,y / dey / bpl` loop would be
~11 cycles/byte. The ~7× overhead is why a conceptually "free" linear copy (the
shadow) actually costs as much as the real screen scroll.

## Full-scroll cycle budget

Putting the pieces together for one `region_up(0,23)`:

| Component | Bytes | Cyc/byte | Cycles |
|-----------|------:|---------:|-------:|
| Video copy (AUX+MAIN, 2×23×40) | 1840 | 80 | 147,200 |
| Shadow copy (23×80) | 1840 | 80 | 147,200 |
| Blanks (video 2×40 + shadow 80) | 160 | ~50 | ~8,000 |
| `serial_pump` (~406 idle calls @ ~76 cyc) | — | — | ~30,900 |
| Loop / pointer-setup preambles | — | — | ~11,800 |
| **Total** | | | **~345,000** |

`serial_pump` is called on a fixed stride inside each pass (every 8 bytes in the
video/blank loops, every 16 in the shadow loop, plus once per row from
`region_up`). With an empty ring its idle path is ~70 cycles plus the 6-cycle
`jsr`, and it runs ~406 times per scroll.

These cycle totals describe the historical benchmark binary. The current
interrupt-driven driver removes every pump call, so current `build/screen80.s`
has neither the stride check nor `_serial_pump`; the table remains useful for
reproducing the shadow-removal comparison, not as a current scroll budget.

## Measured vs. predicted

The benchmark isolates the marginal per-scroll cost as the **slope** between two
otherwise-identical scroll workloads (this cancels the fixed `ESC[2J` + DSR
round-trip overhead). Baseline (`before.json`, emulated time, deterministic to
sub-millisecond):

| Workload | Scrolls | Emulated time |
|----------|--------:|--------------:|
| `scroll_il` | 20 | 7.143 s |
| `scroll_big` | 40 | 14.008 s |

```
per-scroll = (14.008 - 7.143) / (40 - 20) = 0.343 s  ->  ~350K cycles @ ~1.02 MHz
```

(The Apple IIe's effective 65C02 clock is ~1.0205–1.023 MHz; the ~2% ambiguity is
larger than the model error.) The emulated-time measurement is CPU-bound during
these workloads — the tiny payload is delivered before rendering starts, so
emulated seconds × clock ≈ CPU cycles spent.

**~345K predicted vs. ~350K measured — agreement within ~1–2%.** The dominant
294K "copy" term is counted exactly, so this is a genuine cross-check, not a fit.
`scroll_big` being exactly 2× `scroll_il` also confirms the per-scroll cost is
linear with ~zero intercept.

## Implication: removing the shadow

The shadow pass alone accounts for roughly:

| Shadow-only work | Cycles |
|------------------|-------:|
| Shadow copy (1840 B × 80) | 147,200 |
| Shadow blank + its pumps + preambles | ~16,000 |
| **Total** | **~163,000 (~160 ms)** |

Removing it drops the scroll path from ~345K to ~182K cycles, predicting a
per-scroll cost of **~343 ms → ~180 ms (~48% faster)**. Crucially, the scroll
path only *deletes* work (`shadow_region_*` / `shadow_blank_from`) and adds no new
reads, so this prediction is clean — and it landed almost exactly (measured
below). The other ops that *read* the shadow (`scr_insert_chars`,
`scr_save/restore`) now read the video page back instead, but those are off the
hot scroll path.

## Measured: after removing the shadow

The shadow was removed and the benchmark re-run (`after.json`, same harness,
emulated time). The prediction holds almost exactly — the isolated per-scroll
cost drops from **343 ms to 181 ms**:

```
per-scroll (after) = (7.411 - 3.788) / (40 - 20) = 0.181 s   (predicted ~0.180 s)
```

Full before/after, emulated-time median (seconds; lower is faster):

| Workload | Before | After | Faster |
|----------|-------:|------:|-------:|
| `scroll_il`  (20 scrolls)               |  7.143 | 3.788 | **47.0%** |
| `scroll_big` (40 scrolls)               | 14.008 | 7.411 | **47.1%** |
| `cat_narrow` (short lines, scroll-heavy) |  8.197 | 4.651 | 43.3% |
| `cat_wide`   (full-width lines)          |  5.689 | 4.426 | 22.2% |
| `clear_spam` (`ESC[2J` heavy)           |  6.472 | 3.660 | 43.4% |
| `altscreen`  (save/restore)             | 12.049 | 9.983 | 17.1% |

Reading the numbers:

- **Scroll-bound workloads (`scroll_il`, `scroll_big`, `cat_narrow`) land at
  ~43–47% faster**, right on the ~48% prediction: the shadow scroll pass was
  copying as many bytes as both video banks combined, and it is simply gone.
- **`clear_spam` (~43%)** improves for the same reason — `ESC[2J` used to blank
  the shadow as well as both video banks.
- **`cat_wide` (22%)** shows a smaller win: full-width lines spend proportionally
  more time in `scr_put` (per-character rendering, unchanged) and less in
  scrolling, so removing the shadow moves a smaller share of the total.
- **`altscreen` (17%)** is the smallest win. Removing the shadow still helps — the
  save has no shadow to maintain, and the ALTBUFFER rendering between save and
  restore no longer mirrors every write — but the save now reads the bank-split
  video page back a row at a time (`read_row_glyphs`), and the restore redraw
  bank-switches per cell. Those make the save/restore itself a bit more
  expensive, so the net gain is modest.

### Historical cost of removal: two polled-RX overruns

Removing the shadow shifted the cc65-compiled timing of the render loops, which
re-exposed two thin receive-overrun margins in the former polled, single-byte-RX
6551 path (see [docs/LESSONS.md](LESSONS.md)). At the time they were mitigated by
pumping during row read-back and after each bank-switched cell. The
interrupt-driven RX handler supersedes those timing patches: the same loops now
contain no serial calls, and RDR service is independent of their cycle count.

## Reproducing

- Baseline numbers: `python client/bench.py` (see `docs/TESTING.md` for the
  harness). Emulated-time pass is throttle-independent and deterministic.
- Assembly: `make` regenerates `build/*.s`; inspect `_row_copy` in
  `build/screen80.s`, `_serial_put` in `build/serial.s`, and the handwritten
  `serial_isr.s`. Line numbers shift when the code changes, so search by label.
