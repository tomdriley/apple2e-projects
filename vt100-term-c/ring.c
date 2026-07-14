/* 6551 receive ring storage and main-context helpers. The ordered producer and
 * consumer operations live in ring_io.s so IRQ and main code share one ABI. */
#include "ring.h"

unsigned char          rx_ring[RING_SIZE];
volatile unsigned char r_head;
volatile unsigned char r_tail;
volatile unsigned char ring_drop_count;

void ring_reset(void)
{
    r_head          = 0;
    r_tail          = 0;
    ring_drop_count = 0;
}

/* Full is (head + 1) == tail: one slot is always left empty as a sentinel, so
 * head == tail unambiguously means empty. */
unsigned char ring_full(void) { return (unsigned char)((unsigned char)(r_head + 1) == r_tail); }

unsigned char ring_empty(void) { return (unsigned char)(r_head == r_tail); }

/* Occupancy (0..255). With one slot reserved as a sentinel the pointer
 * difference is always a single byte, so no clamp is needed. */
unsigned char ring_count(void) { return (unsigned char)(r_head - r_tail); }
