;----------------------------------------------------------------------
; SNAKE  for the Apple ][ / ][e  (DOS 3.3)
; ca65 / ld65 syntax.  Pure 6502, low-res mixed graphics.
;
;   Build:
;     ca65 snake.s -o snake.o
;     ld65 -C snake.cfg snake.o -o SNAKE.BIN
;   Put on a DOS 3.3 disk image at load address $0800, e.g. AppleCommander:
;     java -jar AppleCommander.jar -p mydisk.dsk SNAKE B 0x0800 < SNAKE.BIN
;   Then on the Apple:
;     ] BRUN SNAKE
;
; Controls:  Arrow keys, or W/A/S/D.   ESC or Q quits to DOS.
;
; Design summary:
;  - 40x48 low-res screen in MIXED mode: top 40 rows are the playfield,
;    bottom 4 text lines show the score / messages.
;  - The SCREEN ITSELF is the collision map. We read a cell with the ROM
;    SCRN routine before moving the head into it:
;        border colour or snake colour  -> dead
;        food colour                    -> grow + spawn new food
;        black                          -> normal move (erase the tail)
;    The one exception: moving into the cell the tail is *vacating* this
;    same step is legal (classic tail-follow), so we special-case it.
;  - The body is a ring buffer (snake_x/snake_y, 256 bytes each) indexed by
;    8-bit head/tail pointers that wrap mod 256 for free.
;----------------------------------------------------------------------

.setcpu "6502"

;----------------------------------------------------------------------
; Apple II ROM monitor entry points
;----------------------------------------------------------------------
PLOT    = $F800          ; A=vertical(0-47), Y=horizontal(0-39); colour=COLOR
SETCOL  = $F864          ; A=lo-res colour (0-15) -> COLOR
SCRN    = $F871          ; A=vertical, Y=horizontal -> returns colour in A
CLRTOP  = $F836          ; clear top 40 rows of lo-res screen to black
HOME    = $FC58          ; clear text window, home cursor
DOSWARM = $3D0           ; DOS 3.3 warm-start vector

;----------------------------------------------------------------------
; Soft switches / hardware
;----------------------------------------------------------------------
KBD     = $C000          ; bit7 set => key available; value = ASCII|$80
KBDSTRB = $C010          ; read to clear keyboard strobe
TXTCLR  = $C050          ; graphics
TXTSET  = $C051          ; text
MIXSET  = $C053          ; mixed (4 text lines at bottom)
LOWSCR  = $C054          ; display page 1
LORES   = $C056          ; lo-res

;----------------------------------------------------------------------
; Zero-page variables.  Kept in $06-$1B to avoid the lo-res ROM
; scratch ($26-$32) and the text/cursor variables ($20-$25).
;----------------------------------------------------------------------
direction = $06          ; current direction 0=up 1=down 2=left 3=right
head_x    = $07          ; head column (0-39)
head_y    = $08          ; head row    (0-39)
new_x     = $09          ; new head column
new_y     = $0A          ; new head row
head      = $0B          ; ring buffer head index
tail      = $0C          ; ring buffer tail index
length    = $0D          ; current snake length
seed      = $0E          ; PRNG state, 2 bytes ($0E/$0F)
food_x    = $10
food_y    = $11
score     = $12          ; BCD score, 2 bytes ($12/$13)
step_delay = $14         ; delay outer-loop count (smaller = faster)
grew      = $15          ; 1 if the snake ate this step
tmp       = $16
tmp2      = $17
ptr       = $18          ; print destination pointer (2 bytes $18/$19)
src       = $1A          ; print source pointer      (2 bytes $1A/$1B)

;----------------------------------------------------------------------
; Body ring buffers (uninitialised RAM, not part of the binary image)
;----------------------------------------------------------------------
snake_x = $6000          ; 256 bytes
snake_y = $6100          ; 256 bytes

;----------------------------------------------------------------------
; Constants
;----------------------------------------------------------------------
COL_BG     = $00         ; black
COL_BORDER = $09         ; orange
COL_SNAKE  = $0C         ; green
COL_FOOD   = $0D         ; yellow

INIT_DELAY = $60         ; starting delay (larger = slower)
MIN_DELAY  = $10         ; fastest the game gets
MAX_LENGTH = $F0         ; win at length 240

; Text-screen line base addresses (page 1) used here
ROW_SCORE  = $0650       ; text row 20
ROW_MSG    = $0750       ; text row 22
ROW_HINT   = $07D0       ; text row 23

