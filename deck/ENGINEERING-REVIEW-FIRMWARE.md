# ENGINEERING-REVIEW-FIRMWARE — C / AMY / ESP32-S3 layer

Scope: the fork's AMY changes (`amy/src`, submodule @ 4b33ce5), `tulip/shared`
(amy_connector.c, display.c, modtulip.c), `tulip/esp32s3` (tasks, sdkconfig,
partitions), plus the four open items (reverb crackle, PRIu64 bug, WDT crash
class, littlefs corruption) and an optimization hunt. Repo-only review; no
device was touched. Sibling review of the Python layer is separate; the
existing `deck/ENGINEERING-REVIEW.md` E-numbers are cross-referenced where
this review confirms or extends them.

Timing baseline used throughout: `AMY_BLOCK_SIZE=256` @ 44100 Hz → **5.805 ms
per block**, i.e. **~1.39 M cycles per core per block** at 240 MHz. "% of
core" below means percent of that budget.

---

## Executive summary

1. **The reverb crackle is a code bug, not a headroom or CPU problem.** The
   build defines only `AMY_AUX_REVERB` (`tulip/shared/tulip.mk:8`,
   `esp32s3/esp32_common.cmake:359`), but the per-bus *insert* reverb block in
   `amy_fill_buffer` is compiled out only under `#ifndef AMY_MASTER_REVERB`
   (`amy/src/amy.c:2007`). Whenever the shared room is on, the **same**
   Stautner–Puckette network runs **twice per 256-sample block** on the same
   state: once as an insert on bus 0 (pre-volume, full-scale input), once as
   the aux return (post-volume, send-scaled input). The delay lines advance
   512 samples per 5.8 ms frame and their contents alternate ~24 dB in level
   every 256 samples — an 86 Hz amplitude-chopped tail that reads exactly as
   "crackle that gets worse with reverb level, even at low volume" (the
   insert pass fills the tank pre-volume, so the wet return is *not* scaled
   by master volume). This was flagged as E-3 in the earlier review and is
   **still live** in the current submodule. One-line guard fix; also refunds
   ~3–5 % of core 1. See C-1 and verdict 4a.
2. The fixed-point chain itself has healthy headroom (s8.23, ±256 internal,
   soft-clip from 0.90 FS) — no saturation path explains the symptom.
3. The flash fence + priority drop + Python quiet-gate stack works, but it is
   correctness-by-convention spread over three layers. The right home is a
   ~20-line change in the esp32 port's partition write/erase path with a
   block-boundary handshake, plus an experiment with flash auto-suspend that
   could delete the entire crash class. See C-2 / verdict 4c.
4. Concrete cycle-savers, ranked in the optimization section: fix C-1
   (~3–5 %/core-1), unity-gain output path (quality + ~0.5 %), `render_pcm`
   phase hoist (~1–4 % under kits), PIE the dual-core bus sum and block
   clears (~1–1.5 %), block-process the reverb early reflections (~1 %),
   per-file `-O3` on the three render translation units (measure; typ.
   3–8 % on these loops). Total realistic recovery on the audio cores:
   **8–15 %** without touching audio quality, more than half of it from the
   C-1 fix alone.
5. The PIE work already in the tree (`algorithms.c` block clear/copy) is
   correct and honest about its limits: int32 SAMPLE has no PIE 32-bit
   multiply, so only block moves/clears/adds vectorize. The remaining PIE
   candidates are adds and clears, listed below — worthwhile but not
   transformative.
6. Task priority reasoning (`ESP_TASK_PRIO_MAX-3`) checks out against IDF's
   IPC task priority; one leftover hazard (`SEQUENCER_TASK_PRIORITY` at
   MAX-1 in `tasks.h`) is dead code today and should be deleted before it
   becomes live again.

---

## Findings

Severity: CRIT = audible/crash today; HIGH = correctness risk or large perf;
MED = measurable perf/quality; LOW = hygiene.

### C-1 — CRIT: AUX reverb double-runs the network per block (root cause of the crackle)

**Where:** `amy/src/amy.c:2007-2018` (per-bus insert apply, guarded only by
`#ifndef AMY_MASTER_REVERB`) vs `amy/src/amy.c:2025-2065` (`AMY_AUX_REVERB`
block). Build defines `AMY_AUX_REVERB` only (`tulip/shared/tulip.mk:8`,
`tulip/esp32s3/esp32_common.cmake:359`); `AMY_MASTER_REVERB` is defined
nowhere, so the insert block **is compiled in**.

**Mechanism:** both blocks gate on `amy_global.bus[0]->reverb.level > 0` and
share `bus[0]->reverb.rev`. Order per block: (1) `stereo_reverb()` runs as an
insert on `fbl[0][0]` — input is the *pre-volume* bus-0 mix scaled 1/16 into
the tank, and `in + level*wet` is written back in place; (2) the aux fold
computes `dry`/`aux` from the (now already wet) buses; (3)
`stereo_reverb_wet()` runs the **same 10 delay lines and 4 filter states
again**, input scaled by `volume_scale*send/16`.

