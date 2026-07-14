.setcpu "6502"

.export _serial_irq_install
.export _serial_irq_status
.export _serial_irq_shutdown
.export _serial_irq_handler
.export _serial_irq_reset_handler

.export _serial_irq_active
.export _serial_irq_seen
.export _serial_irq_chained
.export _serial_irq_saved_irqloc
.export _serial_irq_saved_softev
.export _serial_irq_saved_pwredup
.export _serial_irq_saved_command
.export _serial_irq_saved_control

.import _ring_push
.import _rx_ring
.import _r_head
.import _r_tail
.import _ring_drop_count

IRQLOC          = $03fe
SOFTEV          = $03f2
PWREDUP         = $03f4
MON_SAVED_A     = $0045

ACIA_STATUS_IRQ = $80
ACIA_STATUS_RDRF = $08
ACIA_COMMAND_RX_IRQ = $09
ACIA_COMMAND_POLL = $0b
ACIA_CONTROL_9600 = $1e

.segment "BSS"

_serial_irq_active:        .res 1
_serial_irq_seen:          .res 1
_serial_irq_chained:       .res 1
_serial_irq_saved_irqloc:  .res 2
_serial_irq_saved_softev:  .res 2
_serial_irq_saved_pwredup: .res 1
_serial_irq_saved_command: .res 1
_serial_irq_saved_control: .res 1
saved_install_p:           .res 1
acia_base_low:             .res 1
acia_base_high:            .res 1

.segment "CODE"

.macro PATCH_OPERAND target
        sta     target+1
        stx     target+2
.endmacro

; Patch every runtime ACIA access to the detected slot. No IRQ-time code uses
; an indirect pointer or cc65 zero page.
.proc patch_acia_operands
        lda     acia_base_low
        ldx     acia_base_high
        PATCH_OPERAND ::_serial_irq_install::acia_data
        PATCH_OPERAND ::_serial_irq_status::acia_data
        PATCH_OPERAND ::_serial_irq_handler::acia_data
        PATCH_OPERAND ::irq_uninstall_common::acia_data

        clc
        adc     #$01
        bcc     status_address
        inx
status_address:
        PATCH_OPERAND ::_serial_irq_install::acia_status_reset
        PATCH_OPERAND ::_serial_irq_install::acia_status_clear
        PATCH_OPERAND ::_serial_irq_status::acia_status
        PATCH_OPERAND ::_serial_irq_handler::acia_status
        PATCH_OPERAND ::irq_uninstall_common::acia_status

        clc
        adc     #$01
        bcc     command_address
        inx
command_address:
        PATCH_OPERAND ::_serial_irq_install::acia_command_read
        PATCH_OPERAND ::_serial_irq_install::acia_command_disable
        PATCH_OPERAND ::_serial_irq_install::acia_command_enable
        PATCH_OPERAND ::irq_uninstall_common::acia_command_disable
        PATCH_OPERAND ::irq_uninstall_common::acia_command_restore

        clc
        adc     #$01
        bcc     control_address
        inx
control_address:
        PATCH_OPERAND ::_serial_irq_install::acia_control_read
        PATCH_OPERAND ::_serial_irq_install::acia_control_configure
        PATCH_OPERAND ::irq_uninstall_common::acia_control_restore
        rts
.endproc

; fastcall unsigned char serial_irq_install(unsigned base)
.proc _serial_irq_install
        sta     acia_base_low
        stx     acia_base_high
        php
        pla
        sta     saved_install_p
        sei
        cld

        jsr     patch_acia_operands

        lda     #$00
        sta     _serial_irq_active
        sta     _serial_irq_seen
        sta     _serial_irq_chained

acia_command_read:
        lda     $ffff
        sta     _serial_irq_saved_command
acia_control_read:
        lda     $ffff
        sta     _serial_irq_saved_control

        lda     IRQLOC
        sta     _serial_irq_saved_irqloc
        sta     ::_serial_irq_handler::chain_jump+1
        lda     IRQLOC+1
        sta     _serial_irq_saved_irqloc+1
        sta     ::_serial_irq_handler::chain_jump+2

        lda     SOFTEV
        sta     _serial_irq_saved_softev
        sta     ::_serial_irq_reset_handler::reset_jump+1
        lda     SOFTEV+1
        sta     _serial_irq_saved_softev+1
        sta     ::_serial_irq_reset_handler::reset_jump+2
        lda     PWREDUP
        sta     _serial_irq_saved_pwredup

        ; Mark the state restorable before exposing either vector.
        lda     #$01
        sta     _serial_irq_active

        lda     #<_serial_irq_reset_handler
        sta     SOFTEV
        lda     #>_serial_irq_reset_handler
        sta     SOFTEV+1
        eor     #$a5
        sta     PWREDUP

        lda     #<_serial_irq_handler
        sta     IRQLOC
        lda     #>_serial_irq_handler
        sta     IRQLOC+1

        ; Preserve the existing reset/configuration behavior, then enable only
        ; receiver interrupts. Transmit remains fully polled.
        lda     #$00
