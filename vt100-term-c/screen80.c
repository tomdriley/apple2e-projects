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

/* Cursor + screen state below is intentionally non-static: the conformance
 * state probe (client/conformance) reads these from RAM using the
 * addresses ld65 records in build/vt100.map. Exporting them changes no code and
 * keeps their fixed BSS addresses; it only adds them to the symbol table. */
unsigned char cur_col;
unsigned char cur_row;

/* Cursor saved across an alternate-screen switch (DECSET ?1049). */
unsigned char saved_screen_col;
unsigned char saved_screen_row;

/* Scroll region (0-based, inclusive). LF at the bottom margin and RI at the top
 * margin scroll only these rows; DECSTBM sets them. Default: the whole screen. */
unsigned char scroll_top = 0;
unsigned char scroll_bot = SCR_ROWS - 1;

/* Current character attribute: 0 = normal, 1 = inverse (SGR 7). */
unsigned char cur_attr;

#define BANK_AUX()  (TXTPAGE2 = 0) /* PAGE2 on : CPU sees AUX $0400-$07FF */
#define BANK_MAIN() (TXTPAGE1 = 0) /* PAGE2 off: CPU sees MAIN            */
#define BLANK       0xA0           /* high-bit space = empty cell         */
#define PERROW      40             /* bytes per row within one bank        */

/* --- Reading glyphs back from the video page -------------------------------
 * The text page is split across two banks (even columns in AUX, odd in MAIN),
 * selected by the PAGE2 soft switch. Rendering only ever writes the video page;
 * the few operations that need the current on-screen glyphs (inserting/deleting
 * characters within a line, and saving the screen for the alternate buffer) read
 * them back from the video page a whole row at a time (see read_row_glyphs),
 * which costs just two bank switches per row. */

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

/* Read one whole video row's 80 glyphs into buf, using just two bank switches:
 * even columns come from AUX, odd columns from MAIN. Leaves MAIN banked. buf
 * must live outside the $0400-$07FF text page (a stack or fixed-RAM buffer).
 *
 * Reading a whole row is slow in cc65 (~3 ms), which is several 9600-baud byte
 * times, so we drain the ACIA as we go: the 6551 has a single receive register
 * (no FIFO) and reception is polled, so without this a host streaming bytes
 * during a save (DECSET ?1049h reads all 24 rows back-to-back) would overrun the
 * register and silently drop the bytes that follow. serial_pump() only touches
 * the ACIA (I/O space) and the ring buffer (outside $0400-$07FF), so it is safe
 * to call with either bank paged in. */
static void read_row_glyphs(unsigned char row, unsigned char *buf)
{
    unsigned char *base = (unsigned char *)rowbase[row];
    unsigned char  i;
    BANK_AUX();
    for (i = 0; i < PERROW; ++i) {
        buf[i << 1] = base[i];
        if ((i & 7) == 0) {
            serial_pump();
        }
    }
    BANK_MAIN();
    for (i = 0; i < PERROW; ++i) {
        buf[(i << 1) + 1] = base[i];
        if ((i & 7) == 0) {
            serial_pump();
        }
    }
}

/* Fill columns [from..last] of one row with blanks. */
static void row_blank_from(unsigned char row, unsigned char from)
{
    unsigned char col;
    for (col = from; col < SCR_COLS; ++col) {
        cell_put(col, row, BLANK);
        serial_pump(); /* per-cell ACIA drain (see blank_to) */
    }
}

/* Fill columns [0..to] of one row with blanks in the video page. Used by the
 * "erase to cursor" / "erase whole line" operations. */
