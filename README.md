# Apple IIe Bare-Metal Examples

A collection of small, self-contained programs for the **Apple IIe**, written in
**6502 assembly** and **C** (via the [cc65](https://cc65.github.io) cross
toolchain). Each one is a standalone project with its own `Makefile` that builds
a bootable `.dsk` image and launches it in [MAME](https://www.mamedev.org) — and
every one also runs on **real hardware**.

The examples form a rough learning progression: from a "Hello, World!" that fits
in a single 256-byte boot sector, through keyboard and paddle input, a lo-res
Snake game, serial communication over the Super Serial Card, and finally a full
80×24 VT100 terminal that bridges an Apple IIe to a modern shell.

## What's inside

Most examples come in an **assembly** and a **C** flavour so you can compare the
hand-written 6502 with what cc65 generates from the equivalent C.

### Fundamentals

| Project | Lang | What it does |
|---|---|---|
| [hello-asm](hello-asm/) | asm | Self-booting "HELLO, WORLD!" — the whole program is one boot sector. Has a [README](hello-asm/README.md). |
| [hello-c](hello-c/) | C | The same "HELLO, WORLD!", written in C and located to run straight from the boot sector. |
| [keyboard-test-asm](keyboard-test-asm/) | asm | Prints a prompt, then echoes every keypress by polling the keyboard soft switch. |
| [keyboard-test-c](keyboard-test-c/) | C | The C version of the keyboard echo loop. |
| [pread-test-c](pread-test-c/) | C | Reads a paddle / game-controller axis (monitor `PREAD`) and prints the value in a loop. |
| [print-all-c](print-all-c/) | C | Dumps all 256 byte values `$00`–`$FF` through `COUT` — a quick tour of the character ROM. |

### Games

| Project | Lang | What it does |
|---|---|---|
| [snake-asm](snake-asm/) | asm | Snake in lo-res mixed graphics. Uses the screen itself as the collision map; body is a 256-entry ring buffer. Runs from DOS 3.3 (`BRUN SNAKE`). |
| [snake-c](snake-c/) | C | A close C transliteration of `snake.s`, deliberately structured so the generated 6502 stays recognisable. |

### Serial & terminals

| Project | Lang | What it does |
|---|---|---|
| [ssc-serial-c](ssc-serial-c/) | C | Talks to a host over the **Super Serial Card** (6551 ACIA): sends a banner, then echoes everything it receives. Includes a Python client and round-trip test. Has a [README](ssc-serial-c/README.md). |
| [vt100-term-c](vt100-term-c/) | C | A full **80-column VT100/ANSI terminal** driving auxiliary memory directly, with a Python bridge that relays a real WSL bash session and a MAME test suite. Has a [README](vt100-term-c/README.md) and [docs/](vt100-term-c/docs/). |

## Toolchain

Every project shares the same cross-development setup (paths are configured at
the top of each `Makefile`):

- **[cc65](https://cc65.github.io)** — `ca65` assembler, `ld65` linker, and the
  `cc65` C compiler, all targeting a bare 6502 (`-t none`).
- **[MAME](https://www.mamedev.org)** — the `apple2e` driver, for running and
  debugging the disk images.
- **[AppleCommander](https://applecommander.github.io)** — writes files onto DOS
  3.3 disk images (used by the `BRUN`-style projects).
- **GNU Make** and **Git Bash** — the build environment (these examples were
  developed on Windows; `SHELL` is set to Git Bash in each `Makefile`).

## Common workflow

Each project's `Makefile` exposes the same targets. From inside a project
directory:

```bash
make        # build the bootable disk image → build/<name>.dsk
make run    # build, then launch in MAME
make debug  # build, then launch in MAME with the debugger
make clean  # remove the build/ directory
```

Build artefacts (`build/`) and MAME's per-machine runtime state (`nvram/`,
`snap/`, `cfg/`) are generated on demand and are git-ignored.

## How the disks boot

There are two booting styles in this repo:

**Boot-sector programs** (`hello-*`, `keyboard-test-*`, `pread-test-c`,
`print-all-c`, `ssc-serial-c`). The whole program fits in **track 0, sector 0**.
The Disk II boot ROM (`$C600`) loads that single 256-byte sector to `$0800` and
jumps to `$0801`; the byte at `$0800` is the sector count. In a DOS-order `.dsk`
image, sector 0 is simply the first 256 bytes of the file, so the `Makefile`
patches the program in with one `dd`. There is no OS — the program *is* the boot
sector. (The Makefiles fail the build if the image exceeds 256 bytes.)

**DOS 3.3 `BRUN` programs** (`snake-*`, `vt100-term-c`). These are larger than a
sector, so they ride on a real DOS 3.3 disk. AppleCommander copies the binary
onto a base image with a load address of `$0800`, and a greeting program `BRUN`s
it automatically on boot — on MAME and on real hardware alike.

## Repository conventions

The projects share a handful of small building blocks:

- **`monitor.s` / `monitor.h`** — a registry of Apple II ROM entry points and
  hardware addresses (`HOME`, `COUT`, keyboard, soft switches, …) as symbols
  only; no bytes are emitted.
- **`crt0.s`** — the boot/startup shim for the C programs: it carries the
  boot-sector count byte, sets up the cc65 C software stack, zeroes BSS, and
  `jsr`s into `_start`.
- **`*.cfg`** — the `ld65` linker config (memory map) that locates each image at
  `$0800`.

Apple II text uses **high-bit ASCII**: normal glyphs have bit 7 set (`$C1` =
`'A'`), and `$8D` is carriage return. Bytes without the high bit render as
inverse/flashing.
