# The shell bridge

The bridge connects a real shell to the Apple terminal, turning the IIe into a
login console for Linux. It runs on the PC side and relays raw bytes between a
pseudo-terminal running bash and the transport to the Apple (a TCP socket for
MAME, or a serial port for real hardware).

> The bytes on the wire are a plain 8N1 serial TTY — see
> [PROTOCOL.md](PROTOCOL.md) for the host-agnostic contract (framing, XON/XOFF,
> the ready-handshake, the DSR/DA replies) and stock-Linux recipes
> (`agetty`/`socat`) that need no Windows tooling at all. This bridge is just one
> host that speaks it.

```
   WSL bash  ──▶  pywinpty pty (ConPTY, 80x24)  ──▶  TcpLink / SerialLink  ──▶  Apple terminal
             ◀──                                 ◀──
```

## Transports — `client/serial_link.py`

Both transports expose the same tiny interface: `write(bytes)` and a
non-blocking `read(n) -> bytes`.

- **`TcpLink`** listens on `127.0.0.1:6551` for MAME's null modem to connect out,
  then relays over the accepted socket. Start it **before** launching MAME.
- **`SerialLink`** opens a USB/RS-232 adapter (auto-detecting the port) for real
  hardware.
- **`PtyLink`** (the `posix` transport) opens a POSIX pseudo-terminal with the
  Python stdlib alone — no `pyserial`, no `pywinpty` — and prints a slave device
  for a stock Unix host to attach to (e.g. `socat` bridging it to a real serial
  line, or `agetty` running a login). It exists to prove the wire is a standard
  serial TTY; see [PROTOCOL.md §6](PROTOCOL.md#6-implementing-a-host-on-stock-linux).

`open_link("tcp"|"serial"|"posix", ...)` returns the right one.

## Interactive bridge — `client/vt100_shell.py`

```sh
python client/vt100_shell.py tcp        # with MAME (listens; then run `make run`)
python client/vt100_shell.py serial     # real hardware, auto-detect the port
```

It first handshakes with the terminal (sends `ESC[6n` until it gets a cursor
report), so bash only starts once the terminal is booted and reading — otherwise
bash's first output would be lost to the one-byte 6551 register. Then it spawns
`wsl.exe` bash on an 80×24 pseudo-terminal via **pywinpty** (Windows ConPTY) and
relays bytes both ways in a thread.

### ConPTY notes

- `pywinpty`'s `read()` returns a `str` (ConPTY output is UTF-8). The relay
  encodes to UTF-8 toward the Apple, and decodes incoming bytes as latin-1 toward
  the pty.
- ConPTY injects a startup probe (`ESC[1t ESC[c ESC[?1004h ESC[?9001h`) when a
  program attaches. The terminal's parser consumes these; the `ESC[c` triggers
  the Device Attributes reply, which is why `serial_put()` must drain RX while
  transmitting (see [docs/SERIAL.md](SERIAL.md)).
- A fully interactive `bash -i` under ConPTY + WSL interop can stall on job
  control. The automated test harness therefore runs each command in a fresh
  `wsl.exe -e bash -c "…"` instead (see [docs/TESTING.md](TESTING.md)); the
  interactive bridge is for a human at the keyboard.

## Flow control

The terminal applies XON/XOFF (see [docs/SERIAL.md](SERIAL.md)); on the host side
set `stty ixon` so bursts pause when the Apple falls behind. For real hardware,
also make sure your adapter and the card agree on 9600 8N1.

## terminfo

With `TERM=vt100` the shell already emits sequences this terminal handles. For a
tighter fit, [client/apple2e-vt.terminfo](../client/apple2e-vt.terminfo)
describes exactly the capabilities implemented here, so ncurses emits only what
the terminal supports. Install it in WSL and select it:

```sh
tic -x apple2e-vt.terminfo      # in WSL, installs to ~/.terminfo
export TERM=apple2e-vt          # in the shell that runs under the bridge
```

It advertises the cursor, erase, scroll-region, insert/delete, inverse-video,
alternate-screen, application-cursor-key, and line-drawing capabilities the
firmware provides — and nothing it does not.

## Real hardware

1. Transfer `build/vt100.dsk` to a physical disk (e.g. with ADTPro) or run it
   from a disk emulator.
2. With power off, set the Super Serial Card's **SW2:6 Interrupts switch On**.
3. Boot the IIe; the greeting program `BRUN`s the terminal automatically.
4. Wire a USB/RS-232 adapter to the Super Serial Card and run
   `python client/vt100_shell.py serial` on the PC.

The firmware is identical to the MAME build — only the transport differs. The
interrupt path is verified under real-ROM MAME but has not yet been validated
on a physical IIe/SSC.
