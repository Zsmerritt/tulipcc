// midi_router.h -- the C-side MIDI channel route table (review O-2).
// Producer: modtulip.c (tulip.midi_routes upload from deck/forwarder).
// Consumer: amy_connector.c tulip_midi_input_hook.
#ifndef __MIDI_ROUTER_H
#define __MIDI_ROUTER_H

#include <stdint.h>

typedef struct {
    uint16_t board_mask;   // bit d: forward raw channel bytes to device d
    uint8_t  flags;        // TULIP_MIDI_ROUTE_PY: Python routing needed
} tulip_midi_route_t;
#define TULIP_MIDI_ROUTE_PY 1

extern tulip_midi_route_t tulip_midi_routes[17];   // index by channel 1..16
extern volatile uint8_t tulip_midi_route_active;
extern volatile uint8_t tulip_midi_notify_all;
extern volatile uint8_t tulip_midi_py_pending;
extern volatile uint32_t tulip_midi_activity;

#endif