;----------------------------------------------------------------------
; Macro: print a zero-terminated ASCII string at a screen address
;----------------------------------------------------------------------
.macro  print_xy dest, text
        lda     #<text
        sta     src
        lda     #>text
        sta     src+1
        lda     #<dest
        sta     ptr
        lda     #>dest
        sta     ptr+1
        jsr     print_string
.endmacro

;======================================================================
.segment "CODE"
;======================================================================

;----------------------------------------------------------------------
; entry  - DOS BRUN jumps here ($0800)
;----------------------------------------------------------------------
entry:
        jsr     title_screen

reset_game:
        jsr     init_graphics       ; switch to mixed lo-res
        jsr     CLRTOP              ; clear playfield
        jsr     clear_text_window   ; clear the 4 text lines
        jsr     draw_border
        jsr     init_snake          ; sets head/tail/length/head_x/head_y/direction
        lda     #0
        sta     score
        sta     score+1
        lda     #INIT_DELAY
        sta     step_delay
        jsr     draw_score
        print_xy ROW_HINT, hint
        jsr     place_food

;----------------------------------------------------------------------
; Main loop
;----------------------------------------------------------------------
game_loop:
        jsr     read_key            ; may update direction (or quit)

        ; --- compute the new head position ---
        ldx     direction
        lda     head_x
        clc
        adc     col_delta,x
        sta     new_x
        lda     head_y
        clc
        adc     row_delta,x
        sta     new_y

        ; --- read what's in the target cell ---
        ldy     new_x
        lda     new_y
        jsr     SCRN                ; A = colour at (new_x,new_y)

        cmp     #COL_FOOD
        beq     @grow
        cmp     #COL_BG
        beq     @move
        cmp     #COL_SNAKE
        bne     @killit             ; border or anything else = death
        ; snake-coloured: legal only if it is the cell the tail vacates
        ldx     tail
        lda     new_x
        cmp     snake_x,x
        bne     @killit
        lda     new_y
        cmp     snake_y,x
        beq     @move               ; tail vacating: legal, treat as normal move
@killit:
        jmp     @dead

@move:
        lda     #0
        sta     grew
        jmp     @advance
@grow:
        lda     #1
        sta     grew

@advance:
        ; --- erase the tail first (unless we grew) ---
        lda     grew
        bne     @skiptail
        ldx     tail
        lda     #COL_BG
        jsr     SETCOL
        ldy     snake_x,x
        lda     snake_y,x
        jsr     PLOT
        inc     tail                ; wraps mod 256
@skiptail:

        ; --- push the new head into the ring and draw it ---
        inc     head                ; wraps mod 256
        ldx     head
        lda     new_x
        sta     snake_x,x
        sta     head_x
        lda     new_y
        sta     snake_y,x
        sta     head_y
        lda     #COL_SNAKE
        jsr     SETCOL
        ldy     new_x
        lda     new_y
        jsr     PLOT

        ; --- if we ate: score, speed up, spawn food, check win ---
        lda     grew
        beq     @afterfood
        inc     length
        lda     length
        cmp     #MAX_LENGTH
        bcs     @win
        sed
        clc
        lda     score
        adc     #1
        sta     score
        lda     score+1
        adc     #0
        sta     score+1
        cld
        jsr     draw_score
        lda     step_delay
        cmp     #MIN_DELAY+1
        bcc     @nospeed
        sec
        sbc     #2
        sta     step_delay
@nospeed:
        jsr     place_food
@afterfood:
        jsr     delay
        jmp     game_loop

@dead:
        jsr     game_over
        jmp     reset_game
@win:
        jsr     win_screen
        jmp     reset_game

;----------------------------------------------------------------------
; init_graphics - mixed lo-res graphics, page 1
;----------------------------------------------------------------------
init_graphics:
        lda     TXTCLR
        lda     MIXSET
        lda     LORES
        lda     LOWSCR
        rts

;----------------------------------------------------------------------
; draw_border - orange frame around the 40x40 playfield
;----------------------------------------------------------------------
draw_border:
        lda     #COL_BORDER
        jsr     SETCOL
        ; top row 0 and bottom row 39
        lda     #0
        sta     tmp                 ; column counter
@cols:
        ldy     tmp
        lda     #0
        jsr     PLOT
        ldy     tmp
        lda     #39
        jsr     PLOT
        inc     tmp
        lda     tmp
        cmp     #40
        bne     @cols
        ; left col 0 and right col 39
        lda     #0
        sta     tmp                 ; row counter
@rows:
        ldy     #0
        lda     tmp
        jsr     PLOT
        ldy     #39
        lda     tmp
        jsr     PLOT
        inc     tmp
        lda     tmp
        cmp     #40
        bne     @rows
        rts

