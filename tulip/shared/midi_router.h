// midi_router.h -- the C-side MIDI channel route table.
//
// A Tulip UI that owns MIDI routing (which channels reach which output boards,
// and which still need Python) can upload the whole 16-channel table once via
// tulip.midi_routes(); the AMY-side MIDI input hook (amy_connector.c) then
// forwards board bytes and skips waking the Python scheduler for channels C
// fully owns. This keeps per-message routing -- a table lookup -- off the
// MicroPython task, where it otherwise costs an MP schedule + heap allocs per
// message under a CC/pitch-bend stream.
//
// Producer: modtulip.c (tulip.midi_routes upload). Consumer: amy_connector.c
// tulip_midi_input_hook. The producer/consumer run on different tasks; see
// tulip.midi_routes() for the deactivate-around-rewrite concurrency invariant.
#ifndef __MIDI_ROUTER_H
#define __MIDI_ROUTER_H

#include <stdint.h>

typedef struct {
    uint16_t board_mask;   // bit d: forward raw channel bytes to output device d
    uint8_t  flags;        // TULIP_MIDI_ROUTE_PY: Python routing still needed
} tulip_midi_route_t;
#define TULIP_MIDI_ROUTE_PY 1

extern tulip_midi_route_t tulip_midi_routes[17];   // index by channel 1..16
extern volatile uint8_t  tulip_midi_route_active;  // table uploaded at least once
extern volatile uint8_t  tulip_midi_notify_all;    // schedule Python per message
extern volatile uint32_t tulip_midi_activity;      // messages seen (meter/UI poll)

// Reset the router to its cold-boot default (route_active = 0, notify_all = 1),
// releasing every channel back to Python. Called from the port soft-reset path
// so C route state doesn't outlive the Python session that uploaded it.
void tulip_midi_router_reset(void);

#endif // __MIDI_ROUTER_H
