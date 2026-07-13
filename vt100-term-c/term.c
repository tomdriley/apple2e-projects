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
    /* In application-cursor-keys mode (DECCKM) the host wants ESC O x, otherwise
     * the normal ESC [ x. Full-screen apps like vi enable DECCKM. */
    serial_put(vt100_app_cursor() ? 'O' : '[');
    serial_put((char)c);
}

/* Idle loop passes to wait through before painting the cursor. The host streams
 * a case/line as a back-to-back burst; at 9600 baud the firmware drains each
 * byte well inside one byte-time, so the receive ring goes momentarily empty
 * between bytes of the same burst. Painting on the very first empty pass would
 * therefore invert-and-restore the cursor cell around *every* received byte,
 * adding per-byte work that can push the 6551's one-byte receive register into
 * overrun (a dropped byte shifts the rest of the line left). Debouncing past the
 * sub-millisecond inter-byte gap means the cursor is only painted once the host
 * truly stops sending, so reception runs at full speed and the cursor still
 * appears promptly (well under a frame) when the terminal is idle. */
#define CURSOR_IDLE_PASSES 1200u

void start(void)
{
    volatile unsigned char off = MOTOR_OFF; /* read soft switch: stop drive motor */
    unsigned char          c;
    unsigned int           idle; /* consecutive idle passes since the last byte */

    (void)off;

    serial_init();
    scr_init();
    vt100_init();
    idle = 0;

    scr_puts("VT100 TERMINAL  80x24  READY");
    scr_cr();
    scr_lf();

    serial_puts("VT100-BOOT\r\n"); /* headless boot marker */

    for (;;) {
        serial_pump(); /* drain the ACIA into the ring buffer */
        if (serial_rx_ready()) {
            idle = 0;
            scr_cursor_erase();               /* clear the overlay before rendering */
            vt100_feed((char)serial_getch()); /* parse + render (ESC sequences) */
        } else if (idle < CURSOR_IDLE_PASSES) {
            idle++; /* still settling: hold off so a burst never flickers/overruns */
        } else {
            scr_cursor_paint(); /* genuinely idle: show the cursor (paints once) */
        }
        if (KBD & 0x80) { /* a key is waiting */
            c       = KBD & 0x7F;
            KBDSTRB = 0; /* clear the strobe */
            send_key(c); /* send the keystroke (arrows -> ANSI) */
        }
    }
}
