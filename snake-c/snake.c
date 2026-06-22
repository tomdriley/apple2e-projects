/*----------------------------------------------------------------------
 * SNAKE  for the Apple ][ / ][e  (DOS 3.3) — C port (cc65)
 *
 * A close transliteration of snake.s. The structure, routine names and
 * control flow deliberately mirror the hand-written assembly so the
 * generated 6502 stays recognisable. See snake.s for the full design notes;
 * the short version:
 *
 *  - 40x48 low-res screen in MIXED mode: top 40 rows are the playfield,
 *    bottom 4 text lines show the score / messages.
 *  - The SCREEN ITSELF is the collision map. We read a cell with the ROM
 *    SCRN routine before moving the head into it:
 *        border colour or snake colour  -> dead
 *        food colour                    -> grow + spawn new food
 *        black                          -> normal move (erase the tail)
 *    Moving into the cell the tail is *vacating* this same step is legal,
 *    so we special-case it.
 *  - The body is a ring buffer (snake_x/snake_y, 256 bytes each) indexed by
 *    8-bit head/tail pointers that wrap mod 256 for free.
 *
 * Build via the Makefile (cc65 -O -Cl), ship on a DOS 3.3 disk, BRUN SNAKE.
 *
 * Controls:  Arrow keys, or W/A/S/D.   ESC or Q quits to DOS.
 *--------------------------------------------------------------------*/

#include "monitor.h"

/* 256: the number of values an 8-bit index takes (and the wrap of the head/tail
 * ring indices).  Used for the ring buffers below and to tile the text page. */
#define BYTE_SPAN   0x100

/*----------------------------------------------------------------------
 * Body ring buffers (uninitialised RAM, zeroed by crt0). The original
 * pinned these at $6000/$6100; here we let the linker place them in BSS.
 *--------------------------------------------------------------------*/
static unsigned char snake_x[BYTE_SPAN];
static unsigned char snake_y[BYTE_SPAN];

/*----------------------------------------------------------------------
 * Game state (mirrors the zero-page variables DIR/HX/HY/... in snake.s)
 *--------------------------------------------------------------------*/
static unsigned char direction;    /* 0=up 1=down 2=left 3=right           */
static unsigned char head_x, head_y;   /* head column / row                */
static unsigned char new_x, new_y;     /* candidate new head column / row  */
static unsigned char head;         /* ring buffer head index               */
static unsigned char tail;         /* ring buffer tail index               */
static unsigned char length;       /* current snake length                 */
static unsigned int  seed;         /* PRNG state (16-bit LFSR)             */
static unsigned char food_x, food_y;
static unsigned int  score;        /* score, packed BCD (4 digits), like snake.s */
static unsigned char step_delay;   /* delay outer-loop count (smaller = faster)  */
static unsigned char grew;         /* 1 if the snake ate this step         */

/*----------------------------------------------------------------------
 * Lo-res colours (SETCOL values)
 *--------------------------------------------------------------------*/
#define COL_BG      0x00        /* black  */
#define COL_BORDER  0x09        /* orange */
#define COL_SNAKE   0x0C        /* green  */
#define COL_FOOD    0x0D        /* yellow */

/*----------------------------------------------------------------------
 * Playfield geometry (everything derives from GRID_SIZE)
 *--------------------------------------------------------------------*/
#define GRID_SIZE   40                  /* playfield is GRID_SIZE x GRID_SIZE cells */
#define GRID_LAST   (GRID_SIZE - 1)     /* 39: border row / column                 */
#define INTERIOR    (GRID_SIZE - 2)     /* 38: span of interior cells              */
#define CENTER      (GRID_SIZE / 2)     /* 20: middle of the playfield             */
#define BORDER      1                   /* border is one cell thick                */

/*----------------------------------------------------------------------
 * Text screen (40 columns x 24 rows of high-bit ASCII)
 *--------------------------------------------------------------------*/
#define TEXT_COLS   40
#define TEXT_ROWS   24

/*----------------------------------------------------------------------
 * Pace and win condition
 *--------------------------------------------------------------------*/
#define INIT_DELAY  0x60        /* starting delay (larger = slower)  */
#define MIN_DELAY   0x10        /* fastest the game gets             */
#define DELAY_STEP  2           /* delay removed per food eaten      */
#define MAX_LENGTH  0xF0        /* win at length 240                 */
#define START_LENGTH 4          /* initial snake length              */

