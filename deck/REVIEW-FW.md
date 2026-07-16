# Firmware Review — Tulip CC deck fork, C layer (round 1)

Scope: `amy/src/` (render/fill pipeline, PCM, FX, oscillators, filters, partials, FM,
PIE blockops), `tulip/esp32s3/` (flash fence wrap, tasks, board configs, cmake),
`tulip/shared/amy_connector.c`. Target: TULIP4_R11, ESP32-S3 N32R8, octal PSRAM+flash
@120MHz, IDF v5.4.1 (per `.github/workflows/*.yml`), dual-core render, 256-sample
blocks @44.1kHz.

**Budget arithmetic used throughout:** one block = 256/44100 s = 5.805 ms; at 240 MHz
that is **1,393,197 cycles per core per block** ("1.39M"). 1% of a core ≈ 13.9k cycles.

Task layout as built (verified):
- core 0: `amy_r_task` render oscs 0..N/2 (prio MAX−3, `amy/src/amy.h:1228,1231`),
  `midi_task` (prio MAX−2, `amy/src/amy_midi.h:66-69`), display (MIN), touchscreen
  (MIN+1), sequencer (MAX−3) — `tulip/esp32s3/tasks.h`.
- core 1: `amy_fb_task` executes deltas, renders oscs N/2..N, runs FX + fill + I2S
  (prio MAX−3, `amy/src/amy.h:1229,1232`), MicroPython (MIN+1), USB (MIN+1).
- `max_oscs` = 250 default (`amy/src/api.c:38`); fbl/scratch/output blocks in internal
  SRAM, synth structs / deltas / delay lines in PSRAM (`amy/src/api.c:46-52`).

---

## 1. Executive summary

The deck-fork C layer is in good shape where it has been recently worked: the flash
fence (wrap + total_blocks handshake) is a correct design with two concrete coverage
gaps; the bus-activity mask (OPT-11) is internally consistent; the PIE asm in
`amy_blockops.h`/`algorithms.c` has correct constraints and guards. The most serious
findings are (a) a **completely unvalidated bus index** that flows from external input
(MIDI sysex AMY messages, `y` param) into array indexes of size 4 — including a
stack-array write in `amy_fill_buffer` — giving memory corruption on the audio task
(FW-1); (b) **`render_wavetable` reads sample RAM that can live in mmap'd flash with
no fence check**, reopening the exact dual-core TG1WDT crash the fence was built to
close (FW-3); (c) a wave-type check that misses `PCM_LEFT/PCM_RIGHT` so note-off can
never stop a looping stereo sample (FW-2); and (d) a cross-task synth-mutation window
(patch loads execute deltas on the MIDI/MP task while renders are in flight, including
a free+realloc of `synthinfo`) that is a real use-after-free path (FW-4). On the
optimization side the single biggest lever is not micro-code but **scheduling**: the
static `AMY_OSCS/2` core split puts essentially all busy oscillators on core 0 (voices
allocate from low osc numbers) while core 1 waits — a dynamic split recovers up to
~13% of the block budget at peak. A ranked table with cycle math is in §3.

Finding counts: **1 CRIT, 3 HIGH, 6 MED, 5 LOW/INFO.**

---

## 2. Findings

### FW-1 (CRIT) — Bus index from the wire is never range-checked; OOB writes on the audio task

**Where:** `amy/src/parse.c:464` and `:810` (`e->bus = atoi(...)`, the `y` wire param);
`amy/src/amy.c:628-630` (bus-directed command stores `e->bus` into `d.osc` and raises
`highest_bus` unclamped), `amy/src/amy.c:666-667` (same for osc-directed events),
`amy/src/amy.c:1327` (`DELTA_TO_SYNTH_I(BUS, bus)` → `synth[osc]->bus`, uint8),
`amy/src/amy.c:1454-1475` (`amy_global.bus[bus]->...` deref), `amy/src/amy.c:1867-1872`
(`fbl[core][bus]`, `1u << bus`), `amy/src/amy.c:2095-2097` (`SAMPLE
volume_scale[AMY_NUM_BUSES]` written up to `highest_bus`). `AMY_NUM_BUSES` is 4
(`amy/src/amy.h:141`). Nothing anywhere clamps: `grep 'bus >= AMY_NUM_BUSES'` has zero
hits in `amy/src`.

**Failure path (walked):** a sysex AMY message (header 00 03 45, `amy/src/amy_midi.c:326`)
or any `amy.send` string carrying e.g. `y37`:
1. `parse.c:810` sets `e->bus = 37`.
2. If the event also carries `l` (echo level etc.), `amy.c:628` sets `d.osc = 37` and
   `amy.c:630` sets `amy_global.highest_bus = 37`. `play_delta` (`amy.c:1462`) then does
   `config_echo(37, ...)` → `amy_global.bus[37]->echo...` — `bus[]` has 4 entries
   (`amy.h:912`); this dereferences a garbage pointer and **writes** through it.
3. If instead it's an osc event, `synth[osc]->bus = 37`; next block `amy_render`
   (`amy.c:1867-1872`) computes `1u << 37` (UB) and `amy_block_zero(fbl[core][37], 2KB)`
   — `fbl` is a 2×4 pointer array; index 37 reads a wild pointer from .bss and zeroes
   2KB through it.
4. Even with no osc playing, `amy_fill_buffer` loops `bus <= highest_bus` and writes
   `volume_scale[bus]` (`amy.c:2096-2097`) — a 4-entry **stack** array on the fill task
   → stack smash at exactly the moment audio runs.

