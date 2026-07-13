# Architecture

The terminal is split into small modules with one responsibility each. The ANSI
parser talks to the screen only through a thin interface, so it stays free of
Apple-specific details and could be unit-tested against a mock screen.

## Modules and data flow

```mermaid
flowchart LR
    subgraph Apple IIe / MAME
        KBD[Keyboard $C000] --> TERM
        TERM[term.c<br/>main loop] -->|received byte| VT[vt100.c<br/>ANSI parser]
        VT -->|scr_put / gotoxy / erase / scroll| SCR[screen80.c<br/>80-col driver]
        SCR --> AUX[(Aux/Main<br/>text page<br/>$0400-$07FF)]
        TERM -->|keystroke / arrow| SER[serial.c<br/>6551 driver]
        VT -->|DSR / DA replies| SER
        SER <--> ACIA[(6551 ACIA<br/>slot 2 $C0A8)]
    end
    ACIA <-->|RS-232 / null modem| HOST[Serial host<br/>client/*.py]
    AUX --> VIDEO[80-col video]
    AUX -.read both banks<br/>between frames.-> LUA[screen_watch.lua<br/>test harness]
```

- **`term.c`** owns the main loop. Each pass feeds one already-buffered byte to
  the parser if any is ready, then polls the keyboard and queues any keystroke.
- **`vt100.c`** is a byte-at-a-time state machine. Printable characters go to the
  screen; escape sequences drive cursor moves, erases, scrolling, and mode
  changes; queries (ESC[6n, ESC[c) are answered back over serial.
- **`screen80.c`** implements the `screen.h` interface against the IIe's
  interleaved 80-column text page. Tests read that page directly by toggling
  `PAGE2` from MAME's frame notifier while the CPU is paused between frames.
- **`serial.c` / `ring.c` / `serial_isr.s`** drive the 6551. C auto-detects the
  slot and queues TX; the assembly ISR produces the RX ring, consumes the TX
  ring, and front-pushes XOFF at high water.
- **`monitor.s/.h`** is just a registry of hardware addresses (soft switches,
  I/O locations, ROM entry points). It emits no code.
- **`crt0.s`** is the startup shim: set up the cc65 C stack, zero BSS, call
  `start()`, remove the serial IRQ handler, and return to DOS on exit.

## Boot flow

```mermaid
sequenceDiagram
    participant DOS as DOS 3.3
    participant HELLO as HELLO (Applesoft)
    participant CRT0 as crt0.s
    participant TERM as term.c start()
    DOS->>HELLO: run greeting program on boot
    HELLO->>CRT0: BRUN VT100 (loads $0800, JMP $0800)
    CRT0->>CRT0: init C stack, zero BSS
    CRT0->>TERM: jsr _start
    TERM->>TERM: serial_init(); scr_init(); vt100_init()
    TERM->>TERM: draw banner, send "VT100-BOOT\r\n"
    TERM->>TERM: loop: feed buffered RX / poll keyboard
```

Making the terminal the DOS 3.3 **greeting program** (via `hello.bas`, which
`BRUN`s the binary) means it starts automatically on both MAME and real
hardware, with no keystroke-timing hacks.

## Memory map

The linker config ([vt100.cfg](../vt100.cfg)) places everything in main RAM
below the cc65 C stack:

| Region | Address | Purpose |
|--------|---------|---------|
| Zero page | `$0080–$009E` | cc65 zero-page (above the monitor's usage) |
| Text page | `$0400–$07FF` | 80-column display (aux = even cols, main = odd) |
| Program | `$0800–$6800` | crt0 + code + rodata + data + bss (loads at `$0800`) |
| Alternate screen save | `$6800–$6F7F` | saved 80×24 display bytes |
| Free RAM gap | `$7000–$777F` | unused space below the C stack |
| C stack | `$7800–$8000` | 2 KB, grows down from `$8000` |

The program image loads at `$0800` and leaves a gap below the C stack. The
alternate-screen save area starts at `$6800`; `$7000–$777F` is now just free RAM
in that gap.

## Reading the video page

The real 80-column text page is split across two memory banks selected by the
`PAGE2` soft switch (even columns in auxiliary memory, odd columns in main).
Most screen operations only write the text page. The few operations that must
read current glyphs — insert/delete characters and alternate-screen save — call
`read_row_glyphs(row, buf)`, which reads one row with two bank switches: even
columns from AUX, odd columns from MAIN.

The external test monitor also reads the real text page. Reading both banks
requires toggling `PAGE2`, so `screen_watch.lua` does it from MAME's machine
frame notifier, where the CPU is paused between frames. It reads MAIN and AUX,
restores the terminal's `PAGE2` state, and therefore does not race the running
terminal. See [docs/80COLUMN.md](80COLUMN.md) and [docs/TESTING.md](TESTING.md).

## Why cc65 `-Cl` (static locals)

The build uses `-Cl`, which gives functions statically allocated locals instead
of software-stack frames — smaller and faster 6502 code. C remains
non-reentrant: there is no recursion, and the interrupt handler is pure assembly
with private zero-page scratch and no C calls. See
[docs/HACKING.md](HACKING.md) for the cc65 conventions that matter here.