/*----------------------------------------------------------------------
 * Directions (index into the row/column delta tables)
 *--------------------------------------------------------------------*/
#define DIR_UP      0
#define DIR_DOWN    1
#define DIR_LEFT    2
#define DIR_RIGHT   3
#define OPPOSITE(d) ((d) ^ 1)   /* up<->down, left<->right */

/*----------------------------------------------------------------------
 * Text / video bytes — the Apple II 40-column screen wants high-bit-set ASCII
 *--------------------------------------------------------------------*/
#define HIGH_BIT    0x80
#define VIDEO_SPACE 0xA0        /* ' ' | HIGH_BIT            */
#define VIDEO_ZERO  0xB0        /* '0' | HIGH_BIT (add 0..9) */

/*----------------------------------------------------------------------
 * Keyboard.  KBD bit 7 set means a key is waiting; the byte is the key's
 * ASCII value with the high bit set, so keycodes are computed from ASCII.
 *--------------------------------------------------------------------*/
#define KEY_READY   0x80
#define KEYCODE(c)  ((unsigned char)((c) | HIGH_BIT))
#define ARROW_UP    0x0B
#define ARROW_DOWN  0x0A
#define ARROW_LEFT  0x08
#define ARROW_RIGHT 0x15
#define ASCII_ESC   0x1B

/*----------------------------------------------------------------------
 * PRNG: 16-bit Galois LFSR
 *--------------------------------------------------------------------*/
#define LFSR_POLY   0xB400
#define SEED_START  0x3CA5      /* title-screen seed (lo=$A5, hi=$3C) */
#define SEED_RESET  0x00A5      /* used if the state ever hits zero   */

/*----------------------------------------------------------------------
 * Packed-BCD score formatting: the score is two bytes, each holding two
 * decimal digits as 4-bit nibbles.
 *--------------------------------------------------------------------*/
#define BYTE_BITS   8           /* split the 16-bit score into high/low byte */
#define NIBBLE_BITS 4           /* split a byte into its two BCD digits       */
#define LOW_NIBBLE  0x0F        /* mask for the low digit of a byte           */

/*----------------------------------------------------------------------
 * Text screen addressing.  The 24 rows are stored interleaved as three blocks
 * of eight lines: consecutive lines within a block sit TEXT_LINE_STRIDE bytes
 * apart, and the three blocks sit TEXT_BLOCK_STRIDE bytes apart.  row/col are
 * constants at every call site, so each TEXT_XY use folds to one literal
 * address (no runtime arithmetic).
 *--------------------------------------------------------------------*/
#define TEXT_PAGE1           0x0400            /* text page 1 base ($0400)        */
#define TEXT_PAGE_BYTES      (4 * BYTE_SPAN)   /* whole page = 1 KB ($0400-$07FF) */
#define TEXT_LINES_PER_BLOCK 8       /* 24 rows = 3 interleaved blocks of 8     */
#define TEXT_LINE_STRIDE     0x80    /* bytes between lines within a block      */
#define TEXT_BLOCK_STRIDE    0x28    /* bytes between blocks (= 40 columns)     */
#define TEXT_XY(row, col)                                          \
    ((unsigned char *)(TEXT_PAGE1                                  \
        + ((row) % (TEXT_LINES_PER_BLOCK)) * (TEXT_LINE_STRIDE)    \
        + ((row) / (TEXT_LINES_PER_BLOCK)) * (TEXT_BLOCK_STRIDE)   \
        + (col)))

/* Mixed lo-res mode shows graphics on top and four text lines below, the first
 * of which is row TEXT_AREA_TOP. */
#define TEXT_AREA_TOP 20
#define ROW_SCORE   TEXT_XY(TEXT_AREA_TOP + 0, 0)   /* score line   */
#define ROW_BLANK   TEXT_XY(TEXT_AREA_TOP + 1, 0)   /* blank spacer */
#define ROW_MSG     TEXT_XY(TEXT_AREA_TOP + 2, 0)   /* message line */
#define ROW_HINT    TEXT_XY(TEXT_AREA_TOP + 3, 0)   /* hint line    */

