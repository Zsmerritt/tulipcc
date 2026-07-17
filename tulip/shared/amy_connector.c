// amy_connector.c
// all the stuff that connects Tulip to AMY
// like MIDI queue -> python
// like external CV
// like t-sequencer?
// like alles / wifi stuff?


#include "polyfills.h"
#include "py/mphal.h"
#include "py/runtime.h"
#include "py/builtin.h"
#include "amy_connector.h"
#include <stdio.h>
#include <string.h>
#ifdef ESP_PLATFORM
#include "esp_system.h"
#include "esp_attr.h"
#ifdef GAMMA9001
#include "esp_partition.h"
#endif
#endif
#ifdef AMYBOARD
// For amyboard_set_midi_out(): re-point the MIDI UART's TX line at runtime.
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/uart.h"
#include "driver/gpio.h"
#endif
uint8_t * external_map;

#ifdef AMY_IS_EXTERNAL
uint8_t * sysex_buffer;
uint16_t sysex_len = 0;
#endif

#ifdef __EMSCRIPTEN__

void midi_out(uint8_t * bytes, uint16_t len) {
    EM_ASM(
            if(midiOutputDevice != null) {
                midiOutputDevice.send(HEAPU8.subarray($0, $0 + $1));
            }, bytes, len
        );
}

#endif

// A queue to store the AMY midi messages coming IN
uint8_t last_midi[MIDI_QUEUE_DEPTH][MAX_MIDI_BYTES_PER_MESSAGE];
uint8_t last_midi_len[MIDI_QUEUE_DEPTH];
// midi_callback is a GC-rooted mp_state slot now -- the alias comes from
// tsequencer.h (a plain extern here would collide with the macro).
#include "tsequencer.h"

// volatile: ring shared between the writer task(s) and the MP task (reader)
// on different cores -- the E-11 discipline promised the compiler was never
// told about (review F-6). Push/pop protocols live in midi_in_ring.h (shared
// with the host harness in tests/midi_input/ -- run it if you touch them).
// Default build: SPSC int16_t indices, sole writer = the AMY MIDI task
// (USB + midi_local funnel through amy_midi_inject). AMY_MIDI_MPSC build:
// uint16_t monotonic counters, multi-writer CAS claim.
#include "midi_in_ring.h"
#ifdef AMY_MIDI_MPSC
volatile uint16_t midi_queue_head = 0;
volatile uint16_t midi_queue_tail = 0;
#else
volatile int16_t midi_queue_head = 0;
volatile int16_t midi_queue_tail = 0;
#endif
// Messages the ring refused because it was full (drop-newest). The silent
// twin of amy_midi_inject_drops -- exposed with it via tulip.midi_in_drops()
// so a stalled Python drain shows up as numbers, not as mystery-missing
// notes. Logged on the same power-of-two schedule.
volatile uint32_t tulip_midi_ring_drops = 0;

// ---- C-side MIDI channel router (review O-2 / boundary sketch 1) ----
// Python paid ~150-400us + ~3 heap allocs PER MESSAGE for routing that is
// pure table lookup: which boards get this channel's bytes, and whether
// any Python-routed (layered) internal instrument listens. deck/forwarder
// uploads the table after each rebuild (tulip.midi_routes); the input
// hook then forwards board bytes and drops Python entirely for channels
// C fully owns. An MPE controller streaming CC/bend at 300 msg/s used to
// cost 5-12% of the MP core and a GC-pause's worth of allocs per minute.
#include "midi_router.h"
void tulip_send_midi_out_device(uint8_t* buf, uint16_t len, int device);  // defined below
tulip_midi_route_t tulip_midi_routes[17];      // index by channel 1..16
volatile uint8_t tulip_midi_route_active = 0;  // table uploaded at least once
volatile uint8_t tulip_midi_notify_all = 1;    // schedule Python per message
                                               // (legacy default; midimon)
volatile uint8_t tulip_midi_py_pending = 0;    // outstanding scheduler entry
volatile uint32_t tulip_midi_activity = 0;     // messages seen (meter/UI poll)


#ifdef ESP_PLATFORM
#include "driver/i2c.h"

