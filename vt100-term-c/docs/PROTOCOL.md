# The wire protocol

This terminal speaks a plain, standard serial TTY — **nothing bespoke**. The
Apple IIe is the far end of an ordinary 8N1 line: raw bytes in, raw bytes out,
software (XON/XOFF) flow control, and a couple of standard VT100 query replies.
Any host that can open a serial port — Linux, macOS, a microcontroller, `socat`,
`agetty` — can drive it. The Windows/WSL bridge in [BRIDGE.md](BRIDGE.md) is just
*one* host; this document is the contract that host implements.

Everything below is what the firmware actually does. The transmit/receive line
discipline lives in [serial.c](../serial.c); the query replies live in
[vt100.c](../vt100.c). The reference host abstraction is
[client/serial_link.py](../client/serial_link.py) (`write(bytes)` + non-blocking
`read(n) -> bytes`).

## 1. Physical line

| Parameter | Value | Source |
|-----------|-------|--------|
| Baud | 9600 | `CTRL_9600_8N1 = 0x1E` written to the 6551 control register |
| Data bits | 8 | same control byte |
| Parity | none | `CMD_NO_PARITY = 0x0B` (command register) |
| Stop bits | 1 | control byte |
| Modem lines | DTR + RTS asserted, held static | command byte `0x0B` |

So the line is **9600 8N1**. DTR and RTS are asserted once at init and never
toggled — there is **no hardware (RTS/CTS) flow control**; pacing is purely
software XON/XOFF (§3). The 6551 ACIA sits on a Super Serial Card whose slot is
auto-detected (see [SERIAL.md](SERIAL.md)); slot detection is invisible on the
wire.

## 2. Framing

There is **no packet framing**. The wire is a raw, full-duplex, 8-bit-clean byte
stream in both directions:

- **Host → terminal:** UTF-8/ASCII text and VT100/ANSI escape sequences. The
  firmware's parser (`NORMAL → ESC → CSI`, see [TERMINAL.md](TERMINAL.md))
  consumes them a byte at a time. Bytes are processed strictly in order, which is
  what makes the ready-handshake in §4 exact.
- **Terminal → host:** keyboard input, plus the query replies in §5. No framing,
  no length prefixes — just the reply bytes inline in the stream.

Line endings are not rewritten on the wire; the terminal renders `CR` and `LF`
per the VT100 rules documented in [TERMINAL.md](TERMINAL.md).

## 3. Flow control (XON/XOFF) — one direction

Flow control exists so the host does not overrun the 6551, which buffers **exactly
one** received byte (~1 ms at 9600 baud). The firmware keeps a 256-byte receive
ring and throttles the host with the standard control bytes:

| Byte | Value | Meaning on this wire |
|------|-------|----------------------|
| `XOFF` | `0x13` (`Ctrl-S`, DC3) | terminal → host: **stop sending**, my ring is filling |
| `XON`  | `0x11` (`Ctrl-Q`, DC1) | terminal → host: **resume**, my ring has drained |

Behavior (from `serial_pump` / `serial_getch` in [serial.c](../serial.c)):

- The terminal sends **XOFF** once the ring reaches **192** bytes (`RING_HIGH`).
- The terminal sends **XON** once the ring drains back to **64** bytes
  (`RING_LOW`).

Flow control is **unidirectional**: the terminal *sends* XON/XOFF to pace the
host; it does **not** honor XON/XOFF coming *from* the host for its own
transmissions (`serial_put` only waits for the transmit register to empty). The
host must therefore drain the terminal's output promptly and never expects to
pause it.

> **Host requirement:** honor XON/XOFF from the terminal. On a POSIX tty that is
> `stty ixon` (see §6); in code, pause transmission on `0x13` and resume on
> `0x11`, exactly as [client/bench.py](../client/bench.py) does. XOFF alone is not
> a hard guarantee (bytes already in the OS/socket pipe still arrive), which is
> why the harness *also* uses the windowed handshake in §4.

## 4. Ready-handshake (and windowed pacing)

DSR (**Device Status Report**) is the standard VT100/ECMA-48 mechanism by which a
host asks the terminal to report its status, and the terminal answers *inline on
the same byte stream* — there is no separate control channel (§1). The firmware
implements the two standard DSR requests — `ESC[6n` (report cursor position) and
`ESC[5n` (report operating status) — alongside Device Attributes (`ESC[c`); the
exact replies are tabulated in §5.

DSR matters here for more than status reporting: `ESC[6n` is the terminal's only
*in-band, request/response* back-channel. Because the line has **no hardware flow
control** (§1) and only software XON/XOFF — which a host cannot react to instantly,
so bytes already in flight can still overrun the ring (§3) — a host benefits from a
positive "you have processed everything up to exactly here" signal. An ordered DSR
reply is precisely that, which is why the query does double duty below.

Because bytes are processed strictly in order (§2), a Device Status Report request
appended to a payload is only answered **after** everything before it has been
drawn. That turns the standard `ESC[6n` cursor query into two things:

1. **A boot/ready gate.** After the terminal boots it has a single-byte receiver;
   the host's first bytes would be lost if sent too early. So a host sends
   `ESC[6n` repeatedly until it receives a cursor report (`ESC[…R`). Only then is
   the terminal known to be up and reading. This is exactly
   [`vt100_shell.wait_ready`](../client/vt100_shell.py) and
   [`bench.Terminal.sync`](../client/bench.py).

2. **A lossless windowed ack.** For long streams the host sends a bounded window
   of bytes, appends `ESC[6n`, and waits for the `ESC[…R` reply before sending the
   next window. No more than one window is ever in flight, so the ring cannot
   overflow. See [`bench.Terminal.send_windowed`](../client/bench.py) (window =
   96 bytes, under half the ring).

