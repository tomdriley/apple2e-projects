/* VT100 terminal for the Apple IIe over the Super Serial Card.
 *
 * Phase 1: an 80-column dumb terminal. Received bytes render to the 80-column
 * screen (screen80.c); keystrokes go out the 6551 (serial.c, which auto-detects
 * the Super Serial Card's slot). CR/LF/BS are handled directly; the full VT100
 * escape parser (vt100.c) arrives in the next phase. A "VT100-BOOT" marker is
 * sent over serial at startup as a headless boot signal.
 */
#include "monitor.h"
#include "screen.h"
#include "serial.h"
#include "vt100.h"

static void scr_puts(const char *s)
{
    while (*s) {
        scr_put(*s++);
    }
}

static void serial_puts(const char *s)
{
    while (*s) {
        serial_put(*s++);
    }
}

/* Send one keystroke to the host, translating the Apple's arrow keys into the
 * VT100 cursor sequences a host expects. The IIe arrows produce raw control
 * codes ($08/$15/$0B/$0A); sent verbatim a host reads them as backspace etc.,
 * not cursor movement. The DELETE key ($7F) still works as backspace. */
static void send_key(unsigned char c)
{
    switch (c) {
    case 0x08:
        c = 'D';
        break; /* left arrow  -> ESC[D */
    case 0x15:
        c = 'C';
        break; /* right arrow -> ESC[C */
    case 0x0B:
        c = 'A';
        break; /* up arrow    -> ESC[A */
    case 0x0A:
        c = 'B';
        break; /* down arrow  -> ESC[B */
    default:
        serial_put((char)c);
        return;
    }
    serial_put(0x1B);
    serial_put('[');
    serial_put((char)c);
}

void start(void)
{
    volatile unsigned char off = MOTOR_OFF; /* read soft switch: stop drive motor */
    unsigned char          c;

    (void)off;

    serial_init();
    scr_init();
    vt100_init();

    scr_puts("VT100 TERMINAL  80x24  READY");
    scr_cr();
    scr_lf();

    serial_puts("VT100-BOOT\r\n"); /* headless boot marker */

    for (;;) {
        serial_pump(); /* drain the ACIA into the ring buffer */
        if (serial_rx_ready()) {
            vt100_feed((char)serial_getch()); /* parse + render (ESC sequences) */
        }
        if (KBD & 0x80) { /* a key is waiting */
            c       = KBD & 0x7F;
            KBDSTRB = 0; /* clear the strobe */
            send_key(c); /* send the keystroke (arrows -> ANSI) */
        }
    }
}