Consequences, with numbers:

- Delay-line time base runs 2×: `next_in` advances 512 samples per 5.8 ms
  frame. The designed delays (58.6/69.4/74.5/86.1 ms tank, 13.6–75.3 ms
  reflections, `delay.c:222-240`) all halve; T60 roughly halves.
- The tank content *alternates every 256 samples* between insert-pass energy
  (full-scale, pre-volume: bus sum ~1.0 FS → tank input 0.0625 FS) and
  return-pass energy (post-volume send: at volume 1 → 0.1×send×0.0625 ≈
  0.006 FS). That is a ~24 dB square-wave AM on the tail at ~86 Hz — dense
  odd harmonics across the band = crackle/buzz, amplitude proportional to
  `level`. The read offsets (2586, 3062, 3286, 3798 ≠ multiples of 256)
  guarantee each output sample mixes chunks of both scales.
- Because the insert pass feeds the tank **before** volume scaling, the wet
  the return adds (`*r_acc += MUL8_SS(level, d1)`, `delay.c:483`) is NOT
  proportional to master volume. Turn the volume down and the dry mix
  shrinks while the chopped tail doesn't — "crackles even at low volume."
- CPU: a full second pass ≈ 200–250 cycles/sample (see 4a below) ≈ 51–64 k
  cycles/block ≈ **3.7–4.6 % of core 1**, pure waste.

**Fix (in the amy fork):**

```c
// amy.c:2007
#if !defined(AMY_MASTER_REVERB) && !defined(AMY_AUX_REVERB)
        if(AMY_HAS_REVERB) { ... per-bus insert reverb ... }
#endif
```

and mirror the `bus = 0` clamp in `config_reverb` (`amy.c:369-371`) for
`AMY_AUX_REVERB` too, so a patch string carrying baked `h` params on a
non-zero bus can't allocate a stray second room (3 tank+6 ref lines ≈ 108 KB
PSRAM each).

**Verification:** with the fix, sweep `reverb.level` 0→2 at volume 0.5 and
2.0 with a sustained pad + kit hits; the tail should now be smooth and scale
with volume; `tulip.cpu(1)` should show `amy_fb_task` drop ~3–5 %.

### C-2 — HIGH: flash fence is a 3-layer convention with a real race window

**Where:** `amy/src/pcm.c:49-51, 351-358` (fence + per-block check),
`tulip/shared/modtulip.c:58-62` (`tulip.flash_fence`),
`tulip/shared/amy_connector.c:408-415` (window widening), `amy/src/amy.h:1213-1221`
(priority drop), plus the Python `fenced_write`/quiet-gate.

Three issues:

1. **Opt-in coverage** (E-2 in the prior review, still true): any write path
   that forgets `tulip.flash_fence(1)` re-arms the crash. The C layer cannot
   tell.
2. **Race window:** `render_pcm` samples the fence **once per block before
   the loop** (`pcm.c:351`). A fence raised mid-block does nothing until the
   next block — up to 5.8 ms during which flash fetches continue. The
   "callers should wait ~2 render blocks (>=12 ms)" rule lives in a comment
   (`modtulip.c:55-56`); nothing enforces it.
3. **The fence flag is set from the MP task on core 1 with no barrier.** In
   practice the volatile store + ≥12 ms wait makes it visible, but the
   correctness argument is timing, not synchronization.

**Priority reasoning validated:** `ESP_TASK_PRIO_MAX == configMAX_PRIORITIES`
(25); ESP-IDF's `esp_ipc` tasks run at `configMAX_PRIORITIES-1` (24). At the
old MAX-1 the AMY tasks tied the IPC task, and with `CONFIG_FREERTOS_HZ=1000`
a same-priority CPU-bound AMY task can hold the core until the next tick —
so the flash guard's park request could stall ~1 ms per attempt, repeatedly,
under littlefs repair-erase chains. At MAX-3 (=22) AMY sits below both IPC
(24) and AMY-MIDI (23, `amy_midi.h:69`) and above everything UI. Correct.
The I2S ring absorbs the park: default `I2S_CHANNEL_DEFAULT_CONFIG` gives
6×240 frames ≈ **32.6 ms** of DMA buffer — fine for page programs (0.3–3 ms)
and typical 4 KB sector erases, but a worst-case multi-sector erase chain
(45 ms+ each on typical NOR) can still underrun audibly even when everything
"works." That's a dropout, not a crash — acceptable, but worth knowing.

**Fix (right long-term design)** — see verdict 4c below: move the fence into
the C storage layer with a block handshake, and prototype
`CONFIG_SPI_FLASH_AUTO_SUSPEND`.

### C-3 — MED: `AMY_PROFILE_PRINT` prints literal `luus` (PRIu64 on nano printf)

