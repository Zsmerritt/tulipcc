// midi_in_ring.h
// The last_midi ring: complete MIDI messages parsed by AMY on their way to
// Python (tulip.midi_in). Shared VERBATIM between the firmware
// (amy_connector.c writes, modtulip.c reads) and the host concurrency
// harness (tests/midi_input/), which differential-tests both disciplines --
// change this file and run the harness before flashing.
//
// ---------------------------------------------------------------------------
// SPEC -- the contract every discipline here must satisfy. The harness
// enforces it; the funnel build satisfies S1-S4 by construction (single
// writer), the MPSC build must earn them:
//
//  S1. PER-WRITER FIFO: messages pushed by one producer are delivered to the
//      reader in that producer's push order. (Global order across producers
//      is NOT specified -- any interleaving of per-producer orders is
//      correct, which is why the harness never compares full sequences.)
//  S2. NO LOSS, NO DUPLICATION: every push either delivers exactly once or
//      returns 0 (dropped); the caller must COUNT 0-returns where the reader
//      can see them (tulip.midi_in_drops()). consumed + counted-drops ==
//      pushed, per producer.
//  S3. NO TORN MESSAGES: a delivered message's bytes are exactly the bytes
//      of one push -- never a mix of two pushes' payloads, never a payload
//      under another push's length.
//  S4. DROP-NEWEST ON FULL: when the ring is full the INCOMING message is
//      dropped; already-queued messages are never overwritten and the
//      reader's cursor is never touched by a writer. (Chosen over
//      drop-oldest so a flood can't corrupt the slot Python is copying.)
//
// DISCIPLINES:
//  - Default (funnel) build: SPSC. head/tail are int16_t ring INDICES.
//    Exactly ONE task (the AMY MIDI task) may call push; only the MP task
//    moves head. Writer release-stores tail after the payload; reader
//    acquire-loads tail. This is the pre-existing E-11/FW-10/C2 discipline,
//    moved here so the harness can compile it.
//  - AMY_MIDI_MPSC build (experimental, compile-gated): multi-producer.
//    head/tail are uint16_t MONOTONIC counters (slot = value % depth; depth
//    must divide 65536). Writers claim a slot by CAS on tail, fill it, then
//    PUBLISH by release-storing the slot's len; lens[] doubles as the
//    per-slot publish flag (0 = empty/consumed, nonzero = published --
//    message lengths are always >= 1 so 0 is unambiguous). The reader stops
//    at the first unpublished slot, so a claim that hasn't published yet
//    briefly holds back later messages (head-of-line, microseconds) but
//    order and integrity hold.
//    KNOWN RESIDUAL (documented, not defended): if a writer stalls between
//    its tail-load and CAS for a full 65536 messages (~4.5 hours of max-rate
//    MIDI), the counter wraps to the same value and the CAS succeeds
//    against a recycled slot (classic ABA). The len!=0 guard narrows this
//    to "treated as full"; a stall of that length means the task is dead
//    anyway. ALSO: a writer killed between claim and publish wedges the
//    reader at that slot permanently -- do not vTaskDelete a producer
//    mid-push (stop_midi already doesn't).
// ---------------------------------------------------------------------------

#ifndef __MIDI_IN_RING_H
#define __MIDI_IN_RING_H

#include <stdint.h>

#define MIDI_IN_RING_MSG_BYTES 3

// ---- SPSC (default build) -------------------------------------------------
// Returns 1 if queued, 0 if full (caller counts the drop -- S2/S4).
static inline int midi_in_ring_push_spsc(
        uint8_t (*slots)[MIDI_IN_RING_MSG_BYTES], uint8_t *lens,
        volatile int16_t *head, volatile int16_t *tail, int16_t depth,
        const uint8_t *data, uint16_t len)
{
    int16_t t = *tail;
    int16_t next = (int16_t)((t + 1) % depth);
    // SPSC discipline (E-11): only this writer moves TAIL; only the reader
    // moves HEAD. Drop-NEWEST on full: the writer never touches head (S4).
    if (next == *head) return 0;
    // Clamp stored length to what is actually copied (C-8: a >255-byte blob
    // stored len%256 while only 3 bytes landed).
    uint8_t n = (len > MIDI_IN_RING_MSG_BYTES) ? MIDI_IN_RING_MSG_BYTES : (uint8_t)len;
    for (uint8_t i = 0; i < n; i++) slots[t][i] = data[i];
    lens[t] = n;
    // RELEASE-publish (FW-10): the payload stores must be visible before the
    // tail moves; volatile alone doesn't order the non-volatile payload
    // writes against it under -O3/LTO. Pairs with the reader's ACQUIRE (C2).
    __atomic_store_n(tail, next, __ATOMIC_RELEASE);
    return 1;
}

