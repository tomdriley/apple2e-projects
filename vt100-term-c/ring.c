/* 6551 receive ring buffer: count-free sentinel-slot pointer FIFO.
 *
 * Extracted from serial.c so the driver and host test share the real
 * implementation. serial_isr.s imports rx_ring/r_head/r_tail directly because
 * the IRQ must remain pure assembly; these are module internals, not public C
 * API. The indices are volatile because one side changes asynchronously.
 */
#include "ring.h"

unsigned char          rx_ring[RING_SIZE];
volatile unsigned char r_head, r_tail;

void ring_reset(void)
{
    r_head = 0;
    r_tail = 0;
}

unsigned char ring_full(void)
{
    return (unsigned char)((unsigned char)(r_head + 1) == r_tail);
}

unsigned char ring_empty(void) { return (unsigned char)(r_head == r_tail); }

unsigned char ring_push(unsigned char b)
{
    unsigned char head;
    unsigned char nh;
    head = r_head;
    nh   = (unsigned char)(head + 1);
    if (nh != r_tail) {
        rx_ring[head] = b;
        r_head        = nh;
        return 1;
    }
    return 0;
}

int ring_pop(void)
{
    unsigned char b;
    unsigned char tail;
    if (r_head == r_tail) {
        return -1;
    }
    tail   = r_tail;
    b      = rx_ring[tail];
    r_tail = (unsigned char)(tail + 1);
    return (int)b;
}

unsigned char ring_count(void) { return (unsigned char)(r_head - r_tail); }