**Where:** `amy/src/amy.h:475-479`; symptom already noted at
`tulip/esp32s3/esp32_common.cmake:360-363`.

The MicroPython esp32 base sdkconfig builds newlib in **nano-format** mode;
nano `printf` does not implement `%llu`, so `"%10" PRIu64 "us"` → `%10llu` is
consumed as far as `%10l`, printing garbage + the literal tail `luus`, and —
worse — misaligning the vararg cursor for the following fields, so the
percent columns after it are wrong too, not just ugly.

**Portable fix** (works on nano printf, glibc, MSVC; float printf is already
used two fields later so `%f` support is proven present):

```c
#define AMY_PROFILE_PRINT(tag) \
    if(profiles[tag].calls) {\
        fprintf(stderr,"%40s: %10" PRIu32 " calls %10.0fus total [%6.2f%% wall %6.2f%% render] %9.0fus per call\n", \
        profile_tag_name(tag), profiles[tag].calls, \
        (double)profiles[tag].us_total, \
        ((float)(profiles[tag].us_total) / (float)(amy_get_us() - profile_start_us))*100.0f, \
        ((float)(profiles[tag].us_total) / (float)(profiles[AMY_RENDER].us_total))*100.0f, \
        (double)profiles[tag].us_total / (double)profiles[tag].calls);\
    }
```

(`double` holds integers exactly to 2^53 — decades of µs — and sidesteps the
64-bit integer formatter entirely.) Alternative if you want exact integers:
print `(unsigned long)(us_total / 1000)` as ms. After fixing, re-enable
`AMY_DEBUG` for one profiling build and archive per-tag numbers for the deck
workload; every estimate in this document should be replaced by measurement.

### C-4 — MED: unity-gain output path quantizes the final mix to ~13 bits

**Where:** `amy/src/amy.c:2062-2063` (`volume_scale[0] = F2S(1.0)` after the
aux fold) feeding `amy.c:2105` (`fsample += MUL8_SS(volume_scale[bus], ...)`).

`MUL8_SS(a,b) = ((a>>12)*(b>>11))` (`amy_fixedpoint.h:198`). With
`a = F2S(1.0) = 2^23`: `a>>12 = 2048`, so the "multiply by 1.0" is
`(b>>11)<<11` — **it zeroes the low 11 bits of every output sample**. After
`S2L` (>>8) and the ESP `>>1` (`amy.c:2126-2143`), the DAC sees steps of 4
int16 LSBs: ~13-bit effective output on exactly the path the deck always
uses (aux reverb folds everything to bus 0 with unity scale). Same applies
to `AMY_MASTER_REVERB` builds. This is a constant -66 dBFS-ish quantization
floor — hiss, distinct from (and additive to) the C-1 crackle.

**Fix:** special-case unity in the output loop — when `sum_hi == 0 &&
volume_scale[0] == F2S(1.0f)`, read `fsample = fbl[0][0][...]` directly (no
multiply). One branch per block if you hoist the check out of the loop (two
loop variants). Bonus: removes 512 multiplies/block (~0.5 % core 1).

### C-5 — MED: `render_pcm` inner loop does per-sample pointer-chased PSRAM state updates

**Where:** `amy/src/pcm.c:364-431`.

`synth` is an array of pointers into `MALLOC_CAP_SPIRAM` structs
(`api.c:47`). The loop executes, per sample: `synth[osc]->phase` **read,
update, write** (lines 365, 402-403) through a PSRAM-cached pointer chase,
plus two per-sample branches that are loop-invariant (`preset->type !=
AMY_PCM_TYPE_FILE`, `preset->channels == 2`). Every other renderer in the
codebase (see `render_lut*`, `oscillators.c:66-115`) hoists phase into a
local and writes it back once.

**Fix:** hoist `PHASOR phase = synth[osc]->phase;` and `base_index` to
locals; write back after the loop; split the loop into (mono, no-loop),
(mono, looping), (stereo) variants chosen once — same idiom the file's own
sibling `delay_line_in_out` and the render_lut family already use. Estimated
10–25 cycles/sample/voice saved (dirty-line traffic + dependency stalls +
branches): ~3–6 k cycles per PCM voice per block; a busy kit + GM pattern
(8–10 PCM voices) recovers **1–4 % of a core**. Low risk, pure refactor.

### C-6 — MED: blocking I2C write inside the render path when CV is mapped

**Where:** `tulip/shared/amy_connector.c:84-129` (`external_cv_render`).