```
host                              terminal
  │  ESC [ 6 n                       │
  │ ───────────────────────────────▶│   (queued behind prior bytes)
  │                                  │   … draws everything before it …
  │  ESC [ <row> ; <col> R           │
  │ ◀───────────────────────────────│   ready / window acked
```

## 5. Query replies (the standard VT100 answers)

The firmware answers three standard queries. Replies are ASCII, emitted a byte at
a time by `serial_put` (which drains RX while it waits so a multi-byte reply never
causes an overrun). Row/column are **1-based decimal**.

| Request (host → terminal) | Reply (terminal → host) | Meaning |
|---------------------------|-------------------------|---------|
| `ESC [ 6 n` (DSR, cursor) | `ESC [ <row> ; <col> R` | Cursor Position Report (CPR), 1-based |
| `ESC [ 5 n` (DSR, status) | `ESC [ 0 n` | terminal OK |
| `ESC [ c` or `ESC [ 0 c` (DA) | `ESC [ ? 1 ; 0 c` | Device Attributes: "VT100, no options" |

Example: with the cursor on row 1, column 1, `ESC[6n` yields the 6 bytes
`1B 5B 31 3B 31 52` (`ESC [ 1 ; 1 R`). The `ESC[?1;0c` answer to `ESC[c` is why
`serial_put` drains RX mid-transmit — see [SERIAL.md](SERIAL.md).

A host can match these with the same regexes the harness uses:

```python
CPR = re.compile(rb"\x1b\[(\d+);(\d+)R")   # ESC[<row>;<col>R
DA  = b"\x1b[?1;0c"                         # ESC[?1;0c
```

## 6. Implementing a host on stock Linux

Nothing above needs Windows, `pywinpty`, or the bundled Python bridge. Two
recipes below use only base tooling and treat the Apple as an ordinary serial TTY.
`/dev/ttyUSB0` is the USB/RS-232 adapter wired to the Super Serial Card; set it to
match the firmware line:

```sh
stty -F /dev/ttyUSB0 9600 cs8 -cstopb -parenb ixon
```

`cs8 -cstopb -parenb` = 8N1; `ixon` enables outbound XON/XOFF so the kernel pauses
when the terminal sends `Ctrl-S` (§3).

### 6a. The Apple as a literal Unix login TTY (`agetty`)

Point a getty at the serial line and the Apple becomes a login console — no custom
software at all:

```sh
# 9600 baud, device, terminal type. Runs login(1) over the wire.
/sbin/agetty 9600 ttyUSB0 vt100
```

(Under systemd this is `serial-getty@ttyUSB0.service` with `TERM=vt100`; use the
bundled [apple2e-vt terminfo](../client/apple2e-vt.terminfo) for the tightest
capability fit, per [BRIDGE.md](BRIDGE.md).) Type at the Apple keyboard, get a
Linux login prompt back — the whole session rides the 8N1 line and the XON/XOFF
pacing above.

### 6b. `socat` PTY ⇄ serial bridge (and the Python harness from Linux)

`socat` bridges the serial line to a pseudo-terminal that other tools open by
name:

```sh
socat -d -d FILE:/dev/ttyUSB0,raw,echo=0,b9600 PTY,raw,echo=0,link=/tmp/apple
# now /tmp/apple is a PTY connected straight through to the Apple
```

The reference harness gets the mirror-image of this for free: the **`posix`**
transport in [client/serial_link.py](../client/serial_link.py) (`PtyLink`) opens a
PTY with the Python stdlib alone — no `pyserial`, no `pywinpty` — and prints the
slave device to attach to:

```python
from serial_link import open_link
link = open_link("posix")          # prints e.g. "[pty ready: attach a host to /dev/pts/7 @ 9600 8N1]"
link.write(b"\x1b[6n")             # same write()/read() as tcp & serial links
```

Bridge that slave to the real line with `socat`, and every existing harness
(`vt100_test.py`, `shell_test.py`, `bench.py`) runs unmodified from Linux against
hardware. For the emulator, the cross-platform `tcp` transport already works on
Linux with no changes.

### 6c. A host in a few lines — the protocol *is* the interface

No language runtime is special. A cursor query and its reply, in pure shell:

```sh
stty -F /dev/ttyUSB0 9600 cs8 -cstopb -parenb -echo raw
printf '\033[6n' > /dev/ttyUSB0            # ask for the cursor position
head -c 16 < /dev/ttyUSB0 | xxd            # read back: ESC [ <row> ; <col> R
```

…or in a few lines of C, opening the device and doing the same `write`/`read`:

```c
#include <fcntl.h>
#include <termios.h>
#include <unistd.h>

int main(void) {
    int fd = open("/dev/ttyUSB0", O_RDWR | O_NOCTTY);
    struct termios t; tcgetattr(fd, &t); cfmakeraw(&t);
    cfsetispeed(&t, B9600); cfsetospeed(&t, B9600);
    t.c_iflag |= IXON;                     /* honor the terminal's XON/XOFF */
    tcsetattr(fd, TCSANOW, &t);
    write(fd, "\033[6n", 4);               /* DSR: request cursor position */
    char buf[16]; ssize_t n = read(fd, buf, sizeof buf);  /* ESC[<row>;<col>R */
    return n > 0 ? 0 : 1;
}
```

Because the contract is just bytes on an 8N1 line, the *tooling* is
interchangeable and the *protocol* is the only interface that matters.

## See also

- [SERIAL.md](SERIAL.md) — the 6551 driver, ring buffer, and overrun handling
  behind §3.
- [BRIDGE.md](BRIDGE.md) — the reference hosts (MAME/TCP and the WSL bash bridge)
  that speak this protocol.
- [TERMINAL.md](TERMINAL.md) — every escape sequence the parser accepts.
