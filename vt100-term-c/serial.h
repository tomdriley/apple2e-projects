#ifndef SERIAL_H
#define SERIAL_H

/* 6551 ACIA on the Super Serial Card. serial_init() auto-detects the card's
 * slot from its firmware signature (like ADTPro's FindSlot) and falls back to
 * slot 2 ($C0A8) if none is found. */

void          serial_init(void);     /* find the card, reset it, 9600 8N1          */
void          serial_put(char c);    /* block until the transmitter is free, send  */
void          serial_pump(void);     /* drain ACIA -> ring buffer, manage XON/XOFF */
unsigned char serial_rx_ready(void); /* count of bytes waiting in the ring buffer  */
int           serial_getch(void);    /* next buffered byte, or -1 if none          */
unsigned char serial_slot(void);     /* detected slot 1..7 (0 = fallback default)  */

#endif /* SERIAL_H */
