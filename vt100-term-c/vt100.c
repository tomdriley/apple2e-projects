/* VT100/ANSI escape-sequence parser.
 *
 * Bytes arriving over serial are fed one at a time to vt100_feed(). Printable
 * characters go to the screen; a small state machine (NORMAL -> ESC -> CSI)
 * recognizes the common cursor, erase, and report sequences. Private and
 * unrecognized sequences (colors/SGR, ESC[?25h, ...) are consumed and ignored
 * so they never corrupt the display. OSC/DCS/PM/APC strings (ESC], ESC P, ...)
 * are swallowed until their ST or BEL terminator so their payloads never print.
 * Cursor-position reports (ESC[6n) are answered over the serial line, which is
 * what the automated test checks.
 */
#include "vt100.h"
#include "monitor.h"
#include "screen.h"
#include "serial.h"

#define S_NORMAL  0
#define S_ESC     1
#define S_CSI     2
#define S_CHARSET 3
#define S_STR     4 /* OSC/DCS/PM/APC string: swallow until ST or BEL */
#define S_STR_ESC 5 /* saw ESC inside a string; a following '\' ends ST */
#define MAXPARAM  4

/* Answerback message returned for ENQ (0x05). This terminal has no
 * user-programmable answerback memory, so it uses a short, fixed, printable
 * identity string (no control bytes). */
#define ANSWERBACK "A2VT100"

static unsigned char state;
static unsigned int  param[MAXPARAM];
static unsigned char nparam;           /* index of the parameter currently being built */
static unsigned char priv;             /* a private marker ('?') was seen in this CSI */
static unsigned char csi_gt;           /* a '>' or '=' DA marker was seen in this CSI */
static unsigned char csi_intermediate; /* one 0x20-0x2F byte; 0xFF if multiple */
/* Non-static so the conformance state probe can read them from RAM;
 * exporting them changes no code, only the symbol table. */
unsigned char        app_cursor;   /* DECCKM: application cursor keys enabled */
unsigned char        attr_inverse; /* SGR 7: inverse video currently selected */
static unsigned char charset_g0;   /* the charset select (ESC(x) targets G0 */
static unsigned char g0_special;   /* G0 is DEC special graphics (line drawing) */
static unsigned char saved_col, saved_row;

void vt100_init(void)
{
    state        = S_NORMAL;
    saved_col    = 0;
    saved_row    = 0;
    app_cursor   = 0;
    attr_inverse = 0;
    g0_special   = 0;
}

static void reset_params(void)
{
    unsigned char i;
    for (i = 0; i < MAXPARAM; ++i) {
        param[i] = 0;
    }
    nparam           = 0;
    priv             = 0;
    csi_gt           = 0;
    csi_intermediate = 0;
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

static void send_answerback(void) /* ENQ (0x05): emit the fixed answerback */
{
    static const char msg[] = ANSWERBACK;
    unsigned char     i;
    for (i = 0; msg[i] != '\0'; ++i) {
        serial_put(msg[i]);
    }
}

static void csi_dispatch(unsigned char f)
{
    unsigned char row = scr_row();
    unsigned char col = scr_col();
    unsigned int  n;

    if (csi_intermediate != 0) {
        if (csi_intermediate == '!' && f == 'p') {
            /* DECSTR soft reset: modes/attrs/region, without clearing the screen. */
            app_cursor   = 0;
            attr_inverse = 0;
            g0_special   = 0;
            scr_set_attr(0);
            scr_set_cursor_visible(1);
            scr_set_region(0, SCR_ROWS - 1);
        }
        /* Other intermediate sequences, including unsupported DECRQM '$p',
         * are consumed without aliasing a command with the same final byte. */
        return;
    }

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
            /* ED/DECSED param 2 erases the whole display but, per ECMA-48
               8.3.39, must not move the active position. scr_clear_all()
               homes the cursor (correct for RIS/init/alt-screen), so restore
               the pre-erase position here to erase in place. */
            scr_clear_all();
            scr_gotoxy(col, row);
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
    case 'c': /* device attributes */
        if (csi_gt == '>') {
            /* secondary DA (ESC[>c): identify as a VT220-family terminal, version 0 */
            serial_put(0x1B);
            serial_put('[');
            serial_put('>');
            serial_put('1');
            serial_put(';');
            serial_put('0');
            serial_put(';');
            serial_put('0');
            serial_put('c');
        } else if (csi_gt == 0) {
            /* primary DA (ESC[c): identify as a base VT100 */
            serial_put(0x1B);
            serial_put('[');
            serial_put('?');
            serial_put('1');
            serial_put(';');
            serial_put('0');
            serial_put('c');
        }
        /* tertiary DA (ESC[=c): consumed silently -- no simple reply exists */
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
            case 25: /* DECTCEM: show (h) / hide (l) the visible text cursor */
                scr_set_cursor_visible(on);
                break;
            case 47: /* alternate screen buffer */
            case 1047:
            case 1049:
                if (on) {
                    scr_save_screen();
                    scr_clear_all();
                } else {
                    scr_restore_screen();
                }
                /* Reset SGR inverse on both enter and exit: a pager may leave
                 * inverse video selected when it quits, which would otherwise
                 * bleed into the restored shell. */
                attr_inverse = 0;
                scr_set_attr(0);
                break;
            default:
                break;
            }
        }
        break;
    case 'u': /* restore cursor */
        scr_gotoxy(saved_col, saved_row);
        break;
    case 'm': { /* SGR: we render inverse video; other attributes are ignored */
        unsigned char k;
        for (k = 0; k <= nparam; ++k) {
            unsigned int p = getp(k);
            if (p == 38 || p == 48) {
                /* extended color: skip its arguments so a color index or RGB
                 * component is never misread as an attribute (e.g. 38;5;7 must
                 * not turn on inverse video). */
                if (getp((unsigned char)(k + 1)) == 2) {
                    k = (unsigned char)(k + 4); /* 38;2;r;g;b */
                } else if (getp((unsigned char)(k + 1)) == 5) {
                    k = (unsigned char)(k + 2); /* 38;5;idx */
                } else {
                    k = (unsigned char)(k + 1);
                }
            } else if (p == 0 || p == 27) {
                attr_inverse = 0; /* reset / inverse off */
            } else if (p == 7) {
                attr_inverse = 1; /* inverse on */
            }
        }
        scr_set_attr(attr_inverse);
        break;
    }
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
    default: /* colors/bold and any unrecognized final byte: ignore */
        break;
    }
}

