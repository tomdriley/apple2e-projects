/* Host-side unit test for the 6551 receive ring buffer.
 *
 * The real ring lives in serial.c as file-scope statics wired directly to the
 * 6551's memory-mapped registers, so it cannot be linked and exercised in
 * isolation. This test therefore MIRRORS serial.c's ring FIFO -- the same
 * types, guards and pop/push order -- and runs it on the host through cc65's
 * sim6502 target + the sim65 simulator, so it is compiled by the *real* cc65
 * compiler and gets cc65's exact integer/wraparound semantics (the whole point
 * of the bug). Keep this mirror in sync with serial.c if the ring changes; a
 * follow-up (relevant to future interrupt-driven RX) is to extract the ring into a
 * shared module this test can link directly instead of mirroring.
 *
 * It exercises both variants:
 *   - "fixed"  : r_count widened to unsigned, guard `r_count < RING_SIZE`.
 *   - "buggy"  : the pre-fix logic (unsigned char count, full never detected),
 *                modelled as an unconditional write so we do not re-introduce
 *                the "comparison is always true" warning the fix removed.
 * Asserting on both proves the scenarios have teeth: the fixed ring must keep
 * FIFO integrity across an overflow while the buggy ring must corrupt it.
 *
 * main() returns the number of failed checks (0 == success) so `make test`
 * fails the build if the ring logic regresses.
 */
#include <stdio.h>

#define RING_SIZE 256

/* ---- Fixed ring: mirrors serial.c's fixed ring ------------------------- */
static unsigned char f_ring[RING_SIZE];
static unsigned char f_head, f_tail;
static unsigned      f_count; /* wider than a byte: a full ring holds 256 */

static void f_reset(void)
{
    f_head  = 0;
    f_tail  = 0;
    f_count = 0;
}

/* Returns 1 if the byte was accepted, 0 if the ring was full (byte dropped). */
static unsigned char f_put(unsigned char b)
{
    if (f_count < RING_SIZE) { /* mirrors serial.c: r_count < RING_SIZE */
        f_ring[f_head++] = b;
        ++f_count;
        return 1;
    }
    return 0;
}

/* Returns the popped byte, or -1 when empty (mirrors serial_getch). */
static int f_get(void)
{
    unsigned char b;
    if (f_count == 0) {
        return -1;
    }
    b = f_ring[f_tail++];
    --f_count;
    return (int)b;
}

/* Mirrors serial_rx_ready: clamp the lone value 256 so it never truncates to 0. */
static unsigned char f_ready(void) { return (unsigned char)(f_count > 255 ? 255 : f_count); }

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
     * Push 300 bytes into a 256-slot ring. The first 256 carry values 0..255;
     * the surplus 44 carry a sentinel (0xAA). A correct ring keeps the first
     * 256 in FIFO order and drops the surplus. */
    f_reset();
    accepted = 0;
    for (i = 0; i < 300; ++i) {
        v = (unsigned char)(i < 256 ? i : 0xAA);
        accepted += f_put(v);
    }
    check("fixed: accepts exactly RING_SIZE, drops the overflow", accepted == 256 && f_count == 256);
    check("fixed: full ring reports ready (rx_ready clamps 256->255)", f_ready() == 255);
    ok = 1;
    for (i = 0; i < 256; ++i) {
        g = f_get();
        if (g != (int)i) {
            ok = 0;
        }
    }
    check("fixed: FIFO integrity preserved across overflow (bytes 0..255)", ok);
    check("fixed: empty after draining, no phantom bytes", f_get() == -1 && f_count == 0);

    /* Same scenario on the buggy ring must misbehave, proving the checks bite. */
    b_reset();
    for (i = 0; i < 300; ++i) {
        v = (unsigned char)(i < 256 ? i : 0xAA);
        b_put(v);
    }
    check("buggy: count corrupted by wrap (full never detected)", b_count == (unsigned char)300);
    first = b_get(); /* slot 0 was overwritten when head lapped tail */
    check("buggy: FIFO corrupted after overflow (early byte overwritten)", first == 0xAA);

    /* --- Scenario B: rx_ready must not truncate a full ring to "empty" --- */
    f_reset();
    for (i = 0; i < 256; ++i) {
        (void)f_put((unsigned char)i);
    }
    check("fixed: rx_ready nonzero when full (no 256->0 truncation)", f_ready() != 0);
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
    check("fixed: FIFO order preserved without overflow", ok && f_count == 0);

    /* --- Scenario D: head/tail wrap mod 256 keeps FIFO order ------------- *
     * Push 200, pop 100, push 150 more (head/tail cross the 256 boundary but
     * occupancy stays < RING_SIZE, so nothing is dropped). */
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
    check("fixed: head/tail wrap mod 256 keeps FIFO order", ok && f_count == 0);

    if (failures == 0) {
        printf("ring_test: PASS\n");
    } else {
        printf("ring_test: FAIL (%d)\n", failures);
    }
    return failures;
}
