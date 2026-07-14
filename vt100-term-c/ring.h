#ifndef RING_H
#define RING_H

/* Receive ring buffer: a lock-free single-producer/single-consumer FIFO.
 * The head (producer) and tail (consumer) pointers are unsigned char, so they
 * wrap mod RING_SIZE (256) for free. There is deliberately no separate
 * occupancy counter: one slot is always left empty as a sentinel, so the two
 * pointers alone disambiguate the states a bare count would confuse --
 *   empty : head == tail
 *   full  : (unsigned char)(head + 1) == tail     (255 bytes usable)
 *   count : (unsigned char)(head - tail)          (0..255)
 * The assembly producer stores data before publishing head; the assembly
 * consumer reads data before publishing tail. Each pointer has one writer, and
 * each byte-sized publication is atomic on the 6502. */

#define RING_SIZE 256 /* array size; head/tail wrap for free, 255 usable */

extern unsigned char          rx_ring[RING_SIZE];
extern volatile unsigned char r_head;
extern volatile unsigned char r_tail;
extern volatile unsigned char ring_drop_count;

void                       ring_reset(void);           /* clear the ring (make it empty)     */
unsigned char __fastcall__ ring_push(unsigned char b); /* 1 accepted, 0 dropped */
int                        ring_pop(void);             /* next byte, or -1 if empty          */
unsigned char              ring_count(void);           /* occupancy 0..255                   */
unsigned char              ring_full(void);            /* 1 if full, else 0                  */
unsigned char              ring_empty(void);           /* 1 if empty, else 0                 */

#endif /* RING_H */
