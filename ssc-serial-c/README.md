# Apple IIe Super Serial Card demo in C (self-booting)

A tiny self-booting Apple IIe program that talks to a host over the **Super
Serial Card** (SSC), plus a Python client for the other end of the wire. The
Apple sends a banner and then echoes back everything it receives, proving a full
round trip over the serial port — in MAME or on real hardware.

## Quickstart

```bash
make        # compile → link → disk image → build/ssc-serial.dsk
make run    # build + launch in MAME (start the client first — see below)
make debug  # build + launch in MAME with the debugger
make clean  # remove build/
```

The 6502 side is written in C (cc65) and located to run straight from the boot
sector — no DOS/ProDOS involved.

## What it does

1. Brings the 6551 ACIA up at **9600 baud, 8N1**, no parity, DTR/RTS asserted.
2. Sends a banner out the serial port (and mirrors it on the 40-column screen).
3. Enters an echo loop: every byte that arrives over serial is sent back out
   (round-trip proof) and printed on screen (receive proof).

Carriage returns going out the wire get an LF appended, so remote terminals see
clean `CR+LF` line endings.

## Running the demo

MAME's null modem **connects out** to a TCP socket, so the host listener must be
running **before** MAME starts.

### In MAME

```bash
# terminal 1 — host client listens for MAME
python client/serial_demo.py tcp --port 6551

# terminal 2 — build + run (Makefile wires the SSC to socket 6551)
make run
```

Type a line in terminal 1, press Enter, and watch it come back echoed. The
equivalent raw MAME invocation:

```bash
mame apple2e -rompath "C:\mame\roms" \
     -sl2 ssc -sl2:ssc:rs232 null_modem -bitb socket.127.0.0.1:6551 \
     -flop1 build/ssc-serial.dsk
```

> **MAME needs the SSC firmware ROM** (`a2ssc` / `341-0065-a.bin`, 2 KB). Our
> program pokes the 6551 registers directly and never calls that firmware, but
> MAME still refuses to instantiate the card without it. Drop it in as
> `C:\mame\roms\a2ssc\341-0065-a.bin` and verify with
> `mame -rompath "C:\mame\roms" -verifyroms a2ssc` → *romset a2ssc is good*.

### On real hardware

Wire the SSC to a USB/RS-232 adapter on the host and run:

```bash
python client/serial_demo.py serial            # auto-detect port + baud
python client/serial_demo.py serial --list     # just list serial ports
python client/serial_demo.py serial --device COM3 --baud 9600   # explicit
```

## The Python client (`client/serial_demo.py`)

One relay with two transports:

| Command | Talks to | Notes |
|---|---|---|
| `tcp [--host H] [--port P]` | MAME's `null_modem` socket | listens; start before MAME |
| `serial [--device D] [--baud B]` | real hardware via `pyserial` | both flags optional |

With no `--device`/`--baud`, the `serial` mode **auto-detects**: because the
firmware echoes whatever it receives, the client sends a probe byte to each USB
serial port across common baud rates and keeps the pair that echoes cleanly. If
nothing echoes it falls back to the first port at 9600.

`pyserial` is only needed for the `serial` path (`pip install pyserial`); the
`tcp` path is pure standard library.

## Automated test (`client/roundtrip_test.py`)

Drives the real client and asserts a byte sent from the host is echoed back.

```bash
python client/roundtrip_test.py          # fake-Apple protocol test (no ROMs/emulator)
python client/roundtrip_test.py --mame   # boot the disk in headless MAME, real 6502 code
```

- **fake** (default) — a local stand-in reproduces `ssc-serial.c`'s exact byte
  behavior, so it always runs and pins down the client + wire protocol.
- **mame** — runs the identical round trip against the actual program in headless
  MAME. If the `a2ssc` ROM is absent it reports **SKIPPED**, not failed.

## Pipeline

```
ssc-serial.c --cc65--> build/ssc-serial.s --ca65--> build/ssc-serial.o ┐
crt0.s      --ca65--> build/crt0.o                                     ├─ld65 (linker.cfg)
monitor.s   --ca65--> build/monitor.o                                  ┘        │
                                                                                v
                                            build/boot.bin (≤256 bytes, located at $0800)
                                                                                │ dd
                                                                                v
                              build/ssc-serial.dsk (flat 35-track image, boot sector at offset 0)
```

