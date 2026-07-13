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

        .export _serial_isr_install, _irq_off, _irq_on
        .import _rx_ring, _r_head, _r_tail
        .import _tx_ring, _t_head, _t_tail
        .import _xoff_sent

ST_RDRF   = $08                 ; status: receive data register full
ST_TDRE   = $10                 ; status: transmit data register empty
CMD_RX_ON = $09                 ; command: RX IRQ on, TX IRQ off, DTR/RTS asserted
CMD_TX_ON = $05                 ; command: RX IRQ on, TX IRQ on,  DTR/RTS asserted
XOFF      = $13
RING_HIGH = 192                 ; front-push XOFF once rx_ring occupancy hits this

IRQVEC  = $03FE                 ; Apple monitor user IRQ vector: it does JMP (IRQVEC)
ACCSAVE = $45                   ; monitor stashes A here before JMP (IRQVEC)

        .zeropage
aciap:  .res 2                  ; pointer to the ACIA base (set at install time)
sr_tmp: .res 1                  ; the status byte, held across the handler

        .code

; void serial_isr_install(volatile unsigned char *acia)
; The pointer arrives in A (low) / X (high) per cc65's calling convention. Stores
; the ACIA base, installs the IRQ vector, arms the receiver interrupt, and
; enables CPU interrupts. Call it after the ACIA has been reset and configured.
_serial_isr_install:
        sei
        sta     aciap
        stx     aciap+1
        lda     #<serial_isr
        sta     IRQVEC
        lda     #>serial_isr
        sta     IRQVEC+1
        ldy     #2              ; command register is at ACIA offset 2
        lda     #CMD_RX_ON      ; arm the receiver interrupt
        sta     (aciap),y
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
        txa
        pha                     ; save X
        tya
        pha                     ; save Y

        ldy     #1
        lda     (aciap),y       ; read status (clears the 6551 IRQ latch)
        sta     sr_tmp

        and     #ST_RDRF
        beq     tx_check        ; nothing received -> straight to the TX side

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
        ldy     #2
        lda     #CMD_TX_ON      ; arm TX IRQ so the XOFF actually goes out
        sta     (aciap),y

tx_check:
        lda     sr_tmp
        and     #ST_TDRE
        beq     isr_done        ; transmitter busy -> nothing to do this time
        lda     _t_head
        cmp     _t_tail
        bne     tx_send
        ldy     #2              ; tx_ring empty -> disarm TX IRQ, keep RX IRQ on
        lda     #CMD_RX_ON
        sta     (aciap),y
        jmp     isr_done

tx_send:
        ldx     _t_tail
        lda     _tx_ring,x      ; next byte to transmit
        ldy     #0
        sta     (aciap),y       ; write the data register (clears TDRE)
        inx
        stx     _t_tail

isr_done:
        pla
        tay                     ; restore Y
        pla
        tax                     ; restore X
        lda     ACCSAVE         ; restore A (monitor saved it at $45)
        rti