// Maps synth number -> CV channel (0 = not mapped, 1 = CV1, 2 = CV2)
// Set from Python via amyboard.set_cv_out(channel, synth)
#define MAX_CV_SYNTHS 32
uint8_t cv_synth_map[MAX_CV_SYNTHS];
// Global CV gate (review FW-7/O-8): with no CV mapped, every voice-owned
// osc still paid the synth_for_osc scan (~130 cycles) per block. Setters
// (modtulip tulip_set_cv_* / cv_local) recompute this; the render hook
// early-outs on it. NOTE (documented, not yet implemented): when CV IS
// active, the blocking I2C write below runs on the RENDER task with a
// 10ms worst-case timeout -- an AMYboard mailbox + low-prio drain task is
// the right fix, pending hardware to validate on.
volatile uint8_t tulip_any_cv_active = 0;

void tulip_recompute_cv_active(void) {
    extern uint8_t * external_map;
    uint8_t any = 0;
    for (int i = 0; i < MAX_CV_SYNTHS; i++) {
        if (cv_synth_map[i]) { any = 1; break; }
    }
    if (!any && external_map != NULL) {
        for (uint16_t i = 0; i < amy_global.config.max_oscs; i++) {
            if (external_map[i]) { any = 1; break; }
        }
    }
    tulip_any_cv_active = any;
}

// Look up which synth owns this osc, return synth number or -1
static int synth_for_osc(uint16_t osc) {
    extern uint8_t *osc_to_voice;
    if (osc_to_voice == NULL) return -1;
    uint8_t voice = osc_to_voice[osc];
    if (voice == 255) return -1;  // AMY_UNSET for uint8
    uint16_t voices[MAX_VOICES_PER_INSTRUMENT];
    for (int s = 0; s < MAX_CV_SYNTHS; s++) {
        if (cv_synth_map[s] == 0) continue;  // skip unmapped synths
        int nv = instrument_get_num_voices(s, voices);
        for (int v = 0; v < nv; v++) {
            if (voices[v] == voice) return s;
        }
    }
    return -1;
}

// AMY render hook: route osc audio to CV DAC if its synth is mapped
uint8_t external_cv_render(uint16_t osc, SAMPLE * buf, uint16_t len) {
    if (!tulip_any_cv_active) return 0;   // FW-7/O-8: nothing mapped
    // First check old per-osc map for backward compat. external_map can be
    // NULL (alloc failed in run_amy) while a synth-based CV map still holds
    // the gate above open, so it must be checked here too.
    if(external_map != NULL && external_map[osc]>0) {
        uint8_t cv_channel = external_map[osc] - 1;
#ifdef AMYBOARD
        // AMYboard GP8413 DAC at address 88, channels 0x02/0x04
        // Sample range [-1,1] -> volts [-10,+10] -> DAC [0x0000, 0x7FFF]
        float volts = S2F(buf[0]) * 10.0f;
        uint16_t value_int = (uint16_t)(((volts + 10.0f) / 20.0f) * 0x8000);
        if (value_int > 0x7FFF) value_int = 0x7FFF;
        uint8_t reg = (cv_channel == 0) ? 0x02 : 0x04;
        uint8_t bytes[3] = { reg, value_int & 0xFF, (value_int >> 8) & 0xFF };
        i2c_master_write_to_device(I2C_NUM_0, 88, bytes, 3, 1 /* tick: a wedged bus must not stall a render block (FW-7) */);
#else
        // Tulip CC DAC (different address/format)
        float volts = S2F(buf[0])*2.5f + 2.5f;
        uint16_t value_int = (uint16_t)((volts/10.0) * 65535.0);
        uint8_t bytes[3];
        bytes[2] = (value_int & 0xff00) >> 8;
        bytes[1] = (value_int & 0x00ff);
        uint8_t ch = 0x02;
        uint8_t addr = 89;
        if(cv_channel == 1) ch = 0x04;
        if(cv_channel == 2) addr = 88;
        if(cv_channel == 3) {ch = 0x04; addr=88; }
        bytes[0] = ch;
        i2c_master_write_to_device(I2C_NUM_0, addr, bytes, 3, 1 /* tick (FW-7) */);
#endif
        return 1;
    }
    // Check synth-based CV map
    int s = synth_for_osc(osc);
    if (s >= 0 && s < MAX_CV_SYNTHS && cv_synth_map[s] > 0) {
        uint8_t cv_channel = cv_synth_map[s] - 1;
#ifdef AMYBOARD
        float volts = S2F(buf[0]) * 10.0f;
        uint16_t value_int = (uint16_t)(((volts + 10.0f) / 20.0f) * 0x8000);
        if (value_int > 0x7FFF) value_int = 0x7FFF;
        uint8_t reg = (cv_channel == 0) ? 0x02 : 0x04;
        uint8_t bytes[3] = { reg, value_int & 0xFF, (value_int >> 8) & 0xFF };
        i2c_master_write_to_device(I2C_NUM_0, 88, bytes, 3, 1 /* tick: a wedged bus must not stall a render block (FW-7) */);
#endif
        return 1;
    }
    return 0;
}
#endif

