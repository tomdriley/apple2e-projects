; Self-booting Apple IIe "Hello, World!"
;
; The Disk II boot ROM ($C600) reads track 0 / sector 0 into $0800,
; then jumps to $0801. The byte at $0800 tells the ROM how many
; sectors to load (1 = just this one).

.setcpu "65C02"

COUT1 = $FDF0   ; Character output (40-col screen, bypasses CSW vector)
HOME  = $FC58   ; Clear screen, home cursor

.segment "CODE"

        .byte $01           ; $0800: sector count for boot ROM

start:                      ; $0801: boot ROM jumps here
        jsr HOME
        ldx #0
loop:
        lda message,x
        beq done
        jsr COUT1
        inx
        bne loop
done:
        jmp done            ; spin forever, message stays on screen

message:
        .byte "HELLO, WORLD!", $8D, 0   ; high-bit ASCII, $8D = CR
