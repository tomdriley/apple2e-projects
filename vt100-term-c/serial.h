#ifndef SERIAL_H
#define SERIAL_H

/* 6551 ACIA on the Super Serial Card. serial_init() auto-detects the card's
 * slot from its firmware signature (like ADTPro's FindSlot) and falls back to
 * slot 2 ($C0A8) if none is found. */

extern volatile unsigned char serial_irq_active;
extern volatile unsigned char serial_irq_seen;
extern volatile unsigned char serial_irq_chained;

void          serial_init(void);     /* find the card, reset it, 9600 8N1          */
void          serial_put(char c);    /* block until the transmitter is free, send  */
void          serial_pump(void);     /* service RX fallback and XON/XOFF           */
unsigned char serial_rx_ready(void); /* count of bytes waiting in the ring buffer  */
int           serial_getch(void);    /* next buffered byte, or -1 if none          */
unsigned char serial_slot(void);     /* detected slot 1..7 (0 = fallback default)  */

unsigned char __fastcall__ serial_irq_install(unsigned base);
unsigned char              serial_irq_status(void);
void                       serial_irq_shutdown(void);

#endif /* SERIAL_H */