static void blank_to(unsigned char row, unsigned char to)
{
    unsigned char col;
    for (col = 0; col <= to; ++col) {
        cell_put(col, row, BLANK);
        /* Drain the ACIA after every cell. Each cell_put bank-switches the video
         * page, which in cc65 takes a sizeable fraction of a 9600-baud byte time,
         * so even a short erase (e.g. EL-to-BOL) can span more than one byte time.
         * The 6551 has a single RX register with no FIFO and is polled, so a
         * coarser cadence lets the byte that follows the sequence overrun and be
         * lost before the parser reads it. */
        serial_pump();
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

/* Shift video rows [top..bot] up by one (both banks); blank the bottom row. */
static void region_up(unsigned char top, unsigned char bot)
{
    unsigned char row;

    BANK_AUX();
    for (row = top; row < bot; ++row) {
        row_copy(row, row + 1);
        serial_pump();
    }
    row_blank_bank(bot);

    BANK_MAIN();
    for (row = top; row < bot; ++row) {
        row_copy(row, row + 1);
        serial_pump();
    }
    row_blank_bank(bot);
}

/* Shift video rows [top..bot] down by one (both banks); blank the top row. */
static void region_down(unsigned char top, unsigned char bot)
{
    unsigned char row;

    BANK_AUX();
    for (row = bot; row != top; --row) {
        row_copy(row, row - 1);
        serial_pump();
    }
    row_blank_bank(top);

    BANK_MAIN();
    for (row = bot; row != top; --row) {
        row_copy(row, row - 1);
        serial_pump();
    }
    row_blank_bank(top);
}

static void scroll_up(void) { region_up(scroll_top, scroll_bot); }

static void scroll_down(void) { region_down(scroll_top, scroll_bot); }

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
    cur_attr   = 0;
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

/* IL: insert n blank lines at the cursor row, pushing lines below it down to the
 * bottom margin. DL: delete n lines at the cursor, pulling lines below up. Both
 * are no-ops when the cursor is outside the scroll region. */
void scr_insert_lines(unsigned char n)
{
    unsigned char i;
    if (cur_row < scroll_top || cur_row > scroll_bot) {
        return;
    }
    for (i = 0; i < n; ++i) {
        region_down(cur_row, scroll_bot);
    }
}

void scr_delete_lines(unsigned char n)
{
    unsigned char i;
    if (cur_row < scroll_top || cur_row > scroll_bot) {
        return;
    }
    for (i = 0; i < n; ++i) {
        region_up(cur_row, scroll_bot);
    }
}

/* ICH: insert n blanks at the cursor, shifting the rest of the line right (chars
 * pushed off the right edge are lost). We snapshot the current row's glyphs from
 * the video page and shift from that snapshot. */
void scr_insert_chars(unsigned char n)
{
    unsigned char buf[SCR_COLS];
    unsigned char col;
    if (n == 0) {
        n = 1;
    }
    if (n > SCR_COLS - cur_col) {
        n = SCR_COLS - cur_col;
    }
    read_row_glyphs(cur_row, buf);
    for (col = SCR_COLS - 1; col >= cur_col + n; --col) {
        cell_put(col, cur_row, buf[col - n]);
        serial_pump(); /* per-cell ACIA drain (see blank_to) */
    }
    for (col = cur_col; col < cur_col + n; ++col) {
        cell_put(col, cur_row, BLANK);
        serial_pump(); /* per-cell ACIA drain (see blank_to) */
    }
}

/* DCH: delete n chars at the cursor, shifting the rest of the line left and
 * blanking the vacated right end. */
void scr_delete_chars(unsigned char n)
{
    unsigned char buf[SCR_COLS];
    unsigned char col;
    if (n == 0) {
        n = 1;
    }
    read_row_glyphs(cur_row, buf);
    for (col = cur_col; col + n < SCR_COLS; ++col) {
        cell_put(col, cur_row, buf[col + n]);
        serial_pump(); /* per-cell ACIA drain (see blank_to) */
    }
    for (; col < SCR_COLS; ++col) {
        cell_put(col, cur_row, BLANK);
        serial_pump(); /* per-cell ACIA drain (see blank_to) */
    }
}

/* ECH: erase n chars from the cursor without shifting the rest of the line. */
void scr_erase_chars(unsigned char n)
{
    unsigned int   end;
    unsigned char  col;
    if (n == 0) {
        n = 1;
    }
    end = (unsigned int)cur_col + n;
    if (end > SCR_COLS) {
        end = SCR_COLS;
    }
    for (col = cur_col; col < end; ++col) {
        cell_put(col, cur_row, BLANK);
        serial_pump(); /* per-cell ACIA drain (see blank_to) */
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
    unsigned char u = (unsigned char)c & 0x7F;
    unsigned char glyph;
    if (cur_attr) {
        /* Inverse video uses display codes $00-$3F. $40-$7F map to inverse
         * upper case ($00-$1F); $20-$3F (space, digits, symbols) stay put.
         * (Lower case therefore shows as inverse upper case on the non-enhanced
         * IIe character set, which lacks inverse lower case.) */
        glyph = (u >= 0x40) ? (unsigned char)(u & 0x1F) : u;
    } else {
        glyph = (unsigned char)(u | 0x80); /* normal high-bit ASCII */
    }
    cell_put(cur_col, cur_row, glyph);
    if (++cur_col >= SCR_COLS) {
        cur_col = 0;
        scr_lf();
    }
}

/* SGR attribute select: nonzero = inverse video, zero = normal. */
void scr_set_attr(unsigned char inverse) { cur_attr = inverse; }

void scr_clear_eol(void)
{
    row_blank_from(cur_row, cur_col);
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
    blank_to(cur_row, cur_col); /* partial current row: start..cursor */
}

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

/* Alternate screen (DECSET ?1049/?47/?1047). Save reads the whole 80x24 screen
 * back from the video page into a spare RAM area as display glyphs; restore
 * paints it back later. That gives a clean save/restore for full-screen apps.
 * The save area sits in free RAM below the C stack. */
#define SAVE_BASE ((unsigned char *)0x6800)

void scr_save_screen(void)
{
    unsigned char row;
    for (row = 0; row < SCR_ROWS; ++row) {
        read_row_glyphs(row, SAVE_BASE + (unsigned int)row * SCR_COLS);
        serial_pump(); /* this 1920-byte read is slow; keep RX drained */
    }
    saved_screen_col = cur_col;
    saved_screen_row = cur_row;
}

void scr_restore_screen(void)
{
    unsigned char row, col;
    for (row = 0; row < SCR_ROWS; ++row) {
        unsigned char *s = SAVE_BASE + (unsigned int)row * SCR_COLS;
        for (col = 0; col < SCR_COLS; ++col) {
            cell_put(col, row, s[col]);
            serial_pump(); /* per-cell ACIA drain (see blank_to) */
        }
    }
    cur_col = saved_screen_col;
    cur_row = saved_screen_row;
}

unsigned char scr_col(void) { return cur_col; }
unsigned char scr_row(void) { return cur_row; }
