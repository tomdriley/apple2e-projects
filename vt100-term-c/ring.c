/* 6551 receive ring buffer: the count-free, sentinel-slot pointer FIFO.
 *
 * Extracted from serial.c so both the driver and the host-side unit test link
 * the same code (see docs/LESSONS.md for the ring-full bug this design fixed).
 * The buffer is plain RAM, not memory-mapped, so nothing here is volatile --
 * only serial.c's 6551 register pointer needs that. r_head is written by the
 * producer alone and r_tail by the consumer alone, which keeps the FIFO
 * lock-free for a future interrupt-driven RX path.
 */
#include "ring.h"

static unsigned char ring[RING_SIZE];
static unsigned char r_head, r_tail;

void ring_reset(void)
{
    r_head = 0;
    r_tail = 0;
}

/* Full is (head + 1) == tail: one slot is always left empty as a sentinel, so
 * head == tail unambiguously means empty. */
unsigned char ring_full(void) { return (unsigned char)((unsigned char)(r_head + 1) == r_tail); }

unsigned char ring_empty(void) { return (unsigned char)(r_head == r_tail); }

/* Returns 1 if the byte was accepted, 0 if the ring was full (byte dropped). */
unsigned char ring_push(unsigned char b)
{
    unsigned char nh;
    nh = (unsigned char)(r_head + 1);
    if (nh != r_tail) { /* not full: sentinel slot still open */
        ring[r_head] = b;
        r_head       = nh;
        return 1;
    }
    return 0;
}

/* Returns the popped byte (0..255), or -1 when empty. */
int ring_pop(void)
{
    unsigned char b;
    if (r_head == r_tail) { /* empty */
        return -1;
    }
    b = ring[r_tail++];
    return (int)b;
}

/* Occupancy (0..255). With one slot reserved as a sentinel the pointer
 * difference is always a single byte, so no clamp is needed. */
unsigned char ring_count(void) { return (unsigned char)(r_head - r_tail); }
