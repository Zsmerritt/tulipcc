//  tsequencer.h
#ifndef __TSEQUENCERH
#define __TSEQUENCERH

#define SEQUENCER_SLOTS 8
// 64 (was 32): the pool is a global failure domain -- exhaustion raises at
// the CALLER, and several deck features respond by permanently disabling
// themselves (E-9). Steady state uses 3-4; bursts (panel fills, toasts,
// preview note-offs, tab chains) stacked toward the old cap. 32 more
// pointers of RAM.
#define DEFER_SLOTS 64
#include "py/mphal.h"
#include "py/runtime.h"
#include <stdio.h>
#include "polyfills.h"
#ifndef AMY_IS_EXTERNAL
#include "sequencer.h" 
#else
extern uint32_t sequencer_tick_count;
#define AMY_SEQUENCER_PPQ 48
#endif
// The callback/arg stores are GC ROOT POINTERS (MP_REGISTER_ROOT_POINTER in
// tsequencer.c / modtulip.c), NOT plain C globals: a plain global holding an
// mp_obj_t is invisible to MicroPython's GC, so a lambda whose only
// reference was tulip.defer()'s slot got COLLECTED and its heap block
// reused -- firing the defer then called whatever object had moved in
// (typically a boxed float: the on-device "TypeError: 'float' object isn't
// callable" correlated with saves/rebuilds, which trigger GC). Same hazard
// for seq callbacks and the MIDI drain callback (a collected drain closure
// silently killed ALL MIDI until reboot). The aliases keep every existing
// call site source-compatible.
#define sequencer_callbacks MP_STATE_PORT(tulip_sequencer_callbacks)
#define defer_callbacks MP_STATE_PORT(tulip_defer_callbacks)
#define defer_args MP_STATE_PORT(tulip_defer_args)
#define midi_callback MP_STATE_PORT(tulip_midi_callback_obj_ref)
extern uint32_t sequencer_period[SEQUENCER_SLOTS];
extern uint32_t sequencer_tick[SEQUENCER_SLOTS];
extern uint32_t defer_sysclock[DEFER_SLOTS];


void tsequencer_init();
void tulip_amy_sequencer_hook(uint32_t tick_count);

#endif