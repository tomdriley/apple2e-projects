; serial_isr.s — interrupt-driven 6551 ACIA service for the VT100 terminal.
;
; The Super Serial Card's 6551 shares one IRQ line for both directions. This
; handler, installed at the Apple monitor's user IRQ vector ($03FE), services
; both:
;   RX: reads the data register into rx_ring the instant a byte arrives, so
;       reception no longer depends on the main loop pumping. At the high-water
;       mark it front-pushes XOFF onto tx_ring (jumping any queued output).
;   TX: on TDRE it transmits the next tx_ring byte; when tx_ring drains it
;       disables the transmit interrupt (a 6551 TX IRQ re-asserts forever while
;       TDRE stays set, so it must be masked once there is nothing left to send).
;
; The RX ring lives in ring.c and the TX ring in serial.c (both BSS, above
; $0800), outside the $0400-$07FF text page that 80STORE/PAGE2 banks. This
; handler therefore touches only the rings and the ACIA (I/O space) and MUST
; NOT disturb the PAGE2 soft switch, so the interrupted render path
; (mid-cell_put, with AUX banked) resumes with its bank intact. That is why no
; RDPAGE2 sample/restore is needed here.
;
; cc65's -Cl static locals are non-reentrant, so this handler is pure assembly
; and calls no C code. It owns its zero-page scratch (aciap, sr_tmp).

        .export _serial_isr_install, _serial_isr_remove, _irq_off, _irq_on
        .export _serial_old_irq, _serial_chain_valid, _serial_isr_installed
        .import _rx_ring, _r_head, _r_tail
        .import _tx_ring, _t_head, _t_tail
        .import _xoff_sent, _tx_irq_active

ST_RDRF   = $08                 ; status: receive data register full
ST_TDRE   = $10                 ; status: transmit data register empty
ST_IRQ    = $80                 ; status: this ACIA is asserting IRQ
CMD_RX_ON = $09                 ; command: RX IRQ on, TX IRQ off, DTR/RTS asserted
CMD_TX_ON = $05                 ; command: RX IRQ on, TX IRQ on,  DTR/RTS asserted
CMD_IRQ_OFF = $0A               ; command: RX/TX off, DTR deasserted
XOFF      = $13
RING_HIGH = 192                 ; front-push XOFF once rx_ring occupancy hits this

IRQVEC  = $03FE                 ; Apple monitor user IRQ vector: it does JMP (IRQVEC)
ACCSAVE = $45                   ; monitor stashes A here before JMP (IRQVEC)

        .zeropage
aciap:  .res 2                  ; pointer to the ACIA base (set at install time)
sr_tmp: .res 1                  ; the status byte, held across the handler
irq_owned: .res 1               ; nonzero once this entry services the ACIA

        .bss
_serial_old_irq:       .res 2   ; Monitor IRQLOC saved before installation
_serial_chain_valid:   .res 1   ; old vector is usable (and not our own entry)
_serial_isr_installed: .res 1

        .code

; void serial_isr_install(volatile unsigned char *acia)
; The pointer arrives in A (low) / X (high) per cc65's calling convention. Stores
; the ACIA base, installs the IRQ vector, arms the receiver interrupt, and
; enables CPU interrupts. Call it after the ACIA has been reset and configured.
_serial_isr_install:
        sei
        sta     aciap
        stx     aciap+1
        sta     tx_data_store+1     ; patch STA $ffff to this slot's data register
        stx     tx_data_store+2     ; (absolute STA has no destructive dummy read)
        lda     _serial_isr_installed
        bne     publish_vector
        lda     IRQVEC
        sta     _serial_old_irq
        lda     IRQVEC+1
        sta     _serial_old_irq+1
        lda     _serial_old_irq
        cmp     #<serial_isr
        bne     save_chain
        lda     _serial_old_irq+1
        cmp     #>serial_isr
        bne     save_chain
        ; A prior Ctrl-Reset/reload can leave IRQLOC pointing at this same code
        ; after BSS was cleared. The predecessor is then unknowable; refusing to
        ; self-chain is safer than recursive stack exhaustion.
        lda     #0
        sta     _serial_old_irq
        sta     _serial_old_irq+1
        sta     _serial_chain_valid
        beq     mark_installed
save_chain:
        lda     _serial_old_irq
        sta     chain_target+1
        lda     _serial_old_irq+1
        sta     chain_target+2
        lda     #1
        sta     _serial_chain_valid
mark_installed:
        lda     #1
        sta     _serial_isr_installed
publish_vector:
        lda     #<serial_isr
        sta     IRQVEC
        lda     #>serial_isr
        sta     IRQVEC+1
        ldy     #2              ; command register is at ACIA offset 2
        lda     #CMD_RX_ON      ; arm the receiver interrupt
        sta     (aciap),y
        cli
        rts

