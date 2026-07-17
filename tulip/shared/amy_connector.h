//amy_connector.h
#ifndef AMYCONNECTOR_H
#define AMYCONNECTOR_H


#include <stdint.h>

#define MAX_MIDI_BYTES_TO_PARSE 1024
#define MAX_MIDI_BYTES_PER_MESSAGE 3
#define MIDI_QUEUE_DEPTH 1024
#define MAX_SYSEX_BYTES (16384)
extern uint8_t * sysex_buffer;

// Tulip's last_midi ring: complete MIDI messages on their way to Python
// (tulip.midi_in). DEFINED in amy_connector.c; read by modtulip.c. Declared
// HERE, and not in amy's amy_midi.h, because:
//   * amy never touches them -- the ring is entirely tulip's (amy_midi.c
//     mentions it only in comments), so amy's header was never the right home;
//   * modtulip.c includes amy_midi.h only `#ifndef __EMSCRIPTEN__`, so
//     declarations placed there are invisible to the web build while the code
//     using them still compiles. That is exactly how the web build broke:
//     "error: use of undeclared identifier 'last_midi'". This header is
//     included unconditionally, and already owns the two dimension macros
//     above -- which is why THOSE kept working when the arrays did not.
// One declaration still governs both the definer and the user, so a type
// change cannot silently disagree; that property is why these were centralized
// in the first place, and it is preserved.
extern uint8_t last_midi[MIDI_QUEUE_DEPTH][MAX_MIDI_BYTES_PER_MESSAGE];
extern uint8_t last_midi_len[MIDI_QUEUE_DEPTH];
// Cursors. Default build: SPSC, int16_t INDICES 0..MIDI_QUEUE_DEPTH-1; the sole
// writer is the AMY MIDI task (all other producers funnel through
// amy_midi_inject). AMY_MIDI_MPSC build: multi-producer, uint16_t MONOTONIC
// counters (slot = value % depth; 65536 % 1024 == 0, so the wrap is seamless).
// The protocols operating on these live in midi_in_ring.h.
#ifdef AMY_MIDI_MPSC
extern volatile uint16_t midi_queue_tail;
extern volatile uint16_t midi_queue_head;
#else
extern volatile int16_t midi_queue_tail;
extern volatile int16_t midi_queue_head;
#endif
#ifdef __EMSCRIPTEN__
void midi_out(uint8_t * bytes, uint16_t len) ;

void midi_local(uint8_t * bytes, uint16_t len);
#endif

#endif