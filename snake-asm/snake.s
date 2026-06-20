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
;  - The body is a ring buffer (SNAKEX/SNAKEY, 256 bytes each) indexed by
;    8-bit HEAD/TAIL pointers that wrap mod 256 for free.
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
DIR     = $06            ; current direction 0=up 1=down 2=left 3=right
HX      = $07            ; head column (0-39)
HY      = $08            ; head row    (0-39)
NX      = $09            ; new head column
NY      = $0A            ; new head row
HEAD    = $0B            ; ring buffer head index
TAIL    = $0C            ; ring buffer tail index
LENGTH  = $0D            ; current snake length
SEED    = $0E            ; PRNG state, 2 bytes ($0E/$0F)
FOODX   = $10
FOODY   = $11
SCORE   = $12            ; BCD score, 2 bytes ($12/$13)
SPEED   = $14            ; delay outer-loop count (smaller = faster)
GREW    = $15            ; 1 if the snake ate this step
TMP     = $16
TMP2    = $17
PTR     = $18            ; print destination pointer (2 bytes $18/$19)
SRC     = $1A            ; print source pointer      (2 bytes $1A/$1B)

;----------------------------------------------------------------------
; Body ring buffers (uninitialised RAM, not part of the binary image)
;----------------------------------------------------------------------
SNAKEX  = $6000          ; 256 bytes
SNAKEY  = $6100          ; 256 bytes

;----------------------------------------------------------------------
; Constants
;----------------------------------------------------------------------
COL_BG     = $00         ; black
COL_BORDER = $09         ; orange
COL_SNAKE  = $0C         ; green
COL_FOOD   = $0D         ; yellow

INITSPEED  = $60         ; starting speed (delay)
MINSPEED   = $10         ; fastest the game gets
MAXLEN     = $F0         ; win at length 240

; Text-screen line base addresses (page 1) used here
ROW_SCORE  = $0650       ; text row 20
ROW_MSG    = $0750       ; text row 22
ROW_HINT   = $07D0       ; text row 23

;----------------------------------------------------------------------
; Macro: print a zero-terminated ASCII string at a screen address
;----------------------------------------------------------------------
.macro  PRINTXY dest, str
        lda     #<str
        sta     SRC
        lda     #>str
        sta     SRC+1
        lda     #<dest
        sta     PTR
        lda     #>dest
        sta     PTR+1
        jsr     PRINTSTR
.endmacro

;======================================================================
.segment "CODE"
;======================================================================

;----------------------------------------------------------------------
; ENTRY  - DOS BRUN jumps here ($0800)
;----------------------------------------------------------------------
ENTRY:
        jsr     TITLESCREEN

RESET_GAME:
        jsr     INITGR              ; switch to mixed lo-res
        jsr     CLRTOP              ; clear playfield
        jsr     CLEARTEXTWIN        ; clear the 4 text lines
        jsr     DRAWBORDER
        jsr     INITSNAKE           ; sets HEAD/TAIL/LENGTH/HX/HY/DIR
        lda     #0
        sta     SCORE
        sta     SCORE+1
        lda     #INITSPEED
        sta     SPEED
        jsr     DRAWSCORE
        PRINTXY ROW_HINT, HINT
        jsr     PLACEFOOD

;----------------------------------------------------------------------
; Main loop
;----------------------------------------------------------------------
GAMELOOP:
        jsr     READKEY             ; may update DIR (or quit)

        ; --- compute the new head position ---
        ldx     DIR
        lda     HX
        clc
        adc     DXTAB,x
        sta     NX
        lda     HY
        clc
        adc     DYTAB,x
        sta     NY

        ; --- read what's in the target cell ---
        ldy     NX
        lda     NY
        jsr     SCRN                ; A = colour at (NX,NY)

        cmp     #COL_FOOD
        beq     @grow
        cmp     #COL_BG
        beq     @move
        cmp     #COL_SNAKE
        bne     @killit             ; border or anything else = death
        ; snake-coloured: legal only if it is the cell the tail vacates
        ldx     TAIL
        lda     NX
        cmp     SNAKEX,x
        bne     @killit
        lda     NY
        cmp     SNAKEY,x
        beq     @move               ; tail vacating: legal, treat as normal move
@killit:
        jmp     @dead

@move:
        lda     #0
        sta     GREW
        jmp     @advance
@grow:
        lda     #1
        sta     GREW

@advance:
        ; --- erase the tail first (unless we grew) ---
        lda     GREW
        bne     @skiptail
        ldx     TAIL
        lda     #COL_BG
        jsr     SETCOL
        ldy     SNAKEX,x
        lda     SNAKEY,x
        jsr     PLOT
        inc     TAIL                ; wraps mod 256