The AMY render hook calls `i2c_master_write_to_device(..., pdMS_TO_TICKS(10))`
synchronously from `amy_render` (render task, core 0/1). A 3-byte write at
100 kHz is ~0.3 ms; the 10 ms timeout is a 1.7-block stall if the bus wedges.
Also `synth_for_osc()` walks `MAX_CV_SYNTHS × instrument_get_num_voices` for
**every audible osc every block** once any `cv_synth_map` entry is set.
Dormant on the deck (no CV mapped ⇒ early-out at `external_map[osc]==0` plus
a 32-iteration scan), so MED not HIGH — but if AMYboard CV is ever active,
this belongs on a queue drained by a low-priority task, with the synth→osc
map inverted once at map-set time instead of per osc.

### C-7 — LOW: `SEQUENCER_TASK_PRIORITY (ESP_TASK_PRIO_MAX-1)` is dead code with teeth

**Where:** `tulip/esp32s3/tasks.h:10`.

No task is created with it (the sequencer is an `esp_timer` callback,
`amy/src/sequencer.c:211-224`; `tsequencer_init` just installs a hook). But
it ties the IPC priority (24) — the exact bug class just fixed in AMY. If
someone revives a sequencer task at this constant, the TG1WDT flakiness
returns. Delete it or redefine to something sane (e.g. MAX-4) with a comment
pointing at `amy.h:1213-1219`.

### C-8 — LOW: MIDI queue length truncation

**Where:** `tulip/shared/amy_connector.c:51,172` — `uint8_t
last_midi_len[]` assigned `(uint16_t)len`. Parsed channel messages are ≤3
bytes so this is theoretical today, but a >255-byte non-sysex blob would
store `len % 256` while only `MAX_MIDI_BYTES_PER_MESSAGE` (3) bytes were
copied. Clamp `len` to `MAX_MIDI_BYTES_PER_MESSAGE` when storing.

### C-9 — LOW: `amy_peak_hold` read-reset race — accepted

**Where:** `amy/src/amy.c:1943-1954`, `modtulip.c:372-385`. Single-word
volatile, worst case softens one meter frame; documented in-line. No action.
(Note `amy_peak_hold` is compared/assigned from the *post-clip, pre-shift*
`uintval`, so the meter maxes exactly at soft-clip onset — good choice.)

### C-10 — LOW: `hold_and_modify` runs full float control math per osc even for static oscs

**Where:** `amy/src/amy.c:1596-1681`. Four `compute_breakpoint_scale` calls,
six-coef `combine_controls` ×4, log/exp in `amp_combine_controls` — order
1.5–3 k cycles per osc per block. For a 25-osc piano voice that's ~50 k
cycles/block of control math alone. A "coefs unchanged and no EG/mod active"
fast path is possible but touches core AMY semantics; leave to upstream,
noted here for the profile-first list.

### C-11 — INFO: PIE block ops, hard sync, opsix algorithms — reviewed clean

- `algorithms.c:84-138`: the `ee.vst.128.ip`/`ee.vld.128.ip` clear/copy is
  correct (alignment guard covers pointer and size; `loopnez` handles the
  zero case; `memory` clobber present; scratch is `malloc_caps_block`
  16-byte aligned, `algorithms.c:201`). The claim that nothing else in AMY
  vectorizes is accurate for multiplies (PIE has no 32×32 vector multiply);
  it is *not* accurate for pure adds — see OPT-4.
- Hard sync (`oscillators.c:232-295`): separate `render_synced_lut*`
  variants keep the per-sample compare out of the normal loops — right
  pattern; the wrap test (`next_sync < sync`) is valid for the u31 phasor.
  `sync_params` recomputes `F2P(freq_of_logfreq(...))` per render — one
  float exp per synced osc per block, negligible.
- opsix algorithms (`algorithms.c:60-80`) are table data; the
  `algorithm >= NUM_ALGORITHMS` clamp at `algorithms.c:212` covers the wire.

### C-12 — INFO: display flush paths reviewed clean; one interaction to watch

`display.c:1031-1063`: PARTIAL flush gates on vsync at most once per LVGL
refresh (guard-bounded 40 ms), then memcpy-tiles into `bg`; DIRECT is
zero-copy. `display_bounce_empty` (`display.c:234-289`) is `IRAM_ATTR` and
runs per bounce chunk on core 0, streaming `bg` from PSRAM — this is the
main PSRAM bandwidth competitor for AMY's core-0 render task. The
120 MHz octal PSRAM (`N16R8/sdkconfig.board`) has headroom, but if core-0
render ever looks anomalously slower than core-1 in profiles, this
contention is why (see OPT-9, adaptive split). The keyboard-flash fix
(PARTIAL only while soft keyboard is up) keeps the copy cost transient.

### C-13 — INFO: partition/vaddr layout

`N16R8/tulip-partitions-16MB.csv` (T-Deck): drums fills flash to exactly
16 MB; no fonts partition — GM banks are N32R8/TULIP builds only
(`tulip-partitions-32MB.csv`). `amy_connector.c` mount order (biggest map
first, `amy_connector.c:551-562`) is correct for the ~16 MB S3 mmap pool and
matches the measured-live notes. The PSRAM-fallback path
(`map_or_load_partition`) correctly skips `widen_flash_fence` for PSRAM
copies so those banks keep sounding through writes — good detail.