// ---- MPSC (AMY_MIDI_MPSC build) --------------------------------------------
// Push from ANY task. Returns 1 if queued, 0 if full/ABA-corner (caller
// counts the drop). depth must divide 65536 (1024 does).
static inline int midi_in_ring_push_mpsc(
        uint8_t (*slots)[MIDI_IN_RING_MSG_BYTES], uint8_t *lens,
        volatile uint16_t *head, volatile uint16_t *tail, uint16_t depth,
        const uint8_t *data, uint16_t len)
{
    for (;;) {
        uint16_t t = __atomic_load_n(tail, __ATOMIC_RELAXED);
        // Full check against the reader-owned monotonic head (S4). Claimed-
        // but-unpublished slots count as used because tail already advanced.
        uint16_t used = (uint16_t)(t - __atomic_load_n(head, __ATOMIC_ACQUIRE));
        if (used >= depth) return 0;
        uint16_t idx = (uint16_t)(t % depth);
        // The slot must be consumed (len 0) before we may reuse it. With
        // used < depth this always holds EXCEPT the wrap/ABA corner above --
        // treat that as full rather than overwrite an unread slot (S2).
        if (__atomic_load_n(&lens[idx], __ATOMIC_ACQUIRE) != 0) return 0;
        if (__atomic_compare_exchange_n(tail, &t, (uint16_t)(t + 1), 0,
                                        __ATOMIC_ACQ_REL, __ATOMIC_RELAXED)) {
            // Slot t is exclusively ours: fill, then PUBLISH via len (S3 --
            // the reader cannot see the slot until the release-store below).
            uint8_t n = (len > MIDI_IN_RING_MSG_BYTES) ? MIDI_IN_RING_MSG_BYTES : (uint8_t)len;
            for (uint8_t i = 0; i < n; i++) slots[idx][i] = data[i];
            __atomic_store_n(&lens[idx], n, __ATOMIC_RELEASE);
            return 1;
        }
        // Lost the claim race to another writer; retry with the fresh tail.
    }
}

// Reader side for the MPSC build (single consumer: the MP task). Returns 1
// with the message copied into out/out_len, 0 if nothing consumable (empty,
// or the next slot is claimed but not yet published -- try again next poll;
// FIFO holds because head doesn't move). Copies BEFORE releasing the slot
// and releases the slot BEFORE advancing head, so a GC pause between poll
// and use can never race a writer into the bytes being returned.
static inline int midi_in_ring_pop_mpsc(
        uint8_t (*slots)[MIDI_IN_RING_MSG_BYTES], uint8_t *lens,
        volatile uint16_t *head, volatile uint16_t *tail, uint16_t depth,
        uint8_t *out, uint8_t *out_len)
{
    uint16_t h = *head;  // reader-owned, plain read
    if (h == __atomic_load_n(tail, __ATOMIC_ACQUIRE)) return 0;
    uint16_t idx = (uint16_t)(h % depth);
    uint8_t n = __atomic_load_n(&lens[idx], __ATOMIC_ACQUIRE);
    if (n == 0) return 0;  // claimed, not yet published
    if (n > MIDI_IN_RING_MSG_BYTES) n = MIDI_IN_RING_MSG_BYTES;
    for (uint8_t i = 0; i < n; i++) out[i] = slots[idx][i];
    // Hand the slot back to writers, then advance past it.
    __atomic_store_n(&lens[idx], 0, __ATOMIC_RELEASE);
    __atomic_store_n(head, (uint16_t)(h + 1), __ATOMIC_RELEASE);
    *out_len = n;
    return 1;
}

#endif // __MIDI_IN_RING_H
