# Serial I/O (6551 ACIA)

[serial.c](../serial.c), [serial_irq.s](../serial_irq.s), and
[ring_io.s](../ring_io.s) implement a focused receive-interrupt architecture for
the Super Serial Card's 6551 ACIA. The line is **9600 baud, 8N1**. RX is
interrupt-driven into a 255-byte FIFO; TX remains blocking and polled.

## Registers and card switch

The 6551's four registers sit at `$C088 + slot*16`:

| Offset | Register | Runtime use |
|--------|----------|-------------|
| `+0` | Data | Read RDR / write TDR |
| `+1` | Status | IRQ `$80`, RDRF `$08`, TDRE `$10`; a read clears the IRQ latch |
| `+2` | Command | `$09` = RX IRQ on, TX IRQ off, no parity/echo, DTR + RTS asserted |
| `+3` | Control | `$1E` = 9600 baud, 8 data bits, 1 stop bit |

`$0B` preserves the same line configuration with receiver IRQ disabled; the
installer and teardown paths use it while vectors or card state are changing.
The Super Serial Card also has a physical interrupt gate: **SW2:6 must be On**.
Setting command `$09` cannot raise the Apple II IRQ line while that switch is
Off.

The detected register base is a `volatile` pointer, but runtime status reads do
not go through C. A status read clears the 6551 interrupt latch, so both the ISR
and main-context helper use self-patched absolute operands in `serial_irq.s`.

TDR writes also require special care. cc65 emits `STA (zp),Y` for an indirect
write, and the NMOS 6502 dummy-reads the destination before writing. A dummy read
of the data register consumes RDR. `write_tdr()` therefore selects a
slot-specific **volatile absolute store** (`STA $C0x8`), which has no destructive
read cycle. TX IRQ and a transmit FIFO are intentionally out of scope.

## Slot auto-detection

`serial_init()` scans slots 7 to 1 for the Super Serial Card firmware signature
used by ADTPro's `FindSlot`:

```
$Cn05 == $38   $Cn07 == $18   $Cn0B == $01   $Cn0C == $31
```

The first match selects `$C088 + slot*16`. If nothing matches, the driver falls
back to slot 2 (`$C0A8`), where the MAME configuration installs the card.
`serial_irq_install()` patches every absolute data/status/command/control
operand before exposing the handler.

## RX interrupt path

```mermaid
flowchart LR
    ACIA[6551 one-byte RDR] -->|receiver IRQ| ISR[serial_irq.s]
    ISR -->|store byte, then publish head| RING[255-byte SPSC ring]
    RING -->|read byte, then publish tail| MAIN[term.c / vt100_feed]
    MAIN -->|high-water XOFF / low-water XON| TX[polled serial_put]
```

### Installation

With CPU IRQ masked, the installer:

1. Saves the prior 6551 command/control, Monitor `IRQLOC` vector
   (`$03FE/$03FF`), soft-reset vector `SOFTEV` (`$03F2/$03F3`), and `PWREDUP`
   check byte (`$03F4`).
2. Patches all absolute ACIA operands and the absolute prior-handler/reset jump
   targets.
3. Marks the state restorable, installs the Ctrl-Reset cleanup hook and valid
   `PWREDUP`, then installs the RX handler in `IRQLOC`.
4. Resets/configures the 6551, clears stale receive state, writes command `$09`
   last, and enables CPU IRQs.

Installing the reset hook before the IRQ vector means Ctrl-Reset cannot strand a
partially installed vector, even if reset lands in the middle of setup.

### Handler and chaining

Apple Monitor IRQ dispatch saves the interrupted accumulator at `$45` and jumps
through `IRQLOC` with the hardware P/PC frame still on the stack. The handler
saves the exact Monitor-dispatch P/A plus interrupted X/Y before reading the
patched SSC status register.

- If this SSC did not assert IRQ, the handler restores exact entry A/P/X/Y and
  jumps to the saved prior handler. It does not intercept DOS, keyboard, or
  another slot card's interrupt.
- If RDRF is set, it reads RDR immediately, pushes the byte through the assembly
  ring producer, marks IRQ telemetry, restores X/Y, reloads interrupted A from
  `$45`, and executes `RTI`.
- A card interrupt without RDRF is acknowledged by the status read and returned.
  A simultaneous foreign IRQ remains asserted and retriggers, then chains.

The ISR calls no C and uses no cc65 software-stack or zero-page runtime helper.
That separation is mandatory because the C build uses `-Cl` static locals.

### Status-read serialization

`serial_put()` still polls TDRE and `serial_pump()` remains as a fallback, but
both call `serial_irq_status()`. That assembly helper preserves the caller's
interrupt state, masks IRQ, reads status, captures/enqueues RDR if full, then
restores the caller's state. Main code therefore cannot clear the IRQ latch and
race the ISR.