acia_status_reset:
        sta     $ffff
        lda     #ACIA_COMMAND_POLL
acia_command_disable:
        sta     $ffff
        lda     #ACIA_CONTROL_9600
acia_control_configure:
        sta     $ffff
acia_status_clear:
        lda     $ffff
acia_data:
        lda     $ffff
        lda     #ACIA_COMMAND_RX_IRQ
acia_command_enable:
        sta     $ffff

        cli
        lda     #$01
        ldx     #$00
        rts
.endproc

; Read status with IRQ masked. If RDR became full before this main-context
; poll, consume and publish it before restoring the caller's interrupt state.
.proc _serial_irq_status
        php
        sei
acia_status:
        lda     $ffff
        pha
        and     #ACIA_STATUS_RDRF
        beq     no_receive
acia_data:
        lda     $ffff
        jsr     _ring_push
no_receive:
        pla
        tax
        plp
        txa
        ldx     #$00
        rts
.endproc

; Apple Monitor IRQ entry has saved the interrupted A at $45 and left the
; hardware P/PC frame on stack. Preserve the exact Monitor-dispatch A/P and the
; interrupted X/Y so a foreign IRQ sees the same contract as this handler did.
.proc _serial_irq_handler
        php
        pha
        txa
        pha
        tya
        pha

acia_status:
        lda     $ffff
        tax
        and     #ACIA_STATUS_IRQ
        beq     chain
        txa
        and     #ACIA_STATUS_RDRF
        beq     handled

acia_data:
        lda     $ffff
        jsr     _ring_push
        lda     #$01
        sta     _serial_irq_seen

handled:
        pla
        tay
        pla
        tax
        pla
        plp
        lda     MON_SAVED_A
        rti

chain:
        lda     #$01
        sta     _serial_irq_chained
        pla
        tay
        pla
        tax
        pla
        plp
chain_jump:
        jmp     $ffff
.endproc

; Caller must have IRQ masked. The reset hook and normal exit share this exact
; disarm/restore order.
.proc irq_uninstall_common
        lda     _serial_irq_active
        beq     done

        lda     #ACIA_COMMAND_POLL
acia_command_disable:
        sta     $ffff
acia_status:
        lda     $ffff
acia_data:
        lda     $ffff

        lda     _serial_irq_saved_irqloc
        sta     IRQLOC
        lda     _serial_irq_saved_irqloc+1
        sta     IRQLOC+1

        lda     _serial_irq_saved_control
acia_control_restore:
        sta     $ffff
        lda     _serial_irq_saved_command
acia_command_restore:
        sta     $ffff

        lda     _serial_irq_saved_softev
        sta     SOFTEV
        lda     _serial_irq_saved_softev+1
        sta     SOFTEV+1
        lda     _serial_irq_saved_pwredup
        sta     PWREDUP

        lda     #$00
        sta     _serial_irq_active
done:
        rts
.endproc

.proc _serial_irq_shutdown
        php
        sei
        lda     _serial_irq_active
        beq     inactive
        jsr     irq_uninstall_common
        pla
        lda     saved_install_p
        pha
        plp
        rts

inactive:
        plp
        rts
.endproc

.proc _serial_irq_reset_handler
        sei
        jsr     irq_uninstall_common
reset_jump:
        jmp     $ffff
.endproc

; With 80STORE active, PAGE2 affects only $0400-$07ff. Keep every object the
; IRQ path touches above that banked text-page window.
.assert (_rx_ring >= $0800), lderror, "RX ring must be PAGE2-safe"
.assert (_r_head >= $0800), lderror, "RX head must be PAGE2-safe"
.assert (_r_tail >= $0800), lderror, "RX tail must be PAGE2-safe"
.assert (_ring_drop_count >= $0800), lderror, "RX drop state must be PAGE2-safe"
.assert (_serial_irq_active >= $0800), lderror, "IRQ state must be PAGE2-safe"
