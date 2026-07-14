.setcpu "6502"

.export _ring_push
.export _ring_pop

.import _rx_ring
.import _r_head
.import _r_tail
.import _ring_drop_count

.segment "CODE"

; fastcall unsigned char ring_push(unsigned char byte)
; Store the byte before publishing the producer-owned head.
.proc _ring_push
        pha
        ldx     _r_head
        inx
        cpx     _r_tail
        beq     full

        dex
        pla
        sta     _rx_ring,x
        inx
        stx     _r_head
        lda     #$01
        ldx     #$00
        rts

full:
        pla
        lda     _ring_drop_count
        cmp     #$ff
        beq     dropped
        inc     _ring_drop_count
dropped:
        lda     #$00
        tax
        rts
.endproc

; int ring_pop(void)
; Read the byte before publishing the consumer-owned tail.
.proc _ring_pop
        ldx     _r_tail
        cpx     _r_head
        beq     empty

        lda     _rx_ring,x
        inx
        stx     _r_tail
        ldx     #$00
        rts

empty:
        lda     #$ff
        tax
        rts
.endproc
