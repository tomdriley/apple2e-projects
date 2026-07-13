#ifndef SERIAL_H
#define SERIAL_H

/* 6551 ACIA on the Super Serial Card. serial_init() auto-detects the card's
 * slot from its firmware signature (like ADTPro's FindSlot) and falls back to
 * slot 2 ($C0A8) if none is found. Reception and transmission are
 * interrupt-driven (see serial_isr.s): a shared ISR fills the RX ring the moment
 * a byte arrives and drains the TX ring on demand, so the main loop no longer
 * has to pump the ACIA to avoid receive overruns. */

void          serial_init(void);     /* find the card, reset it, 9600 8N1, arm IRQ */
void          serial_put(char c);    /* queue a byte; waits only if TX ring is full */
void          serial_write(const char *data, unsigned char len); /* queue one TX burst */
unsigned char serial_rx_ready(void); /* count of bytes waiting in the ring buffer  */
int           serial_getch(void);    /* next buffered byte, or -1 if none          */
unsigned char serial_slot(void);     /* detected slot 1..7 (0 = fallback default)  */

#endif /* SERIAL_H */
