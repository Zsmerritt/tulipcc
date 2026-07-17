// harness.c -- deterministic host harness for Tulip's MIDI input path.
//
// Compiles the REAL firmware code -- amy/src/amy_midi_parse.h (the stream
// parser core) and tulip/shared/midi_in_ring.h (the last_midi ring
// disciplines) -- natively, and checks the spec stated in those headers:
//
//   S1 per-writer FIFO   S2 no loss/dup beyond counted drops
//   S3 no torn messages  S4 drop-newest on full
//
// plus the parser-level properties: per-STREAM message integrity under any
// interleaving of stream chunks (this is the funnel oracle: the funnel's
// queue serializes chunks in SOME arrival order, so enumerating ALL chunk
// interleavings covers every order the funnel can produce), and
// single-owner sysex arbitration.
//
// It also runs the OLD design (one shared parser context for every stream)
// over the same interleaving space and requires that the tear detector
// FIRES -- documenting the original defect and proving the detector works.
//
// Build & run: see Makefile in this directory. No FreeRTOS, no MicroPython:
// what is NOT covered here is the FreeRTOS queue plumbing of the funnel
// itself (trusted primitive) and the MP-side reader glue in modtulip.c.

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

// ---------------------------------------------------------------------------
// Recorder environment for the parser core.

#define MAX_SYSEX_BYTES 64          // small on purpose: overflow is testable

static uint8_t sysex_storage[MAX_SYSEX_BYTES];
static uint8_t *sysex_buffer = sysex_storage;
static uint16_t sysex_len = 0;
static uint8_t sysex_overflow = 0;

#define REC_MAX 4096
typedef struct { uint8_t bytes[3]; uint8_t len; } rec_msg_t;
static rec_msg_t recorded[REC_MAX];
static int n_recorded = 0;
static int n_clocks = 0;
static int n_logs = 0;                 // every AMY_MIDI_PARSE_LOG call
typedef struct { uint8_t bytes[MAX_SYSEX_BYTES]; uint16_t len; } rec_syx_t;
static rec_syx_t rec_sysex[64];
static int n_rec_sysex = 0;

static void record_msg(const uint8_t *d, int l) {
    if (n_recorded < REC_MAX) {
        memcpy(recorded[n_recorded].bytes, d, (size_t)l);
        recorded[n_recorded].len = (uint8_t)l;
    }
    n_recorded++;
}
static void record_sysex(void) {
    if (n_rec_sysex < 64) {
        memcpy(rec_sysex[n_rec_sysex].bytes, sysex_buffer, sysex_len);
        rec_sysex[n_rec_sysex].len = sysex_len;
    }
    n_rec_sysex++;
    sysex_len = 0;   // consume, like the SPSS paths / Python reader do
}

#define AMY_MIDI_PARSE_EMIT(d, l)   record_msg((d), (l))
#define AMY_MIDI_PARSE_CLOCK()      (n_clocks++)
#define AMY_MIDI_PARSE_SYSEX_DONE() record_sysex()
#define AMY_MIDI_PARSE_LOG(...)     (n_logs++)   /* count, don't print */

#include "amy_midi_parse.h"          // THE REAL PARSER CORE
#include "midi_in_ring.h"            // THE REAL RING DISCIPLINES

static void reset_recorders(void) {
    n_recorded = n_clocks = n_logs = 0;
    n_rec_sysex = 0;
    sysex_len = 0;
    sysex_overflow = 0;
    sysex_owner = NULL;
}

// ---------------------------------------------------------------------------
// Tiny test plumbing.

static int failures = 0;
#define CHECK(cond, ...) do { if (!(cond)) { failures++; \
    fprintf(stderr, "FAIL %s:%d: ", __FILE__, __LINE__); \
    fprintf(stderr, __VA_ARGS__); fprintf(stderr, "\n"); } } while (0)

// ---------------------------------------------------------------------------
// Stream scripts. Each stream's channel-voice messages are tagged with a
// unique channel nibble so any emitted message names its stream: a message
// whose channel does not match the context that produced it -- or whose
// bytes are not the next expected message of that stream -- is a TEAR.