**Fix:** clamp at ingestion: in `amy_event_to_deltas_queue` (`amy.c:628` and `:665-667`)
reject/clamp `e->bus >= AMY_NUM_BUSES`; belt-and-braces clamp `synth[osc]->bus` in
`amy_render` and `d->osc` in the bus-directed branch of `play_delta`. One-line clamps;
re-verify by sending `y200` over sysex and via `amy.send`.

### FW-2 (HIGH) — `hold_and_modify` misses PCM_LEFT/PCM_RIGHT: note-off cannot stop a looping stereo sample

**Where:** `amy/src/amy.c:1687`:
```c
if (synth[osc]->wave != PCM && synth[osc]->wave != CUSTOM)  msynth[osc]->feedback = synth[osc]->feedback;
```
but the PCM family is three waves: `AMY_WAVE_IS_PCM(w) ((w)==PCM || (w)==PCM_LEFT || (w)==PCM_RIGHT)`
(`amy/src/amy.h:311`), and `pcm_note_on`/`pcm_note_off`/`render_pcm` all serve
PCM_LEFT/PCM_RIGHT (`amy/src/amy.c:1226-1230`, `pcm.c:385-390`).

**Failure path:** the loop-stop protocol is: `pcm_note_off` (`pcm.c:289-296`) clears
`msynth[osc]->feedback = 0` so `render_pcm`'s loop test (`pcm.c:417`) lets the sample
run to its end. For `wave == PCM_LEFT` (or `_RIGHT`), `hold_and_modify` re-copies
`synth->feedback` (still >0) into `msynth->feedback` **every block**, undoing the
note-off. Because `pcm_note_on` also sets `terminate_on_silence = 0` (`pcm.c:262`), the
zero-amp reaper (`amy.c:1832`) never fires either: the voice loops forever, burning
~0.4%/core each (render + h&m) and permanently occupying the osc. Second-order effect:
`feedback>=2` "sustain-through-release" (`pcm.c:279`) also never re-arms correctly for
stereo presets.

**Fix:** `if (!AMY_WAVE_IS_PCM(synth[osc]->wave) && synth[osc]->wave != CUSTOM) ...`.
Re-verify: load a stereo looped memory sample as PCM_LEFT, note-on then note-off,
confirm it ends.

### FW-3 (HIGH) — `render_wavetable` bypasses the flash fence: the TG1WDT crash class is still open

**Where:** `amy/src/oscillators.c:869-887`. It fetches
`pcm_get_sample_ram_for_preset(preset, ...)` (`pcm.c:196-206`) — which happily returns
pointers into the **mmap'd `fonts`/`drums` partitions** for preset numbers in the
GAMMA9001/GM/GM_BIG ranges — and then `render_lut` streams samples from that pointer
with **no `amy_flash_fence` check**. The fence check exists only in `render_pcm`
(`pcm.c:351-358`).

**Failure path:** osc with `wave=WAVETABLE, preset=<GM-range number>` (reachable from
Python or the wire; nothing restricts wavetable presets to memory PCM) is sounding;
deckcfg/decklog/editor-save triggers `esp_partition_write` → fence raises, cache
suspends for the program op → `render_lut` fetches `wavetable_sample_ram[...]` from
flash-mapped vaddr → cache-disabled access → dual-core interrupt WDT, the exact crash
documented at `pcm.c:39-48` and `flash_fence_wrap.c:3-8`. The fence wrap made writes
safe *for render_pcm only*; this is the remaining renderer that can touch the window.

**Fix:** after resolving `wavetable_sample_ram` (`oscillators.c:869-875`), apply the
same window test as `pcm.c:351-353` and return 0 (phase held) while fenced. Also audit
`compute_mod_pcm` (`pcm.c:449`) — it goes through `render_pcm`, so it's covered.

### FW-4 (HIGH) — Patch-load path mutates synth state on the MIDI/MP task while renders are in flight (UAF window)

**Where:** `amy/src/amy.c:641-648`: an event with `voices`/`synth` + `patch_number`
calls `amy_execute_deltas()` **and** `patches_load_patch(e)` synchronously on whatever
task delivered the event. On Tulip that is the MIDI task (program change / sysex,
prio MAX−2, core 0) or the MP task. Renders are NOT serialized against this: the
delta lock only guards the queue; `amy_render` on both cores reads `synth[]`/`msynth[]`
lock-free (`amy.c:1865-1890`), by design sequenced only against the *fill task's own*
`amy_execute_deltas` (`i2s.c:323-326`).

**Failure path (concrete UAF):** fill task has notified `amy_r_task`; both cores are
mid-`amy_render`. MIDI program change arrives → MIDI task (higher prio, core 0,
preempts the render task) runs `amy_event_to_deltas_queue` → `patches_load_patch` →
deltas → `play_delta` → `ensure_osc_allocd(osc, more_breakpoints)` decides to realloc:
`free_osc(osc); alloc_osc(osc, ...)` (`amy.c:1002-1003`). Core 1 is concurrently inside
`render_osc_wave(osc, ...)` holding `synth[osc]` — freed memory. Even without realloc,
half-applied patch state is read mid-block (audible artifacts). The same window covers
`pcm_load`/`pcm_unload_preset` mutating the memorypcm linked list (`pcm.c:558-575`)
while `render_pcm` walks it on the other core.

**Fix options (in order of preference):** (1) never execute deltas outside the fill
task: replace `amy.c:643`'s `amy_execute_deltas()` + synchronous `patches_load_patch`
with a queued "load patch" delta drained at the top of `esp_fill_audio_buffer_task`;
(2) or gate `amy_render` start/end with a light rwlock the loader takes exclusively.
Re-verify with a MIDI program-change flood during a held chord (the deck UX-review
harness can drive this).