;----------------------------------------------------------------------
; init_snake - 4-segment snake in the middle, heading right
;----------------------------------------------------------------------
init_snake:
        lda     #18
        sta     snake_x+0
        lda     #19
        sta     snake_x+1
        lda     #20
        sta     snake_x+2
        lda     #21
        sta     snake_x+3
        lda     #20
        sta     snake_y+0
        sta     snake_y+1
        sta     snake_y+2
        sta     snake_y+3

        lda     #COL_SNAKE
        jsr     SETCOL
        ldx     #0
@pl:
        ldy     snake_x,x           ; horizontal
        lda     snake_y,x           ; vertical
        jsr     PLOT                ; PLOT preserves X
        inx
        cpx     #4
        bne     @pl

        lda     #0
        sta     tail
        lda     #3
        sta     head
        lda     #4
        sta     length
        lda     #21
        sta     head_x
        lda     #20
        sta     head_y
        lda     #3                  ; direction = right
        sta     direction
        rts

;----------------------------------------------------------------------
; place_food - random empty interior cell (cols/rows 1..38)
;----------------------------------------------------------------------
place_food:
@retry:
        jsr     next_random
        jsr     reduce_mod38
        clc
        adc     #1
        sta     food_x
        jsr     next_random
        jsr     reduce_mod38
        clc
        adc     #1
        sta     food_y
        ldy     food_x
        lda     food_y
        jsr     SCRN
        cmp     #COL_BG
        bne     @retry              ; occupied; try again
        lda     #COL_FOOD
        jsr     SETCOL
        ldy     food_x
        lda     food_y
        jsr     PLOT
        rts

;----------------------------------------------------------------------
; read_key - poll keyboard, update direction (reject 180-degree reversal)
;----------------------------------------------------------------------
read_key:
        lda     KBD
        bpl     @none               ; bit7 clear => no key waiting
        sta     tmp
        lda     KBDSTRB             ; clear strobe
        lda     tmp

        cmp     #$D7                ; W
        beq     @up
        cmp     #$F7                ; w
        beq     @up
        cmp     #$8B                ; up-arrow
        beq     @up
        cmp     #$D3                ; S
        beq     @down
        cmp     #$F3                ; s
        beq     @down
        cmp     #$8A                ; down-arrow
        beq     @down
        cmp     #$C1                ; A
        beq     @left
        cmp     #$E1                ; a
        beq     @left
        cmp     #$88                ; left-arrow
        beq     @left
        cmp     #$C4                ; D
        beq     @right
        cmp     #$E4                ; d
        beq     @right
        cmp     #$95                ; right-arrow
        beq     @right
        cmp     #$9B                ; ESC
        beq     @quit
        cmp     #$D1                ; Q
        beq     @quit
        cmp     #$F1                ; q
        beq     @quit
@none:
        rts
@up:
        lda     #0
        jmp     @apply
@down:
        lda     #1
        jmp     @apply
@left:
        lda     #2
        jmp     @apply
@right:
        lda     #3
@apply:
        sta     tmp2                ; candidate direction
        lda     direction
        eor     #1                  ; opposite of current
        cmp     tmp2
        beq     @none               ; reversal -> ignore
        lda     tmp2
        sta     direction
        rts
@quit:
        jmp     quit_game

;----------------------------------------------------------------------
; draw_score - "SCORE " + 4 BCD digits at text row 20
;----------------------------------------------------------------------
draw_score:
        ldx     #0
@lbl:
        lda     score_label,x
        beq     @digits
        ora     #$80
        sta     ROW_SCORE,x
        inx
        bne     @lbl
@digits:
        lda     score+1
        lsr
        lsr
        lsr
        lsr
        jsr     draw_digit
        lda     score+1
        and     #$0F
        jsr     draw_digit
        lda     score
        lsr
        lsr
        lsr
        lsr
        jsr     draw_digit
        lda     score
        and     #$0F
        jsr     draw_digit
        rts
draw_digit:
        ora     #$B0                ; nibble 0-9 -> '0'-'9' normal video
        sta     ROW_SCORE,x
        inx
        rts

;----------------------------------------------------------------------
; print_string - copy (src) string to (ptr), OR'ing $80, until a $00 byte
;----------------------------------------------------------------------
print_string:
        ldy     #0
@lp:
        lda     (src),y
        beq     @done
        ora     #$80
        sta     (ptr),y
        iny
        bne     @lp
@done:
        rts