// I am called when AMY receives MIDI in, whether it has been processed (played in a instrument) or not
// In tulip i just fill up the last_midi queue so that MIDI input is accessible to Python
// I also process sysex if given.
// NOTE: this hook does NOT call midi_msg_handler. amy_event_midi_message_received()
// (amy/src/amy_midi.c) already ran it on this exact data immediately before
// calling us -- doing it again here dispatched every CC mapping and every
// default-note mapping TWICE (double notes on unclaimed channels).
void tulip_midi_input_hook(uint8_t * data, uint16_t len, uint8_t is_sysex) {
    if(is_sysex) {
        // f0 and f7 are stripped on some platforms.
        // memmove + clamp, NOT a forward byte loop: on internal-AMY builds
        // `data` IS sysex_buffer (amy_midi.c passes it straight in), so the
        // old copy smeared byte 0 across the whole message (F0 F0 F0 ...).
        if(data[0]!=0xf0) {
            uint16_t n = len;
            if(n > MAX_SYSEX_BYTES - 2) n = MAX_SYSEX_BYTES - 2;
            memmove(sysex_buffer + 1, data, n);
            sysex_buffer[0] = 0xf0;
            sysex_buffer[n + 1] = 0xf7;
            sysex_len = n + 2;
        } else {
            uint16_t n = len;
            if(n > MAX_SYSEX_BYTES) n = MAX_SYSEX_BYTES;
            memmove(sysex_buffer, data, n);
            sysex_len = n;
        }
        if(midi_callback!=NULL) mp_sched_schedule(midi_callback, mp_const_true);
    } else {
        tulip_midi_activity++;
        // C router first (O-2): board forwarding + the "does Python even
        // need to see this?" decision are table lookups, not Python.
        if (len >= 1 && data[0] >= 0x80 && data[0] < 0xF0) {
            tulip_midi_route_t *r = &tulip_midi_routes[(data[0] & 0x0F) + 1];
            // Forward board bytes BEFORE the route_active gate (review C6):
            // during the microsecond route-table rewrite route_active is 0,
            // and Python's _route still sees c_router active so it also skips
            // boards -- a board-directed message in that window used to be
            // dropped. board_mask is a single 16-bit store, so reading it
            // mid-rewrite yields the old or new mask, never a torn value.
            // Before the first upload board_mask is 0, so this forwards
            // nothing -- identical to the old skip-the-whole-block behavior.
            uint16_t bm = r->board_mask;
            for (int d = 0; bm; ++d, bm >>= 1) {
                if (bm & 1) tulip_send_midi_out_device(data, len, d);
            }
            // The Python-skip decision still gates on route_active: only once
            // the table is fully published do we trust flags/notify_all to
            // drop Python entirely.
            if (tulip_midi_route_active
                    && !(r->flags & TULIP_MIDI_ROUTE_PY) && !tulip_midi_notify_all) {
                return;   // fully handled in C: no queue, no scheduler, no GC
            }
        }
        // Ring push -- the discipline (SPSC drop-newest by default, MPSC
        // claim/publish under AMY_MIDI_MPSC) lives in midi_in_ring.h with
        // its spec; the harness in tests/midi_input/ tests exactly this
        // code. A refused push is drop-newest by contract: COUNT it and say
        // so (first drop + every power of two), because "the ring was full"
        // used to be indistinguishable from "the note never arrived".
#ifdef AMY_MIDI_MPSC
        int pushed = midi_in_ring_push_mpsc(last_midi, last_midi_len,
                &midi_queue_head, &midi_queue_tail, MIDI_QUEUE_DEPTH, data, len);
#else
        int pushed = midi_in_ring_push_spsc(last_midi, last_midi_len,
                &midi_queue_head, &midi_queue_tail, MIDI_QUEUE_DEPTH, data, len);
#endif
        if (!pushed) {
            uint32_t n = ++tulip_midi_ring_drops;
            if ((n & (n - 1)) == 0)
                fprintf(stderr, "tulip midi ring full -- %u messages dropped so far "
                        "(is Python draining midi_in?)\n", (unsigned)n);
        }

        // Tell Python -- COALESCED: one outstanding scheduler entry serves
        // the whole backlog (the drain loops until midi_in empties, which
        // clears the flag). A CC storm used to enqueue one scheduler entry
        // per message.
        if (midi_callback != NULL && !tulip_midi_py_pending) {
            if (mp_sched_schedule(midi_callback, mp_const_false)) {
                tulip_midi_py_pending = 1;
            }
        }
    }
}

