/* VT100/ANSI escape-sequence parser.
 *
 * Bytes arriving over serial are fed one at a time to vt100_feed(). Printable
 * characters go to the screen; a small state machine (NORMAL -> ESC -> CSI)
 * recognizes the common cursor, erase, and report sequences. Private and
 * unrecognized sequences (colors/SGR, ESC[?25h, ...) are consumed and ignored
 * so they never corrupt the display. Cursor-position reports (ESC[6n) are
 * answered over the serial line, which is what the automated test checks.
 */
#include "vt100.h"
#include "monitor.h"
#include "screen.h"
#include "serial.h"

#define S_NORMAL 0
#define S_ESC    1
#define S_CSI    2
#define MAXPARAM 4

static unsigned char state;
static unsigned int  param[MAXPARAM];
static unsigned char nparam; /* index of the parameter currently being built */
static unsigned char priv;   /* a private marker ('?') was seen in this CSI */
static unsigned char app_cursor; /* DECCKM: application cursor keys enabled */
static unsigned char saved_col, saved_row;

void vt100_init(void)
{
    state      = S_NORMAL;
    saved_col  = 0;
    saved_row  = 0;
    app_cursor = 0;
}

static void reset_params(void)
{
    unsigned char i;
    for (i = 0; i < MAXPARAM; ++i) {
        param[i] = 0;
    }
    nparam = 0;
    priv   = 0;
}

static unsigned int getp(unsigned char i)
{
    return (i <= nparam) ? param[i] : 0;
}

static void beep(void)
{
    unsigned char i;
    unsigned int  j;
    for (i = 0; i < 128; ++i) {
        (void)SPKR; /* reading $C030 toggles the speaker */
        for (j = 0; j < 60; ++j) {
            /* crude half-period delay */
        }
    }
}

static void put_dec(unsigned char n)
{
    char          buf[3];
    unsigned char i = 0;
    if (n == 0) {
        serial_put('0');
        return;
    }
    while (n != 0) {
        buf[i++] = (char)('0' + (n % 10));
        n /= 10;
    }
    while (i != 0) {
        serial_put(buf[--i]);
    }
}

static void report_cursor(void) /* ESC [ row ; col R  (1-based) */
{
    serial_put(0x1B);
    serial_put('[');
    put_dec((unsigned char)(scr_row() + 1));
    serial_put(';');
    put_dec((unsigned char)(scr_col() + 1));
    serial_put('R');
}