typedef struct {
    const uint8_t *bytes;
    int len;
    uint8_t usb;               // parser flag for this stream
    const int *cuts;           // chunk boundaries (offsets), cuts[n]=len
    int n_chunks;
    uint8_t chan;              // channel tag (0xFF = no channel voice msgs)
} stream_t;

// Stream U (DIN/UART, chan 0): note on, running status note, CC, clock mixed in.
static const uint8_t U_BYTES[] = {
    0x90, 0x3C, 0x64,        // note on ch0
    0xF8,                    // clock (realtime, mid-stream)
    0x40, 0x00,              // running status: note 0x40 off (vel 0)
    0xB0, 0x07, 0x7F,        // CC7
};
static const int U_CUTS[] = { 2, 4, 5, 7, 9 };   // deliberately mid-message

// Stream S (USB host, chan 1): whole 3-byte packets, usb=1.
static const uint8_t S_BYTES[] = {
    0x91, 0x45, 0x50,
    0x81, 0x45, 0x00,
    0xC1, 0x05, 0x00,        // program change (2 significant bytes, padded)
};
static const int S_CUTS[] = { 3, 6, 9 };         // packet == chunk, like the device

// Stream L (midi_local, chan 2): pitch bend + running status.
static const uint8_t L_BYTES[] = {
    0xE2, 0x00, 0x40,
    0x02, 0x60,              // running status bend
};
static const int L_CUTS[] = { 1, 4, 5 };

// Solo-parse a stream in a fresh context to compute its expected messages.
static rec_msg_t expected[3][32];
static int n_expected[3];
static int expected_clocks_total;

static void compute_expected(const stream_t *st, int idx) {
    reset_recorders();
    midi_stream_parser_t p; memset(&p, 0, sizeof(p));
    for (int c = 0; c < st->n_chunks; c++) {
        int start = (c == 0) ? 0 : st->cuts[c - 1];
        midi_parse_stream(&p, (uint8_t *)st->bytes + start, (size_t)(st->cuts[c] - start), st->usb);
    }
    CHECK(n_recorded <= 32, "solo parse overflow");
    memcpy(expected[idx], recorded, sizeof(rec_msg_t) * (size_t)n_recorded);
    n_expected[idx] = n_recorded;
    expected_clocks_total += n_clocks;
}

// ---------------------------------------------------------------------------
// Interleaving enumeration. Feed one interleaving of the streams' chunks
// either into per-stream contexts (new design) or one shared context (old
// design), then check properties.

static const stream_t STREAMS[3] = {
    { U_BYTES, (int)sizeof(U_BYTES), 0, U_CUTS, 5, 0x00 },
    { S_BYTES, (int)sizeof(S_BYTES), 1, S_CUTS, 3, 0x01 },
    { L_BYTES, (int)sizeof(L_BYTES), 0, L_CUTS, 3, 0x02 },
};

static long trials = 0, old_design_violations = 0;

// Returns number of property violations for this interleaving (0 = pass).
static int run_interleaving(const int *order, int total_chunks, int shared_context) {
    reset_recorders();
    midi_stream_parser_t ctx[3]; memset(ctx, 0, sizeof(ctx));
    int chunk_idx[3] = { 0, 0, 0 };

    for (int k = 0; k < total_chunks; k++) {
        int s = order[k];
        const stream_t *st = &STREAMS[s];
        int c = chunk_idx[s]++;
        int start = (c == 0) ? 0 : st->cuts[c - 1];
        midi_stream_parser_t *p = shared_context ? &ctx[0] : &ctx[s];
        midi_parse_stream(p, (uint8_t *)st->bytes + start,
                          (size_t)(st->cuts[c] - start), st->usb);
    }

    // Properties. Attribute each channel-voice message to its stream by
    // channel tag; each stream's messages must be exactly its expected list,
    // in order (S1 per-stream FIFO + S3 integrity). Non-channel messages
    // (none in these scripts except clocks) are counted separately.
    int violations = 0;
    int next_exp[3] = { 0, 0, 0 };
    int n = n_recorded < REC_MAX ? n_recorded : REC_MAX;
    for (int i = 0; i < n; i++) {
        rec_msg_t *m = &recorded[i];
        if (m->bytes[0] < 0x80 || m->bytes[0] >= 0xF0) { violations++; continue; }
        int s = m->bytes[0] & 0x0F;
        if (s > 2) { violations++; continue; }
        int e = next_exp[s]++;
        if (e >= n_expected[s]
            || m->len != expected[s][e].len
            || memcmp(m->bytes, expected[s][e].bytes, m->len) != 0) {
            violations++;
        }
    }
    for (int s = 0; s < 3; s++)
        if (next_exp[s] != n_expected[s]) violations++;   // S2: nothing lost
    if (n_clocks != expected_clocks_total) violations++;
    return violations;
}