---

## Verdicts on the open items

### 4a — Reverb crackle: **architecture bug (C-1), not headroom, not CPU**

**Headroom audit** of `stereo_reverb_wet` (`delay.c:416-518`), worst case:

- Input attenuated 1/16 (`MUL0_SS(F2S(0.0625), in)`). The 6-stage early
  lattice is feedforward; worst-case gain ≤2 per stage ⇒ tail sum ≤ 4× the
  raw input into the tank.
- Tank matrix: `d1±d2±d3±d4` written raw, but each `d_i` is read through
  `LPF()` which applies `SMULR6(liveness/2, ·)` — effective matrix spectral
  radius = liveness = 0.85 < 1. Steady-state tank bound ≈ 4·acc/(1−0.85) ≈
  27× acc ≈ 6.7× the bus input. With bus input ≤ ~4.0 (four hot buses,
  pre-volume in the buggy insert path) tank ≤ ~27; wet add `MUL8_SS(level,
  d1)` with level ≤ 2 stays ≤ ~54 — comfortably inside the s8.23 ±256 range,
  and `SMULR6`'s |a·b| < 128 constraint holds throughout (lpcoef 0.427,
  liveness/2 0.425 against ≤~30-magnitude samples). Output soft-clip engages
  at 0.90 FS (`FIRST_NONLIN 29491/32768`, `clipping_lookup_table.h:5`).
  **No overflow path exists at plausible signal levels.**
- Quantization: at very low send levels the tank runs near the `>>11`
  granularity of `SMULR6`, so wet resolution degrades ~24 dB below the s8.23
  ideal — that's hiss/sputter at extreme low volume, not crackle. (If the
  deck wants reverb quality independent of master volume, drop
  `volume_scale` out of `send_scale` at `amy.c:2035` and apply volume only
  in the output stage; costs nothing, changes the master-room-equivalence
  property.)