/* Monitor text-window zero-page variables */
#define WNDLFT  (*(unsigned char *)0x20)
#define WNDWDTH (*(unsigned char *)0x21)
#define WNDTOP  (*(unsigned char *)0x22)
#define WNDBTM  (*(unsigned char *)0x23)

/* Touching a soft switch toggles hardware as a side effect; the value is
 * irrelevant. cc65's optimizer drops a plain `(void)SWITCH;` read, which would
 * silently skip the graphics-mode selects and keyboard-strobe clears. Routing
 * each access through a store to a volatile sink forces it to be emitted (a
 * write to a volatile object is a side effect the compiler must preserve). */
static volatile unsigned char soft_sink;
#define TOUCH_SOFT_SWITCH(sw)  (soft_sink = (sw))

/*----------------------------------------------------------------------
 * Direction deltas (up,down,left,right) — DXTAB/DYTAB in snake.s
 *--------------------------------------------------------------------*/
static const signed char col_delta[4] = { 0, 0, -1, 1 };
static const signed char row_delta[4] = { -1, 1, 0, 0 };

/*----------------------------------------------------------------------
 * Strings (the original ORs $80 at draw time; print_string does the same)
 *--------------------------------------------------------------------*/
static const char SCORE_LABEL[] = "SCORE ";
static const char HINT[]        = "ARROWS/WASD  ESC=QUIT";
static const char MSG_OVER[]    = "GAME OVER - PRESS A KEY";
static const char MSG_WIN[]     = "YOU WIN! - PRESS A KEY";

static const char TITLE_1[] = "APPLE II SNAKE";
static const char TITLE_2[] = "EAT THE YELLOW DOTS";
static const char TITLE_3[] = "AVOID WALLS AND YOURSELF";
static const char TITLE_4[] = "MOVE: ARROWS OR W A S D";
static const char TITLE_5[] = "ESC = QUIT";
static const char TITLE_6[] = "PRESS ANY KEY TO START";

/* Where each title line is drawn — (row, col) on the 40x24 text screen.
 * A hand-tuned layout carried over from snake.s; there is no formula. */
#define TITLE_1_ROW  5
#define TITLE_1_COL  8
#define TITLE_2_ROW  8
#define TITLE_2_COL  5
#define TITLE_3_ROW  9
#define TITLE_3_COL  5
#define TITLE_4_ROW 12
#define TITLE_4_COL  5
#define TITLE_5_ROW 14
#define TITLE_5_COL  5
#define TITLE_6_ROW 18
#define TITLE_6_COL  5

/*----------------------------------------------------------------------
 * Forward declarations (lets the definitions follow snake.s's order)
 *--------------------------------------------------------------------*/
static void          init_graphics(void);
static void          draw_border(void);
static void          init_snake(void);
static void          place_food(void);
static void          read_key(void);
static void          increment_score(void);
static void          draw_score(void);
static void          print_string(unsigned char *dest, const char *text);
static void          clear_text_window(void);
static void          clear_text(void);
static void          delay(void);
static unsigned char next_random(void);
static unsigned char reduce_mod38(unsigned char a);
static void          game_over(void);
static void          win_screen(void);
static void          wait_key(void);
static void          title_screen(void);
static void          quit_game(void);
static void          play_game(void);

/*----------------------------------------------------------------------
 * ENTRY - title screen, then keep playing rounds (RESET_GAME loop)
 *--------------------------------------------------------------------*/
void start(void)
{
    title_screen();
    for (;;) {                  /* RESET_GAME: each round re-inits the board */
        play_game();
    }
}

/*----------------------------------------------------------------------
 * play_game - one round: set up, run the main loop, return on death/win
 *--------------------------------------------------------------------*/
