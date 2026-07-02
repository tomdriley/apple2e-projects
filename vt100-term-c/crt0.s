; crt0.s — startup shim for the C build of the VT100 terminal on DOS 3.3.
;
; The terminal is far larger than one boot sector, so (like snake) it ships as a
; DOS 3.3 binary launched with `BRUN VT100` at $0800. DOS jumps to $0800 — i.e.
; straight to the `start` label below. crt0 sets up the cc65 C software stack,
; zeroes BSS (terminal + parser state), then calls _start (our C entry point).
; If start() ever returns, fall back to the DOS warm-start vector.

        .export   _exit
        .import   _start, zerobss
        .import   __STACKSTART__
        .importzp c_sp

        .segment  "STARTUP"

start:                            ; $0800: DOS BRUN jumps here
        lda     #<__STACKSTART__  ; init cc65 C stack pointer
        sta     c_sp
        lda     #>__STACKSTART__
        sta     c_sp+1
        jsr     zerobss           ; clear BSS (terminal + parser state)
        jsr     _start            ; run the C program
_exit:  jmp     $03D0             ; if start() returns, fall back to DOS warm-start