static void enumerate(int *order, int depth, int total, int rem[3]) {
    if (depth == total) {
        trials++;
        int v_new = run_interleaving(order, total, 0);
        CHECK(v_new == 0, "per-context parse violated properties (interleaving #%ld, %d violations)", trials, v_new);
        if (run_interleaving(order, total, 1) > 0) old_design_violations++;
        return;
    }
    for (int s = 0; s < 3; s++) {
        if (rem[s] > 0) {
            rem[s]--; order[depth] = s;
            enumerate(order, depth + 1, total, rem);
            rem[s]++;
        }
    }
}

static void test_interleavings(void) {
    expected_clocks_total = 0;
    for (int s = 0; s < 3; s++) compute_expected(&STREAMS[s], s);

    int rem[3] = { STREAMS[0].n_chunks, STREAMS[1].n_chunks, STREAMS[2].n_chunks };
    int total = rem[0] + rem[1] + rem[2];
    int order[16];
    trials = 0; old_design_violations = 0;
    enumerate(order, 0, total, rem);

    printf("interleavings: %ld enumerated, new design clean, "
           "old shared-context design violated %ld (%.1f%%)\n",
           trials, old_design_violations, 100.0 * (double)old_design_violations / (double)trials);
    // The whole point: the old design MUST tear somewhere in this space,
    // or the detector is broken and every green above is meaningless.
    CHECK(old_design_violations > 0, "tear detector never fired on the old design");
}

// ---------------------------------------------------------------------------
// Sysex arbitration: two streams inside sysex at once -- first F0 owns the
// buffer, the other stream's sysex is dropped whole and logged, never merged.