### FW-5 (MED) — MIDI task outranks the core-0 render task

**Where:** `amy/src/amy_midi.h:66-69` (`MIDI_TASK_COREID 0`, prio `ESP_TASK_PRIO_MAX-2`)
vs render at MAX−3 on the same core (`amy/src/amy.h:1228,1231`).

**Failure/cost:** every MIDI byte burst preempts osc rendering; a sysex patch upload
(KBytes, parsed in `convert_midi_bytes_to_messages` → handlers, `amy_midi.c:390-460`)
can hold core 0 for >1 block at a priority the render task cannot preempt, and via
FW-4 also does heavy work there. The fill task (core 1) blocks in
`ulTaskNotifyTake(portMAX_DELAY)` (`i2s.c:280`) — the whole pipeline stalls; the I2S
DMA ring absorbs only a few ms. The comment at `amy.h:1221-1227` shows priority
interactions here were already bitten once (flash-guard IPC tie).

**Fix:** drop MIDI task to below render (e.g. MAX−4) — MIDI latency of one block
(5.8ms) is inaudible next to the current risk — or move it to core 1 below the fill
task. Pairs with the FW-4 fix.

### FW-6 (MED) — Chained oscs across the static core split can be rendered twice (racy `render_clock` guard)

**Where:** `amy/src/i2s.c:264` vs `:278` (core 1 renders 0..N/2, core 0 renders
N/2..N); `amy/src/amy.c:1808-1815` chained-osc recursion; the only reentry guard is
`synth[osc]->render_clock != amy_global.total_samples` (`amy.c:1761-1762`), a plain
read/write with no atomicity.

**Failure path:** osc A < N/2 (core 1) has `chained_osc = B >= N/2` (core 0's range,
B AUDIBLE). Both cores can pass the `render_clock` test before either writes it → B
is rendered twice in the same block into two different buffers: phase advances 2×
(pitch chirp), envelope state (`last_amp`, filter delays) written concurrently. Voices
allocated near the N/2 boundary by `patches.c` make this reachable in practice, not
just via exotic API use. Not a crash (all fields are ≤32-bit), but an audible
correctness race.

**Fix:** either partition by voice (never split a chain across the midpoint — patches
knows chain extents), or make the render_clock claim a `__atomic_compare_exchange` so
exactly one core renders and the other adds nothing that block.

### FW-7 (MED) — Blocking I2C write inside the render hook

**Where:** `tulip/shared/amy_connector.c:115,129,143` — `i2c_master_write_to_device(...,
pdMS_TO_TICKS(10))` called from `external_cv_render`, which `amy_render` invokes on the
render/fill tasks (`amy.c:1879-1885`) for every audible osc when a CV map is active.

**Failure/cost:** a held/slow I2C bus (shared with touch on some boards) stalls a
render block by up to 10ms — nearly two full block budgets — at MAX−3 priority →
guaranteed underrun cascade. Even the happy path (~0.1-0.2ms at 400kHz for addr+3
bytes) is 4-8k cycles (0.3-0.6%) spent *blocked* on the audio task per mapped osc.
Secondary: with no CV mapped, `synth_for_osc` still scans `cv_synth_map[32]` for every
voice-owned osc (`amy_connector.c:92-98`), ~100+ cycles/osc/block (see OPT-8).

**Fix:** render hook writes the latest CV sample to a per-channel mailbox; a MIN+1
priority task does the I2C. Early-out `external_cv_render` on a global
`any_cv_mapped` flag.

### FW-8 (MED) — Fence wrap does not cover `esp_flash_*` direct writes

**Where:** `tulip/esp32s3/esp32_common.cmake:412-414` wraps only
`esp_partition_write/write_raw/erase_range`. MicroPython's legacy `esp.flash_write()` /
`esp.flash_erase()` (`micropython/ports/esp32/modesp.c`) call `esp_flash_write` /
`esp_flash_erase_region` on the chip object directly, bypassing the partition API and
therefore the fence. littlefs/NVS/OTA are covered (they funnel through
`esp_partition_*`), so the main deck paths are safe — but the "no caller can forget the
fence again" claim (`flash_fence_wrap.c:11-15`) has this one hole, plus panic-time
core-dump writes (irrelevant — already crashing).

**Fix:** add `--wrap=esp_flash_write` / `--wrap=esp_flash_erase_region` with the same
acquire/release (recursion is safe: partition API calls esp_flash internally, and
`fence_depth` is a counter), or accept and document that `esp.flash_write` is
forbidden from Python.

### FW-9 (MED) — Delta pool: malloc under the queue lock; `abort()` on exhaustion

**Where:** `amy/src/amy.c:2338-2344` (`delta_get` → `deltas_add_pool_block` mallocs a
2048-delta SPIRAM block), called inside `add_delta_to_queue` **while holding
`amy_queue_lock`** (`amy.c:551-557`); `amy.c:2318-2321` aborts the whole firmware after
16 blocks (32,768 queued deltas).

**Failure path:** (a) priority inversion: the fill task's `amy_execute_deltas` blocks
on `amy_queue_lock` while a MIN+1 MP task inside `delta_get` waits on the heap lock
held by another low task → audio stalls for an unbounded time (mutex priority
inheritance helps for the queue lock but not the heap lock chain). (b) A runaway
Python loop scheduling far-future events (e.g. `time=+hours` sequencer misuse) reaches
the 16-block cap → `abort()` → device reboot mid-performance.