@skiptail:

        ; --- push the new head into the ring and draw it ---
        inc     HEAD                ; wraps mod 256
        ldx     HEAD
        lda     NX
        sta     SNAKEX,x
        sta     HX
        lda     NY
        sta     SNAKEY,x
        sta     HY
        lda     #COL_SNAKE
        jsr     SETCOL
        ldy     NX
        lda     NY
        jsr     PLOT

        ; --- if we ate: score, speed up, spawn food, check win ---
        lda     GREW
        beq     @afterfood
        inc     LENGTH
        lda     LENGTH
        cmp     #MAXLEN
        bcs     @win
        sed
        clc
        lda     SCORE
        adc     #1
        sta     SCORE
        lda     SCORE+1
        adc     #0
        sta     SCORE+1
        cld
        jsr     DRAWSCORE
        lda     SPEED
        cmp     #MINSPEED+1
        bcc     @nospeed
        sec
        sbc     #2
        sta     SPEED
@nospeed:
        jsr     PLACEFOOD
@afterfood:
        jsr     DELAY
        jmp     GAMELOOP

@dead:
        jsr     GAMEOVER
        jmp     RESET_GAME
@win:
        jsr     WINSCREEN
        jmp     RESET_GAME

;----------------------------------------------------------------------
; INITGR - mixed lo-res graphics, page 1
;----------------------------------------------------------------------
INITGR:
        lda     TXTCLR
        lda     MIXSET
        lda     LORES
        lda     LOWSCR
        rts

;----------------------------------------------------------------------
; DRAWBORDER - orange frame around the 40x40 playfield
;----------------------------------------------------------------------
DRAWBORDER:
        lda     #COL_BORDER
        jsr     SETCOL
        ; top row 0 and bottom row 39
        lda     #0
        sta     TMP                 ; column counter
@cols:
        ldy     TMP
        lda     #0
        jsr     PLOT
        ldy     TMP
        lda     #39
        jsr     PLOT
        inc     TMP
        lda     TMP
        cmp     #40
        bne     @cols
        ; left col 0 and right col 39
        lda     #0
        sta     TMP                 ; row counter
@rows:
        ldy     #0
        lda     TMP
        jsr     PLOT
        ldy     #39
        lda     TMP
        jsr     PLOT
        inc     TMP
        lda     TMP
        cmp     #40
        bne     @rows
        rts

;----------------------------------------------------------------------
; INITSNAKE - 4-segment snake in the middle, heading right
;----------------------------------------------------------------------
INITSNAKE:
        lda     #18
        sta     SNAKEX+0
        lda     #19
        sta     SNAKEX+1
        lda     #20
        sta     SNAKEX+2
        lda     #21
        sta     SNAKEX+3
        lda     #20
        sta     SNAKEY+0
        sta     SNAKEY+1
        sta     SNAKEY+2
        sta     SNAKEY+3

        lda     #COL_SNAKE
        jsr     SETCOL
        ldx     #0
@pl:
        ldy     SNAKEX,x            ; horizontal
        lda     SNAKEY,x            ; vertical
        jsr     PLOT                ; PLOT preserves X
        inx
        cpx     #4
        bne     @pl

        lda     #0
        sta     TAIL
        lda     #3
        sta     HEAD
        lda     #4
        sta     LENGTH
        lda     #21
        sta     HX
        lda     #20
        sta     HY
        lda     #3                  ; direction = right
        sta     DIR
        rts

;----------------------------------------------------------------------
; PLACEFOOD - random empty interior cell (cols/rows 1..38)
;----------------------------------------------------------------------
PLACEFOOD:
@retry:
        jsr     PRNG
        jsr     REDUCE38
        clc
        adc     #1
        sta     FOODX
        jsr     PRNG
        jsr     REDUCE38
        clc
        adc     #1
        sta     FOODY
        ldy     FOODX
        lda     FOODY
        jsr     SCRN
        cmp     #COL_BG
        bne     @retry              ; occupied; try again
        lda     #COL_FOOD
        jsr     SETCOL
        ldy     FOODX
        lda     FOODY
        jsr     PLOT
        rts

;----------------------------------------------------------------------
; READKEY - poll keyboard, update DIR (reject 180-degree reversal)
;----------------------------------------------------------------------
READKEY:
        lda     KBD
        bpl     @none               ; bit7 clear => no key waiting
        sta     TMP
        lda     KBDSTRB             ; clear strobe
        lda     TMP

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
        sta     TMP2                ; candidate direction
        lda     DIR
        eor     #1                  ; opposite of current
        cmp     TMP2
        beq     @none               ; reversal -> ignore
        lda     TMP2
        sta     DIR
        rts
@quit:
        jmp     QUITGAME

;----------------------------------------------------------------------
; DRAWSCORE - "SCORE " + 4 BCD digits at text row 20
;----------------------------------------------------------------------
DRAWSCORE:
        ldx     #0
@lbl:
        lda     SCORELBL,x
        beq     @digits
        ora     #$80
        sta     ROW_SCORE,x
        inx
        bne     @lbl
