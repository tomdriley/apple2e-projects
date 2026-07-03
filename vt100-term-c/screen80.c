/* 80-column text screen driver using the Apple IIe's auxiliary memory directly.
 *
 * With 80STORE on, the text page $0400-$07FF is split between two banks: even
 * columns live in AUX memory, odd columns in MAIN, each row holding 40 bytes
 * per bank. The PAGE2 soft switch steers CPU access between the banks without
 * changing what is displayed. We track the cursor ourselves (so the VT100
 * cursor-position report can be exact) and drive scrolling and clears by hand.
 *
 * Screen bytes use "high" ASCII (0x80 | c); with the alternate character set on
 * that renders normal upper- and lower-case text.
 */
#include "monitor.h"
#include "screen.h"
#include "serial.h"

/* Start address of each text row inside the interleaved $0400-$07FF page. */
static const unsigned rowbase[SCR_ROWS] = {
    0x0400, 0x0480, 0x0500, 0x0580, 0x0600, 0x0680, 0x0700, 0x0780,
    0x0428, 0x04A8, 0x0528, 0x05A8, 0x0628, 0x06A8, 0x0728, 0x07A8,
    0x0450, 0x04D0, 0x0550, 0x05D0, 0x0650, 0x06D0, 0x0750, 0x07D0
};

static unsigned char cur_col;
static unsigned char cur_row;

/* Scroll region (0-based, inclusive). LF at the bottom margin and RI at the top
 * margin scroll only these rows; DECSTBM sets them. Default: the whole screen. */
static unsigned char scroll_top = 0;
static unsigned char scroll_bot = SCR_ROWS - 1;

#define BANK_AUX()  (TXTPAGE2 = 0) /* PAGE2 on : CPU sees AUX $0400-$07FF */
#define BANK_MAIN() (TXTPAGE1 = 0) /* PAGE2 off: CPU sees MAIN            */
#define BLANK       0xA0           /* high-bit space = empty cell         */
#define PERROW      40             /* bytes per row within one bank        */

/* --- Shadow copy of the screen ---------------------------------------------
 * The real text page is split across two memory banks (even columns in AUX,
 * odd in MAIN) and is selected by the PAGE2 soft switch. An external monitor
 * such as MAME's Lua cannot read both banks without toggling PAGE2, and doing
 * that asynchronously races with the running terminal and corrupts the display.
 * To make the screen observable without any bank switching, we mirror every
 * visible glyph into a plain, linear, non-banked RAM buffer at a fixed address.
 * The test harness reads this buffer directly: 80 bytes per row, 24 rows.
 *
 * It lives in the free gap above the linked image (MEMORY top is $7000) and
 * below the C stack (bottom $7800), so nothing else ever touches it. */
static unsigned char *const shadowrow[SCR_ROWS] = {
    (unsigned char *)0x7000, (unsigned char *)0x7050, (unsigned char *)0x70A0,
    (unsigned char *)0x70F0, (unsigned char *)0x7140, (unsigned char *)0x7190,
    (unsigned char *)0x71E0, (unsigned char *)0x7230, (unsigned char *)0x7280,
    (unsigned char *)0x72D0, (unsigned char *)0x7320, (unsigned char *)0x7370,
    (unsigned char *)0x73C0, (unsigned char *)0x7410, (unsigned char *)0x7460,
    (unsigned char *)0x74B0, (unsigned char *)0x7500, (unsigned char *)0x7550,
    (unsigned char *)0x75A0, (unsigned char *)0x75F0, (unsigned char *)0x7640,
    (unsigned char *)0x7690, (unsigned char *)0x76E0, (unsigned char *)0x7730
};

/* Fill shadow columns [from..last] of one row with blanks. */
static void shadow_blank_from(unsigned char row, unsigned char from)
{
    unsigned char *r = shadowrow[row];
    unsigned char  col;
    for (col = from; col < SCR_COLS; ++col) {
        r[col] = BLANK;
    }
}