void midi_local(uint8_t * bytes, uint16_t len) {
#ifndef AMY_IS_EXTERNAL
#ifdef ESP_PLATFORM
    // Runs on the MicroPython task (core 1). The stream parser and everything
    // under it belong to the AMY MIDI task alone (per-stream parser state,
    // MPE globals, the SPSC ring's single-writer discipline) -- hand the
    // bytes over instead of parsing here. Costs one queue hop (<=~1ms, the
    // MIDI task's poll period); a dropped hand-off is counted and logged
    // (tulip.midi_in_drops()).
    amy_midi_inject(AMY_MIDI_SOURCE_LOCAL, bytes, len);
#else
    // Desktop builds: no funnel task exists; keep the direct parse. (The
    // macOS CoreMIDI-callback-vs-MP-thread overlap predates this code and is
    // unchanged by it.)
    convert_midi_bytes_to_messages(bytes, len, 0);
#endif
#endif
#ifdef __EMSCRIPTEN__
    for(uint16_t i=0;i<len;i++) {
        EM_ASM(
            if(typeof amy_process_single_midi_byte === 'function') {
                amy_process_single_midi_byte($0, 1);
            }, bytes[i]);
    }
#endif
}

extern bool midi_has_out;
extern void send_usb_midi_out(uint8_t * data, uint16_t len, int device);
extern void usb_broadcast_midi_out(uint8_t * data, uint16_t len);

// device: -1 = broadcast to all USB-MIDI devices + AMY (default, back-compatible);
//          0 = primary USB-MIDI device only; 1.. = one specific extra device only.
// A targeted device send does NOT also go to AMY, so routing a note to a board
// doesn't double-play it on the Tulip's own synth.
void tulip_send_midi_out_device(uint8_t* buf, uint16_t len, int device) {
#ifdef ESP_PLATFORM
#ifndef TDECK
#ifndef AMYBOARD
    if(device < 0) {
        usb_broadcast_midi_out(buf, len);
    } else {
        send_usb_midi_out(buf, len, device);
    }
#endif
#endif
#endif
#ifndef AMY_IS_EXTERNAL
    if(device < 0) amy_external_midi_output(buf, len);
#endif
}

// Back-compatible entry point (broadcast + AMY), used by existing callers.
void tulip_send_midi_out(uint8_t* buf, uint16_t len) {
    tulip_send_midi_out_device(buf, len, -1);
}

#ifndef AMY_IS_EXTERNAL

#if (defined AMYBOARD) || (defined TULIP)
#include "tulip_helpers.h"
// map the mp_obj_t to a file handle


static mp_obj_t *g_files[MAX_OPEN_FILES]; // index 1..MAX_OPEN_FILES-1 used

static uint32_t alloc_handle(mp_obj_t f) {
    for (uint32_t i = 1; i < MAX_OPEN_FILES; i++) {
        if (g_files[i] == NULL) {
            g_files[i] = f;
            return i;
        }
    }
    return HANDLE_INVALID; // table full
}

static mp_obj_t lookup_handle(uint32_t h) {
    if (h == 0 || h >= MAX_OPEN_FILES) return NULL;
    return g_files[h];
}

static void free_handle(uint32_t h) {
    if (h == 0 || h >= MAX_OPEN_FILES) return;
    g_files[h] = NULL;
}