The Makefile fails the build if `boot.bin` exceeds 256 bytes — the whole program
must fit in a single boot sector.

## How booting works (no OS involved)

1. The IIe autostart ROM finds the Disk II controller in slot 6 and runs its
   boot ROM at `$C600`.
2. That reads **track 0, sector 0** into **$0800** and jumps to **$0801**.
3. The byte at `$0800` is the sector count (`crt0.s` emits `$01` = this sector).
4. `crt0.s` at `$0801` sets up the cc65 C software stack, then `jsr _start` into
   the compiled C `start()`.

In a DOS-order `.dsk`, track 0 sector 0 is just the first 256 bytes of the file,
so the Makefile patches it in with a single `dd`.

## How the serial link works

The program drives the 6551 ACIA on the SSC directly via memory-mapped
registers (slot 2 → `$C0A8–$C0AB`); it does not use the card's firmware ROM.

| Register | Address | Use |
|---|---|---|
| Data    | `$C0A8` | read = RX byte, write = TX byte |
| Status  | `$C0A9` | read = status (RDRF `$08`, TDRE `$10`); any write = reset |
| Command | `$C0AA` | parity / IRQ / DTR / RTS (`$0B` = no parity, DTR+RTS on) |
| Control | `$C0AB` | baud + word length + stop bits (`$1E` = 9600 8N1) |

On the emulator side, MAME bridges the card's RS-232 port to a `null_modem`
whose bit stream is a TCP socket (`-bitb socket.127.0.0.1:6551`). MAME is the
socket **client**, which is why the host must listen first.

## Key addresses

| Address | What |
|---|---|
| `$0800` | Boot load target; first byte = sector count |
| `$0801` | Boot ROM jump target (crt0 entry) |
| `$0080` | cc65 zero page (above the monitor's) |
| `$8000` | Top of the 2 KB C software stack (grows down) |
| `$C0A8`–`$C0AB` | 6551 ACIA (SSC in slot 2) |
| `$C200` | SSC slot-2 firmware select (activates its `$C800` ROM — unused here) |
| `$C600` | Disk II boot ROM (slot 6) |
| `$FC58` | HOME — monitor ROM: clear screen, home cursor |
| `$FDED` | COUT — monitor ROM: print char in A (high-bit ASCII) |

## Files

| File | Role |
|---|---|
| `ssc-serial.c` | the 6502 program: init 6551, banner, echo loop |
| `crt0.s` | boot-sector startup shim (sector-count byte, C stack, `jsr _start`) |
| `monitor.s` / `monitor.h` | hardware/ROM address registry (symbols only) |
| `linker.cfg` | memory map: locate at `$0800`, stack at `$8000` |
| `Makefile` | build + MAME run/debug wiring |
| `client/serial_demo.py` | host client (tcp / serial, auto-detect) |
| `client/roundtrip_test.py` | automated round-trip test (fake / mame) |

## Tools

Requires: [cc65](https://cc65.github.io), [MAME](https://www.mamedev.org), GNU
Make, Git Bash, and Python 3 (+ `pyserial` for the real-hardware path). Tool
paths are set at the top of the `Makefile`.

## Troubleshooting

- **"Access is denied" / permission error on a COM port** — another program has
  the port open. ADTPro, a serial terminal, or a previous run of the client will
  hold it; close that program (ADTPro is the usual culprit right after dumping a
  ROM) and retry. Only one process can own a COM port at a time.
- **"address already in use" on the TCP port** — a stale `serial_demo.py tcp` or
  another MAME is still bound to 6551. Close it, or use a different `--port`
  (match it in the MAME `-bitb socket…:PORT`).
- **MAME: "341-0065-a.bin NOT FOUND" / romset a2ssc not found** — install the SSC
  ROM as described above; pass `-rompath "C:\mame\roms"`.
- **No echo when auto-detecting baud** — the Apple must be powered on and sitting
  in the echo loop (that's what the probe listens for). It then falls back to
  9600 on the first port.
- **MAME video** — this machine needs BGFX/D3D11 (`-video bgfx -bgfx_backend
  d3d11`, already in the Makefile); D3D9 is unavailable.