/* Shift the shadow up one row within the scroll region; blank the bottom row. */
static void shadow_scroll(void)
{
    unsigned char row, i;
    for (row = scroll_top; row < scroll_bot; ++row) {
        unsigned char *d = shadowrow[row];
        unsigned char *s = shadowrow[row + 1];
        for (i = 0; i < SCR_COLS; ++i) {
            d[i] = s[i];
        }
    }
    shadow_blank_from(scroll_bot, 0);
}

/* Shift the shadow down one row within the scroll region; blank the top row. */
static void shadow_scroll_down(void)
{
    unsigned char row, i;
    for (row = scroll_bot; row != scroll_top; --row) {
        unsigned char *d = shadowrow[row];
        unsigned char *s = shadowrow[row - 1];
        for (i = 0; i < SCR_COLS; ++i) {
            d[i] = s[i];
        }
    }
    shadow_blank_from(scroll_top, 0);
}

/* Write one already-high-bit glyph to the cell at (col,row). */
static void cell_put(unsigned char col, unsigned char row, unsigned char ch)
{
    unsigned char *p = (unsigned char *)(rowbase[row] + (col >> 1));
    if (col & 1) {
        BANK_MAIN(); /* odd columns are stored in main memory */
    } else {
        BANK_AUX(); /* even columns are stored in aux memory  */
    }
    *p = ch;
    BANK_MAIN(); /* leave main banked in as the resting state */
}

/* Fill columns [from..last] of one row with blanks. */
static void row_blank_from(unsigned char row, unsigned char from)
{
    unsigned char col;
    for (col = from; col < SCR_COLS; ++col) {
        cell_put(col, row, BLANK);
    }
}

/* Fill columns [0..to] of one row with blanks, in both the video page and the
 * shadow. Used by the "erase to cursor" / "erase whole line" operations. */
static void blank_to(unsigned char row, unsigned char to)
{
    unsigned char *r = shadowrow[row];
    unsigned char  col;
    for (col = 0; col <= to; ++col) {
        cell_put(col, row, BLANK);
        r[col] = BLANK;
        if ((col & 7) == 0) {
            serial_pump(); /* drain the ACIA so the next byte isn't overrun */
        }
    }
}

/* Copy the 40 bytes of one row to another within the currently banked memory. */
static void row_copy(unsigned char dst, unsigned char src)
{
    unsigned char *d = (unsigned char *)rowbase[dst];
    unsigned char *s = (unsigned char *)rowbase[src];
    unsigned char  i;
    for (i = 0; i < PERROW; ++i) {
        d[i] = s[i];
        if ((i & 7) == 0) {
            serial_pump(); /* drain the ACIA often enough not to overrun */
        }
    }
}

static void row_blank_bank(unsigned char row)
{
    unsigned char *d = (unsigned char *)rowbase[row];
    unsigned char  i;
    for (i = 0; i < PERROW; ++i) {
        d[i] = BLANK;
        if ((i & 7) == 0) {
            serial_pump(); /* drain the ACIA often enough not to overrun */
        }
    }
}

static void scroll_up(void)
{
    unsigned char row;

    BANK_AUX(); /* shift the region up one, once per bank */
    for (row = scroll_top; row < scroll_bot; ++row) {
        row_copy(row, row + 1);
        serial_pump(); /* keep the ACIA drained while we work */
    }
    row_blank_bank(scroll_bot);

    BANK_MAIN();
    for (row = scroll_top; row < scroll_bot; ++row) {
        row_copy(row, row + 1);
        serial_pump();
    }
    row_blank_bank(scroll_bot);

    shadow_scroll();
}

static void scroll_down(void)
{
    unsigned char row;

    BANK_AUX(); /* shift the region down one, once per bank */
    for (row = scroll_bot; row != scroll_top; --row) {
        row_copy(row, row - 1);
        serial_pump();
    }
    row_blank_bank(scroll_top);

    BANK_MAIN();
    for (row = scroll_bot; row != scroll_top; --row) {
        row_copy(row, row - 1);
        serial_pump();
    }
    row_blank_bank(scroll_top);

    shadow_scroll_down();
}