uint32_t mp_fopen_hook(char * filename, const char * mode) {
    mp_obj_t f = tulip_fopen(filename, mode);
    if (!f) {
        return HANDLE_INVALID;
    }
    uint32_t h = alloc_handle(f);
    if (h == HANDLE_INVALID) {
        tulip_fclose(f);
        return HANDLE_INVALID;
    }
    return h;
}

uint32_t mp_fwrite_hook(uint32_t fptr, uint8_t * bytes, uint32_t len) {

    mp_obj_t f = lookup_handle(fptr);
    if (!f) {
        return 0;
    }
    uint32_t w = tulip_fwrite(f, bytes, len);
    return w;
}
#define MAX_MP_FREAD_SIZE 64
uint32_t mp_fread_hook(uint32_t fptr, uint8_t * bytes, uint32_t len) {
    mp_obj_t f = lookup_handle(fptr);
    if (!f) {
        return 0;
    }
    uint32_t total = 0;
    while (total < len) {
        uint32_t chunk = len - total;
        if (chunk > MAX_MP_FREAD_SIZE) {
            chunk = MAX_MP_FREAD_SIZE;
        }
        uint32_t r = tulip_fread(f, bytes + total, chunk);
        total += r;
        if (r < chunk) {
            break;
        }
    }
    return total;
}
void mp_fseek_hook(uint32_t fptr, uint32_t pos) {
    mp_obj_t f = lookup_handle(fptr);
    if (!f) {
        return;
    }
    (void)tulip_fseek(f, pos);
}

void mp_fclose_hook(uint32_t fptr) {
    mp_obj_t f = lookup_handle(fptr);
    if (f) {
        tulip_fclose(f);
        free_handle(fptr);
    }
}

STATIC mp_obj_t tulip_environment_transfer_done(size_t n_args, const mp_obj_t *args) {
    mp_obj_t mod = mp_import_name(MP_QSTR_amyboard, mp_const_none, MP_OBJ_NEW_SMALL_INT(0));
    mp_obj_t fn = mp_load_attr(mod, MP_QSTR_environment_transfer_done);
    return mp_call_function_0(fn);
}
STATIC MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(tulip_environment_transfer_done_obj, 0, 1, tulip_environment_transfer_done);

void mp_exec_hook(const char *code) {
#if defined(AMYBOARD)
    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        mp_obj_t code_str = mp_obj_new_str(code, strlen(code));
        mp_call_function_1(MP_OBJ_FROM_PTR(&mp_builtin_exec_obj), code_str);
        nlr_pop();
    } else {
        fprintf(stderr, "mp_exec_hook: exec raised, ignoring\n");
        mp_obj_print_exception(&mp_plat_print, MP_OBJ_FROM_PTR(nlr.ret_val));
    }
#else
    (void)code;
#endif
}

void mp_update_file_hook(const char *filename) {
#if defined(AMYBOARD)
    // Call amyboard.update_sketch_knobs(filename) synchronously. This runs
    // from the C-side zA handler, so any unhandled Python exception would
    // NLR-longjmp out of the sysex parser mid-message and the subsequent zD
    // would never be processed (Pull would hang). Wrap the call in an NLR
    // buffer so exceptions get swallowed and the parser can continue.
    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        mp_obj_t mod = mp_import_name(MP_QSTR_amyboard, mp_const_none, MP_OBJ_NEW_SMALL_INT(0));
        mp_obj_t fn = mp_load_attr(mod, MP_QSTR_update_sketch_knobs);
        mp_obj_t path = mp_obj_new_str(filename, strlen(filename));
        mp_call_function_1(fn, path);
        nlr_pop();
    } else {
        // Python raised — swallow so the sysex parser can continue to zD.
        fprintf(stderr, "mp_update_file_hook: update_sketch_knobs raised, ignoring\n");
        mp_obj_print_exception(&mp_plat_print, MP_OBJ_FROM_PTR(nlr.ret_val));
    }
#else
    (void)filename;
#endif
}

void mp_file_transfer_done_hook(const char *filename) {
#if defined(AMYBOARD)
    if (filename == NULL || filename[0] == '\0') {
        return;
    }
    const char *leaf = filename;
    const char *slash = strrchr(filename, '/');
    if (slash != NULL && slash[1] != '\0') {
        leaf = slash + 1;
    }
    if (strcmp(leaf, "sketch.py") == 0) {
        mp_sched_schedule(MP_OBJ_FROM_PTR(&tulip_environment_transfer_done_obj), mp_const_none);
    }
#else
    (void)filename;
#endif
}


