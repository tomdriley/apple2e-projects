/* Host-side unit tests for the interrupt-driven 6551 ring buffers.
 *
 * The RX tests link the real ring.c implementation. The TX
 * tests model serial.c/serial_isr.s operations because the hardware ISR cannot
 * run under sim65; they cover FIFO order, urgent XOFF front-push, wraparound,
 * full handling, and the near-full enqueue/front-push interleaving that makes
 * serial_put's short interrupt-masked critical section necessary.
 *
 * The RX section exercises two variants:
 *   - "real"   : the shipped design -- a count-free single-producer/single-
 *                consumer FIFO. head/tail are unsigned char (free mod-256 wrap)
 *                and one slot is kept as a sentinel, so `head == tail` means
 *                empty and `(head + 1) == tail` means full (255 bytes usable).
 *                No occupancy counter exists to overflow.
 *   - "buggy"  : the original pre-fix logic (an unsigned char occupancy count,
 *                so the `count != RING_SIZE` guard was always true and a full
 *                ring was never detected). Modelled as an unconditional write so
 *                we do not re-introduce the "comparison is always true" warning.
 * Asserting on both proves the scenarios have teeth: the real ring must keep
 * FIFO integrity across an overflow while the buggy ring must corrupt it.
 *
 * main() returns the number of failed checks (0 == success) so `make test`
 * fails the build if the ring logic regresses.
 */
#include <stdio.h>

#include "../ring.h"

/* ---- Real RX ring: aliases onto the shipped ring.c module --------------- */
static void          f_reset(void) { ring_reset(); }
static unsigned char f_put(unsigned char b) { return ring_push(b); }
static int           f_get(void) { return ring_pop(); }
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

/* ---- TX ring model: main enqueue + ISR consume/front-push ---------------- */
#define XON      0x11
#define XOFF     0x13
#define RING_LOW 64

static unsigned char t_ring[RING_SIZE];
static unsigned char t_head, t_tail;

static void t_reset(void)
{
    t_head = 0;
    t_tail = 0;
}

static unsigned char t_put(unsigned char b)
{
    unsigned char nh;
    nh = (unsigned char)(t_head + 1);
    if (nh == t_tail) {
        return 0;
    }
    t_ring[t_head] = b;
    t_head         = nh;
    return 1;
}

static unsigned char t_push_front(unsigned char b)
{
    unsigned char nt;
    nt = (unsigned char)(t_tail - 1);
    if (nt == t_head) {
        return 0;
    }
    t_ring[nt] = b;
    t_tail     = nt;
    return 1;
}

static int t_get(void)
{
    unsigned char b;
    if (t_head == t_tail) {
        return -1;
    }
    b      = t_ring[t_tail];
    t_tail = (unsigned char)(t_tail + 1);
    return (int)b;
}

static unsigned char t_count(void) { return (unsigned char)(t_head - t_tail); }

/* One retry of serial.c's resume_rx operation. A full queue or renewed RX
 * pressure must leave paused set; only claiming an XON slot clears it. */
static unsigned char t_try_resume(unsigned char *paused, unsigned char rx_count)
{
    if (*paused == 0 || rx_count > RING_LOW || t_put(XON) == 0) {
        return 0;
    }
    *paused = 0;
    return 1;
}

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
    unsigned char reserved;
    unsigned char paused;
    int           first;

    printf("ring_test: 6551 RX/TX ring buffers\n");

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

    /* --- TX Scenario E: XOFF jumps queued output ------------------------- */
    t_reset();
    (void)t_put('A');
    (void)t_put('B');
    check("tx: front-pushed XOFF becomes the next byte",
        t_push_front(XOFF) != 0 && t_get() == XOFF);
    check("tx: queued output retains FIFO order after XOFF",
        t_get() == 'A' && t_get() == 'B' && t_get() == -1);

    /* An empty queue at tail zero exercises the front-push wrap 0 -> 255. */
    t_reset();
    check("tx: front-push wraps tail and works on an empty queue",
        t_push_front(XOFF) != 0 && t_count() == 1 && t_get() == XOFF && t_count() == 0);

    /* --- TX Scenario F: sentinel handling at capacity -------------------- */
    t_reset();
    for (i = 0; i < 255; ++i) {
        (void)t_put((unsigned char)i);
    }
    check("tx: full queue rejects normal enqueue", t_count() == 255 && t_put(0xAA) == 0);
    check("tx: full queue rejects XOFF without overwriting data",
        t_push_front(XOFF) == 0 && t_count() == 255 && t_get() == 0);

    /* --- TX Scenario G: the near-full enqueue/front-push race ------------ *
     * At 254 bytes, either side may claim the final sentinel-adjacent slot,
     * but not both. serial_put masks IRQs around its capacity recheck, store
     * and head publish, making these the only two possible interleavings. */
    t_reset();
    for (i = 0; i < 254; ++i) {
        (void)t_put((unsigned char)i);
    }
    check("tx: main-first enqueue leaves no slot for XOFF",
        t_put(0xAA) != 0 && t_push_front(XOFF) == 0 && t_count() == 255);

    t_reset();
    for (i = 0; i < 254; ++i) {
        (void)t_put((unsigned char)i);
    }
    check("tx: ISR-first XOFF leaves no slot for main enqueue",
        t_push_front(XOFF) != 0 && t_put(0xAA) == 0 && t_count() == 255);
    check("tx: ISR-first queue sends XOFF before freeing main's slot",
        t_get() == XOFF && t_put(0xAA) != 0 && t_count() == 255);

    /* Teeth check: the old unmasked check/store/publish sequence lets XOFF
     * claim the sentinel after main has checked it, then publishes head ==
     * tail and makes a full queue look empty. */
    t_reset();
    for (i = 0; i < 254; ++i) {
        (void)t_put((unsigned char)i);
    }
    reserved = (unsigned char)(t_head + 1);
    (void)t_push_front(XOFF);
    t_ring[t_head] = 0xAA;
    t_head         = reserved;
    check("tx teeth: unmasked near-full interleaving collapses head onto tail",
        t_head == t_tail && t_count() == 0);

    /* --- TX Scenario H: XON owns a queue slot before clearing pause ------- */
    t_reset();
    for (i = 0; i < 255; ++i) {
        (void)t_put((unsigned char)i);
    }
    paused = 1;
    check("tx: full queue defers XON without clearing throttled state",
        t_try_resume(&paused, RING_LOW) == 0 && paused == 1 && t_count() == 255);
    (void)t_get();
    check("tx: XON claim and throttled-state clear are one operation",
        t_try_resume(&paused, RING_LOW) != 0 && paused == 0 && t_count() == 255
            && t_ring[(unsigned char)(t_head - 1)] == XON);
    t_reset();
    paused = 1;
    check("tx: renewed RX pressure keeps the sender throttled",
        t_try_resume(&paused, RING_LOW + 1) == 0 && paused == 1 && t_count() == 0);

    if (failures == 0) {
        printf("ring_test: PASS\n");
    } else {
        printf("ring_test: FAIL (%d)\n", failures);
    }
    return failures;
}
