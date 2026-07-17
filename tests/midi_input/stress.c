// stress.c -- real-concurrency stress for the MPSC candidate, with the
// mutex-serialized funnel model as the oracle for the property checker.
//
// Threads are REAL (pthreads; w64devkit's winpthreads on Windows). The
// properties checked are the spec from midi_in_ring.h / amy_midi_parse.h:
//   S1 per-writer FIFO, S2 conservation (delivered + counted drops == sent),
//   S3 no torn messages, S4 drop-newest.
// The ring is kept SMALL (16 slots) so the saturated case -- full ring, two
// writers claiming, reader draining -- is hit millions of times, because
// that is exactly where an MPSC goes wrong.
//
// HONESTY NOTE on sanitizers: ThreadSanitizer does not exist on Windows
// toolchains (w64devkit/MinGW GCC has no TSan runtime; MSVC and clang-cl
// have none either). `make tsan` in the Makefile builds this same file with
// -fsanitize=thread and MUST be run whenever a POSIX host is available
// (CI, macOS, Linux). On Windows this binary still exercises true
// multi-core interleavings -- but x86's strong memory model means a missing
// acquire/release can pass here and fail on the ESP32-S3; the ordering
// annotations were reviewed against that, not proven here.

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <pthread.h>
#include <sched.h>
#include <time.h>

// ---------------------------------------------------------------------------
// Parser core environment (MPSC mode: sysex ownership via CAS).

#define MAX_SYSEX_BYTES 256
static uint8_t sysex_storage[MAX_SYSEX_BYTES];
static uint8_t *sysex_buffer = sysex_storage;
static uint16_t sysex_len = 0;
static uint8_t sysex_overflow = 0;

#define N_WRITERS 3
#define RING_DEPTH 16            // must divide 65536; small => saturation
#define MSGS_PER_WRITER 60000    // < 65536 so per-writer seq never wraps

static uint8_t ring_slots[RING_DEPTH][3];
static uint8_t ring_lens[RING_DEPTH];
static volatile uint16_t ring_head = 0, ring_tail = 0;

// Per-thread stream id for the EMIT hook (parser tests).
static __thread int my_writer_id = -1;
static long push_drops[N_WRITERS];
static long sysex_delivered = 0, sysex_lost_logs = 0;
static pthread_mutex_t sysex_rec_mu = PTHREAD_MUTEX_INITIALIZER;
static long sysex_bad_payload = 0;

// EMIT: tag byte0's channel nibble with the writer id (the scripts below
// guarantee channel-voice statuses), then push to the shared MPSC ring.
static void emit_from_parser(const uint8_t *d, int l);
static void sysex_done_hook(void);

#define AMY_MIDI_PARSE_MPSC 1
#define AMY_MIDI_PARSE_EMIT(d, l)   emit_from_parser((d), (l))
#define AMY_MIDI_PARSE_CLOCK()      ((void)0)
#define AMY_MIDI_PARSE_SYSEX_DONE() sysex_done_hook()
#define AMY_MIDI_PARSE_LOG(...)     __atomic_add_fetch(&sysex_lost_logs, 1, __ATOMIC_RELAXED)

#include "amy_midi_parse.h"
#include "midi_in_ring.h"

static void emit_from_parser(const uint8_t *d, int l) {
    if (!midi_in_ring_push_mpsc(ring_slots, ring_lens, &ring_head, &ring_tail,
                                RING_DEPTH, d, (uint16_t)l))
        push_drops[my_writer_id]++;
}

static void sysex_done_hook(void) {
    // Runs with sysex ownership held (see amy_midi_parse.h): safe to read.
    pthread_mutex_lock(&sysex_rec_mu);
    sysex_delivered++;
    // Payload must be all one writer's fill byte -- a mix means two streams
    // merged into the buffer (the original defect).
    for (uint16_t i = 1; i < sysex_len; i++)
        if (sysex_buffer[i] != sysex_buffer[0]) { sysex_bad_payload++; break; }
    sysex_len = 0;
    pthread_mutex_unlock(&sysex_rec_mu);
}

// ---------------------------------------------------------------------------
static int failures = 0;
#define CHECK(cond, ...) do { if (!(cond)) { failures++; \
    fprintf(stderr, "FAIL %s:%d: ", __FILE__, __LINE__); \
    fprintf(stderr, __VA_ARGS__); fprintf(stderr, "\n"); } } while (0)

// ===========================================================================
// TEST A: raw MPSC ring, N writers x MSGS_PER_WRITER tagged messages,
// 1 reader, tiny ring. Message = [writer, seq_hi, seq_lo] (not MIDI --
// the ring is payload-agnostic and this gives a full 16-bit sequence).

