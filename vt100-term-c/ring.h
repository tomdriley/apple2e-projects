#ifndef RING_H
#define RING_H

/* Receive ring buffer: a lock-free single-producer/single-consumer FIFO.
 * The head (producer) and tail (consumer) pointers are unsigned char, so they
 * wrap mod RING_SIZE (256) for free. One slot is always left empty as a
 * sentinel:
 *   empty : head == tail
 *   full  : (unsigned char)(head + 1) == tail     (255 bytes usable)
 *   count : (unsigned char)(head - tail)          (0..255)
 *
 * The interrupt handler is the producer and the main loop is the consumer.
 * Each pointer has one writer, and byte stores are atomic on the 6502, so the
 * receive FIFO needs no critical section. */

#define RING_SIZE 256

void          ring_reset(void);
unsigned char ring_push(unsigned char b); /* used by the host unit test */
int           ring_pop(void);
unsigned char ring_count(void);
unsigned char ring_full(void);
unsigned char ring_empty(void);

#endif /* RING_H */