#ifdef ESP_PLATFORM
RTC_NOINIT_ATTR uint32_t amyboard_bootloader_flag;
#define AMYBOARD_BOOTLOADER_MAGIC 0xABCD0001
#endif

void mp_reboot_hook(uint8_t mode) {
#if defined(AMYBOARD) && defined(ESP_PLATFORM)
    if (mode == 0) {
        // Bootloader mode: skip sketch on next boot.
        amyboard_bootloader_flag = AMYBOARD_BOOTLOADER_MAGIC;
        esp_restart();
    } else if (mode == 1) {
        // Normal reboot: run sketch as usual.
        esp_restart();
    }
#endif
}

#ifdef ESP_PLATFORM
// Grow AMY's flash-fence window to cover every mmap'd PCM bank: renders from
// [lo, hi) emit silence while amy_flash_fence is up (tulip.flash_fence), so
// a flash program/erase can never race a mapped sample fetch -- one such
// fetch during the cache-suspended write window hard-crashes the chip
// (dual-core TG1WDT, reproduced live). PSRAM-fallback banks land outside the
// window and keep sounding through writes.
static void widen_flash_fence(const void *map, uint32_t size) {
    if (amy_flash_fence_lo == NULL || map < amy_flash_fence_lo)
        amy_flash_fence_lo = map;
    const void *end = (const void *)((const uint8_t *)map + size);
    if (end > amy_flash_fence_hi)
        amy_flash_fence_hi = end;
}
#endif

#if defined(GAMMA9001) && defined(ESP_PLATFORM)
// Map the `drums` flash partition (raw drums.bin from the amy repo, flashed by
// fs_create.py) into the data address space and hand it to AMY, which serves
// the Gamma9001 bank presets (256+) straight out of it. If the partition is
// missing or unreadable, those presets stay silent -- the baked TR-808 kit
// (patch 384) still works.
static void mount_gamma9001_drums(void) {
    const esp_partition_t *part = esp_partition_find_first(
        ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY, "drums");
    if (part == NULL) {
        fprintf(stderr, "gamma9001: no drums partition, bank presets unavailable\n");
        return;
    }
    const void *map = NULL;
    esp_partition_mmap_handle_t handle;  // never unmapped; the samples live as long as AMY
    esp_err_t err = esp_partition_mmap(part, 0, part->size, ESP_PARTITION_MMAP_DATA, &map, &handle);
    if (err != ESP_OK || map == NULL) {
        fprintf(stderr, "gamma9001: drums partition mmap failed (%d)\n", (int)err);
        return;
    }
    widen_flash_fence(map, part->size);
    amy_set_gamma9001_pcm((const int16_t *)map);
}
#endif

#if defined(GM_FONTS) && defined(ESP_PLATFORM)
// GM SoundFont banks: the `fonts` partition holds the GeneralUser bank at 0
// and the big multi-font bank at 0x4B0000 (fs_create.py assembles them),
// mapped separately (a single 12.5MB map needs contiguous vaddr the S3
// doesn't have). Whatever doesn't fit the 16MB dynamic mmap pool falls back
// to a PSRAM copy inside map_or_load_partition.
// Must match GM_BIG_OFFSET in tulip/fs_create.py and the `fonts` partition
// geometry in boards/N32R8/tulip-partitions-32MB.csv. The GeneralUser bank
// grew past the old 0x300000 when its capped presets were rebaked with their
// real length + loops (tools/gm/README.md); a stale value here reads the big
// bank from the wrong offset and plays garbage.
#define GM_BIG_BYTE_OFFSET 0x4B0000
static const void *map_or_load_partition(const esp_partition_t *part,
                                     uint32_t off, uint32_t size,
                                     const char *what) {
    const void *map = NULL;
    esp_partition_mmap_handle_t handle;  // never unmapped; lives as long as AMY
    esp_err_t err = esp_partition_mmap(part, off, size,
                                       ESP_PARTITION_MMAP_DATA, &map, &handle);
    if (err == ESP_OK && map != NULL) {
        widen_flash_fence(map, size);
        return map;
    }
    // The S3's dynamic flash-mmap pool is a hard 16MB (the lower half of the
    // 32MB data space is statically claimed by the PSRAM aperture + the
    // SPIRAM_FETCH_INSTRUCTIONS/RODATA relocations, measured live), and the
    // three PCM banks total 16.02MB -- whichever bank loses the packing race
    // gets COPIED into PSRAM instead (~9.8MB free; AMY just reads a pointer).
    void *buf = heap_caps_malloc(size, MALLOC_CAP_SPIRAM);
    if (buf == NULL) {
        fprintf(stderr, "gm: %s mmap failed (%d) and PSRAM alloc of %u failed; bank unavailable\n",
                what, (int)err, (unsigned)size);
        return NULL;
    }
    if (esp_partition_read(part, off, buf, size) != ESP_OK) {
        free(buf);
        fprintf(stderr, "gm: %s partition read failed; bank unavailable\n", what);
        return NULL;
    }
    fprintf(stderr, "gm: %s vaddr pool full -> loaded %u bytes into PSRAM\n",
            what, (unsigned)size);
    return buf;
}