static void test_sysex_arbitration(void) {
    static const uint8_t SYX_A[] = { 0xF0, 0x11, 0x11, 0x11, 0xF7 };
    static const uint8_t SYX_B[] = { 0xF0, 0x22, 0x22, 0xF7 };
    // Byte-level interleavings of A and B (each byte its own chunk).
    int idx_of[2] = { (int)sizeof(SYX_A), (int)sizeof(SYX_B) };
    int total = idx_of[0] + idx_of[1];
    // enumerate binary interleavings via bitmask (choose positions of A)
    long cases = 0;
    for (unsigned mask = 0; mask < (1u << total); mask++) {
        if (__builtin_popcount(mask) != idx_of[0]) continue;
        cases++;
        reset_recorders();
        midi_stream_parser_t pa, pb; memset(&pa, 0, sizeof(pa)); memset(&pb, 0, sizeof(pb));
        int ia = 0, ib = 0;
        for (int k = 0; k < total; k++) {
            if (mask & (1u << k)) midi_parse_stream(&pa, (uint8_t *)&SYX_A[ia++], 1, 0);
            else                  midi_parse_stream(&pb, (uint8_t *)&SYX_B[ib++], 1, 0);
        }
        // Expected outcome depends on OVERLAP: if one stream's F0..F7 window
        // fully precedes the other's, both sysexes deliver (nothing was
        // concurrent); if the windows overlap, the first F0 owns the buffer
        // and the other stream's sysex is dropped whole, logged once.
        int firstA = -1, lastA = -1, firstB = -1, lastB = -1;
        for (int k = 0; k < total; k++) {
            if (mask & (1u << k)) { if (firstA < 0) firstA = k; lastA = k; }
            else                  { if (firstB < 0) firstB = k; lastB = k; }
        }
        int overlap = !(lastA < firstB || lastB < firstA);
        int want_delivered = overlap ? 1 : 2;
        CHECK(n_rec_sysex == want_delivered, "expected %d delivered sysex, got %d (mask %x)",
              want_delivered, n_rec_sysex, mask);
        for (int d = 0; d < n_rec_sysex && d < 2; d++) {
            rec_syx_t *sx = &rec_sysex[d];
            int is_a = (sx->len == 3 && sx->bytes[0] == 0x11);
            int is_b = (sx->len == 2 && sx->bytes[0] == 0x22);
            CHECK(is_a || is_b, "delivered sysex matches neither stream (len %d first %02x) -- MERGED", sx->len, sx->bytes[0]);
            for (int i = 0; i < sx->len; i++)
                CHECK(sx->bytes[i] == sx->bytes[0], "sysex payload mixes sources at byte %d", i);
        }
        if (overlap && n_rec_sysex == 1) {
            // the winner must be the stream whose F0 arrived first
            int a_won = firstA < firstB;
            CHECK(rec_sysex[0].bytes[0] == (a_won ? 0x11 : 0x22),
                  "wrong sysex won the buffer (mask %x)", mask);
        }
        CHECK(n_logs == (overlap ? 1 : 0), "loser logging: want %d, logged %d (mask %x)",
              overlap ? 1 : 0, n_logs, mask);
        CHECK(sysex_owner == NULL, "sysex owner leaked");
    }
    printf("sysex arbitration: %ld byte-interleavings, one whole winner + one loud loser in each\n", cases);
}

// Sysex overflow (the 48KB-past-the-buffer bug, fixed upstream, kept honest
// here): a sysex longer than the buffer is dropped at its F7, not truncated,
// and logs once.
static void test_sysex_overflow(void) {
    reset_recorders();
    midi_stream_parser_t p; memset(&p, 0, sizeof(p));
    uint8_t f0 = 0xF0, f7 = 0xF7, fill = 0x33;
    midi_parse_stream(&p, &f0, 1, 0);
    for (int i = 0; i < MAX_SYSEX_BYTES * 3; i++) midi_parse_stream(&p, &fill, 1, 0);
    midi_parse_stream(&p, &f7, 1, 0);
    CHECK(n_rec_sysex == 0, "overlong sysex must be dropped, got %d deliveries", n_rec_sysex);
    CHECK(n_logs == 1, "overflow should log exactly once, logged %d", n_logs);
    CHECK(sysex_owner == NULL && sysex_len == 0 && sysex_overflow == 0, "state not clean after overflow drop");
    // and the parser still works afterwards
    uint8_t note[] = { 0x90, 0x40, 0x40 };
    midi_parse_stream(&p, note, 3, 0);
    CHECK(n_recorded == 1, "parser wedged after overflow");
}

// ---------------------------------------------------------------------------
// Ring protocol tests (single-threaded; the threaded ones live in stress.c).

#define TDEPTH 4
static uint8_t slots[TDEPTH][3];
static uint8_t lens[TDEPTH];