static void play_game(void)
{
    unsigned char cell;

    init_graphics();            /* switch to mixed lo-res */
    CLRTOP();                   /* clear playfield        */
    clear_text_window();        /* clear the 4 text lines */
    draw_border();
    init_snake();               /* sets head/tail/length/head_x/head_y/direction */
    score = 0;
    step_delay = INIT_DELAY;
    draw_score();
    print_string(ROW_HINT, HINT);
    place_food();

    /* --- main loop --- */
    for (;;) {
        read_key();             /* may update direction (or quit) */

        /* --- compute the new head position --- */
        new_x = (unsigned char)(head_x + col_delta[direction]);
        new_y = (unsigned char)(head_y + row_delta[direction]);

        /* --- read what's in the target cell --- */
        cell = SCRN(new_y, new_x);

        if (cell == COL_FOOD) {
            grew = 1;
        } else if (cell == COL_BG) {
            grew = 0;
        } else if (cell == COL_SNAKE) {
            /* snake-coloured: legal only if it is the cell the tail vacates */
            if (new_x == snake_x[tail] && new_y == snake_y[tail]) {
                grew = 0;       /* tail vacating: treat as a normal move */
            } else {
                game_over();
                return;
            }
        } else {
            game_over();        /* border or anything else = death */
            return;
        }

        /* --- erase the tail first (unless we grew) --- */
        if (!grew) {
            SETCOL(COL_BG);
            PLOT(snake_y[tail], snake_x[tail]);
            ++tail;             /* wraps mod 256 */
        }

        /* --- push the new head into the ring and draw it --- */
        ++head;                 /* wraps mod 256 */
        snake_x[head] = new_x;
        head_x = new_x;
        snake_y[head] = new_y;
        head_y = new_y;
        SETCOL(COL_SNAKE);
        PLOT(new_y, new_x);

        /* --- if we ate: score, speed up, spawn food, check win --- */
        if (grew) {
            ++length;
            if (length >= MAX_LENGTH) {
                win_screen();
                return;
            }
            increment_score();
            draw_score();
            if (step_delay >= MIN_DELAY + 1) {
                step_delay -= DELAY_STEP;
            }
            place_food();
        }

        delay();
    }
}

/*----------------------------------------------------------------------
 * init_graphics - mixed lo-res graphics, page 1
 *--------------------------------------------------------------------*/
static void init_graphics(void)
{
    TOUCH_SOFT_SWITCH(TXTCLR);
    TOUCH_SOFT_SWITCH(MIXSET);
    TOUCH_SOFT_SWITCH(LORES);
    TOUCH_SOFT_SWITCH(LOWSCR);
}

/*----------------------------------------------------------------------
 * draw_border - orange frame around the GRID_SIZE x GRID_SIZE playfield
 *--------------------------------------------------------------------*/
static void draw_border(void)
{
    unsigned char i;
    SETCOL(COL_BORDER);
    for (i = 0; i < GRID_SIZE; ++i) {   /* top row 0 and bottom row 39 */
        PLOT(0, i);
        PLOT(GRID_LAST, i);
    }
    for (i = 0; i < GRID_SIZE; ++i) {   /* left col 0 and right col 39 */
        PLOT(i, 0);
        PLOT(i, GRID_LAST);
    }
}

/*----------------------------------------------------------------------
 * init_snake - 4-segment snake in the middle, heading right
 *--------------------------------------------------------------------*/
static void init_snake(void)
{
    unsigned char i;

    snake_x[0] = CENTER - 2;
    snake_x[1] = CENTER - 1;
    snake_x[2] = CENTER;
    snake_x[3] = CENTER + 1;
    snake_y[0] = CENTER;
    snake_y[1] = CENTER;
    snake_y[2] = CENTER;
    snake_y[3] = CENTER;

    SETCOL(COL_SNAKE);
    for (i = 0; i < START_LENGTH; ++i) {
        PLOT(snake_y[i], snake_x[i]);
    }

    tail = 0;
    head = START_LENGTH - 1;
    length = START_LENGTH;
    head_x = CENTER + 1;
    head_y = CENTER;
    direction = DIR_RIGHT;
}

/*----------------------------------------------------------------------
 * place_food - random empty interior cell (cols/rows 1..38)
 *--------------------------------------------------------------------*/
static void place_food(void)
{
    do {
        food_x = (unsigned char)(reduce_mod38(next_random()) + BORDER);
        food_y = (unsigned char)(reduce_mod38(next_random()) + BORDER);
    } while (SCRN(food_y, food_x) != COL_BG);   /* occupied; try again */

    SETCOL(COL_FOOD);
    PLOT(food_y, food_x);
}

/*----------------------------------------------------------------------
 * read_key - poll keyboard, update direction (reject 180-degree reversal)
 *--------------------------------------------------------------------*/
