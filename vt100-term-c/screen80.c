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

#define BANK_AUX()  (TXTPAGE2 = 0) /* PAGE2 on : CPU sees AUX $0400-$07FF */
#define BANK_MAIN() (TXTPAGE1 = 0) /* PAGE2 off: CPU sees MAIN            */
#define BLANK       0xA0           /* high-bit space = empty cell         */
#define PERROW      40             /* bytes per row within one bank        */

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

    BANK_AUX(); /* shift every row up one, once per bank */
    for (row = 0; row < SCR_ROWS - 1; ++row) {
        row_copy(row, row + 1);
        serial_pump(); /* keep the ACIA drained while we work */
    }
    row_blank_bank(SCR_ROWS - 1);

    BANK_MAIN();
    for (row = 0; row < SCR_ROWS - 1; ++row) {
        row_copy(row, row + 1);
        serial_pump();
    }
    row_blank_bank(SCR_ROWS - 1);
}

void scr_init(void)
{
    SET80STORE = 0; /* PAGE2 now banks the text page for the CPU */
    SET80VID   = 0; /* 80-column video on                        */
    SETALTCHAR = 0; /* alternate character set -> real lowercase  */
    TXTSET     = 0; /* text mode                                 */
    MIXCLR     = 0; /* full screen (no mixed graphics)           */
    TXTPAGE1   = 0; /* display page 1 / main bank for the CPU    */
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
    if (cur_row >= SCR_ROWS - 1) {
        scroll_up();
        cur_row = SCR_ROWS - 1;
    } else {
        ++cur_row;
    }
}

void scr_bs(void)
{
    if (cur_col != 0) {
        --cur_col;
    }
}

void scr_put(char c)
{
    cell_put(cur_col, cur_row, (unsigned char)c | 0x80);
    if (++cur_col >= SCR_COLS) {
        cur_col = 0;
        scr_lf();
    }
}

void scr_clear_eol(void) { row_blank_from(cur_row, cur_col); }

void scr_clear_eop(void)
{
    unsigned char row;
    row_blank_from(cur_row, cur_col); /* partial current row */
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
    cur_col = 0;
    cur_row = 0;
}

unsigned char scr_col(void) { return cur_col; }
unsigned char scr_row(void) { return cur_row; }
