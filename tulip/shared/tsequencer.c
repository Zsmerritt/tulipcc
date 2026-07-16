
#include "tsequencer.h"
#include <inttypes.h>

// The callback/arg stores are GC-rooted mp_state slots; the registrations
// live in modtulip.c (root-pointer collection only scans QSTR-bearing
// sources) and tsequencer.h aliases the old names onto them.
uint32_t sequencer_period[SEQUENCER_SLOTS];
uint32_t sequencer_tick[SEQUENCER_SLOTS];
uint32_t defer_sysclock[DEFER_SLOTS];

#ifdef AMY_IS_EXTERNAL
uint32_t sequencer_tick_count = 0;
#endif

void tulip_amy_sequencer_hook(uint32_t tick_count) {
    #ifdef AMY_IS_EXTERNAL
        sequencer_tick_count = tick_count;
    #endif
    for(uint8_t i=0;i<DEFER_SLOTS;i++) {
        if(defer_callbacks[i] != NULL && get_ticks_ms() > defer_sysclock[i]) {
            // Clear the slot ONLY when the scheduler accepted the entry
            // (review F-3): mp_sched_schedule fails when the shared queue
            // is full -- exactly when the MP task is stalled and deck code
            // is leaning on defers. Dropping silently wedged the config
            // write chain (all saves stopped), stuck preview notes, and
            // half-completed Back navigation. A refused slot stays armed
            // and retries next tick.
            if (mp_sched_schedule(defer_callbacks[i], defer_args[i])) {
                defer_callbacks[i] = NULL; defer_sysclock[i] = 0; defer_args[i] = NULL;
            }
        }
    }

    for(uint8_t i=0;i<SEQUENCER_SLOTS;i++) {
        if(sequencer_period[i]!=0) {
            uint32_t offset = tick_count % sequencer_period[i];
            if(offset == sequencer_tick[i]) {
                mp_sched_schedule(sequencer_callbacks[i], mp_obj_new_int(tick_count));
            }
        }
    }
}


void tsequencer_init() {
    for(uint8_t i=0;i<SEQUENCER_SLOTS;i++) { sequencer_callbacks[i] = NULL; sequencer_period[i] = 0; sequencer_tick[i] = 0; }
    for(uint8_t i=0;i<DEFER_SLOTS;i++) { defer_callbacks[i] = NULL; defer_sysclock[i] = 0; }
    #ifndef AMY_IS_EXTERNAL
    amy_global.config.amy_external_sequencer_hook = tulip_amy_sequencer_hook;
    #endif
}