static void read_key(void)
{
    unsigned char key = KBD;
    unsigned char new_direction;

    if (!(key & KEY_READY)) {
        return;                 /* no key waiting */
    }
    TOUCH_SOFT_SWITCH(KBDSTRB); /* clear strobe */

    switch (key) {
        case KEYCODE('W'): case KEYCODE('w'): case KEYCODE(ARROW_UP):
            new_direction = DIR_UP;    break;
        case KEYCODE('S'): case KEYCODE('s'): case KEYCODE(ARROW_DOWN):
            new_direction = DIR_DOWN;  break;
        case KEYCODE('A'): case KEYCODE('a'): case KEYCODE(ARROW_LEFT):
            new_direction = DIR_LEFT;  break;
        case KEYCODE('D'): case KEYCODE('d'): case KEYCODE(ARROW_RIGHT):
            new_direction = DIR_RIGHT; break;
        case KEYCODE(ASCII_ESC): case KEYCODE('Q'): case KEYCODE('q'):
            quit_game(); return;
        default:
            return;
    }

    if ((unsigned char)OPPOSITE(direction) == new_direction) {
        return;                 /* reversal -> ignore */
    }
    direction = new_direction;
}

/*----------------------------------------------------------------------
 * increment_score - add 1 to the packed-BCD score. The 6502 decimal flag (D)
 *   has no portable C equivalent, so this one operation is inline assembly,
 *   byte-for-byte the SED/CLC/ADC/CLD block from snake.s. Keeping the whole
 *   sed..cld sequence in a single asm block is what makes it correct: the D
 *   and carry flags never cross a C statement boundary.
 *--------------------------------------------------------------------*/
static void increment_score(void)
{
    __asm__("sed");
    __asm__("clc");
    __asm__("lda %v", score);
    __asm__("adc #$01");
    __asm__("sta %v", score);
    __asm__("lda %v+1", score);
    __asm__("adc #$00");
    __asm__("sta %v+1", score);
    __asm__("cld");
}

/*----------------------------------------------------------------------
 * draw_score - "SCORE " + 4 BCD digits at text row 20 (nibble extraction,
 *   exactly like snake.s now that the score is packed BCD).
 *--------------------------------------------------------------------*/
static void draw_score(void)
{
    unsigned char hi = (unsigned char)(score >> BYTE_BITS);
    unsigned char lo = (unsigned char)score;
    unsigned char i;

    for (i = 0; SCORE_LABEL[i]; ++i) {
        ROW_SCORE[i] = SCORE_LABEL[i] | HIGH_BIT;
    }
    ROW_SCORE[i + 0] = (hi >> NIBBLE_BITS) | VIDEO_ZERO;
    ROW_SCORE[i + 1] = (hi & LOW_NIBBLE)   | VIDEO_ZERO;
    ROW_SCORE[i + 2] = (lo >> NIBBLE_BITS) | VIDEO_ZERO;
    ROW_SCORE[i + 3] = (lo & LOW_NIBBLE)   | VIDEO_ZERO;
}

/*----------------------------------------------------------------------
 * print_string - copy string to a screen address, OR'ing $80, until NUL
 *--------------------------------------------------------------------*/
static void print_string(unsigned char *dest, const char *text)
{
    unsigned char i;
    for (i = 0; text[i]; ++i) {
        dest[i] = text[i] | HIGH_BIT;
    }
}

/*----------------------------------------------------------------------
 * clear_text_window - blank the 4 bottom text lines
 *--------------------------------------------------------------------*/
static void clear_text_window(void)
{
    unsigned char i;
    for (i = 0; i < TEXT_COLS; ++i) {
        ROW_SCORE[i] = VIDEO_SPACE;
        ROW_BLANK[i] = VIDEO_SPACE;
        ROW_MSG[i]   = VIDEO_SPACE;
        ROW_HINT[i]  = VIDEO_SPACE;
    }
}

/*----------------------------------------------------------------------
 * clear_text - fill the whole text page with spaces
 *--------------------------------------------------------------------*/