unsigned char vt100_app_cursor(void) { return app_cursor; }

/* Map DEC special-graphics (line-drawing) codes to the closest ASCII the IIe's
 * character set can show. Real box-drawing glyphs would need MouseText, which the
 * non-enhanced apple2e lacks, so corners/tees/cross collapse to '+'. */
static char dec_graphic(unsigned char c)
{
    switch (c) {
    case 'q':
        return '-'; /* horizontal line */
    case 'x':
        return '|'; /* vertical line */
    case 'j':
    case 'k':
    case 'l':
    case 'm':
    case 'n':
    case 't':
    case 'u':
    case 'v':
    case 'w':
        return '+'; /* corners, tees, cross */
    default:
        return (char)c;
    }
}

void vt100_feed(char ch)
{
    unsigned char c = (unsigned char)ch & 0x7F;

    switch (state) {
    case S_NORMAL:
        if (c == 0x1B) {
            state = S_ESC;
        } else if (c == 0x0D) {
            scr_cr();
        } else if (c == 0x0A || c == 0x0B || c == 0x0C) {
            scr_lf(); /* LF, VT, and FF all index down one line */
        } else if (c == 0x08) {
            scr_bs();
        } else if (c == 0x09) {
            unsigned char t = (unsigned char)((scr_col() & 0xF8) + 8);
            if (t >= SCR_COLS)
                t = SCR_COLS - 1;
            scr_gotoxy(t, scr_row());
        } else if (c == 0x07) {
            beep();
        } else if (c == 0x05) {
            send_answerback(); /* ENQ: return the answerback over the host link */
        } else if (c >= 0x20 && c < 0x7F) {
            scr_put(g0_special ? dec_graphic(c) : (char)c);
        }
        break;

    case S_ESC:
        if (c == '[') {
            reset_params();
            state = S_CSI;
        } else if (c == '(' || c == ')') {
            charset_g0 = (unsigned char)(c == '('); /* which G-set to designate */
            state      = S_CHARSET;
        } else if (c == ']' || c == 'P' || c == '^' || c == '_') {
            /* OSC (]), DCS (P), PM (^) and APC (_) introduce a string that runs
             * until ST (ESC \) or, for OSC, BEL. Swallow the payload so it never
             * prints as literal text. */
            state = S_STR;
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
            case 'c': /* RIS: hard reset to the initial state */
                app_cursor   = 0;
                attr_inverse = 0;
                g0_special   = 0;
                saved_col    = 0;
                saved_row    = 0;
                scr_set_attr(0);
                scr_set_cursor_visible(1);
                scr_set_region(0, SCR_ROWS - 1);
                scr_clear_all();
                break;
            default: /* ignore other two-byte escape sequences */
                break;
            }
            state = S_NORMAL;
        }
        break;

    case S_CHARSET: /* the byte after ESC( or ESC): 0 = line drawing, B = ASCII */
        if (charset_g0) {
            if (c == '0') {
                g0_special = 1;
            } else if (c == 'B') {
                g0_special = 0;
            }
        }
        state = S_NORMAL;
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
        } else if (c == '>' || c == '=') {
            csi_gt = c; /* secondary/tertiary DA marker (ESC[>c / ESC[=c) */
        } else if (c >= 0x20 && c <= 0x2F) {
            /* Preserve a single CSI intermediate so commands sharing a final
             * byte (for example DECSTR !p and DECRQM $p) stay distinct. */
            if (csi_intermediate == 0) {
                csi_intermediate = c;
            } else {
                csi_intermediate = 0xFF; /* multiple intermediates: unsupported */
            }
        } else if (c >= 0x40 && c <= 0x7E) {
            csi_dispatch(c); /* a final byte: act and return to normal */
            state = S_NORMAL;
        }
        /* else: consume an unsupported parameter/control byte, stay in CSI */
        break;

    case S_STR: /* OSC/DCS/PM/APC payload: swallow until ST (ESC \) or BEL */
        if (c == 0x07) {
            state = S_NORMAL; /* BEL terminates (common OSC form) */
        } else if (c == 0x1B) {
            state = S_STR_ESC; /* possible ST introducer */
        }
        /* else: consume the payload byte and stay in the string */
        break;

    case S_STR_ESC: /* saw ESC in a string; '\' completes ST, anything else ends it too */
        state = S_NORMAL;
        break;
    }
}
