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
 * Because each pointer is written by exactly one side and a single-byte store
 * is atomic on the 6502, this needs no critical section even once RX becomes
 * interrupt-driven (the producer moves into the ISR, the consumer stays in the
 * main loop). It also sidesteps the class of bug where an occupancy counter is
 * too narrow to represent a full ring. */

#define RING_SIZE 256 /* array size; head/tail wrap for free, 255 usable */

void          ring_reset(void);           /* clear the ring (make it empty)     */
unsigned char ring_push(unsigned char b); /* store b; 1 if accepted, 0 if full  */
int           ring_pop(void);             /* next byte, or -1 if empty          */
unsigned char ring_count(void);           /* occupancy 0..255                   */
unsigned char ring_full(void);            /* 1 if full, else 0                  */
unsigned char ring_empty(void);           /* 1 if empty, else 0                 */

#endif /* RING_H */
