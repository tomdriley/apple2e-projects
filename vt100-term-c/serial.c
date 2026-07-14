/* 6551 ACIA driver with Super Serial Card slot auto-detection.
 *
 * The 6551's four registers sit at $C088 + slot*16 (data, status, command,
 * control). Rather than hardcode slot 2 ($C0A8), serial_init() scans the slots
 * for the card's firmware signature the same way ADTPro's FindSlot does, so the
 * terminal works wherever the card is installed. If no card is found it falls
 * back to slot 2, which is where MAME wires the SSC.
 */
#include "serial.h"
#include "ring.h"

#define ST_TDRE 0x10 /* transmit data register empty */

#define XON       0x11
#define XOFF      0x13
#define RING_HIGH 192 /* send XOFF once the ring reaches this occupancy    */
#define RING_LOW  64  /* send XON once the ring drains back to this        */

/* Register base: [0]=data, [1]=status, [2]=command, [3]=control. Runtime
 * status/data access is serialized in serial_irq.s after IRQ installation. */
static volatile unsigned char *acia = (volatile unsigned char *)0xC0A8; /* slot 2 */
static unsigned char           found_slot;

/* The receive FIFO itself lives in ring.c (a lock-free single-producer/
 * single-consumer pointer ring); serial.c layers XON/XOFF flow control on top
 * of it. Keeping the ring in its own module lets the host unit test link the
 * real code and gives interrupt-driven RX a clean seam. */
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
    ring_reset();
    xoff_sent = 0;
    serial_irq_install((unsigned)acia);
}

/* Use absolute stores for TDR. An indirect indexed 6502 store dummy-reads the
 * destination first, which would destructively consume a pending byte from RDR. */
static void write_tdr(unsigned char c)
{
    switch (found_slot) {
    case 1:
        *(volatile unsigned char *)0xC098 = c;
        break;
    case 3:
        *(volatile unsigned char *)0xC0B8 = c;
        break;
    case 4:
        *(volatile unsigned char *)0xC0C8 = c;
        break;
    case 5:
        *(volatile unsigned char *)0xC0D8 = c;
        break;
    case 6:
        *(volatile unsigned char *)0xC0E8 = c;
        break;
    case 7:
        *(volatile unsigned char *)0xC0F8 = c;
        break;
    case 2:
    default: /* no detected card: preserve the slot-2 fallback */
        *(volatile unsigned char *)0xC0A8 = c;
        break;
    }
}

void serial_put(char c)
{
    while ((serial_irq_status() & ST_TDRE) == 0)
        ;
    write_tdr((unsigned char)c);
}

/* Keep the safe polling fallback and manage flow control in main context. */
void serial_pump(void)
{
    (void)serial_irq_status();
    if (xoff_sent == 0 && ring_count() >= RING_HIGH) {
        serial_put((char)XOFF);
        xoff_sent = 1;
    }
}

/* Returns the ring occupancy (0..255); callers use it only as a "bytes waiting"
 * boolean. */
unsigned char serial_rx_ready(void) { return ring_count(); }

int serial_getch(void)
{
    int c;
    c = ring_pop();
    if (c < 0) { /* empty */
        return -1;
    }
    if (xoff_sent != 0 && ring_count() <= RING_LOW) {
        serial_put((char)XON);
        xoff_sent = 0;
    }
    return c;
}

unsigned char serial_slot(void) { return found_slot; }
