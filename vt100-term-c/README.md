# Apple IIe 80-Column VT100 Terminal

A VT100/ANSI terminal for the Apple IIe, written in C (cc65) and driving the
**80-column display directly through auxiliary memory**. It talks to the outside
world over a **Super Serial Card** (6551 ACIA), so a real Apple IIe — or MAME's
`apple2e` — can act as the console for a modern shell. A Python bridge relays a
real **WSL bash** session to the terminal, and an automated test suite drives
that bridge into MAME and asserts what actually renders on the 80×24 screen.

```
   WSL bash  ──pty──▶  Python bridge  ──socket/serial──▶  6551  ──▶  VT100 parser  ──▶  80-col screen
   (Linux)            (pywinpty)         (null modem)      ACIA       (vt100.c)          (screen80.c)
```

## Features

| Area | Supported |
|------|-----------|
| Display | 80×24 text via direct auxiliary-memory access, hardware-accurate scrolling |
| Cursor motion | CUP/HVP, CUU/CUD/CUF/CUB, CHA/HPA, VPA, CNL/CPL, save/restore (DECSC/DECRC) |
| Vertical motion | IND, RI (reverse index), NEL, with a settable scroll region (DECSTBM) |
| Erase | ED 0/1/2, EL 0/1/2 |
| Editing | IL, DL, ICH, DCH, ECH |
| Reports | DSR cursor-position report (ESC[6n), Device Attributes (ESC[c) |
| Modes | Application cursor keys (DECCKM, ESC[?1h/l) |
| Keyboard | Full ASCII; arrow keys → ESC[A..D (or ESC O A..D in application mode) |
| Flow control | XON/XOFF plus RX draining during transmit and slow screen operations |
| Serial | 9600 8N1, Super Serial Card slot auto-detection |

Colors and other unimplemented SGR attributes are parsed and ignored so they
never corrupt the screen. See [docs/TERMINAL.md](docs/TERMINAL.md) for the full
sequence table.

## Quick start

### Build

```sh
make
```

This cross-compiles with cc65, links a raw binary at `$0800`, and writes a
bootable DOS 3.3 disk image to `build/vt100.dsk`. The disk's greeting program
`BRUN`s the terminal, so it comes up automatically on boot — on MAME and on real
hardware alike.

Prerequisites (Windows + Git Bash): `cc65`, `AppleCommander`, `MAME` with the
`apple2e` ROM set, and the Super Serial Card ROM `a2ssc` (see
[docs/SERIAL.md](docs/SERIAL.md)). Adjust the tool paths at the top of the
[Makefile](Makefile) if yours differ.

### Run in MAME with a demo host

In one terminal, start a serial host that listens for MAME:

```sh
.venv/Scripts/python.exe client/vt100_host.py tcp        # scripted 80-col demo
```

In another, launch the emulator (it connects out to the host's socket):

```sh
make run
```

### Drive a real WSL bash session

```sh
.venv/Scripts/python.exe client/vt100_shell.py tcp       # then: make run
```

The Apple becomes a login console for Linux. On real hardware, use
`vt100_shell.py serial` and wire a USB/RS-232 adapter to the card. See
[docs/BRIDGE.md](docs/BRIDGE.md).

### Test

```sh
.venv/Scripts/python.exe client/vt100_test.py            # 29 cursor tests (DSR)
.venv/Scripts/python.exe client/vt100_test.py --keys     # keyboard → serial
.venv/Scripts/python.exe client/vt100_test.py --keys --app  # application cursor keys
.venv/Scripts/python.exe client/shell_test.py            # real WSL bash → screen render
```

The suites boot the terminal in headless MAME and check the results over the
serial socket — cursor tests via the terminal's own position reports, shell
tests by reading a snapshot of the rendered 80×24 screen. See
[docs/TESTING.md](docs/TESTING.md).

## Repository layout

```
vt100-term-c/
  crt0.s          startup shim (C stack, zero BSS, call _start, exit to DOS)
  monitor.s/.h    hardware address registry (soft switches, I/O, COUT)
  serial.c/.h     6551 ACIA driver: slot detect, RX ring, XON/XOFF
  screen.h        thin 80×24 screen interface (keeps the parser portable)
  screen80.c      direct-aux 80-column driver + off-screen shadow buffer
  vt100.c/.h      ANSI/VT100 escape-sequence parser (state machine)
  term.c          main loop: serial → parser → screen, keyboard → serial
  vt100.cfg       cc65 linker config (memory map)
  Makefile        build + run + test targets
  hello.bas       DOS 3.3 greeting that BRUNs the terminal
  client/         Python + Lua host, bridge, and test tooling
  docs/           architecture, terminal, testing, and hacking guides
```

## Documentation

| Doc | What it covers |
|-----|----------------|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Modules, data flow, memory map, boot flow |
| [docs/TERMINAL.md](docs/TERMINAL.md) | Every supported escape sequence, the parser state machine, keyboard map |
| [docs/80COLUMN.md](docs/80COLUMN.md) | The auxiliary-memory 80-column scheme and scrolling |
| [docs/SERIAL.md](docs/SERIAL.md) | The 6551 driver, slot detection, ring buffer, flow control, overrun nuances |
| [docs/BRIDGE.md](docs/BRIDGE.md) | The WSL bash bridge (pywinpty/ConPTY), transports, real hardware |
| [docs/TESTING.md](docs/TESTING.md) | The MAME test harnesses and **how to add a test** |
| [docs/HACKING.md](docs/HACKING.md) | **How to add an escape sequence or screen op**, cc65 gotchas, performance |
| [docs/LESSONS.md](docs/LESSONS.md) | Design decisions and the bugs that shaped them |

## License / provenance

This project is one of several Apple IIe examples in the repository. It contains
no Apple ROM code. The Super Serial Card firmware ROM must be supplied
separately (dump your own card; see [docs/SERIAL.md](docs/SERIAL.md)).
