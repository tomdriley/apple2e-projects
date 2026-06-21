; crt0.s — bare-metal startup for a self-booting Apple II cc65 program.
;
; The Disk II boot ROM reads sector(s) into $0800 and jumps to $0801.
; The byte at $0800 is the sector count for the boot ROM. crt0 sets up the
; cc65 C software stack, zeroes BSS, then calls _start (our C entry point).

        .export   _exit
        .import   _start
        .import   __STACKSTART__
        .importzp c_sp

        .segment  "STARTUP"

        .byte     $01             ; $0800: sector count for boot ROM (1 = this sector)

start:                            ; $0801: boot ROM jumps here
        lda     #<__STACKSTART__  ; init cc65 C stack pointer
        sta     c_sp
        lda     #>__STACKSTART__
        sta     c_sp+1
        jsr     _start            ; run the C program
        ; NOTE: BSS is empty for this program, so no zerobss call is needed.
        ; If you add uninitialized globals/statics, import & jsr zerobss here.
_exit:  jmp     _exit             ; spin forever
