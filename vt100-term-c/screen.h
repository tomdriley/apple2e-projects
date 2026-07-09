#ifndef SCREEN_H
#define SCREEN_H

/* 80x24 text screen interface. The VT100 parser and the terminal loop talk to
 * the display only through these calls, so the screen implementation
 * (screen80.c drives the IIe's auxiliary-memory 80-column mode directly) stays
 * isolated. Columns and rows are 0-based. */

#define SCR_COLS 80
#define SCR_ROWS 24

void          scr_init(void);                                       /* 80-col mode, clear, home */
void          scr_put(char c);                                      /* glyph at cursor, advance + wrap */
void          scr_gotoxy(unsigned char col, unsigned char row);     /* cursor to (col,row) */
void          scr_cr(void);                                         /* cursor to column 0 (same row)  */
void          scr_lf(void);                                         /* cursor down, scroll at bottom  */
void          scr_ri(void);                                         /* cursor up, scroll down at top   */
void          scr_bs(void);                                         /* cursor left (no erase)         */
void          scr_clear_eol(void);                                  /* erase cursor..end of line      */
void          scr_clear_bol(void);                                  /* erase start of line..cursor    */
void          scr_clear_line(void);                                 /* erase the whole current line   */
void          scr_clear_eop(void);                                  /* erase cursor..end of screen    */
void          scr_clear_bop(void);                                  /* erase start of screen..cursor  */
void          scr_clear_all(void);                                  /* erase everything, home cursor  */
void          scr_set_region(unsigned char top, unsigned char bot); /* DECSTBM */
void          scr_insert_lines(unsigned char n);                    /* IL: insert blank lines  */
void          scr_delete_lines(unsigned char n);                    /* DL: delete lines        */
void          scr_insert_chars(unsigned char n);                    /* ICH: insert blanks       */
void          scr_delete_chars(unsigned char n);                    /* DCH: delete chars        */
void          scr_erase_chars(unsigned char n);                     /* ECH: erase chars          */
void          scr_save_screen(void);                                /* save screen+cursor (alt screen on) */
void          scr_restore_screen(void);                             /* restore saved screen+cursor        */
void          scr_set_attr(unsigned char inverse);                  /* SGR: inverse video on/off */
unsigned char scr_col(void);                                        /* current column (0-based)       */
unsigned char scr_row(void);                                        /* current row (0-based)          */

/* Visible text cursor. The cursor is an overlay the terminal loop drives: it is
 * painted only while the input is idle and erased before any received byte is
 * rendered, so no screen operation ever runs against a screen that still has the
 * cursor painted on it. */
void scr_set_cursor_visible(unsigned char on); /* DECTCEM ?25: show/hide the cursor */
void scr_cursor_paint(void);                   /* paint the cursor at the current cell (if visible) */
void scr_cursor_erase(void);                   /* restore the true glyph under the cursor           */

#endif /* SCREEN_H */
