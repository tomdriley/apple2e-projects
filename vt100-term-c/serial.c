/* 6551 ACIA driver with Super Serial Card slot auto-detection.
 *
 * The 6551's four registers sit at $C088 + slot*16 (data, status, command,
 * control). Rather than hardcode slot 2 ($C0A8), serial_init() scans the slots
 * for the card's firmware signature the same way ADTPro's FindSlot does, so the
 * terminal works wherever the card is installed. If no card is found it falls
 * back to slot 2, which is where MAME wires the SSC.
 */
#include "serial.h"

#define ST_RDRF       0x08 /* receive data register full   */
#define ST_TDRE       0x10 /* transmit data register empty */
#define CTRL_9600_8N1 0x1E /* 9600 baud, 8 data bits, 1 stop bit */
#define CMD_NO_PARITY 0x0B /* no parity/echo/IRQ; DTR + RTS asserted */

#define XON       0x11
#define XOFF      0x13
#define RING_SIZE 256 /* array size; head/tail wrap for free, 255 usable   */
#define RING_HIGH 192 /* send XOFF once the ring reaches this occupancy    */
#define RING_LOW  64  /* send XON once the ring drains back to this        */

/* Register pointer: [0]=data, [1]=status, [2]=command, [3]=control.
 * volatile: these are memory-mapped registers, so every access must be real. */
static volatile unsigned char *acia = (volatile unsigned char *)0xC0A8; /* slot 2 */
static unsigned char           found_slot;

/* Receive ring buffer with XON/XOFF flow control, structured as a lock-free
 * single-producer/single-consumer FIFO. r_head (producer) and r_tail (consumer)
 * are unsigned char, so they wrap mod RING_SIZE (256) for free. There is
 * deliberately no separate occupancy counter: one slot is always left empty as
 * a sentinel, so the two pointers alone disambiguate the states a bare count
 * would confuse --
 *   empty : r_head == r_tail
 *   full  : (unsigned char)(r_head + 1) == r_tail     (255 bytes usable)
 *   avail : (unsigned char)(r_head - r_tail)          (0..255)
 * Because each pointer is written by exactly one side and a single-byte store is
 * atomic on the 6502, this needs no critical section even once RX becomes
 * interrupt-driven (the producer moves into the ISR, the consumer stays in the
 * main loop). It also sidesteps the class of bug where an occupancy counter is
 * too narrow to represent a full ring. */
static unsigned char ring[RING_SIZE];
static unsigned char r_head, r_tail;
static unsigned char xoff_sent;

/* Scan slots 7..1 for the SSC firmware signature (Pascal 1.1 protocol bytes).
 * Returns 1..7, or 0 if nothing matched. */
static unsigned char find_ssc_slot(void)
{
    const unsigned char *fw;
    unsigned char        slot;

    for (slot = 7; slot != 0; --slot) {
        fw = (const unsigned char *)(0xC000 + ((unsigned)slot << 8));
        if (fw[0x05] == 0x38 && fw[0x07] == 0x18 && fw[0x0B] == 0x01 && fw[0x0C] == 0x31) {
            return slot;
        }
    }
    return 0;
}

void serial_init(void)
{
    found_slot = find_ssc_slot();
    if (found_slot != 0) {
        acia = (volatile unsigned char *)(0xC088 + ((unsigned)found_slot << 4));
    }
    r_head = r_tail = 0;
    xoff_sent       = 0;
    acia[1]         = 0;             /* status write -> soft reset */
    acia[3]         = CTRL_9600_8N1; /* control: baud + word length */
    acia[2]         = CMD_NO_PARITY; /* command: no parity, DTR/RTS on */
}

void serial_put(char c)
{
    unsigned char nh;
    while ((acia[1] & ST_TDRE) == 0) {
        /* While the transmitter drains, keep pulling any received byte into the
         * ring. A reply like the ESC[?1;0c device-attributes answer is several
         * bytes; without this the host's next bytes would overrun the 6551's
         * one-byte receive register while we sit here waiting to transmit. */
        if ((acia[1] & ST_RDRF) != 0) {
            nh = (unsigned char)(r_head + 1);
            if (nh != r_tail) { /* not full: sentinel slot still open */
                ring[r_head] = acia[0];
                r_head       = nh;
            }
        }
    }
    acia[0] = (unsigned char)c;
}

/* Drain every byte the ACIA holds into the ring buffer, then throttle the host
 * with XOFF if the ring is getting full. Called from the main loop and from
 * inside the screen driver's slow clear/scroll loops, so the 6551's one-byte
 * receive register never overruns while we are busy drawing. */
void serial_pump(void)
{
    unsigned char nh;
    nh = (unsigned char)(r_head + 1);
    while ((acia[1] & ST_RDRF) != 0 && nh != r_tail) {
        ring[r_head] = acia[0];
        r_head       = nh;
        nh           = (unsigned char)(r_head + 1);
    }
    if (xoff_sent == 0 && (unsigned char)(r_head - r_tail) >= RING_HIGH) {
        serial_put((char)XOFF);
        xoff_sent = 1;
    }
}

/* Returns the ring occupancy (0..255). With one slot reserved as a sentinel the
 * pointer difference is always a single byte, so no clamp is needed; callers use
 * the result only as a "bytes waiting" boolean. */
unsigned char serial_rx_ready(void) { return (unsigned char)(r_head - r_tail); }

int serial_getch(void)
{
    unsigned char c;
    if (r_head == r_tail) { /* empty */
        return -1;
    }
    c = ring[r_tail++];
    if (xoff_sent != 0 && (unsigned char)(r_head - r_tail) <= RING_LOW) {
        serial_put((char)XON);
        xoff_sent = 0;
    }
    return (int)c;
}

unsigned char serial_slot(void) { return found_slot; }
