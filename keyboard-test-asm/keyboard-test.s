; Keyboard Test Program
;
; The Disk II boot ROM ($C600) reads track 0 / sector 0 into $0800,
; then jumps to $0801. The byte at $0800 tells the ROM how many
; sectors to load (1 = just this one).

.setcpu "65C02"

COUT1 = $FDF0   ; Character output (40-col screen, bypasses CSW vector)
HOME  = $FC58   ; Clear screen, home cursor

KEY_DATA = $C000
KEY_FLAG = $C010

TOP_BIT_BYTE = $80

.segment "CODE"

        .byte $01           ; $0800: sector count for boot ROM

start:                      ; $0801: boot ROM jumps here
        jsr HOME            ; Clear the screen

hello:
        ldx #0              ; Index into message
hello_loop:
        lda hello_message,x ; Read next mesasge character
        beq poll_key_data   ; If 0, end
        ora #TOP_BIT_BYTE   ; Set top bit of character to prevent flashing
        jsr COUT1           ; Print A to screen
        inx                 ; Move index to next character
        bne hello_loop      ; Jump to next character
poll_key_data:
        lda KEY_DATA        ; Read key, top bit is strobe
        bmi read_key        ; Top bit set, key pressed
        jmp poll_key_data   ; Nothing set, continue
read_key:
        jsr COUT1           ; Echo key to the screen
        bit KEY_FLAG        ; Write to key flag clears strobe
        jmp poll_key_data   ; Move on to next key

hello_message:
        .byte $0D
        .byte "Keyboard Test!", $0D
        .byte "--------------", $0D
        .byte "Press any key to see it echo:", $0D
        .byte $0D, 0   ; high-bit ASCII, $0D = CR
