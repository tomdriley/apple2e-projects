; crt0.s — startup shim for the C build of the VT100 terminal on DOS 3.3.
;
; The terminal is far larger than one boot sector, so (like snake) it ships as a
; DOS 3.3 binary launched with `BRUN VT100` at $0800. DOS jumps to $0800 — i.e.
; straight to the `start` label below. crt0 sets up the cc65 C software stack,
; zeroes BSS (terminal + parser state), installs a Ctrl-Reset cleanup vector,
; then calls _start (our C entry point). Returning or resetting removes the
; serial ISR, restores the prior reset vector, and falls back to DOS warm start.

        .export   _exit, _serial_old_reset
        .import   _start, _serial_isr_remove, zerobss
        .import   __STACKSTART__
        .importzp c_sp

SOFTEV  = $03F2                 ; Monitor warm-reset entry vector
PWREDUP = $03F4                 ; SOFTEV high byte EOR $A5 validates warm reset

        .bss
_serial_old_reset: .res 3       ; predecessor SOFTEV low/high + PWREDUP

        .segment  "STARTUP"

start:                            ; $0800: DOS BRUN jumps here
        lda     #<__STACKSTART__  ; init cc65 C stack pointer
        sta     c_sp
        lda     #>__STACKSTART__
        sta     c_sp+1
        jsr     zerobss           ; clear BSS (terminal + parser state)
        ldx     #2
save_reset:
        lda     SOFTEV,x
        sta     _serial_old_reset,x
        dex
        bpl     save_reset
        lda     SOFTEV+1
        eor     #$5A            ; invalidate PWREDUP before either vector byte changes
        sta     PWREDUP
        lda     #<_exit
        sta     SOFTEV
        lda     #>_exit
        sta     SOFTEV+1
        eor     #$A5
        sta     PWREDUP           ; publish a valid cleanup vector last
        jsr     _start            ; run the C program
_exit:  jsr     _serial_isr_remove
        lda     SOFTEV+1
        eor     #$5A            ; keep mixed old/new addresses invalid during restore
        sta     PWREDUP
        lda     _serial_old_reset
        sta     SOFTEV
        lda     _serial_old_reset+1
        sta     SOFTEV+1
        lda     _serial_old_reset+2
        sta     PWREDUP           ; restore predecessor validity last
        jmp     $03D0             ; if start() returns, fall back to DOS warm-start