static void clear_text(void)
{
    unsigned char i = 0;
    do {                        /* TEXT_PAGE_BYTES = 4 * BYTE_SPAN, so four 8-bit spans */
        ((unsigned char *)(TEXT_PAGE1 + 0 * BYTE_SPAN))[i] = VIDEO_SPACE;
        ((unsigned char *)(TEXT_PAGE1 + 1 * BYTE_SPAN))[i] = VIDEO_SPACE;
        ((unsigned char *)(TEXT_PAGE1 + 2 * BYTE_SPAN))[i] = VIDEO_SPACE;
        ((unsigned char *)(TEXT_PAGE1 + 3 * BYTE_SPAN))[i] = VIDEO_SPACE;
        ++i;
    } while (i);
}

/*----------------------------------------------------------------------
 * delay - crude busy-wait; step_delay controls game pace
 *--------------------------------------------------------------------*/
static void delay(void)
{
    unsigned char outer = step_delay;
    do {
        unsigned char inner = 0;
        do {
            --inner;
        } while (inner);
        --outer;
    } while (outer);
}

/*----------------------------------------------------------------------
 * next_random - 16-bit Galois LFSR (poly $B400), returns low byte
 *--------------------------------------------------------------------*/
static unsigned char next_random(void)
{
    unsigned char carry;

    if (seed == 0) {
        seed = SEED_RESET;      /* never let the state sit at zero */
    }
    carry = (unsigned char)(seed & 1);
    seed >>= 1;
    if (carry) {
        seed ^= LFSR_POLY;
    }
    return (unsigned char)seed;
}

/*----------------------------------------------------------------------
 * reduce_mod38 - a mod 38  (result 0..37), by repeated subtraction like
 *   snake.s (avoids pulling in cc65's division helper)
 *--------------------------------------------------------------------*/
static unsigned char reduce_mod38(unsigned char a)
{
    while (a >= INTERIOR) {
        a -= INTERIOR;
    }
    return a;
}

/*----------------------------------------------------------------------
 * game_over / win_screen - show message, wait for a key
 *--------------------------------------------------------------------*/
static void game_over(void)
{
    print_string(ROW_MSG, MSG_OVER);
    wait_key();
}

static void win_screen(void)
{
    print_string(ROW_MSG, MSG_WIN);
    wait_key();
}

static void wait_key(void)
{
    TOUCH_SOFT_SWITCH(KBDSTRB);
    while (!(KBD & KEY_READY)) {
        /* spin */
    }
    TOUCH_SOFT_SWITCH(KBDSTRB);
}

/*----------------------------------------------------------------------
 * title_screen - text-mode splash; stirs the PRNG seed while waiting
 *--------------------------------------------------------------------*/
static void title_screen(void)
{
    TOUCH_SOFT_SWITCH(TXTSET);
    TOUCH_SOFT_SWITCH(LOWSCR);
    WNDLFT = 0;                 /* full text window */
    WNDTOP = 0;
    WNDWDTH = TEXT_COLS;
    WNDBTM = TEXT_ROWS;
    clear_text();

    print_string(TEXT_XY(TITLE_1_ROW, TITLE_1_COL), TITLE_1);
    print_string(TEXT_XY(TITLE_2_ROW, TITLE_2_COL), TITLE_2);
    print_string(TEXT_XY(TITLE_3_ROW, TITLE_3_COL), TITLE_3);
    print_string(TEXT_XY(TITLE_4_ROW, TITLE_4_COL), TITLE_4);
    print_string(TEXT_XY(TITLE_5_ROW, TITLE_5_COL), TITLE_5);
    print_string(TEXT_XY(TITLE_6_ROW, TITLE_6_COL), TITLE_6);

    seed = SEED_START;          /* lo=$A5, hi=$3C */
    TOUCH_SOFT_SWITCH(KBDSTRB);
    while (!(KBD & KEY_READY)) {
        ++seed;                 /* keypress timing seeds the RNG */
    }
    TOUCH_SOFT_SWITCH(KBDSTRB);
}

/*----------------------------------------------------------------------
 * quit_game - back to text mode and DOS
 *--------------------------------------------------------------------*/
static void quit_game(void)
{
    TOUCH_SOFT_SWITCH(TXTSET);
    TOUCH_SOFT_SWITCH(LOWSCR);
    WNDLFT = 0;
    WNDTOP = 0;
    WNDWDTH = TEXT_COLS;
    WNDBTM = TEXT_ROWS;
    HOME();
    DOSWARM();                  /* never returns */
}