**CPU audit**, one pass, per sample: 6 early stages ≈ 48 ops, 4 tank lines
(LPF = 2 SMULR6 + adds, wet MUL8) ≈ 56, matrix ≈ 16, plus ~2.5 PSRAM
cache-line misses/sample across the 20 read+write streams
(`ram_caps_delay = MALLOC_CAP_SPIRAM`, `api.c:50`; 10 lines × 108 KB total
don't fit internal) ≈ 95 cycles. Total ≈ **200–250 cycles/sample ≈ 51–64 k
cycles/block ≈ 3.7–4.6 % of core 1** per pass; the aux fold adds ~0.5 %.
Even doubled by the C-1 bug (~8–10 % total) this cannot cause underruns by
itself with the 32 ms DMA ring. CPU is exonerated.

**Conclusion:** fix C-1 (guard the insert block out under `AMY_AUX_REVERB`,
clamp `config_reverb` to bus 0). Everything reported — level-proportional,
volume-independent, "crackle" texture rather than "distortion" — is
predicted by the double-run. If any grit remains after the fix at high
`level`, apply C-4 (unity-gain quantization) next, then re-test before
touching the reverb math itself.

### 4b — `PRIu64` → literal `luus`: see C-3 for the drop-in fix

Root cause is newlib **nano-format** printf (from MicroPython's esp32 base
sdkconfig) not implementing `%llu`; note it also corrupts the fields printed
*after* it in the same call. Cast to `double` (`%.0f`) or print ms as
`unsigned long`. Do not turn off nano-format globally just for this — it
costs flash in every binary.

### 4c — Interrupt-WDT crash class: layered mitigation critique + the right design

What exists: (1) `amy_flash_fence` window checked per block in `render_pcm`;
(2) AMY tasks at MAX-3 so the flash-guard IPC (prio 24) always preempts;
(3) Python raises the fence + waits + quiets before writes.

Critique:

- Layer (2) is solid and validated (see C-2): it converts "hard crash" into
  "audio task parked for the erase/program window," and the 32 ms I2S ring
  absorbs it.
- Layer (1) is correct C but samples the fence only at block start; safety
  against the residual window rests on layer (3)'s "wait ≥2 blocks"
  convention — enforced by nobody.
- Layer (3) is the weak point *by construction*: every write path in Python,
  present and future, must opt in (prior review E-2 lists the misses). A
  correctness property enforced in three places is enforced in zero.

**The right long-term design** (in preference order):

1. **Move the fence into the C storage layer** — the esp32 port's partition
   block-device ops (`micropython/ports/esp32/esp32_partition.c` write/erase
   paths) — with a handshake instead of a timed wait:

   ```c
   // esp32_partition.c (tulip patch), before esp_partition_write/erase:
   void tulip_flash_fence_acquire(void) {
       extern volatile uint8_t amy_flash_fence;
       amy_flash_fence = 1;
       uint32_t b = amy_global.total_blocks;         // written by fill task
       // Two block boundaries guarantee every render that started before
       // the fence was visible has finished (render+fill are one pipeline).
       while (amy_global.total_blocks < b + 2) vTaskDelay(1);   // ≤ ~12 ms
   }
   void tulip_flash_fence_release(void) { amy_flash_fence = 0; }
   ```

   Nesting counter if writes can overlap. Then delete the Python
   `fenced_write`/quiet-gate machinery and keep `tulip.flash_fence()` only
   as a manual override. Every write — Files delete, factory reset, editor
   save, screenshots, user apps, littlefs GC — is covered automatically, at
   a cost of ≤12 ms latency per write burst (batch: acquire once around a
   whole littlefs transaction, not per 4 KB block, by hooking the
   partition-object level, not the SPI level).
2. **Prototype `CONFIG_SPI_FLASH_AUTO_SUSPEND`.** The S3 + common 16 MB NOR
   parts support program/erase suspend; with it, a cache miss into mapped
   flash during a write *stalls* (~50–100 µs) instead of faulting, which
   deletes the crash class at the hardware level and would let PCM keep
   sounding through writes (no fence silence at all). Caveats: IDF marks it
   experimental per chip, erase times stretch under heavy read traffic, and
   it must be validated against the exact flash part on the deck — hence
   prototype, not default. If it proves out, layers (1) and (3) become dead
   code and layer (2) remains as belt-and-braces.
3. **Not recommended:** copying the hot banks to PSRAM at boot (only ~9.8 MB
   free vs 16.02 MB of banks — already used as the mmap-overflow fallback),
   or trying to make `render_pcm` re-check the fence per sample (pointless
   cost; the handshake solves it at the right layer).

### 4d — littlefs `Corrupted dir pair` on /user: filesystem-level view

`/user` is the `vfs` partition mounted via `uos.mount(Partition, ...)`
(`tulip/shared/py/_boot.py:29-32`) — MicroPython auto-detects littlefs2
(`MICROPY_VFS_LFS2`, built with `LFS2_NO_MALLOC LFS2_NO_ASSERT`,
`esp32_common.cmake:349-354`).

littlefs2 is power-loss-safe **per operation**: an interrupted write leaves
one valid metadata block of the pair. `Corrupted dir pair {A, B}` means
*both* revisions failed CRC, which effectively requires *two* interrupted
commits to the same pair in a row — exactly what the pre-fix WDT crash loop
produced: crash during a metadata commit → reboot → littlefs immediately
rewrites/compacts that same pair on mount (repair-erase chain, as observed
in the amy.h comment) → second crash lands on the pair's sibling block →
both halves torn. Secondary suspects, checked and unlikely: out-of-bounds
writes into the partition (nothing in the C layer writes raw offsets into
`vfs`), and mmap/cache aliasing (littlefs reads go through
`esp_partition_read`, not the mmap window).

Verdict: **symptom of the WDT crash class, not a littlefs bug.** Actions:
(1) fix the crash class per 4c — the corruption source disappears; (2) add a
recovery path: `_boot.py`'s bare `except: print(...)` leaves the deck
without /user — attempt `VfsLfs2.mkfs` + remount behind an explicit user
confirmation (or auto-backup `system` copy) rather than a dead boot; (3)
optional hardening: MicroPython's lfs2 config already uses small
prog/read sizes; leaving `block_cycles` default is fine at this write rate.
Do not enable `LFS2_NO_ASSERT`-style silencing beyond what's there — a
future corruption should fail loudly.

---

## Optimization hunt — ranked

Budget reminder: 1.39 M cycles/core/block; "% " = one core's block budget.
Everything below is repo-derived estimation; land C-3 first and re-rank from
real `AMY_PROFILE_PRINT` numbers.

| # | What | Est. gain | Effort | Risk |
|---|------|-----------|--------|------|
| OPT-1 | **Fix C-1** (remove double reverb pass) | 3.7–4.6 % core 1 + fixes audio | 1-line guard | none |
| OPT-2 | **C-4 unity-gain output fast path** (skip `MUL8_SS(1.0,·)`, two output-loop variants) | ~0.5 % core 1 + ~2 bits output SNR | ½ day | low |
| OPT-3 | **C-5 `render_pcm` phase/branch hoist** + mono/stereo/loop specialization | 3–6 k cy per PCM voice/block → 1–4 % under kits/GM | 1 day | low (pure refactor, A/B against desktop build) |
| OPT-4 | **PIE int32 adds for the dual-core bus sum** (`amy.c:1971-1975`): `fbl[0][b][i] += fbl[1][b][i]` is 512×(buses) scalar adds+loads → `EE.VLD.128.IP`×2 / `EE.VADDS.S32` / `EE.VST.128.IP`, 4-wide. Also expose `algorithms.c` `zero()`/`copy()` in a header and use for the `bzero`s at `amy.c:1830,1835` (per-osc 1 KB + per-bus 2 KB clears; 20 audible oscs ≈ 24 KB/block) | 1–1.5 % combined | 1–2 days | low; fbl already 16-B aligned via `malloc_caps` — assert or fall back like `zero()` does |
| OPT-5 | **Split the reverb early reflections out of the per-sample interleave.** Stages e1–e6 are feedforward (`delay.c:440-476`), so each can be processed as a whole-block pass over its own delay line (sequential streaming, 2 streams live instead of 20; the adds vectorize with PIE). Keep only the 4-tank feedback matrix per-sample. | ~30–40 % of the remaining reverb pass ≈ 1–1.5 % | 2–3 days | medium — bit-exactness must be preserved (it is: feedforward reorder is exact); validate vs reference render |
| OPT-6 | **Per-file `-O3 -funroll-loops`** on `oscillators.c`, `delay.c`, `filters.c`, `pcm.c` via `set_source_files_properties` in `esp32_common.cmake` (whole build stays `-O2`/PERF, `sdkconfig.tulip:6`) | typ. 3–8 % on these loops; measure | hours | low; watch IRAM/flash size delta |
| OPT-7 | **`mix_with_pan` gain caching** (`amy.c:1696-1716`): 4 software sqrts (`dsps_sqrtf_f32_ansi`) + clamps per audible osc per block even when pan is static. Cache `F2S(lgain/rgain)` in msynth keyed on pan value; skip the ramp when `pan_start==pan_end`. 25-osc piano = 100 sqrt/block. | 0.2–0.5 % under piano | ½ day | low |
| OPT-8 | **Interp-partials cost control.** Confirmed ~25 oscs/voice (24 partials via `use_this_partial_map` + control osc, `interp_partials.c:34-39`); ≈ render_lut 25 cy/sample ×24 + 24× `hold_and_modify` ≈ **~200 k cy ≈ 14 % of a core per sustained voice** — 4 held notes ≈ 60 % of core. Cheapest real leverage: a deck-exposed "piano quality" knob that thins `use_this_partial_map` above harmonic 12 (e.g. 24→16 partials = ~35 % of piano cost back); the map is already the mechanism, make it configurable instead of const. | up to ~5 %/voice | 1 day | audible A/B needed |
| OPT-9 | **Adaptive core split in `esp_render_on_cores`** (`i2s.c:264,278`): static `AMY_OSCS/2` ignores that core 0 also runs display bounce + touch and that voice allocation clusters low oscs. Track per-core render µs (already profiled) and move the split point ±N oscs per second toward balance. | worst-case headroom (removes the "one core at 95 %, other at 40 %" failure), 0 % average | 2–3 days | medium |
| OPT-10 | **IRAM_ATTR for the innermost kernels** (`render_lut` family, `stereo_reverb*`, `dsps_biquad_*_split_fb`): with `SPIRAM_FETCH_INSTRUCTIONS/RODATA=y` (`sdkconfig.tulip:29-30`) *all* code+LUTs fetch via the PSRAM cache; IRAM placement removes i-cache misses on the hot kernels and keeps them executable during flash ops (synergy with 4c). | small average, big tail-latency | 1 day | IRAM is scarce on this build — check `idf.py size` first; LUT rodata (`sine/saw/triangle_lutset`, 60 KB+) should stay cached, don't move it |
| OPT-11 | Skip-silent-bus bookkeeping: the per-bus EQ/chorus/echo checks, dual-core sum, and aux fold iterate `highest_bus+1` buses regardless of activity. A per-bus `nonzero_this_block` flag from `amy_render` lets the fold skip dead buses (kits-only scenes run 1 bus). | ~0.3–0.8 % | 1 day | low |

**Not worth it (evaluated, rejected):** PIE for the biquads/reverb tank
(feedback recurrences, no 32-bit vector multiply on PIE — matches the
`algorithms.c:88-90` comment); moving reverb delay lines to internal RAM
(108 KB doesn't exist internally — the heap is designed-full per
`display.c:1177-1181`); float→fixed conversion of `hold_and_modify` (control
rate, ~2 % total, high regression risk); `-ffast-math` globally (breaks
`isnan_c11` and envelope edge cases).

---

## Python→C boundary — two API sketches

Context: MIDI note dispatch already lives in C (`amy_midi.c` instruments);
the deck's Python is in the loop for (a) *every* inbound MIDI message via
`mp_sched_schedule(midi_callback)` (`amy_connector.c:181` — one scheduler
entry per message, even at 1 kHz CC streams), and (b) instrument/config
changes that emit dozens of individual `tulip.amy_send(wire_string)` calls
(`modtulip.c:459-463`), each a full MP call + C parse.

### Sketch 1 — C-side MIDI event filter + channel router (`amy_connector.c`)

Goal: take Python out of the per-event path entirely; Python subscribes to
classes it renders UI for, and channel→instrument routing becomes a C table.

```c
// amy_connector.c — consulted inside tulip_midi_input_hook()
typedef struct {
    uint8_t  dst_synth;     // AMY instrument to receive this channel, 0xFF = passthrough
    int8_t   transpose;     // semitones, applied to note on/off
    uint8_t  vel_scale_q8;  // velocity scale, 1.0 == 256
    uint8_t  flags;         // bit0 drop, bit1 also-notify-python
} tulip_midi_route_t;
tulip_midi_route_t tulip_midi_routes[16];

// Python-visible:
// tulip.midi_route(ch, synth=None, transpose=0, vel=1.0, drop=False, notify=False)
// tulip.midi_notify_mask(mask)   # bit0 note, bit1 cc, bit2 prog, bit3 bend,
//                                # bit4 transport/clock, bit5 sysex
```

`tulip_midi_input_hook` changes: (1) apply the route (rewrite channel/note/
velocity bytes) *before* `midi_msg_handler`/queueing so AMY's C instruments
receive the routed event; (2) replace the unconditional
`mp_sched_schedule(midi_callback, …)` with: enqueue always (ring buffer is
cheap), but schedule Python only if `(class_bit & notify_mask)` **and** no
schedule is already pending (single pending flag, cleared when Python drains
`tulip.midi_in()`). Effect: a CC-heavy controller stream stops generating
per-message MP scheduler traffic and GC pressure; Python wakes once per
drain with the whole batch. Estimated deck-side saving is the sibling
review's to quantify, but the C cost is ~30 lines and zero per-event
allocation.

### Sketch 2 — batched AMY wire messages + C-side kit/patch loader (`modtulip.c`)

```c
// tulip.amy_send_batch(msgs: bytes) -> int
//   msgs = b"v0w8f440...\nv1...\n..." — newline-separated AMY wire messages.
//   C splits on '\n' and calls amy_add_message() per line while holding the
//   AMY lock once; returns count. One MP call + one string object instead of
//   N of each. Timestamped lines keep their own 't' semantics unchanged.

// tulip.kit_load(path: str, base_patch: int) -> int
//   Reads a deck kit blob (concatenated patch_string records, the existing
//   synthkits format) with tulip_fopen/fread in C, feeding each record to
//   amy_add_message() — no Python string churn, no per-record GC, and the
//   file I/O happens under a single flash-fence acquire (ties into 4c).
```

`amy_send_batch` is ~25 lines in `modtulip.c` (split + existing
`amy_add_message`); `kit_load` reuses the `mp_fopen_hook` file plumbing that
already exists for AMY (`amy_connector.c:259-315`). These two remove the
hottest config-time Python↔C chatter (instrument switch, kit load, patch
apply) without inventing a new config schema in C — the wire format *is* the
schema. A full C-side "config-apply struct" was considered and rejected: it
would duplicate deck policy (channel maps, send levels) in a second language
and drift.

---

## Serial-router verdict (deck/SERIAL-PROTOCOL.md)

**Concur with the recorded decision** — keep the typed-message router
host-side in Python; do not build a C console mux now. Independent
reasoning, beyond the doc's own three points:

1. Every observed failure was *outbound parsing ambiguity*; type tags fix
   that at the reader, at zero device cost, and the 2026-07-16 audit closes
   the known holes (qget nonce+checksum was the right fix).
2. A C-side mux must live inside the USB-CDC/raw-REPL core — the recovery
   path of last resort. The WDT/flash history of this device argues for
   keeping that path boring. The regression risk is asymmetric: a mux bug
   can cost the ability to fix mux bugs.
3. The one thing a C mux buys — device-initiated typed frames interleaved
   with a live REPL — has no current consumer. If push telemetry (live
   meters over serial) arrives, implement it as the doc says: a minimal
   framing shim at the CDC layer (`usb_serial_jtag.c`), tagging *only*
   device-initiated frames and passing REPL bytes through untouched, so the
   recovery path remains byte-transparent when the shim is idle.

One addition: the KNOWN GAP row (`mpremote fs cp`) is the only remaining
untyped inbound flow that writes flash; when it moves to a framed push, run
it through the C-side fence of 4c so deploys stop depending on the deploy
script remembering to quiet the synth.

---

## Suggested landing order

1. C-1 guard fix (amy fork) + verify crackle gone — one line, biggest win.
2. C-3 profiler format fix, ship one `AMY_DEBUG` build, capture real per-tag
   numbers for a deck workload; re-rank OPT list against them.
3. C-4 unity-gain output path.
4. 4c step 1 (C storage-layer fence + handshake), delete Python fence
   machinery; file the auto-suspend prototype as a follow-up experiment.
5. OPT-3/OPT-4/OPT-6 as a small perf batch, measured before/after.
6. C-7 delete dead priority, C-8 clamp — hygiene batch.