**Fix:** preallocate a fixed pool sized for the deck (e.g. 8k deltas) at init;
on exhaustion drop the event with a rate-limited log instead of `abort()`; never
malloc under the lock (allocate outside, link inside).

### FW-10 (MED) — MIDI SPSC queue publishes tail without a barrier

**Where:** `tulip/shared/amy_connector.c:204-215`. Writer (MIDI task, core 0) copies
payload into `last_midi[tail][..]`, then `midi_queue_tail = next`. Neither
`midi_queue_tail` nor the payload arrays are `volatile`, and there is no
`__atomic_store_n(..., __ATOMIC_RELEASE)`; the reader is the MP task on core 1. The
SPSC head/tail discipline itself (per the E-11 comment) is right — this is purely a
publication-ordering gap: the compiler is licensed to sink the payload stores past the
tail store (the Xtensa core is in-order, so today the compiler is the only realistic
reorderer — but `-O3`/LTO changes are exactly when such latent bugs appear).

**Fix:** `__atomic_store_n(&midi_queue_tail, next, __ATOMIC_RELEASE)` and matching
acquire load on the reader side (or minimally: make tail/head volatile and insert a
compiler barrier after the payload copy).

### FW-11 (LOW) — Fence drop-timer creation race

**Where:** `tulip/esp32s3/flash_fence_wrap.c:101-110`. Two tasks can both see
`fence_drop_timer == NULL` (creation happens outside `fence_mux`) and both
`esp_timer_create`; one handle leaks (once, ~few hundred bytes) and the loser's timer
is never stopped by later `esp_timer_stop(fence_drop_timer)` calls — a stray early
fence drop becomes possible (then a racing render could fetch during a still-active
burst window; the *handshake* on the next acquire still protects the actual write, so
consequence is limited to losing the batching optimization). Fix: create the timer
once at init, or double-check inside the mux.

### FW-12 (LOW) — OOM paths dereference NULL

- `new_reverb` bzero's an unchecked `malloc_caps` (`amy/src/delay.c:201-203`).
- `alloc_chorus_delay_lines` never checks `delay_mod` (`amy/src/amy.c:304`) though it
  checks the delay lines.
- `filters_init` checks none of its 1+1+3+2N+6N mallocs (`amy/src/filters.c:488-502`).
- `deltas_pool_alloc` writes through unchecked malloc (`amy/src/amy.c:2300-2311`).

On a fragmented 8MB PSRAM after big PSRAM-fallback bank loads (9.5MB bank →
`amy_connector.c:510`), these are reachable. Fix: check + disable the feature with a
log, as `alloc_echo_delay_lines` already does.

### FW-13 (LOW) — `pcm_load` loop-point validation

`amy/src/pcm.c:524-556`: `loopstart` never validated against `length`; `loopend==0`
with `length==0` yields `loopend = length-1 = 0xFFFFFFFF`. `render_pcm`'s loop math
(`pcm.c:418-428`) then never triggers loopend (benign) but `pcm_note_off`'s
phase-to-end conversion (`pcm.c:291`) computes `F2P(huge)` — undefined phase. Host
(Python) currently sends sane values; clamp anyway (`loopstart < loopend <= length`).

### FW-14 (INFO) — Output stage drops one bit on ESP