typedef struct { int id; long sent, drops; } writer_arg_t;
static volatile int writers_done = 0;

static void *ring_writer(void *v) {
    writer_arg_t *w = (writer_arg_t *)v;
    for (uint32_t seq = 0; seq < MSGS_PER_WRITER; seq++) {
        uint8_t m[3] = { (uint8_t)w->id, (uint8_t)(seq >> 8), (uint8_t)(seq & 0xFF) };
        if (midi_in_ring_push_mpsc(ring_slots, ring_lens, &ring_head, &ring_tail,
                                   RING_DEPTH, m, 3))
            w->sent++;
        else
            w->drops++;
        if ((seq & 0x3FF) == 0) sched_yield();   // vary interleavings
    }
    __atomic_add_fetch(&writers_done, 1, __ATOMIC_ACQ_REL);
    return NULL;
}

static void test_ring_stress(void) {
    memset(ring_lens, 0, sizeof(ring_lens));
    ring_head = ring_tail = 0;
    writers_done = 0;

    pthread_t th[N_WRITERS];
    writer_arg_t wa[N_WRITERS];
    for (int i = 0; i < N_WRITERS; i++) {
        wa[i] = (writer_arg_t){ .id = i, .sent = 0, .drops = 0 };
        pthread_create(&th[i], NULL, ring_writer, &wa[i]);
    }

    long delivered[N_WRITERS] = { 0 };
    int32_t last_seq[N_WRITERS] = { -1, -1, -1 };
    long fifo_violations = 0, torn = 0, popped = 0;
    uint8_t out[3], outlen;
    for (;;) {
        if (midi_in_ring_pop_mpsc(ring_slots, ring_lens, &ring_head, &ring_tail,
                                  RING_DEPTH, out, &outlen)) {
            if (outlen != 3 || out[0] >= N_WRITERS) { torn++; continue; }
            int w = out[0];
            int32_t seq = (out[1] << 8) | out[2];
            if (seq <= last_seq[w]) fifo_violations++;      // S1 (dups also land here: S2)
            last_seq[w] = seq;
            delivered[w]++;
            // Slow the reader down periodically so the FULL ring is hit hard
            // -- the saturated claim/drain race is the point of this test.
            if ((++popped & 0x3FF) == 0) {
                struct timespec ts = { 0, 200 * 1000 };  // 0.2ms
                nanosleep(&ts, NULL);
            }
        } else if (__atomic_load_n(&writers_done, __ATOMIC_ACQUIRE) == N_WRITERS) {
            // drain once more after the last writer finished, then stop
            if (!midi_in_ring_pop_mpsc(ring_slots, ring_lens, &ring_head, &ring_tail,
                                       RING_DEPTH, out, &outlen)) break;
            if (outlen == 3 && out[0] < N_WRITERS) {
                int w = out[0];
                int32_t seq = (out[1] << 8) | out[2];
                if (seq <= last_seq[w]) fifo_violations++;
                last_seq[w] = seq;
                delivered[w]++;
            } else torn++;
        }
    }
    long total_drops = 0, total_delivered = 0;
    for (int i = 0; i < N_WRITERS; i++) {
        pthread_join(th[i], NULL);
        CHECK(delivered[i] + wa[i].drops == MSGS_PER_WRITER,
              "S2 conservation writer %d: delivered %ld + drops %ld != %d",
              i, delivered[i], wa[i].drops, MSGS_PER_WRITER);
        total_drops += wa[i].drops; total_delivered += delivered[i];
    }
    CHECK(fifo_violations == 0, "S1/S2: %ld per-writer order/dup violations", fifo_violations);
    CHECK(torn == 0, "S3: %ld torn messages", torn);
    CHECK(total_drops > 0, "saturation never happened -- ring too big or writers too slow for this test to mean anything");
    printf("ring stress (MPSC): %d writers x %d msgs, depth %d: delivered %ld, "
           "dropped %ld (%.1f%%), 0 order/tear violations\n",
           N_WRITERS, MSGS_PER_WRITER, RING_DEPTH,
           total_delivered, total_drops, 100.0 * (double)total_drops / (double)(total_delivered + total_drops));
}

// ===========================================================================
// TEST B: the same load through the FUNNEL MODEL (a mutex serializing pushes
// into the SPSC discipline -- semantically what the FreeRTOS queue + single
// MIDI task do). Same properties must hold; this validates that the checker
// itself accepts a known-correct serialization.

static pthread_mutex_t funnel_mu = PTHREAD_MUTEX_INITIALIZER;
static volatile int16_t s_head = 0, s_tail = 0;