### PAGE2 invariant

The handler does not touch `PAGE2`. With 80STORE active, `PAGE2` banks only
`$0400-$07FF`; the program, ring, indices, and telemetry are at `$0800` or above.
Link-time assertions enforce every IRQ-touched data symbol. An interrupt may
therefore arrive while `screen80.c` has AUX selected, and the interrupted code
resumes with its bank selection unchanged.

### Exit and Ctrl-Reset

Normal `_exit` and the installed Ctrl-Reset hook share an idempotent teardown:

1. mask CPU IRQ and disable receiver IRQ;
2. acknowledge/drain pending SSC state;
3. restore `IRQLOC`;
4. restore prior 6551 control/command;
5. restore `SOFTEV` and `PWREDUP`.

Normal exit restores the pre-install CPU interrupt state before DOS warm start.
Ctrl-Reset jumps to the saved soft-reset target after cleanup, preserving DOS's
reset behavior.

## Ring ownership, overflow, and flow control

The FIFO storage and snapshots live in [`ring.c`](../ring.c) /
[`ring.h`](../ring.h); ordered push/pop operations live in
[`ring_io.s`](../ring_io.s):

- the ISR is the normal producer and sole head writer;
- main is the sole consumer and tail writer;
- the polling helper masks IRQ while acting as the fallback producer;
- push writes the byte before publishing head;
- pop reads the byte before publishing tail.

Head and tail are bytes and wrap modulo 256. One slot is the sentinel, so
`head == tail` is empty, `(head + 1) == tail` is full, and usable capacity is
255. Occupancy is `(unsigned char)(head - tail)`.

On full, the producer drops the **newest** byte without changing FIFO contents
and increments `ring_drop_count`, saturating at 255. XON/XOFF normally prevents
this: `serial_pump()` sends XOFF at 192 bytes, and `serial_getch()` sends XON
after occupancy falls to 64. Flow-control transmission remains in main context;
the ISR never blocks or transmits.

The existing pump calls in screen loops are intentionally retained. They service
the polling fallback and give main context opportunities to apply XON/XOFF, but
RDR safety no longer depends on placing a pump inside every one-millisecond CPU
window.

## Receive-overrun history and regressions

The old polled design repeatedly lost bytes whenever rendering or synchronous
replies held the CPU longer than one 9600-baud byte time. Earlier fixes added
per-cell pumps and hardened report TX:

- poll RDR before every TDRE decision;
- use absolute TDR stores to avoid the NMOS dummy read;
- format CPR coordinates without cc65 division.

Those changes remain valuable and closed the deterministic back-to-back
`ESC[6n` report bug. They could not protect arbitrary no-pump work: a BEL delay,
for example, leaves main code away from the ACIA long enough to overwrite RDR.
Receiver IRQ is the architectural fix for that broader class.

ROM-backed regressions cover distinct parts of the design:

- `irq_rx_test.py` sends BEL, waits until `beep()` is in its no-pump delay, then
  streams a marker and requires exact screen/cursor state plus IRQ telemetry.
- `irq_lifecycle_test.py` raises a non-SSC IRQ from a slot-1 Mockingboard VIA
  timer, proves chaining, then presses Ctrl-Reset and checks IRQLOC,
  SOFTEV/PWREDUP, and ACIA restoration.
- `report-da-overlap-lossless` requires seven exact DA replies and following
  private-CSI markers with no residue.
- `report-cpr-6n-idempotent` requires two exact CPR replies with unchanged cursor
  state.
- `make test` runs the production assembly ring operations under sim65 and checks
  capacity, FIFO order, wrap, drop-newest integrity, saturation, and reset.

## MAME wiring

MAME installs the SSC in slot 2 and connects its RS-232 port out to a listening
TCP host:

```
-sl2 ssc -sl2:ssc:rs232 null_modem -bitb socket.127.0.0.1:6551
```

MAME models SW2:6 Off by default. Every project Lua probe loads
`client/ssc_irq.lua`, which sets the modeled **Interrupts** switch On and fails
loudly if the switch is unavailable. `make run` and `make debug` load the same
script. Override the socket with `SERIAL_PORT=6571 make run` or set
`MAME_PORT=6571` for the Python harnesses.

`-aux ext80` supplies the auxiliary RAM. `-sl2 ssc` also requires the `a2ssc`
firmware ROM (`341-0065-a.bin`) in the configured MAME rompath.

## Real hardware

On a physical Apple IIe, set the Super Serial Card's **SW2:6 On**, wire a
USB/RS-232 adapter, and use a Python client's `serial` transport. The RX IRQ path
has been gated with real-ROM MAME; this change has not been validated on a
physical IIe/SSC yet. Physical follow-up should cover Ctrl-Reset cleanup,
sustained bursts, and XON/XOFF.