static void mount_gm_fonts(void) {
    const esp_partition_t *part = esp_partition_find_first(
        ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY, "fonts");
    if (part == NULL) {
        fprintf(stderr, "gm: no fonts partition, GM presets unavailable\n");
        return;
    }
    // Largest map first: the free vaddr pool is only ~128KB bigger than the
    // big bank's 9.5MB, so it must grab its contiguous block before the
    // smaller maps fragment the space (observed live: big-bank mmap short by
    // exactly two MMU pages when mounted after the others).
    const void *big = map_or_load_partition(part, GM_BIG_BYTE_OFFSET,
                                        part->size - GM_BIG_BYTE_OFFSET, "big bank");
    if (big != NULL)
        amy_set_gm_big_pcm((const int16_t *)big);
}

static void mount_gm_fonts_small(void) {
    const esp_partition_t *part = esp_partition_find_first(
        ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY, "fonts");
    if (part == NULL)
        return;
    const void *small = map_or_load_partition(part, 0, GM_BIG_BYTE_OFFSET, "GeneralUser bank");
    if (small != NULL)
        amy_set_gm_pcm((const int16_t *)small);
}
#endif

void run_amy(uint8_t midi_out_pin) {
    amy_config_t amy_config = amy_default_config();
    amy_config.amy_external_midi_input_hook = tulip_midi_input_hook;
    amy_config.amy_external_render_hook = external_cv_render;
    amy_config.amy_external_fopen_hook = mp_fopen_hook;
    amy_config.amy_external_fseek_hook = mp_fseek_hook;
    amy_config.amy_external_fclose_hook = mp_fclose_hook;
    amy_config.amy_external_fread_hook = mp_fread_hook;
    amy_config.amy_external_fwrite_hook = mp_fwrite_hook;
    amy_config.amy_external_file_transfer_done_hook = mp_file_transfer_done_hook;
    amy_config.amy_external_update_file_hook = mp_update_file_hook;
    amy_config.amy_external_exec_hook = mp_exec_hook;
    amy_config.amy_external_reboot_hook = mp_reboot_hook;
    extern void tulip_amy_sequencer_hook(uint32_t tick_count);
    amy_config.amy_external_sequencer_hook = tulip_amy_sequencer_hook;
    amy_config.audio = AMY_AUDIO_IS_I2S;
#if defined(AMYBOARD) || defined(AMYBOARD_WEB)
    extern float cv_input_hook(uint16_t channel);
    amy_config.amy_external_coef_hook = cv_input_hook;
#endif
#ifdef AMYBOARD
    amy_config.features.audio_in = 1;
    amy_config.midi = AMY_MIDI_IS_UART | AMY_MIDI_IS_USB_GADGET;
#else
    amy_config.features.audio_in = 0;
    amy_config.midi = AMY_MIDI_IS_UART;
#endif
    amy_config.features.default_synths = 0; // midi.py does this for us
    // Synthesized drum kits store one RAM patch per hit (~19 per kit) at
    // deterministic slots; the stock 32-slot pool can't hold two kits plus
    // the deck's other patch_string synths.
    amy_config.max_memory_patches = 128;
    amy_config.i2s_lrc = CONFIG_I2S_LRCLK;
    amy_config.i2s_bclk = CONFIG_I2S_BCLK;
    amy_config.i2s_dout = CONFIG_I2S_DOUT;
    amy_config.i2s_din = CONFIG_I2S_DIN;
    amy_config.i2s_mclk = CONFIG_I2S_MCLK;
    amy_config.midi_out = midi_out_pin;
    amy_config.midi_in = MIDI_IN_PIN;
#ifndef AMYBOARD
    amy_config.features.startup_bleep = 1;
#endif
// Mount order = size order (big bank, drums, GeneralUser): the pool is
// tight enough that the largest map must allocate first, and if something
// has to lose, it must not be the Kits (drums) the deck already ships.
#if defined(GM_FONTS) && defined(ESP_PLATFORM)
    mount_gm_fonts();
#endif
#if defined(GAMMA9001) && defined(ESP_PLATFORM)
    mount_gamma9001_drums();
#endif
#if defined(GM_FONTS) && defined(ESP_PLATFORM)
    mount_gm_fonts_small();
#endif
    amy_start(amy_config);
    external_map = malloc_caps(amy_config.max_oscs, MALLOC_CAP_INTERNAL);
    if(external_map == NULL) {
        // unchecked, this NULL-deref'd in the init loop right below. Leave it
        // NULL and keep booting: every reader gates on it, so the cost is
        // per-osc CV out, not the device.
        fprintf(stderr, "run_amy: external_map alloc of %u bytes FAILED -- per-osc CV out disabled\n",
                (unsigned)amy_config.max_oscs);
    } else {
        for(uint16_t i=0;i<amy_config.max_oscs;i++) external_map[i] = 0;
    }
    for(uint8_t i=0;i<MAX_CV_SYNTHS;i++) cv_synth_map[i] = 0;
}