static void *funnel_writer(void *v) {
    writer_arg_t *w = (writer_arg_t *)v;
    for (uint32_t seq = 0; seq < MSGS_PER_WRITER; seq++) {
        uint8_t m[3] = { (uint8_t)w->id, (uint8_t)(seq >> 8), (uint8_t)(seq & 0xFF) };
        pthread_mutex_lock(&funnel_mu);
        int ok = midi_in_ring_push_spsc(ring_slots, ring_lens, &s_head, &s_tail,
                                        RING_DEPTH, m, 3);
        pthread_mutex_unlock(&funnel_mu);
        if (ok) w->sent++; else w->drops++;
        if ((seq & 0x3FF) == 0) sched_yield();
    }
    __atomic_add_fetch(&writers_done, 1, __ATOMIC_ACQ_REL);
    return NULL;
}

static void test_funnel_model(void) {
    memset(ring_lens, 0, sizeof(ring_lens));
    s_head = s_tail = 0;
    writers_done = 0;

    pthread_t th[N_WRITERS];
    writer_arg_t wa[N_WRITERS];
    for (int i = 0; i < N_WRITERS; i++) {
        wa[i] = (writer_arg_t){ .id = i, .sent = 0, .drops = 0 };
        pthread_create(&th[i], NULL, funnel_writer, &wa[i]);
    }
    long delivered[N_WRITERS] = { 0 };
    int32_t last_seq[N_WRITERS] = { -1, -1, -1 };
    long fifo_violations = 0;
    for (;;) {
        int16_t tail = __atomic_load_n(&s_tail, __ATOMIC_ACQUIRE);
        if (s_head != tail) {
            int16_t h = s_head;
            uint8_t out[3]; uint8_t n = ring_lens[h];
            for (int i = 0; i < n && i < 3; i++) out[i] = ring_slots[h][i];
            s_head = (int16_t)((s_head + 1) % RING_DEPTH);
            if (n == 3 && out[0] < N_WRITERS) {
                int w = out[0];
                int32_t seq = (out[1] << 8) | out[2];
                if (seq <= last_seq[w]) fifo_violations++;
                last_seq[w] = seq;
                delivered[w]++;
            } else fifo_violations++;
        } else if (__atomic_load_n(&writers_done, __ATOMIC_ACQUIRE) == N_WRITERS
                   && s_head == __atomic_load_n(&s_tail, __ATOMIC_ACQUIRE)) {
            break;
        }
    }
    long total_drops = 0;
    for (int i = 0; i < N_WRITERS; i++) {
        pthread_join(th[i], NULL);
        CHECK(delivered[i] + wa[i].drops == MSGS_PER_WRITER,
              "funnel S2 writer %d: %ld + %ld != %d", i, delivered[i], wa[i].drops, MSGS_PER_WRITER);
        total_drops += wa[i].drops;
    }
    CHECK(fifo_violations == 0, "funnel model: %ld violations (checker or model broken)", fifo_violations);
    printf("funnel model (mutex+SPSC oracle): same properties hold, dropped %ld\n", total_drops);
}

// ===========================================================================
// TEST C: full pipeline -- N threads each PARSE their own byte stream in
// their own context (AMY_MIDI_PARSE_MPSC: this is exactly the MPSC build's
// hot path), emissions land in the shared MPSC ring, sysex ownership is
// CAS-arbitrated. Streams use running status and mid-message fragmentation.

#define PIPE_REPS 30000
static void *pipeline_writer(void *v) {
    writer_arg_t *w = (writer_arg_t *)v;
    my_writer_id = w->id;
    midi_stream_parser_t ctx; memset(&ctx, 0, sizeof(ctx));
    // note on ch<id>, running-status note, CC -- fragmented on purpose
    uint8_t chan = (uint8_t)w->id;
    uint8_t part1[] = { (uint8_t)(0x90 | chan), 0x3C };
    uint8_t part2[] = { 0x64, 0x40 };
    uint8_t part3[] = { 0x00, (uint8_t)(0xB0 | chan), 0x07 };
    uint8_t part4[] = { 0x7F };
    // every 64 reps, a sysex filled with a per-writer byte (CAS arbitration)
    uint8_t syx[8] = { 0xF0, 0,0,0,0,0,0, 0xF7 };
    memset(syx + 1, 0x10 + w->id, 6);
    for (int r = 0; r < PIPE_REPS; r++) {
        midi_parse_stream(&ctx, part1, sizeof(part1), 0);
        midi_parse_stream(&ctx, part2, sizeof(part2), 0);
        midi_parse_stream(&ctx, part3, sizeof(part3), 0);
        midi_parse_stream(&ctx, part4, sizeof(part4), 0);
        if ((r & 63) == 0)
            for (size_t i = 0; i < sizeof(syx); i++)
                midi_parse_stream(&ctx, &syx[i], 1, 0);
        w->sent += 3;   // 3 channel messages per rep
    }
    __atomic_add_fetch(&writers_done, 1, __ATOMIC_ACQ_REL);
    return NULL;
}