; Disable this driver's ACIA interrupts and restore the predecessor IRQLOC.
; crt0 calls this before its DOS warm start if start() ever returns.
_serial_isr_remove:
        sei
        lda     _serial_isr_installed
        beq     remove_done
        ldy     #2
        lda     #CMD_IRQ_OFF
        sta     (aciap),y
        dey
        lda     (aciap),y       ; clear any modem-status IRQ latched before DTR rose
        lda     _serial_chain_valid
        beq     remove_mark
        lda     _serial_old_irq
        sta     IRQVEC
        lda     _serial_old_irq+1
        sta     IRQVEC+1
remove_mark:
        lda     #0
        sta     _serial_isr_installed
remove_done:
        cli
        rts

; void irq_off(void) / void irq_on(void) — brief critical sections for the C
; side, which shares the command register (arm) and xoff_sent with this ISR.
_irq_off:
        sei
        rts

_irq_on:
        cli
        rts

; --- the interrupt handler -------------------------------------------------
; Entered via JMP (IRQVEC); the monitor has already stashed A at $45. X and Y are
; saved here; A is restored from $45 on exit, then RTI.
serial_isr:
        php                     ; preserve live entry flags for a chained handler
        txa
        pha                     ; save X
        tya
        pha                     ; save Y
        lda     #0
        sta     irq_owned

isr_service:
        ldy     #1
        lda     (aciap),y       ; read status (clears the 6551 IRQ latch)
        sta     sr_tmp
        and     #ST_IRQ
        beq     rx_check
        lda     #1
        sta     irq_owned       ; includes acknowledged DCD/DSR-only changes

rx_check:
        lda     sr_tmp
        and     #ST_RDRF
        beq     tx_check        ; nothing received -> straight to the TX side
        lda     #1
        sta     irq_owned

        ; --- RX: pull the received byte into rx_ring --------------------
        ldy     #0
        lda     (aciap),y       ; read data (clears RDRF)
        ldx     _r_head
        inx                     ; X = r_head + 1
        cpx     _r_tail
        beq     rx_hiwater      ; ring full -> drop the byte
        ldy     _r_head
        sta     _rx_ring,y
        stx     _r_head         ; publish the new head

rx_hiwater:
        ; avail = r_head - r_tail; if >= RING_HIGH and not already throttled,
        ; front-push XOFF so the host stops before rx_ring overflows.
        lda     _r_head
        sec
        sbc     _r_tail
        cmp     #RING_HIGH
        bcc     tx_check
        lda     _xoff_sent
        bne     tx_check
        ldx     _t_tail
        dex                     ; X = t_tail - 1 (the slot just ahead of the queue)
        cpx     _t_head
        beq     tx_check        ; tx_ring full -> cannot inject, skip
        lda     #XOFF
        sta     _tx_ring,x
        stx     _t_tail         ; XOFF is now the next byte to transmit
        lda     #1
        sta     _xoff_sent
        lda     _tx_irq_active
        bne     tx_check             ; an existing TX burst is already armed
        lda     #1
        sta     _tx_irq_active
        ldy     #2
        lda     #CMD_TX_ON      ; arm TX IRQ so the XOFF actually goes out
        sta     (aciap),y

tx_check:
        lda     _tx_irq_active
        beq     resample_if_rx   ; idle TDRE is not a TX interrupt source
        lda     sr_tmp
        and     #ST_TDRE
        beq     resample_if_rx   ; transmitter busy -> no TX work this sample
        lda     #1
        sta     irq_owned
        lda     _t_head
        cmp     _t_tail
        bne     tx_send
        lda     #0
        sta     _tx_irq_active
        ldy     #2              ; tx_ring empty -> disarm TX IRQ, keep RX IRQ on
        lda     #CMD_RX_ON
        sta     (aciap),y
        jmp     isr_service     ; catch a source which arrived during service

tx_send:
        ldx     _t_tail
        lda     _tx_ring,x      ; next byte to transmit
        ; STA (zp),Y performs a dummy read before its write on the 6502. Against
        ; the ACIA data register that read clears RDRF and can silently consume a
        ; byte which completed after our status sample. This absolute operand is
        ; patched by serial_isr_install for the detected SSC slot.
tx_data_store:
        sta     $ffff           ; write the data register (clears TDRE)
        inx
        stx     _t_tail
        jmp     isr_service

resample_if_rx:
        lda     sr_tmp
        and     #ST_RDRF
        beq     dispatch_done
        jmp     isr_service     ; RX work may have overlapped a new ACIA source

dispatch_done:
        lda     irq_owned
        bne     isr_done
        lda     _serial_chain_valid
        beq     isr_done
        pla
        tay                     ; predecessor sees the original Y
        pla
        tax                     ; predecessor sees the original X
        lda     ACCSAVE         ; and the A saved by the Monitor IRQ entry
        plp                     ; restore live entry flags after loading A
chain_target:
        jmp     $ffff           ; operand patched from the predecessor IRQLOC

isr_done:
        pla
        tay                     ; restore Y
        pla
        tax                     ; restore X
        plp                     ; discard/restore the saved live entry flags
        lda     ACCSAVE         ; restore A (monitor saved it at $45)
        rti