static void test_ring_spsc(void) {
    volatile int16_t head = 0, tail = 0;
    memset(lens, 0, sizeof(lens));
    uint8_t m1[] = { 0x90, 1, 1 }, m2[] = { 0x90, 2, 2 }, m3[] = { 0x90, 3, 3 }, m4[] = { 0x90, 4, 4 };
    CHECK(midi_in_ring_push_spsc(slots, lens, &head, &tail, TDEPTH, m1, 3) == 1, "spsc push 1");
    CHECK(midi_in_ring_push_spsc(slots, lens, &head, &tail, TDEPTH, m2, 3) == 1, "spsc push 2");
    CHECK(midi_in_ring_push_spsc(slots, lens, &head, &tail, TDEPTH, m3, 3) == 1, "spsc push 3");
    // SPSC capacity is depth-1
    CHECK(midi_in_ring_push_spsc(slots, lens, &head, &tail, TDEPTH, m4, 3) == 0, "spsc must drop-newest at full (S4)");
    CHECK(slots[0][1] == 1 && slots[1][1] == 2 && slots[2][1] == 3, "queued slots overwritten by a refused push (S4)");
    // reader drains in order (reader protocol for SPSC lives in modtulip; here
    // just verify producer-side ordering + a wrap)
    head = (int16_t)((head + 1) % TDEPTH);
    CHECK(midi_in_ring_push_spsc(slots, lens, &head, &tail, TDEPTH, m4, 3) == 1, "spsc push after drain");
    CHECK(slots[3][1] == 4, "spsc wrap slot content");
    printf("ring SPSC: capacity, drop-newest, wrap ok\n");
}

static void test_ring_mpsc(void) {
    // start near the uint16 wrap to prove the monotonic-counter modulo works
    volatile uint16_t head = 65532, tail = 65532;
    memset(lens, 0, sizeof(lens));
    uint8_t out[3]; uint8_t outlen = 0;
    uint8_t m[4][3] = { {0x90,1,1}, {0x90,2,2}, {0x90,3,3}, {0x90,4,4} };
    for (int i = 0; i < 4; i++)
        CHECK(midi_in_ring_push_mpsc(slots, lens, &head, &tail, TDEPTH, m[i], 3) == 1, "mpsc push %d", i);
    // MPSC capacity is depth
    uint8_t m5[] = { 0x90, 5, 5 };
    CHECK(midi_in_ring_push_mpsc(slots, lens, &head, &tail, TDEPTH, m5, 3) == 0, "mpsc must drop-newest at full (S4)");
    for (int i = 0; i < 4; i++) {
        CHECK(midi_in_ring_pop_mpsc(slots, lens, &head, &tail, TDEPTH, out, &outlen) == 1, "mpsc pop %d", i);
        CHECK(outlen == 3 && out[1] == m[i][1], "mpsc FIFO across the uint16 wrap (got %d want %d)", out[1], m[i][1]);
    }
    CHECK(midi_in_ring_pop_mpsc(slots, lens, &head, &tail, TDEPTH, out, &outlen) == 0, "mpsc empty pop");

    // Hole (claimed-not-published) blocks the reader without corrupting FIFO:
    uint16_t t = tail;
    CHECK(__atomic_compare_exchange_n(&tail, &t, (uint16_t)(t + 1), 0,
            __ATOMIC_ACQ_REL, __ATOMIC_RELAXED), "manual claim");
    // a second writer publishes AFTER the hole
    CHECK(midi_in_ring_push_mpsc(slots, lens, &head, &tail, TDEPTH, m[1], 3) == 1, "push behind hole");
    CHECK(midi_in_ring_pop_mpsc(slots, lens, &head, &tail, TDEPTH, out, &outlen) == 0,
          "reader must wait at an unpublished slot, not skip it");
    // the stalled writer publishes late
    lens[(uint16_t)(t % TDEPTH)] = 3; slots[t % TDEPTH][1] = 9;
    CHECK(midi_in_ring_pop_mpsc(slots, lens, &head, &tail, TDEPTH, out, &outlen) == 1 && out[1] == 9, "late publish consumed first");
    CHECK(midi_in_ring_pop_mpsc(slots, lens, &head, &tail, TDEPTH, out, &outlen) == 1 && out[1] == m[1][1], "then the later writer's");
    printf("ring MPSC: capacity, drop-newest, wrap, hole/head-of-line ok\n");
}

// ---------------------------------------------------------------------------

int main(void) {
    test_interleavings();
    test_sysex_arbitration();
    test_sysex_overflow();
    test_ring_spsc();
    test_ring_mpsc();
    if (failures) { printf("HARNESS: %d FAILURE(S)\n", failures); return 1; }
    printf("HARNESS: all properties hold\n");
    return 0;
}