static void csi_dispatch(unsigned char f)
{
    unsigned char row = scr_row();
    unsigned char col = scr_col();
    unsigned int  n;

    switch (f) {
    case 'A': /* cursor up */
        n = getp(0);
        if (n == 0)
            n = 1;
        row = (n > row) ? 0 : (unsigned char)(row - n);
        scr_gotoxy(col, row);
        break;
    case 'B': /* cursor down */
        n = getp(0);
        if (n == 0)
            n = 1;
        n = row + n;
        if (n >= SCR_ROWS)
            n = SCR_ROWS - 1;
        scr_gotoxy(col, (unsigned char)n);
        break;
    case 'C': /* cursor forward */
        n = getp(0);
        if (n == 0)
            n = 1;
        n = col + n;
        if (n >= SCR_COLS)
            n = SCR_COLS - 1;
        scr_gotoxy((unsigned char)n, row);
        break;
    case 'D': /* cursor back */
        n = getp(0);
        if (n == 0)
            n = 1;
        col = (n > col) ? 0 : (unsigned char)(col - n);
        scr_gotoxy(col, row);
        break;
    case 'H': /* cursor position */
    case 'f': {
        unsigned int rr = getp(0);
        unsigned int cc = getp(1);
        if (rr == 0)
            rr = 1;
        if (cc == 0)
            cc = 1;
        scr_gotoxy((unsigned char)(cc - 1), (unsigned char)(rr - 1));
        break;
    }
    case 'G': /* CHA: cursor to absolute column (row unchanged) */
    case '`': /* HPA: horizontal position absolute (same as CHA) */
        n = getp(0);
        if (n == 0)
            n = 1;
        if (n > SCR_COLS)
            n = SCR_COLS;
        scr_gotoxy((unsigned char)(n - 1), row);
        break;
    case 'd': /* VPA: cursor to absolute row (column unchanged) */
        n = getp(0);
        if (n == 0)
            n = 1;
        if (n > SCR_ROWS)
            n = SCR_ROWS;
        scr_gotoxy(col, (unsigned char)(n - 1));
        break;
    case 'E': /* CNL: cursor to start of line n rows down */
        n = getp(0);
        if (n == 0)
            n = 1;
        n = row + n;
        if (n >= SCR_ROWS)
            n = SCR_ROWS - 1;
        scr_gotoxy(0, (unsigned char)n);
        break;
    case 'F': /* CPL: cursor to start of line n rows up */
        n = getp(0);
        if (n == 0)
            n = 1;
        row = (n > row) ? 0 : (unsigned char)(row - n);
        scr_gotoxy(0, row);
        break;
    case 'J': /* erase in display: 0 = to end, 1 = to cursor, 2 = all */
        n = getp(0);
        if (n == 1) {
            scr_clear_bop();
        } else if (n == 2) {
            scr_clear_all();
        } else {
            scr_clear_eop();
        }
        break;
    case 'K': /* erase in line: 0 = to end, 1 = to cursor, 2 = whole line */
        n = getp(0);
        if (n == 1) {
            scr_clear_bol();
        } else if (n == 2) {
            scr_clear_line();
        } else {
            scr_clear_eol();
        }
        break;
    case 'n': /* device status report */
        n = getp(0);
        if (n == 6) {
            report_cursor();
        } else if (n == 5) {
            serial_put(0x1B);
            serial_put('[');
            serial_put('0');
            serial_put('n');
        }
        break;
    case 'c': /* device attributes: identify as a VT100 */
        serial_put(0x1B);
        serial_put('[');
        serial_put('?');
        serial_put('1');
        serial_put(';');
        serial_put('0');
        serial_put('c');
        break;
    case 's': /* save cursor */
        saved_col = scr_col();
        saved_row = scr_row();
        break;
    case 'h': /* set mode / DEC private set (ESC[?..h) */
    case 'l': /* reset mode / DEC private reset (ESC[?..l) */
        if (priv) {
            unsigned char on = (unsigned char)(f == 'h');
            switch (getp(0)) {
            case 1: /* DECCKM: application cursor keys */
                app_cursor = on;
                break;
            case 47:   /* alternate screen buffer */
            case 1047:
            case 1049:
                if (on) {
                    scr_save_screen();
                    scr_clear_all();
                } else {
                    scr_restore_screen();
                }
                break;
            default:
                break;
            }
        }
        break;
    case 'u': /* restore cursor */
        scr_gotoxy(saved_col, saved_row);
        break;
    case 'r': /* DECSTBM: set top/bottom scroll margins (not the private ?..r) */
        if (priv == 0) {
            unsigned int top = getp(0);
            unsigned int bot = getp(1);
            if (top == 0)
                top = 1;
            if (bot == 0)
                bot = SCR_ROWS;
            scr_set_region((unsigned char)(top - 1), (unsigned char)(bot - 1));
        }
        break;
    case 'L': /* IL: insert blank lines */
        n = getp(0);
        scr_insert_lines((unsigned char)(n ? n : 1));
        break;
    case 'M': /* DL: delete lines */
        n = getp(0);
        scr_delete_lines((unsigned char)(n ? n : 1));
        break;
    case '@': /* ICH: insert blank characters */
        n = getp(0);
        scr_insert_chars((unsigned char)(n ? n : 1));
        break;
    case 'P': /* DCH: delete characters */
        n = getp(0);
        scr_delete_chars((unsigned char)(n ? n : 1));
        break;
    case 'X': /* ECH: erase characters */
        n = getp(0);
        scr_erase_chars((unsigned char)(n ? n : 1));
        break;
    default: /* SGR ('m') and anything unrecognized: ignore */
        break;
    }
}

unsigned char vt100_app_cursor(void) { return app_cursor; }

void vt100_feed(char ch)
{
    unsigned char c = (unsigned char)ch & 0x7F;

    switch (state) {
    case S_NORMAL:
        if (c == 0x1B) {
            state = S_ESC;
        } else if (c == 0x0D) {
            scr_cr();
        } else if (c == 0x0A) {
            scr_lf();
        } else if (c == 0x08) {
            scr_bs();
        } else if (c == 0x09) {
            unsigned char t = (unsigned char)((scr_col() & 0xF8) + 8);
            if (t >= SCR_COLS)
                t = SCR_COLS - 1;
            scr_gotoxy(t, scr_row());
        } else if (c == 0x07) {
            beep();
        } else if (c >= 0x20 && c < 0x7F) {
            scr_put((char)c);
        }
        break;

    case S_ESC:
        if (c == '[') {
            reset_params();
            state = S_CSI;
        } else {
            switch (c) {
            case 'D': /* IND: index -- down one line, scroll at bottom */
                scr_lf();
                break;
            case 'M': /* RI: reverse index -- up one line, scroll at top */
                scr_ri();
                break;
            case 'E': /* NEL: next line -- CR + LF */
                scr_cr();
                scr_lf();
                break;
            default: /* ignore other two-byte escape sequences */
                break;
            }
            state = S_NORMAL;
        }
        break;

    case S_CSI:
        if (c >= '0' && c <= '9') {
            param[nparam] = param[nparam] * 10 + (c - '0');
        } else if (c == ';') {
            if (nparam < MAXPARAM - 1) {
                ++nparam;
            }
        } else if (c == '?') {
            priv = 1; /* private-mode marker (e.g. ESC[?25h) */
        } else if (c >= 0x40 && c <= 0x7E) {
            csi_dispatch(c); /* a final byte: act and return to normal */
            state = S_NORMAL;
        }
        /* else: an intermediate byte -> consume, stay in CSI */
        break;
    }
}
