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

#define CTRL_9600_8N1 0x1E /* 9600 baud, 8 data bits, 1 stop bit */
/* Command register: no parity/echo, DTR + RTS asserted, receiver IRQ enabled.
 * CMD_RX_ON leaves the transmit IRQ off; the ISR raises it to CMD_TX_ON while
 * the transmit ring is draining and drops it back when the ring empties.
 * (The old polled driver used 0x0B, which disabled the receiver IRQ.) */
#define CMD_TX_ON     0x05 /* RX IRQ on, TX IRQ on  */

#define XON      0x11
#define RING_LOW 64 /* send XON once the ring drains back to this        */
/* XOFF (and its high-water threshold) lives in serial_isr.s, where the RX ISR
 * front-pushes it the moment the ring fills past the high-water mark. */

/* Assembly helpers (serial_isr.s): the shared 6551 interrupt handler plus brief
 * critical sections for the C side, which shares the command register and
 * xoff_sent with the ISR. */
void serial_isr_install(volatile unsigned char *acia_base);
void irq_off(void); /* SEI: mask CPU interrupts   */
void irq_on(void);  /* CLI: enable CPU interrupts */

/* Register pointer: [0]=data, [1]=status, [2]=command, [3]=control.
 * volatile: these are memory-mapped registers, so every access must be real. */
static volatile unsigned char *acia = (volatile unsigned char *)0xC0A8; /* slot 2 */
static unsigned char           found_slot;

/* The RX FIFO lives in ring.c; serial_isr.s owns its
 * producer end and the main loop consumes it through ring_pop().
 *
 * The TX FIFO runs in the opposite direction: the main loop appends at t_head,
 * while the ISR drains and may front-push urgent XOFF at t_tail. The front-push
 * is a second producer operation, so serial_put masks interrupts while it
 * rechecks capacity, stores the byte and publishes t_head. Without that small
 * critical section, a near-full queue could have its sentinel consumed by an
 * interleaved XOFF push and momentarily look empty. Ordinary output also leaves
 * one usable slot reserved so high-water XOFF can never be starved. */
unsigned char          tx_ring[RING_SIZE];
volatile unsigned char t_head, t_tail;
volatile unsigned char xoff_sent;
volatile unsigned char tx_irq_active;

/* Called only with CPU interrupts masked. The command register is written on
 * the idle->active transition, not for every queued byte: MAME's 6551 updates
 * its internal divider on every command write, and real hardware needs no
 * redundant re-arming either. */
static void arm_tx(void)
{
    if (tx_irq_active == 0) {
        tx_irq_active = 1;
        acia[2]       = CMD_TX_ON;
    }
}

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
    t_head = t_tail = 0;
    xoff_sent       = 0;
    tx_irq_active   = 0;
    acia[1]         = 0;             /* status write -> soft reset */
    acia[3]         = CTRL_9600_8N1; /* control: baud + word length */
    /* Install the IRQ vector and arm the receiver interrupt (sets the command
     * register and enables CPU interrupts). From here the ISR owns rx_ring's
     * producer end and tx_ring's consumer end. */
    serial_isr_install(acia);
}

/* Queue one byte for transmission. Ordinary output uses at most 254 slots,
 * reserving the final usable slot for urgent XOFF. Interrupts are re-enabled
 * between retries so the ISR can keep receiving and drain the TX queue. The
 * capacity check, store and head publish are one critical section because the
 * ISR can also add XOFF at the front. */
void serial_put(char c)
{
    unsigned char nh;
    for (;;) {
        irq_off();
        nh = (unsigned char)(t_head + 1);
        if (nh != t_tail && (unsigned char)(nh + 1) != t_tail) {
            break;
        }
        irq_on();
    }
    tx_ring[t_head] = (unsigned char)c;
    t_head          = nh;
    arm_tx();
    irq_on();
}

/* Queue a complete protocol reply before starting an idle transmitter. RX IRQs
 * are re-enabled between byte publications, but TX remains disarmed until the
 * final byte is visible. If the ring fills, arm the partial burst so it can
 * drain, then continue. */
void serial_write(const char *data, unsigned char len)
{
    unsigned char nh;
    while (len != 0) {
        irq_off();
        nh = (unsigned char)(t_head + 1);
        if (nh == t_tail || (unsigned char)(nh + 1) == t_tail) {
            arm_tx();
            irq_on();
            continue;
        }
        tx_ring[t_head] = (unsigned char)*data++;
        t_head          = nh;
        --len;
        if (len == 0) {
            arm_tx();
        }
        irq_on();
    }
}

/* Queue XON and clear the throttled state as one ISR-safe operation. XON may
 * claim the reserved control slot because the host is already paused. Keep
 * xoff_sent set while a full TX ring makes us wait, so the RX ISR cannot
 * mistake the pause for a new high-water crossing. Recheck RX occupancy after
 * every wait because the host may not have honored XOFF yet. */
static void resume_rx(void)
{
    unsigned char nh;
    for (;;) {
        irq_off();
        if (xoff_sent == 0 || ring_count() > RING_LOW) {
            irq_on();
            return;
        }
        nh = (unsigned char)(t_head + 1);
        if (nh != t_tail) {
            break;
        }
        irq_on();
    }
    tx_ring[t_head] = XON;
    t_head          = nh;
    xoff_sent       = 0;
    arm_tx();
    irq_on();
}

unsigned char serial_rx_ready(void) { return ring_count(); }

int serial_getch(void)
{
    int c;
    c = ring_pop();
    if (c < 0) {
        return -1;
    }
    if (xoff_sent != 0 && ring_count() <= RING_LOW) {
        resume_rx();
    }
    return c;
}

unsigned char serial_slot(void) { return found_slot; }