@digits:
        lda     SCORE+1
        lsr
        lsr
        lsr
        lsr
        jsr     DIGIT
        lda     SCORE+1
        and     #$0F
        jsr     DIGIT
        lda     SCORE
        lsr
        lsr
        lsr
        lsr
        jsr     DIGIT
        lda     SCORE
        and     #$0F
        jsr     DIGIT
        rts
DIGIT:
        ora     #$B0                ; nibble 0-9 -> '0'-'9' normal video
        sta     ROW_SCORE,x
        inx
        rts

;----------------------------------------------------------------------
; PRINTSTR - copy (SRC) string to (PTR), OR'ing $80, until a $00 byte
;----------------------------------------------------------------------
PRINTSTR:
        ldy     #0
@lp:
        lda     (SRC),y
        beq     @done
        ora     #$80
        sta     (PTR),y
        iny
        bne     @lp
@done:
        rts

;----------------------------------------------------------------------
; CLEARTEXTWIN - blank the 4 bottom text lines
;----------------------------------------------------------------------
CLEARTEXTWIN:
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
; CLRTEXT - fill the whole text page with spaces
;----------------------------------------------------------------------
CLRTEXT:
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
; DELAY - crude busy-wait; SPEED controls game pace
;----------------------------------------------------------------------
DELAY:
        ldx     SPEED
@o:
        ldy     #0
@i:
        dey
        bne     @i
        dex
        bne     @o
        rts

;----------------------------------------------------------------------
; PRNG - 16-bit Galois LFSR (poly $B400), returns low byte in A
;----------------------------------------------------------------------
PRNG:
        lda     SEED
        ora     SEED+1
        bne     @ok
        lda     #$A5                ; never let the state sit at zero
        sta     SEED
@ok:
        lsr     SEED+1
        ror     SEED
        bcc     @nofb
        lda     SEED+1
        eor     #$B4
        sta     SEED+1
@nofb:
        lda     SEED
        rts

;----------------------------------------------------------------------
; REDUCE38 - A mod 38  (result 0..37)
;----------------------------------------------------------------------
REDUCE38:
@lp:
        cmp     #38
        bcc     @done
        sbc     #38
        bcs     @lp
@done:
        rts

;----------------------------------------------------------------------
; GAMEOVER / WINSCREEN - show message, wait for a key
;----------------------------------------------------------------------
GAMEOVER:
        PRINTXY ROW_MSG, MSG_OVER
        jsr     WAITKEY
        rts
WINSCREEN:
        PRINTXY ROW_MSG, MSG_WIN
        jsr     WAITKEY
        rts

WAITKEY:
        lda     KBDSTRB
@w:
        lda     KBD
        bpl     @w
        lda     KBDSTRB
        rts

;----------------------------------------------------------------------
; TITLESCREEN - text-mode splash; stirs the PRNG seed while waiting
;----------------------------------------------------------------------
TITLESCREEN:
        lda     TXTSET
        lda     LOWSCR
        lda     #0                  ; full text window
        sta     $20                 ; WNDLFT
        sta     $22                 ; WNDTOP
        lda     #40
        sta     $21                 ; WNDWDTH
        lda     #24
        sta     $23                 ; WNDBTM
        jsr     CLRTEXT

        PRINTXY $0688, T1           ; row 5,  col 8
        PRINTXY $042D, T2           ; row 8,  col 5
        PRINTXY $04AD, T3           ; row 9,  col 5
        PRINTXY $062D, T4           ; row 12, col 5
        PRINTXY $072D, T5           ; row 14, col 5
        PRINTXY $0555, T6           ; row 18, col 5

        lda     #$A5
        sta     SEED
        lda     #$3C
        sta     SEED+1
        lda     KBDSTRB
@wait:
        inc     SEED                ; keypress timing seeds the RNG
        bne     @ns
        inc     SEED+1
@ns:
        lda     KBD
        bpl     @wait
        lda     KBDSTRB
        rts

;----------------------------------------------------------------------
; QUITGAME - back to text mode and DOS
;----------------------------------------------------------------------
QUITGAME:
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
DXTAB:    .byte 0,0,$FF,1           ; up,down,left,right  (column delta)
DYTAB:    .byte $FF,1,0,0           ; up,down,left,right  (row delta)

SCORELBL: .byte "SCORE ",0
HINT:     .byte "ARROWS/WASD  ESC=QUIT",0
MSG_OVER: .byte "GAME OVER - PRESS A KEY",0
MSG_WIN:  .byte "YOU WIN! - PRESS A KEY",0

T1:       .byte "APPLE II SNAKE",0
T2:       .byte "EAT THE YELLOW DOTS",0
T3:       .byte "AVOID WALLS AND YOURSELF",0
T4:       .byte "MOVE: ARROWS OR W A S D",0
T5:       .byte "ESC = QUIT",0
T6:       .byte "PRESS ANY KEY TO START",0