;----------------------------------------------------------------------
; clear_text_window - blank the 4 bottom text lines
;----------------------------------------------------------------------
clear_text_window:
        ldx     #0
        lda     #$A0                ; space, normal video
@l:
        sta     ROW_SCORE,x
        sta     $06D0,x             ; row 21
        sta     ROW_MSG,x
        sta     ROW_HINT,x
        inx
        cpx     #40
        bne     @l
        rts

;----------------------------------------------------------------------
; clear_text - fill the whole text page with spaces
;----------------------------------------------------------------------
clear_text:
        lda     #$A0
        ldx     #0
@l:
        sta     $0400,x
        sta     $0500,x
        sta     $0600,x
        sta     $0700,x
        inx
        bne     @l
        rts

;----------------------------------------------------------------------
; delay - crude busy-wait; step_delay controls game pace
;----------------------------------------------------------------------
delay:
        ldx     step_delay
@o:
        ldy     #0
@i:
        dey
        bne     @i
        dex
        bne     @o
        rts

;----------------------------------------------------------------------
; next_random - 16-bit Galois LFSR (poly $B400), returns low byte in A
;----------------------------------------------------------------------
next_random:
        lda     seed
        ora     seed+1
        bne     @ok
        lda     #$A5                ; never let the state sit at zero
        sta     seed
@ok:
        lsr     seed+1
        ror     seed
        bcc     @nofb
        lda     seed+1
        eor     #$B4
        sta     seed+1
@nofb:
        lda     seed
        rts

;----------------------------------------------------------------------
; reduce_mod38 - A mod 38  (result 0..37)
;----------------------------------------------------------------------
reduce_mod38:
@lp:
        cmp     #38
        bcc     @done
        sbc     #38
        bcs     @lp
@done:
        rts

;----------------------------------------------------------------------
; game_over / win_screen - show message, wait for a key
;----------------------------------------------------------------------
game_over:
        print_xy ROW_MSG, msg_over
        jsr     wait_key
        rts
win_screen:
        print_xy ROW_MSG, msg_win
        jsr     wait_key
        rts

wait_key:
        lda     KBDSTRB
@w:
        lda     KBD
        bpl     @w
        lda     KBDSTRB
        rts

;----------------------------------------------------------------------
; title_screen - text-mode splash; stirs the PRNG seed while waiting
;----------------------------------------------------------------------
title_screen:
        lda     TXTSET
        lda     LOWSCR
        lda     #0                  ; full text window
        sta     $20                 ; WNDLFT
        sta     $22                 ; WNDTOP
        lda     #40
        sta     $21                 ; WNDWDTH
        lda     #24
        sta     $23                 ; WNDBTM
        jsr     clear_text

        print_xy $0688, title_1     ; row 5,  col 8
        print_xy $042D, title_2     ; row 8,  col 5
        print_xy $04AD, title_3     ; row 9,  col 5
        print_xy $062D, title_4     ; row 12, col 5
        print_xy $072D, title_5     ; row 14, col 5
        print_xy $0555, title_6     ; row 18, col 5

        lda     #$A5
        sta     seed
        lda     #$3C
        sta     seed+1
        lda     KBDSTRB
@wait:
        inc     seed                ; keypress timing seeds the RNG
        bne     @ns
        inc     seed+1
@ns:
        lda     KBD
        bpl     @wait
        lda     KBDSTRB
        rts

;----------------------------------------------------------------------
; quit_game - back to text mode and DOS
;----------------------------------------------------------------------
quit_game:
        lda     TXTSET
        lda     LOWSCR
        lda     #0
        sta     $20
        sta     $22
        lda     #40
        sta     $21
        lda     #24
        sta     $23
        jsr     HOME
        jmp     DOSWARM

;----------------------------------------------------------------------
; Data
;----------------------------------------------------------------------
col_delta:   .byte 0,0,$FF,1        ; up,down,left,right  (column delta)
row_delta:   .byte $FF,1,0,0        ; up,down,left,right  (row delta)

score_label: .byte "SCORE ",0
hint:        .byte "ARROWS/WASD  ESC=QUIT",0
msg_over:    .byte "GAME OVER - PRESS A KEY",0
msg_win:     .byte "YOU WIN! - PRESS A KEY",0

title_1:     .byte "APPLE II SNAKE",0
title_2:     .byte "EAT THE YELLOW DOTS",0
title_3:     .byte "AVOID WALLS AND YOURSELF",0
title_4:     .byte "MOVE: ARROWS OR W A S D",0
title_5:     .byte "ESC = QUIT",0
title_6:     .byte "PRESS ANY KEY TO START",0
