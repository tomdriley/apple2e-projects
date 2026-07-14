/* Host-side unit test for the 6551 receive ring buffer.
 *
 * The RX ring now lives in its own module (../ring.c + ../ring.h), so this test
 * LINKS the real implementation directly instead of mirroring it -- the buggy
 * drift risk of a hand-copied duplicate is gone. It is built with cc65's
 * sim6502 target and run under the sim65 simulator, so the ring is compiled by
 * the *real* cc65 compiler and gets cc65's exact integer/wraparound semantics
 * (the whole point of the bug).
 *
 * It exercises two rings:
 *   - "real"   : the shipped ring module -- a count-free single-producer/single-
 *                consumer FIFO. head/tail are unsigned char (free mod-256 wrap)
 *                and one slot is kept as a sentinel, so `head == tail` means
 *                empty and `(head + 1) == tail` means full (255 bytes usable).
 *                No occupancy counter exists to overflow.
 *   - "buggy"  : a local model of the original pre-fix logic (an unsigned char
 *                occupancy count, so the `count != RING_SIZE` guard was always
 *                true and a full ring was never detected). Modelled as an
 *                unconditional write so we do not re-introduce the "comparison
 *                is always true" warning.
 * Asserting on both proves the scenarios have teeth: the real ring must keep
 * FIFO integrity across an overflow while the buggy ring must corrupt it.
 *
 * main() returns the number of failed checks (0 == success) so `make test`
 * fails the build if the ring logic regresses.
 */
#include <stdio.h>

#include "../ring.h"

/* ---- Real ring: thin aliases onto the shipped ring.c module ------------- */
static void f_reset(void) { ring_reset(); }

/* Returns 1 if the byte was accepted, 0 if the ring was full (byte dropped). */
static unsigned char f_put(unsigned char b) { return ring_push(b); }

/* Returns the popped byte, or -1 when empty. */
static int f_get(void) { return ring_pop(); }

/* Occupancy: the single-byte pointer distance. */
static unsigned char f_ready(void) { return ring_count(); }

/* ---- Buggy ring: the pre-fix behaviour, for a teeth check --------------- */
static unsigned char b_ring[RING_SIZE];
static unsigned char b_head, b_tail;
static unsigned char b_count; /* the bug: an unsigned char cannot hold 256 */

static void b_reset(void)
{
    b_head  = 0;
    b_tail  = 0;
    b_count = 0;
}

static void b_put(unsigned char b)
{
    /* Pre-fix behaviour: the `b_count != RING_SIZE` guard was always true for
     * an unsigned char count, so a full ring was never detected -- every byte
     * was written and ++count wrapped 255->0. Modelled as an unconditional
     * write to reproduce that runtime behaviour without re-triggering the
     * "comparison is always true" warning the fix removed. */
    b_ring[b_head++] = b;
    ++b_count; /* unsigned char: wraps 255 -> 0, corrupting the count */
}

static int b_get(void)
{
    unsigned char b;
    if (b_count == 0) {
        return -1;
    }
    b = b_ring[b_tail++];
    --b_count;
    return (int)b;
}

static unsigned char b_ready(void) { return b_count; }

/* ---- Test harness ------------------------------------------------------- */
static int failures = 0;

static void check(const char *name, int cond)
{
    if (cond) {
        printf("  ok   %s\n", name);
    } else {
        printf("  FAIL %s\n", name);
        ++failures;
    }
}

int main(void)
{
    unsigned      i;
    unsigned      accepted;
    int           g;
    int           ok;
    unsigned char v;
    int           first;

    printf("ring_test: 6551 RX ring buffer\n");

    /* --- Scenario A: overflow integrity (the reported bug) --------------- *
     * Push 300 bytes into a 256-slot ring that keeps one sentinel slot, so at
     * most RING_SIZE-1 (255) are accepted (values 0..254) and the surplus is
     * dropped. A correct ring keeps the first 255 in FIFO order and never
     * overwrites unread data. */
    f_reset();
    accepted = 0;
    for (i = 0; i < 300; ++i) {
        accepted += f_put((unsigned char)i);
    }
    check("fixed: accepts RING_SIZE-1 (sentinel slot), drops the overflow",
        accepted == 255 && f_ready() == 255);
    check("fixed: full detected via (head+1)==tail", f_put(0xAA) == 0);
    ok = 1;
    for (i = 0; i < 255; ++i) {
        g = f_get();
        if (g != (int)(unsigned char)i) {
            ok = 0;
        }
    }
    check("fixed: FIFO integrity preserved across overflow (bytes 0..254)", ok);
    check("fixed: empty after draining, no phantom bytes", f_get() == -1 && f_ready() == 0);
    check("fixed: drop telemetry counts rejected newest bytes", ring_drop_count == 46);

    /* Same scenario on the buggy ring must misbehave, proving the checks bite. */
    b_reset();
    for (i = 0; i < 300; ++i) {
        v = (unsigned char)(i < 256 ? i : 0xAA);
        b_put(v);
    }
    check("buggy: count corrupted by wrap (full never detected)", b_count == (unsigned char)300);
    first = b_get(); /* slot 0 was overwritten when head lapped tail */
    check("buggy: FIFO corrupted after overflow (early byte overwritten)", first == 0xAA);

    /* --- Scenario B: a full ring must read as full, never as empty ------- */
    f_reset();
    for (i = 0; i < 256; ++i) {
        (void)f_put((unsigned char)i);
    }
    check("fixed: rx_ready reports 255 when full (never truncates to 0)", f_ready() == 255);
    b_reset();
    for (i = 0; i < 256; ++i) {
        b_put((unsigned char)i);
    }
    check("buggy: rx_ready truncates full ring to 0 (the reported symptom)", b_ready() == 0);

    /* --- Scenario C: ordinary FIFO order, no overflow -------------------- */
    f_reset();
    for (i = 0; i < 200; ++i) {
        (void)f_put((unsigned char)(i * 7));
    }
    ok = 1;
    for (i = 0; i < 200; ++i) {
        if (f_get() != (int)((i * 7) & 0xFF)) {
            ok = 0;
        }
    }
    check("fixed: FIFO order preserved without overflow", ok && f_ready() == 0);

    /* --- Scenario D: head/tail wrap mod 256 keeps FIFO order ------------- *
     * Push 200, pop 100, push 150 more. head/tail cross the 256 boundary but
     * occupancy stays below the 255-byte capacity, so nothing is dropped. */
    f_reset();
    for (i = 0; i < 200; ++i) {
        (void)f_put((unsigned char)i);
    }
    for (i = 0; i < 100; ++i) {
        (void)f_get();
    }
    for (i = 200; i < 350; ++i) {
        (void)f_put((unsigned char)i);
    }
    ok = 1;
    for (i = 100; i < 350; ++i) {
        if (f_get() != (int)(i & 0xFF)) {
            ok = 0;
        }
    }
    check("fixed: head/tail wrap mod 256 keeps FIFO order", ok && f_ready() == 0);

    /* --- Scenario E: drop telemetry saturates and reset clears it ---------- */
    f_reset();
    for (i = 0; i < 255; ++i) {
        (void)f_put((unsigned char)i);
    }
    for (i = 0; i < 300; ++i) {
        (void)f_put(0xA5);
    }
    check("fixed: drop telemetry saturates at 255", ring_drop_count == 255);
    f_reset();
    check("fixed: reset clears drop telemetry", ring_drop_count == 0);

    if (failures == 0) {
        printf("ring_test: PASS\n");
    } else {
        printf("ring_test: FAIL (%d)\n", failures);
    }
    return failures;
}