#ifdef AMYBOARD
// Switch the MIDI OUT TRS standard at runtime (Type A = pin 14, Type B = pin 15)
// without restarting AMY. AMY transmits MIDI via uart_write_bytes(UART_NUM_1, ...) —
// keyed on the UART number, not the GPIO — so moving the UART's TX line to the other
// TRS leg is all that's needed. midi_uart is 1 on AMYboard (amy's esp_get_uart(1) ==
// UART_NUM_1). Only MIDI OUT differs by type; MIDI IN works for both, so RX (MIDI_IN_PIN)
// is left unchanged.
void amyboard_set_midi_out(uint8_t midi_out_pin) {
    const uint8_t other_pin = (midi_out_pin == MIDI_OUT_PIN_A) ? MIDI_OUT_PIN_B : MIDI_OUT_PIN_A;
    // Let any in-flight MIDI byte finish before moving the TX line.
    uart_wait_tx_done(UART_NUM_1, pdMS_TO_TICKS(20));
    // Re-route the UART's TX to the requested TRS data leg.
    uart_set_pin(UART_NUM_1, midi_out_pin, MIDI_IN_PIN, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
    // Disconnect the now-unused leg from the UART and hold it high (MIDI idle/source).
    // Driving it as a plain GPIO output stops it from mirroring the TX signal.
    gpio_reset_pin(other_pin);
    gpio_set_direction(other_pin, GPIO_MODE_OUTPUT);
    gpio_set_level(other_pin, 1);
}
#endif

#elif defined TULIP_DESKTOP

void run_amy(uint8_t capture_device_id, uint8_t playback_device_id) {
    amy_config_t amy_config = amy_default_config();
    amy_config.amy_external_midi_input_hook = tulip_midi_input_hook;
    extern void tulip_amy_sequencer_hook(uint32_t tick_count);
    amy_config.amy_external_sequencer_hook = tulip_amy_sequencer_hook;
    amy_config.features.default_synths = 0; // midi.py does this for us
    amy_config.capture_device_id = capture_device_id;
    amy_config.playback_device_id = playback_device_id;
    amy_config.features.audio_in = 1;
    amy_config.audio = AMY_AUDIO_IS_MINIAUDIO;
    //amy_config.i2s_din = 0;  // Dummy to indicate has audio in.
    amy_config.features.startup_bleep = 1;
#ifdef GAMMA9001
    // Tulip Desktop links drums.bin straight into the binary (see tulip.mk).
    {
        extern const int16_t gamma9001_pcm_data[];
        amy_set_gamma9001_pcm(gamma9001_pcm_data);
    }
#endif
    amy_start(amy_config);
}

#endif

#endif