`amy/src/amy.c:2228-2230`: `uintval >>= 1` ("for some reason, have to drop a bit to
stop hard wrapping on esp") permanently costs 6dB of output level and one bit of
resolution after the soft-clip table. Legacy upstream workaround; since the clip table
already bounds `uintval <= SAMPLE_MAX`, the shift looks vestigial — worth one bench
test on hardware (round 2) rather than blind removal.

### FW-15 (INFO) — sdkconfig notes (TULIP4_R11 = base + sdkconfig.tulip + N32R8)

- `CONFIG_FREERTOS_HZ=1000` (`boards/sdkconfig.tulip:11`) — makes the fence handshake's
  `vTaskDelay(1)` = 1ms; the 25-tick bound = 25ms as designed. Good.
- `CONFIG_SPIRAM_FETCH_INSTRUCTIONS=y` + `CONFIG_SPIRAM_RODATA=y` (`sdkconfig.tulip:26-27`)
  — this is what lets code keep running (and crash on mmap'd-flash *data* fetches)
  during flash ops; the whole fence design depends on these staying set. Add a
  build-time `#error` in `flash_fence_wrap.c` if either is ever unset.
- `CONFIG_SPIRAM_SPEED_120M` + `CONFIG_ESPTOOLPY_FLASHFREQ_120M` under
  `CONFIG_IDF_EXPERIMENTAL_FEATURES=y` (`boards/N32R8/sdkconfig.board:3-10`) — 120MHz
  octal PSRAM is officially experimental (temperature-dependent timing); if field
  units ever show heat-correlated corruption, this is the first suspect.
- `CONFIG_SPIRAM_CACHE_WORKAROUND=y` (`sdkconfig.tulip:2`) is an ESP32(-classic)-only
  option; harmless no-op on S3 — remove to avoid implying it protects anything.
- `CONFIG_FREERTOS_GENERATE_RUN_TIME_STATS=y` adds a timer read per context switch;
  negligible (<0.1%) but free to disable in release builds if unused.

---

## 3. Ranked optimizations

Baseline for percentages: 1.39M cycles/core/block (see header). "Peak" = 60+ active
oscs (kit + piano voices), the regime the deck actually hits.

| # | Optimization | Est. gain (of a core) | Effort | Risk |
|---|---|---|---|---|
| O-1 | **Dynamic core split** (balance active oscs, not osc numbers) | up to ~13% at peak | M | M |
| O-2 | **EG value reuse** (drop 2 of 4 `compute_breakpoint_scale` calls/osc) | 1–2% at peak | M | M |
| O-3 | **PCM fast paths** (mono / no-loop specialized loop) | ~2% during kit bursts | S–M | L |
| O-4 | **-O3 for amy.c, envelope.c** (join the OPT-6 list) | 0.8–2% | trivial | L |
| O-5 | **Filter coefficient cache** (skip biquad regen when cutoff/Q unchanged) | 0.7–1% | S | L |
| O-6 | **Reverb main delay lines → internal SRAM** (if 64KB spare) | 1–1.8% | S | M |
| O-7 | **EQ: per-block (not per-sample) headroom** in `parametric_eq_process` | ~1.3% per EQ'd bus | M | M |
| O-8 | **`external_cv_render` global early-out flag** | 0.3–0.6% | trivial | none |
| O-9 | **Aux fold: iterate a compacted live-bus list** | ~0.4% | S | L |
| O-10 | **KS: replace `% buflen` with compare-wrap** | ~1% per KS osc (rare) | trivial | L |

**Arithmetic:**

- **O-1** — `esp_render_task` renders oscs 0..125, the fill task 125..250
  (`i2s.c:264,278`; N=250 per `api.c:38`). Instruments/patches allocate voices from low
  osc numbers up, so at peak essentially *all* busy oscs sit in core 1's half… while
  the *other* core's task just waits (block time = max(core0_osc, core1_osc) + FX,
  because fill blocks on the notify at `i2s.c:280`). Peak osc work: ~60 oscs ×
  ~6k cycles (PCM: 256 samples × ~21 cyc incl. interp/loop checks ≈ 5.4k, + h&m ~1.5k;
  LUT oscs similar) ≈ **360k cycles ≈ 26% of a core serialized on one core**. An
  even split halves that: **~180k ≈ 13% of the block budget recovered at peak**, which
  is exactly the headroom that decides whether a busy block underruns. Implementation:
  count AUDIBLE oscs cheaply in `amy_execute_deltas` and pick the split index per
  block; keep chains/algo voices on one side (also fixes FW-6).
- **O-2** — `hold_and_modify` calls `compute_breakpoint_scale` 4× per non-partial osc
  per block (offsets 0 and BLOCK_SIZE for both EGs, `amy.c:1623-1624,1680-1681`). Each
  call walks breakpoints + does `exp2_lut`/float ops over PSRAM-resident synthinfo:
  ~250 cycles typical (`envelope.c:60-205`). The offset-BLOCK_SIZE value this block ==
  the offset-0 value next block unless a delta touched the osc; caching it in msynth
  with a "dirty" stamp saves 2 calls: 60 oscs × 500 = **30k cycles = 2.2% (split
  across cores)**. Invalidate on any delta to that osc and on note events.
- **O-3** — `render_pcm`'s per-sample loop re-tests `preset->channels == 2`,
  `preset->type != AMY_PCM_TYPE_FILE`, `synth[osc]->wave == PCM_LEFT/RIGHT`, and loads
  `msynth[osc]->feedback` through two PSRAM pointer hops (`pcm.c:370-432`): ~8–12
  avoidable cycles of a ~50-cycle body. Mono one-shot fast path (the drum-kit case):
  256 × 10 ≈ 2.5k cycles/osc; 12 simultaneous kit voices = **30k ≈ 2.2%** during
  bursts. (The phase hoist C-5 is already done — this is the remaining half.)
- **O-4** — `esp32_common.cmake:378-384` gives -O3+unroll to oscillators/delay/filters/
  pcm but **not** `amy.c` (mix_with_pan `amy.c:1716-1747`, the fill fold loops
  `amy.c:2111-2124,2178-2247`) or `envelope.c` — together plausibly ~25-30% of render
  cycles at -O2. Typical -O3+unroll gain on such loops 3–8% → **0.8–2% overall**.
  Watch PSRAM code-cache pressure (SPIRAM_FETCH_INSTRUCTIONS): measure, don't assume.
- **O-5** — `filter_process` regenerates biquad coefficients every block per filtered
  osc: `cosf`+`sinf` (~120 cyc each, soft-fp assisted) + 5 float divides (S3 FPU has
  no divide instruction; ~40-80 cyc each) ≈ **~700 cycles/osc/block**
  (`filters.c:691-702,28-85`). Sustained notes with static cutoff/Q pay it for
  nothing. Cache keyed on (filter_logfreq quantized, resonance): 15-20 filtered oscs →
  **10–14k = 0.7–1%**.
- **O-6** — reverb runs every block once level>0; its 10 delay lines live in PSRAM
  (`ram_caps_delay = MALLOC_CAP_SPIRAM`, `api.c:50`; lines total ~118KB,
  `delay.c:243-257`). Access = 2 streams/line × 10 lines; 32B cache lines → ~
  640 misses/block × ~40 cyc = **~25k = 1.8%** of the fill core. The four 16KB main
  lines (64KB) in internal SRAM would remove most misses — only if the RAM budget
  survives display + MP heap; check `heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL)`
  at boot before committing.
- **O-7** — `parametric_eq_process` does 3 `nheadroom16(MAXABS2(...))` computations
  *per sample per channel* (`filters.c:561-574`): ~90 cyc/sample/chan → 512 × 90 ≈
  **46k = 3.3% per EQ'd bus**. Computing normbits once per block from the running
  block max (the way `filter_process` does BFP, `filters.c:726-733`) cuts ~40%:
  **~1.3%/bus**. Risk: intra-block state growth (resonant EQ) must keep the
  `top16SMUL_after_a_live` guard for the y-terms.
- **O-8** — with the hook installed but nothing mapped, every voice-owned osc pays the
  `synth_for_osc` scan: 32 iterations of load+test (`amy_connector.c:92-98`) ≈ 130 cyc
  → 60 oscs = **8k ≈ 0.6%**. A single `uint8_t any_cv_active` check makes it ~0.
- **O-9** — aux fold inner loop tests `bus_live` for every bus ≤ highest_bus per
  sample (`amy.c:2115-2116`): with 4 configured but 1 live, 3 wasted tests × 512 ×
  ~4 cyc ≈ **6k ≈ 0.4%**. Precompute the live list once per block.
- **O-10** — `render_ks` does two integer `% buflen` per sample (`oscillators.c:781-786`);
  Xtensa div ≈ 15-30 cyc → 256 × ~50 = **~13k = ~1% per KS osc** — only matters if the
  deck ever ships KS voices.

Not recommended: PIE-vectorizing mix_with_pan / the folds — PIE has no 32×32
multiply usable for s8.23, so only the already-done zero/copy/add paths vectorize
(the `algorithms.c:88-92` comment is accurate).

---

## 4. Requested verdicts

### 4a. Flash-fence design (wrap + total_blocks handshake + 20ms deferred release)

**Verdict: sound design, correctly implemented at its core; two coverage gaps and one
minor race.** The handshake logic is right: `total_blocks` increments at the *end* of
the fill stage (`amy.c:2270`), render N+1 cannot start before fill N (single pipeline,
`i2s.c:316-344`), and `render_pcm` samples the volatile fence once per osc-render
(`pcm.c:351`). So "wait until total_blocks advances by 2" (`flash_fence_wrap.c:85`)
provably outlasts any render that sampled the fence before it was visible — the +2 is
exactly the right bound (one for the in-flight block, one for a block that read the
flag just before the store landed). `FREERTOS_HZ=1000` makes the wait granularity 1ms
and the 25-tick guard 25ms (correct for AMY-not-running). The depth-counted
acquire/release with the timer-deferred drop is a clean batching solution, and the
drop callback's `fence_depth == 0` check inside the mux closes the re-acquire race.
Priority interaction with the IPC flash guard is handled (MAX−3 rationale,
`amy.h:1221-1227`). Gaps: **FW-3** (render_wavetable renders from the fenced window
without checking), **FW-8** (`esp_flash_*` bypass), **FW-11** (timer create race,
minor). Also recommend a `#error` coupling to `SPIRAM_FETCH_INSTRUCTIONS/RODATA`
(FW-15) since the whole scheme presumes code keeps executing during the write.

### 4b. Bus-activity mask (`amy_bus_used` / `bus_live` through `amy_fill_buffer`)

**Verdict: correct as written, conditional on bus-index sanity (FW-1).** Walked flow:
each core accumulates `used` and lazily zeroes `fbl[core][bus]` on first mix
(`amy.c:1863-1872`), publishing `amy_bus_used[core]` after its loop (`amy.c:1892`);
the fill task reads core 1's mask only after the `ulTaskNotifyTake` join
(`i2s.c:280`), which provides the cross-core happens-before. The dual-core merge
handles all three cases (both live → PIE add; core-1-only → copy + mark; neither →
stays stale, `amy.c:2009-2023`). The FX stage's `fx_active` predicate
(`amy.c:2036-2043`) correctly re-marks a zeroed block for *stateful* FX tails
(chorus/echo/EQ, and per-bus reverb only when neither shared-room variant is
compiled), so delay lines and biquads keep advancing on silence; under
`AMY_AUX_REVERB` the shared-room tail is instead kept alive by the aux block running
whenever `bus[0]->reverb.level > 0` with the fold contributing only `bus_live` buses —
consistent. `bus_live |= 1u` after each fold matches the fact the fold wrote bus 0,
and `sum_hi=0` + `volume_scale[0]=1.0` (with the unity fast-path at `amy.c:2191`)
makes the output stage read exactly the folded master. The invariant the whole scheme
rests on — every `synth[osc]->bus <= highest_bus < AMY_NUM_BUSES` — currently holds
only for well-formed input; FW-1 is the hole. One residual nit: `highest_bus` never
shrinks (a one-shot event on bus 3 makes every later block iterate 4 buses forever);
harmless, costs covered under O-9.

### 4c. PIE asm in `amy_blockops.h` (and `algorithms.c` zero/copy)

**Verdict: correct constraints; one environmental dependency to write down.**
- Constraints: all pointer cursors are `"+&r"` (read-write + earlyclobber), preventing
  the count input from being allocated to a cursor register; `"memory"` clobber +
  `volatile` pin ordering; the add kernel's separate read/write dst cursors trail
  correctly (store to `dw[i]` after both loads of iteration i; no intra-loop hazard).
- Guards: `((ptr|nbytes) & 15) == 0` covers alignment and size; `loopnez` with count 0
  legitimately skips the body; `malloc_caps_block` (`amy.c:215-221`) makes fbl /
  per_osc_fb / scratch 16-aligned so the guards pass, with libc fallback otherwise —
  callers need no guarantees. Correct.
- Saturation semantics of `ee.vadds.s32` vs the scalar wrap fallback are documented
  and right for s8.23 (divergence only >48dB over full scale, where saturating wins).
- **Dependency 1 (document it):** q0-q2 cannot be named in clobber lists; safety under
  same-core preemption by another PIE user requires the FreeRTOS port to lazily
  save/restore PIE context. ESP-IDF added S3 PIE coprocessor context save in v5.3;
  this tree builds on **v5.4.1** (workflows), so it holds — but a comment in
  `amy_blockops.h` should state "requires IDF ≥ 5.3 (PIE context save)" so a future
  IDF downgrade or a non-IDF port doesn't silently corrupt vectors mid-loop.
- **Dependency 2 (fine today):** `loopnez` clobbers LBEG/LEND/LCOUNT, which GCC can't
  be told about — safe because the Xtensa GCC backend never emits zero-overhead loops
  itself; interrupts save loop registers as part of base context. Worth the same
  one-line comment.
- Nit: the `(esp_timer_handle_t *)` cast at `flash_fence_wrap.c:106` is vestigial, and
  `bcopy/bzero` (`algorithms.c:114,137`) are deprecated aliases — cosmetic only.

### 4d. `CONFIG_SPI_FLASH_AUTO_SUSPEND` prototype branch for octal flash

**Verdict: not viable on this hardware; don't spend the branch.** The board runs
**octal (OPI) flash at 120MHz** (`CONFIG_ESPTOOLPY_OCT_FLASH=y`,
`FLASHFREQ_120M`, `boards/N32R8/sdkconfig.board:8-17`). ESP-IDF's auto-suspend
implementation targets a whitelist of quad-SPI chips (XMC/GD/FM etc. via the
chip-driver `suspend_cmd_conf` hooks) and is documented as unsupported for octal
flash; on S3 + OPI the Kconfig/driver combination will either refuse or silently not
engage, and Espressif additionally flags auto-suspend erase-resume churn as a
performance/reliability trade even where supported. Also note the interaction that
makes it tempting is narrower than it looks: with suspend, cache would stay enabled
and mmap'd PCM reads would stall for tSUS (~30-50µs) rather than crash — i.e. it
would *replace* the fence, not merely improve it — but only on supported chips. Keep
the fence as the mechanism of record; re-evaluate only if (a) the BOM moves to a
quad-SPI chip on the supported list, or (b) a future IDF release adds OPI suspend
support (check `spi_flash_chip_*.c` suspend tables on each IDF bump). If an
experiment is ever run anyway, it must gate on `esp_flash_chip` suspend capability at
runtime and keep the fence compiled in as fallback.

---

## Previously-raised items — verdicts

### P-1. Block-processing the reverb early reflections (e1..e6 as whole-block passes)

**Verdict: correct with one non-obvious ordering constraint; real but smaller than
claimed (~0.6–1.1% of a core, i.e. ~20–35% of the reverb pass's compute); composes
with O-6 rather than being superseded by it. Effort M, risk M unless AB-tested.**

**Correctness / bit-exactness.** The claim's premise holds: e1..e6 are pure
feedforward delays — each stage's `DL_READ` never depends on another ER stage's line,
and inter-stage data flows only through the per-sample `r_acc`/`l_acc` values
(`delay.c:346-372`). Staging those as two 256-sample scratch buffers (2×1KB, internal)
and running six single-line loops is bit-exact **provided each stage's loop preserves
the existing write-then-read order per sample on its own line** (`DL_WRITE` at
`next_in`, then `DL_READ` at `next_in − fixed_delay`, `delay.c:308-309`). That
proviso is not academic: the write-to-read ring distance is `len − fixed_delay`, which
for **e2 is 2048−1920 = 128** and for **e4 is 1024−855 = 169** — both *less than the
256-sample block*. A naive "write the whole block into the line, then read the whole
block out" variant would return this block's fresh samples instead of 1920/855-sample-
old ones for the tail of the block on those two lines — audibly wrong and not caught
by short tests. So: per-sample (or ≤128-sample-chunk) write-then-read inside each
stage loop; 4-wide PIE chunks are safely below the 128 floor. (e1/e3/e5/e6 distances
are 777/910/302/422 — unconstrained at this block size.) The 4-line feedback tank
(dl1..dl4 + LPF states) genuinely cannot be blocked — sample-recursive — and stays as
is. Mandatory verification: render a few hundred blocks through old and new
implementations and `memcmp` the outputs; anything non-identical is a bug by
construction.

**Gain vs. claim.** The win is register pressure, not memory traffic: PSRAM
compulsory misses are identical either way (same 20 sequential streams, ~32 cache
lines/stream/block). What blocking fixes is that the current interleaved loop keeps
~40+ values live (10 lines × 4 hoisted locals + 4 filter states + coefs +
accumulators, `delay.c:321-334`) against Xtensa's ~12 freely usable ARs → per-sample
spill/reload traffic; each blocked stage loop has ~6 live values, the tank ~20.
Estimating the reverb pass at 256 × ~150–200 = 45–70k cycles today (3–5% of the fill
core including PSRAM stalls), the ER stages' compute+spill share is roughly 40%, and
blocking eliminates perhaps half to two-thirds of that: **~8–15k cycles ≈ 0.6–1.1% of
a core**. "30–40% of the remaining reverb pass" is the optimistic edge of that range
and only materializes if today's loop spills as badly as the live-value count
suggests — gate the work on an `AMY_PROFILE`/ccount measurement of
`stereo_reverb_wet` first. The PIE upside is modest: the butterfly is 32-bit add/sub
(`ee.vadds.s32`-able), but ring positions aren't 16-aligned at arbitrary `next_in`,
so a vector path needs alignment peeling; expect the scalar blocked loops to capture
most of the win.

**Interaction with O-6 (delay lines → internal SRAM).** Orthogonal and additive: O-6
removes PSRAM *miss stalls* (~25k cycles est.), P-1 removes *compute/spill* cycles
(~10k est.). Neither supersedes the other. If choosing one, O-6 first — trivial
effort (allocation-caps flag) for the bigger number, RAM budget permitting. P-1
slightly shrinks O-6's marginal value (blocked stages stream each PSRAM line in one
long sequential burst, which the cache handles better) and O-6 slightly shrinks
P-1's (fewer stall cycles for spills to hide behind). Doing both: ~1.5–2.5% of the
fill core combined; P-1's scratch costs 2KB internal vs O-6's 64KB+. One process
note: `stereo_reverb` and `stereo_reverb_wet` are deliberate near-clones
(`delay.c:311-414,416-518`) and the deck path exercises only `_wet` — change both in
lockstep or a divergence in `stereo_reverb` ships silently.

### P-2. IRAM_ATTR on innermost render kernels

**Verdict: do it, but narrowly and second — first take the free knob (i-cache size),
then IRAM-place at most ~12–16KB of true leaf loops. Expected 0.5–1.5% average, more
under heavy UI redraw. The "keeps them executable during flash ops" synergy argument
is moot on this build.**

**The synergy claim, checked.** With `CONFIG_SPIRAM_FETCH_INSTRUCTIONS=y` +
`CONFIG_SPIRAM_RODATA=y` (`boards/sdkconfig.tulip:26-27`), render code *already*
executes during flash program/erase — that is the stated premise of the fence design
(`pcm.c:41-43`; FW-15 recommends a `#error` coupling). IRAM placement buys nothing on
that axis as configured. The real benefit is **i-cache miss elimination**: all code
fetches share the instruction cache, whose ESP32-S3 default is **16KB**
(`CONFIG_ESP32S3_INSTRUCTION_CACHE_SIZE` is not overridden in any board fragment —
verified `sdkconfig.tulip`, `N32R8/sdkconfig.board`, `TULIP4_R11/sdkconfig.board`),
shared with MicroPython, LVGL, and littlefs. When the UI is active, hot kernels are
evicted between blocks and re-fetched from PSRAM at ~40–80 cycles per 32-byte line.

**Sizing the gain.** A kernel loops 256 samples once resident, so the miss cost is
~once per kernel per block when evicted: ~6 hot kernels × ~50 lines × ~60 cycles ≈
18–25k cycles ≈ **1.3–1.8% worst case (UI thrashing)**, a fraction of that on a quiet
screen — **0.5–1.5% average**. Real, but not first-tier (compare O-1's ~13%).

**Recommended sequence.**
1. **Free knob first:** `CONFIG_ESP32S3_INSTRUCTION_CACHE_SIZE=32KB` (+ 8-way
   `..._ICACHE_ASSOCIATED_WAYS`). Costs 16KB of internal SRAM globally, benefits
   *every* PSRAM-resident code path (MP, LVGL, AMY) with zero code churn. Measure
   with the Xtensa `perfmon` counters (i-cache misses) around
   `esp_fill_audio_buffer_task` before/after.
2. **Then, if still miss-bound, IRAM_ATTR the true leaves only**, in priority order:
   `render_lut` + `render_lut_cub` (every LUT osc, `oscillators.c:192-230`), the
   `render_pcm` sample loop (`pcm.c:370-437`), `stereo_reverb_wet`
   (`delay.c:416-518` — the deck's always-on path; skip `stereo_reverb`),
   `dsps_biquad_f32_ansi_split_fb` (`filters.c:185-213`). Do **not** blanket the
   whole `render_lut` family — with `-O3 -funroll-loops` on those files
   (`esp32_common.cmake:378-384`) each of the six variants can be 1.5–3KB and the
   family alone could blow 15KB+; place the two variants the deck actually spends
   time in. One-off helpers and note-on paths stay in PSRAM.
3. **Size-check mechanically:** IRAM exhaustion is a hard link error (visible), but
   check headroom first — read the map (`iram0_0_seg` usage in
   `build/micropython.map`, or `python -m esp_idf_size build/*.map` → IRAM free) and
   gate on ≥20KB free so IDF/WiFi IRAM growth on the next IDF bump doesn't brick the
   build. Budget ~10% over each function's .text for Xtensa literal pools (IDF's
   linker fragments place them with the function).

**Risk:** low — the failure modes are a link error or a measurable non-win. The only
silent hazard is IRAM_ATTR on a function that calls PSRAM helpers per sample (the
miss just relocates); the leaf-only list above avoids it.

---

## Round-2 re-verification checklist

1. FW-1: send `y200` via `amy.send` and via sysex AMY message; confirm clamp + no
   corruption (heap poisoning build).
2. FW-2: stereo looped sample on PCM_LEFT, note-on → note-off → confirm voice ends.
3. FW-3: WAVETABLE osc with GM-range preset sounding during a `deckcfg` write burst;
   confirm no TG1WDT, silence-and-resume instead.
4. FW-4/5: MIDI program-change flood during held chord; confirm no glitch/UAF
   (run with `CONFIG_HEAP_POISONING_COMPREHENSIVE`).
5. O-1: instrument the per-core render time (cycle counter around `amy_render`) before
   and after the dynamic split; report worst-block numbers with kit+piano.
