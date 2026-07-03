#ifndef SCREEN_H
#define SCREEN_H

/* 80x24 text screen interface. The VT100 parser and the terminal loop talk to
 * the display only through these calls, so the screen implementation
 * (screen80.c drives the IIe's auxiliary-memory 80-column mode directly) stays
 * isolated. Columns and rows are 0-based. */

#define SCR_COLS 80
#define SCR_ROWS 24

void          scr_init(void);  /* 80-col mode, clear, home */
void          scr_put(char c); /* glyph at cursor, advance + wrap */
void          scr_gotoxy(unsigned char col, unsigned char row);
void          scr_cr(void);        /* cursor to column 0 (same row)  */
void          scr_lf(void);        /* cursor down, scroll at bottom  */
void          scr_bs(void);        /* cursor left (no erase)         */
void          scr_clear_eol(void); /* erase cursor..end of line      */
void          scr_clear_bol(void); /* erase start of line..cursor    */
void          scr_clear_line(void);/* erase the whole current line   */
void          scr_clear_eop(void); /* erase cursor..end of screen    */
void          scr_clear_bop(void); /* erase start of screen..cursor  */
void          scr_clear_all(void); /* erase everything, home cursor  */
unsigned char scr_col(void);       /* current column (0-based)       */
unsigned char scr_row(void);       /* current row (0-based)          */

#endif /* SCREEN_H */