void scr_init(void)
{
    SET80STORE = 0; /* PAGE2 now banks the text page for the CPU */
    SET80VID   = 0; /* 80-column video on                        */
    SETALTCHAR = 0; /* alternate character set -> real lowercase  */
    TXTSET     = 0; /* text mode                                 */
    MIXCLR     = 0; /* full screen (no mixed graphics)           */
    TXTPAGE1   = 0; /* display page 1 / main bank for the CPU    */
    scroll_top = 0;
    scroll_bot = SCR_ROWS - 1;
    scr_clear_all();
}

void scr_gotoxy(unsigned char col, unsigned char row)
{
    if (col >= SCR_COLS) {
        col = SCR_COLS - 1;
    }
    if (row >= SCR_ROWS) {
        row = SCR_ROWS - 1;
    }
    cur_col = col;
    cur_row = row;
}

void scr_cr(void) { cur_col = 0; }

void scr_lf(void)
{
    if (cur_row == scroll_bot) {
        scroll_up(); /* at the bottom margin: scroll, cursor stays */
    } else if (cur_row < SCR_ROWS - 1) {
        ++cur_row;
    }
}

void scr_ri(void) /* reverse index: up one row, scrolling down at the top margin */
{
    if (cur_row == scroll_top) {
        scroll_down();
    } else if (cur_row != 0) {
        --cur_row;
    }
}

/* DECSTBM: set the scroll region to rows [top..bot] (0-based, inclusive) and
 * home the cursor. An empty/invalid region resets to the whole screen. */
void scr_set_region(unsigned char top, unsigned char bot)
{
    if (bot >= SCR_ROWS) {
        bot = SCR_ROWS - 1;
    }
    if (top >= bot) {
        top = 0;
        bot = SCR_ROWS - 1;
    }
    scroll_top = top;
    scroll_bot = bot;
    cur_col    = 0;
    cur_row    = 0;
}

void scr_bs(void)
{
    if (cur_col != 0) {
        --cur_col;
    }
}

void scr_put(char c)
{
    unsigned char glyph = (unsigned char)c | 0x80;
    cell_put(cur_col, cur_row, glyph);
    shadowrow[cur_row][cur_col] = glyph;
    if (++cur_col >= SCR_COLS) {
        cur_col = 0;
        scr_lf();
    }
}

void scr_clear_eol(void)
{
    row_blank_from(cur_row, cur_col);
    shadow_blank_from(cur_row, cur_col);
}

void scr_clear_bol(void) { blank_to(cur_row, cur_col); }

void scr_clear_line(void) { blank_to(cur_row, SCR_COLS - 1); }

void scr_clear_bop(void)
{
    unsigned char row;
    BANK_AUX();
    for (row = 0; row < cur_row; ++row) {
        row_blank_bank(row);
        serial_pump();
    }
    BANK_MAIN();
    for (row = 0; row < cur_row; ++row) {
        row_blank_bank(row);
        serial_pump();
    }
    for (row = 0; row < cur_row; ++row) {
        shadow_blank_from(row, 0);
    }
    blank_to(cur_row, cur_col); /* partial current row: start..cursor */
}

void scr_clear_eop(void)
{
    unsigned char row;
    row_blank_from(cur_row, cur_col); /* partial current row */
    shadow_blank_from(cur_row, cur_col);
    BANK_AUX();
    for (row = cur_row + 1; row < SCR_ROWS; ++row) {
        row_blank_bank(row);
        serial_pump();
    }
    BANK_MAIN();
    for (row = cur_row + 1; row < SCR_ROWS; ++row) {
        row_blank_bank(row);
        serial_pump();
    }
    for (row = cur_row + 1; row < SCR_ROWS; ++row) {
        shadow_blank_from(row, 0);
    }
}

void scr_clear_all(void)
{
    unsigned char row;
    BANK_AUX();
    for (row = 0; row < SCR_ROWS; ++row) {
        row_blank_bank(row);
        serial_pump();
    }
    BANK_MAIN();
    for (row = 0; row < SCR_ROWS; ++row) {
        row_blank_bank(row);
        serial_pump();
    }
    for (row = 0; row < SCR_ROWS; ++row) {
        shadow_blank_from(row, 0);
    }
    cur_col = 0;
    cur_row = 0;
}

unsigned char scr_col(void) { return cur_col; }
unsigned char scr_row(void) { return cur_row; }
