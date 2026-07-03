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
#define RING_SIZE 256 /* head/tail are unsigned char, so they wrap for free */
#define RING_HIGH 192 /* send XOFF once the ring reaches this occupancy    */
#define RING_LOW  64  /* send XON once the ring drains back to this        */

/* Register pointer: [0]=data, [1]=status, [2]=command, [3]=control.
 * volatile: these are memory-mapped registers, so every access must be real. */
static volatile unsigned char *acia = (volatile unsigned char *)0xC0A8; /* slot 2 */
static unsigned char           found_slot;

/* Receive ring buffer with XON/XOFF flow control. */
static unsigned char ring[RING_SIZE];
static unsigned char r_head, r_tail, r_count;
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
    r_head = r_tail = r_count = 0;
    xoff_sent                 = 0;
    acia[1]                   = 0;             /* status write -> soft reset */
    acia[3]                   = CTRL_9600_8N1; /* control: baud + word length */
    acia[2]                   = CMD_NO_PARITY; /* command: no parity, DTR/RTS on */
}

void serial_put(char c)
{
    while ((acia[1] & ST_TDRE) == 0) {
        /* While the transmitter drains, keep pulling any received byte into the
         * ring. A reply like the ESC[?1;0c device-attributes answer is several
         * bytes; without this the host's next bytes would overrun the 6551's
         * one-byte receive register while we sit here waiting to transmit. */
        if ((acia[1] & ST_RDRF) != 0 && r_count != RING_SIZE) {
            ring[r_head++] = acia[0];
            ++r_count;
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
    while ((acia[1] & ST_RDRF) != 0 && r_count != RING_SIZE) {
        ring[r_head++] = acia[0];
        ++r_count;
    }
    if (xoff_sent == 0 && r_count >= RING_HIGH) {
        serial_put((char)XOFF);
        xoff_sent = 1;
    }
}

unsigned char serial_rx_ready(void) { return r_count; }

int serial_getch(void)
{
    unsigned char c;
    if (r_count == 0) {
        return -1;
    }
    c = ring[r_tail++];
    --r_count;
    if (xoff_sent != 0 && r_count <= RING_LOW) {
        serial_put((char)XON);
        xoff_sent = 0;
    }
    return (int)c;
}

unsigned char serial_slot(void) { return found_slot; }
