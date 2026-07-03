# The shell bridge

The bridge connects a real shell to the Apple terminal, turning the IIe into a
login console for Linux. It runs on the PC side and relays raw bytes between a
pseudo-terminal running bash and the transport to the Apple (a TCP socket for
MAME, or a serial port for real hardware).

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

`open_link("tcp"|"serial", ...)` returns the right one.

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

## terminfo (planned)

With `TERM=vt100` the shell already emits sequences this terminal handles. A
tighter fit is a custom `apple2e-vt` terminfo entry that advertises exactly the
capabilities implemented here; `tic`-install it in WSL and export
`TERM=apple2e-vt`. ncurses then emits only what the terminal supports, bounding
the surface area. This entry is not yet included.

## Real hardware

1. Transfer `build/vt100.dsk` to a physical disk (e.g. with ADTPro) or run it
   from a disk emulator.
2. Boot the IIe; the greeting program `BRUN`s the terminal automatically.
3. Wire a USB/RS-232 adapter to the Super Serial Card and run
   `python client/vt100_shell.py serial` on the PC.

The firmware is identical to the MAME build — only the transport differs.
