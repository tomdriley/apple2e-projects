# Apple IIe Hello World (self-booting)

## Quickstart

```bash
make        # assemble, link, build disk image → build/hello.dsk
make run    # build + launch in MAME
make debug  # build + launch in MAME with debugger
make clean  # remove build/
```

## Pipeline

```
hello.s --ca65--> build/hello.o --ld65 (hello.cfg)--> build/boot.bin (256 bytes)
build/boot.bin --dd--> build/hello.dsk (flat 35-track image, boot sector at offset 0)
build/hello.dsk --MAME apple2e--> boots straight into "HELLO, WORLD!"
```

## How booting works (no OS involved)

1. Power-on: the IIe firmware initializes the screen/IO vectors, then the
   autostart ROM scans slots for a bootable card and finds the Disk II
   controller in slot 6.
2. The controller's 256-byte boot ROM ($C600) reads **track 0, sector 0**
   of the floppy into memory at **$0800** and jumps to **$0801**.
3. Byte $0800 tells the boot ROM how many sectors to load (we use 1).
4. Our code at $0801 clears the screen and prints via the monitor ROM,
   then spins forever.

Normally that boot sector would be stage 1 of an OS loader (DOS 3.3,
ProDOS). We just put the whole program there instead.

In a `.dsk` (DOS-order) image, track 0 sector 0 is simply the first 256
bytes of the file, which is why the Makefile can patch it with a single `dd` call.

## Key addresses

| Address | What |
|---|---|
| $0800  | Boot sector load target; first byte = sector count |
| $0801  | Boot ROM jump target (code entry) |
| $FC58  | HOME — monitor ROM: clear screen, home cursor |
| $FDF0  | COUT1 — monitor ROM: print char in A to 40-col screen |
| $FDED  | COUT — like COUT1 but via the CSW vector at $36 (DOS hooks this) |
| $C600  | Disk II boot ROM (slot 6) |

Text uses "high-bit ASCII": normal characters have bit 7 set ($C1 = 'A'),
$8D = carriage return. Bytes without the high bit render as inverse/flashing.

## Tools

Requires: [cc65](https://cc65.github.io), [MAME](https://www.mamedev.org), GNU Make, Git Bash.

Tool paths are configured at the top of `Makefile`.

- `ca65 -o build/hello.o hello.s` — assemble to relocatable object
- `ld65 -C hello.cfg -o build/boot.bin build/hello.o` — link/locate at $0800, pad to 256 bytes
- `mame apple2e -flop1 build/hello.dsk` — run (window)
- `mame apple2e -flop1 build/hello.dsk -debug` — run with debugger

Gotchas: MAME must run from its own directory so the rompath resolves (the
Makefile handles this). BGFX/D3D11 video required on this machine (D3D9 unavailable).
