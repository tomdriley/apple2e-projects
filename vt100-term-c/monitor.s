; ---------------------------------------------------------------------------
; monitor.s — Apple IIe ROM entry points and soft-switch / register addresses.
;
; The single registry of hardware addresses for the VT100 terminal. Each
; `sym = addr` + `.export` line is a pure link-time symbol and emits NO bytes
; into the binary. To add an address, put it here and add the matching extern
; declaration in monitor.h.
;
; Note: cc65 mangles C names with a leading underscore (C `COUT` -> `_COUT`).
; ---------------------------------------------------------------------------

; --- Monitor ROM routines (called via jsr) — declared as functions in C ----
        .export _HOME, _COUT, _COUT1
_HOME   = $FC58                 ; clear text screen + home cursor (40-col)
_COUT   = $FDED                 ; output char in A via the CSW hook
_COUT1  = $FDF0                 ; output char in A straight to the 40-col screen

; --- Keyboard --------------------------------------------------------------
        .export _KBD, _KBDSTRB
_KBD     = $C000                ; read: bit7 = key ready, bits6-0 = ASCII
_KBDSTRB = $C010                ; any access clears the keyboard strobe

; --- 80-column / video soft switches (write to trigger) --------------------
        .export _SET80STORE, _SET80VID, _SETALTCHAR
        .export _TXTSET, _MIXCLR, _TXTPAGE1, _TXTPAGE2
_SET80STORE = $C001             ; 80STORE on: PAGE2 banks $0400-$07FF for the CPU
_SET80VID   = $C00D             ; 80-column video on
_SETALTCHAR = $C00F             ; alternate character set on (lowercase)
_TXTSET     = $C051             ; text mode
_MIXCLR     = $C052             ; full screen (no mixed text/graphics)
_TXTPAGE1   = $C054             ; PAGE2 off -> main bank (CPU) / display page 1
_TXTPAGE2   = $C055             ; PAGE2 on  -> aux bank (CPU access; 80STORE on)

; --- Misc ------------------------------------------------------------------
        .export _MOTOR_OFF, _SPKR
_MOTOR_OFF = $C0E8              ; drive motor off (slot 6)
_SPKR      = $C030             ; toggle the speaker (read to click)

; --- Super Serial Card 6551 ACIA in slot 2 ($C0A8-$C0AB) -------------------
        .export _ACIA_DATA, _ACIA_STATUS, _ACIA_COMMAND, _ACIA_CONTROL
_ACIA_DATA    = $C0A8           ; read received byte / write byte to send
_ACIA_STATUS  = $C0A9           ; read status; any write does a soft reset
_ACIA_COMMAND = $C0AA           ; command: parity, echo, IRQ, DTR/RTS
_ACIA_CONTROL = $C0AB           ; control: baud rate, word length, stop bits