static void test_pipeline(void) {
    memset(ring_lens, 0, sizeof(ring_lens));
    ring_head = ring_tail = 0;
    writers_done = 0;
    sysex_delivered = sysex_lost_logs = sysex_bad_payload = 0;
    memset(push_drops, 0, sizeof(push_drops));

    pthread_t th[N_WRITERS];
    writer_arg_t wa[N_WRITERS];
    for (int i = 0; i < N_WRITERS; i++) {
        wa[i] = (writer_arg_t){ .id = i, .sent = 0, .drops = 0 };
        pthread_create(&th[i], NULL, pipeline_writer, &wa[i]);
    }

    // Per-writer expected message cycle: [90|c 3C 64], [90|c 40 00], [B0|c 07 7F]
    long delivered[N_WRITERS] = { 0 };
    long torn = 0;
    uint8_t out[3], outlen;
    for (;;) {
        if (midi_in_ring_pop_mpsc(ring_slots, ring_lens, &ring_head, &ring_tail,
                                  RING_DEPTH, out, &outlen)) {
            if (outlen != 3) { torn++; continue; }
            int c = out[0] & 0x0F;
            if (c >= N_WRITERS) { torn++; continue; }
            // Drops make the cycle skip: accept any of the 3 cycle messages,
            // but its BYTES must exactly match one of writer c's messages --
            // any other byte pattern is a tear.
            uint8_t exp[3][3] = {
                { (uint8_t)(0x90 | c), 0x3C, 0x64 },
                { (uint8_t)(0x90 | c), 0x40, 0x00 },
                { (uint8_t)(0xB0 | c), 0x07, 0x7F },
            };
            int m = -1;
            for (int k = 0; k < 3; k++)
                if (memcmp(out, exp[k], 3) == 0) { m = k; break; }
            if (m < 0) { torn++; continue; }
            delivered[c]++;
        } else if (__atomic_load_n(&writers_done, __ATOMIC_ACQUIRE) == N_WRITERS) {
            if (!midi_in_ring_pop_mpsc(ring_slots, ring_lens, &ring_head, &ring_tail,
                                       RING_DEPTH, out, &outlen)) break;
            // tail drain: same checks as the main branch
            int c = (outlen == 3) ? (out[0] & 0x0F) : N_WRITERS;
            if (c >= N_WRITERS) { torn++; continue; }
            uint8_t exp[3][3] = {
                { (uint8_t)(0x90 | c), 0x3C, 0x64 },
                { (uint8_t)(0x90 | c), 0x40, 0x00 },
                { (uint8_t)(0xB0 | c), 0x07, 0x7F },
            };
            int m = -1;
            for (int k = 0; k < 3; k++)
                if (memcmp(out, exp[k], 3) == 0) { m = k; break; }
            if (m < 0) { torn++; continue; }
            delivered[c]++;
        }
    }
    long total_drops = 0;
    for (int i = 0; i < N_WRITERS; i++) {
        pthread_join(th[i], NULL);
        CHECK(delivered[i] + push_drops[i] == wa[i].sent,
              "pipeline S2 writer %d: delivered %ld + drops %ld != sent %ld",
              i, delivered[i], push_drops[i], wa[i].sent);
        total_drops += push_drops[i];
    }
    CHECK(torn == 0, "pipeline S3: %ld torn messages out of the parser+ring", torn);
    CHECK(sysex_bad_payload == 0, "sysex payloads merged across streams: %ld", sysex_bad_payload);
    long sysex_attempts = (long)N_WRITERS * ((PIPE_REPS + 63) / 64);
    CHECK(sysex_delivered + sysex_lost_logs == sysex_attempts,
          "sysex conservation: delivered %ld + lost %ld != attempts %ld",
          sysex_delivered, sysex_lost_logs, sysex_attempts);
    printf("pipeline (parse-in-writer + MPSC ring): %ld msgs delivered, %ld dropped, "
           "sysex %ld delivered / %ld arbitrated away, 0 tears\n",
           delivered[0] + delivered[1] + delivered[2], total_drops,
           sysex_delivered, sysex_lost_logs);
}

int main(void) {
    test_ring_stress();
    test_funnel_model();
    test_pipeline();
    if (failures) { printf("STRESS: %d FAILURE(S)\n", failures); return 1; }
    printf("STRESS: all properties hold under real threads\n");
    return 0;
}
