# REVIEW-DSP — Audio/DSP core (amy/src), fresh deep scan

## Executive summary

I traced the render pipeline end-to-end for the deck build (ESP32-S3, dual-core,
`AMY_USE_FIXEDPOINT`, `AMY_AUX_REVERB`, `AMY_NCHANS=2`, `AMY_NUM_BUSES=4`):
`esp_fill_audio_buffer_task` -> `amy_execute_deltas` -> `esp_render_on_cores`
(core 0 renders oscs `[AMY_OSCS/2,AMY_OSCS)`, core 1 renders `[0,AMY_OSCS/2)`,
each into its own `fbl[core][bus]`/`per_osc_fb[core][bus]`) -> `amy_fill_buffer`
(mask-aware dual-core merge, per-bus insert FX, aux-send reverb fold, soft-clip
output). The recently-reworked machinery is, on the whole, careful and
internally consistent: the `render_clock` compare-and-swap (FW-6) correctly
prevents a chain crossing the core split from double-advancing an osc; the
lazy bus-clear + `amy_bus_used`/`bus_live` mask (OPT-11) is coherent across the
merge/FX/fold/output stages; the `amy_patch_loading` gate (FW-4) and the delta
pool's OOM-drops-not-aborts policy (FW-9) hold up; the flash fence in
`render_pcm`/`render_wavetable` and the reverb internal-SRAM fallback are sound
for the cases they name; the PIE block ops are correctly alignment-guarded. I
found **no provable crash or silence bug in the common playback path.** The
findings below are one memory-safety gap that hinges on a linker guarantee
(worth confirming because it is exactly the WDT crash class this code fights),
one reverb-OOM memory leak, and several lower-severity correctness/robustness
items, plus a short perf list. Note: I did not run `test_deck.py` — my findings
are all C-side and do not make behavioral claims about the Python deck layer.

Severity counts: **CRIT 0, HIGH 0, MED 2, LOW 4.**

---

## Findings (most severe first)

### 1. [MED — verify; HIGH if confirmed] Flash fence does not cover baked ROM `pcm[]` samples
`amy/src/pcm.c:351-358` (fence check) vs `pcm.c:178`, `pcm.c:162-193`
(`AMY_PCM_TYPE_ROM` path).

The whole fence design (pcm.c:39-51) assumes that the *only* sample memory that
can fault during a flash program/erase is the mmap'd partition window
`[amy_flash_fence_lo, amy_flash_fence_hi)`, and that "computed voices,
PSRAM-loaded banks render on undisturbed." `render_pcm` enforces exactly that:
it emits silence only when `preset->sample_ram` falls inside that window.

But for `AMY_PCM_TYPE_ROM` presets, `sample_ram = (int16_t*)pcm + offset`
(pcm.c:178), where `pcm[]` is the `const int16_t` sample array linked into the
firmware (`pcm_samples_gamma808.h`, ~2.4 MB). That array lives in `.rodata`, and
the pcm_map/gm_map lookup tables read in `get_preset_for_preset_number` are
`const` too. If `.rodata` is served from the memory-mapped flash cache (the
default on ESP32-S3 unless the build deliberately relocates rodata to PSRAM),
then a ROM-preset fetch during a filesystem write faults the cache exactly like
the mmap'd-bank case — the dual-core interrupt-WDT crash this fence exists to
prevent — and the fence's address-window test never matches, so it does not
fire.

Concrete failure path: a note using a ROM preset (`preset_number` below
`GAMMA9001_PRESET_BASE`) is sounding when the config/log layer writes flash;
`render_pcm` loop reads `table[base_index]` from `pcm[]` in flash → cache
suspended → fault.

This is gated entirely by the linker: if the deck build already places `.rodata`
(and the baked `pcm[]`) in PSRAM, this is a non-issue and the pcm.c comment's
claim holds. I could not confirm the placement from my slice (linker/partition
config is outside the DSP files). **Recommendation:** confirm rodata/`pcm[]`
placement; if it is flash-resident, either add an early-out for
`AMY_PCM_TYPE_ROM` while `amy_flash_fence` is up (cheap: `preset->type ==
AMY_PCM_TYPE_ROM` -> return 0), or extend the fence window to cover the rodata
flash region. If the deck never plays baked ROM presets (only GM/GAMMA mmap
banks), downgrade to informational.

### 2. [MED] `deinit_stereo_reverb` leaks lines 2..4/refs when `delay_1` is the failed alloc
`amy/src/delay.c:299-312`, called as the rollback at `delay.c:287-292`.

`deinit_stereo_reverb` guards *all* frees behind `if (rev->delay_1 != NULL)`.
`init_stereo_reverb` allocates `delay_1` first (internal-SRAM then fallback);
if both attempts for `delay_1` return NULL but `delay_2/3/4` and `ref_1..6`
succeed, the init failure check (delay.c:287) calls `deinit_stereo_reverb`,
whose top-level guard is false, so it frees **nothing** and NULLs nothing —
`delay_2..4` and all six ref lines (~50 KB) are orphaned. The init comment
promises deinit "rolls back all partial allocations," which is false in this
ordering. Worse, `rev` persists per-bus, so a later reverb reconfigure re-enters
`init_stereo_reverb` (delay_1 still NULL -> proceeds), overwrites `delay_2` etc.
with fresh pointers, leaking the earlier ones again.

Probability is low (same-size allocs usually fail together, and the internal
fallback makes an isolated `delay_1` failure rare), but it is a genuine logic
bug. **Fix:** make deinit free each pointer independently (each `free()` already
tolerates NULL), i.e. drop the outer `if (rev->delay_1 != NULL)` wrapper and
free/NULL each of the ten lines unconditionally.

### 3. [LOW] `render_ks` uses the global polyphony index, not a per-voice slot
`amy/src/oscillators.c:772-799` (render) and `801-821` (`ks_note_on`).

`ks_note_on` fills `ks_buffer[ks_polyphony_index]` then advances the global
`ks_polyphony_index`, but never records which slot the osc got. `render_ks` then
reads/writes `ks_buffer[ks_polyphony_index]` using the *current* global index,
so every sounding KS voice indexes whatever slot the most-recent note-on left
behind — even a single voice reads a slot one past the one it was seeded into.
Polyphonic Karplus-Strong collapses onto one shared buffer. This is gated by
`amy_global.config.ks_oscs` (render_osc_wave:1855-1859), which the deck does not
appear to enable, so impact is nil unless KS is turned on. **Fix:** store the
allocated slot per-osc at note-on (e.g. in an unused synth field) and read that
in `render_ks` instead of the global.

### 4. [LOW] `pcm_note_on` leaves a stale/closed file handle on a re-parse failure
`amy/src/pcm.c:243-251`.

If `wave_parse_header` fails at note-on for an `AMY_PCM_TYPE_FILE` preset, the
code calls `amy_external_fclose_hook(preset->file_handle)` but does not clear
`preset->file_handle` (still non-zero) or set an error/off state. The next
`render_pcm` sees `type==FILE`, then `fill_sample_from_file` issues
`fseek`/`wave_read` on the now-closed handle — a read from a closed file
descriptor (garbage or fault depending on the platform hook). Reachable only if
a WAV that parsed at load time fails to parse at note-on (file changed/removed);
file-preset PCM also appears unused on the deck (it uses mmap banks). **Fix:**
after the fclose in the failure branch, set `preset->file_handle = 0` and mark
the voice off so `render_pcm` bails at the `sample_ram == NULL` guard.

### 5. [LOW — audit note] PIE inline-asm cannot declare its loop/vector-register clobbers
`amy/src/amy_blockops.h:13-77`, `amy/src/algorithms.c:84-138`.

Per the explicit ask to audit the PIE asm constraints: the alignment guards
(`((ptr | nbytes) & 15u) == 0`) and the `"memory"` clobber are correct, and
`loopnez` safely no-ops on a zero count, so the fallbacks are sound. Two things
cannot be expressed in the constraints and are therefore latent assumptions,
not declared contracts: (a) `q0/q1/q2` are clobbered but Xtensa GCC has no
constraint letter for the EE/PIE `q` registers, so the compiler assumes them
preserved across the asm — safe only because AMY touches `q` regs *only* inside
these self-contained blocks; (b) `loopnez` writes the single hardware
zero-overhead-loop register set (LBEG/LEND/LCOUNT), also undeclarable. Because
these are `static inline`, if one were ever inlined into a caller whose C loop
the compiler lowered to a hardware `loop`, the inner `loopnez` would corrupt the
outer loop's registers. In practice this is the exact esp-dsp idiom and the
callers invoke these at whole-block granularity (not inside HW loops), so risk
is low — but it is worth a comment noting the "do not call from within a
hardware-loop body" constraint, since the constraint list can't enforce it.

### 6. [LOW] `apply_fixed_delay` / `delay_line_in_out_fixed_delay` ignore their `delay_samples` argument
`amy/src/delay.c:150-185` (uses `delay_line->fixed_delay` via `DEL_OUT`, never
the `delay_samples`/`int delay_samples` parameter) and its caller
`delay.c:192-194`.

The echo path passes `amy_global.bus[bus]->echo.delay_samples` into
`apply_fixed_delay` (amy.c:2158), but the actual read offset comes from
`delay_line->fixed_delay`, which is set separately in `config_echo`
(amy.c:287-289). The parameter is dead. This is not a live bug (the two are kept
in sync in `config_echo`), but the redundant, ignored argument invites a future
desync where a caller changes `delay_samples` expecting it to take effect.
**Fix:** drop the unused parameter, or assert it equals `fixed_delay`.

---

## Ranked optimization list (perf-relevant slice)

Deferred items already logged (dynamic core split, EG-value reuse, PCM-loop
specialization, filter-coeff cache, EQ per-block headroom, block early-reflection
reverb, IRAM kernel placement, output-stage `>>1`) are intentionally omitted.

1. **`render_pcm` stereo down-mix uses integer divide per sample.** pcm.c:394
   and 398 do `((int32_t)bl + br) / 2` twice per output sample on stereo PCM
   voices (drum kits) — two signed divides in the inner loop. Replace with
   `>>1` (`SHIFTR`). Correctness is identical to within one LSB of rounding
   direction; saves the divide latency on the hottest sample path under kits.
2. **Hoist the unity-gain test out of the output inner loop.** amy.c:2278 tests
   `volume_scale[bus] == F2S(1.0f)` for every sample×channel×bus. Precompute a
   per-bus boolean (or a per-bus function-pointer/branch selection) once per
   block before the `i`/`c` loops. Small, but it is `AMY_BLOCK_SIZE * AMY_NCHANS`
   redundant comparisons per block (under aux reverb, only bus 0, so minor;
   larger on the non-reverb multi-bus path).
3. **`parametric_eq_process` recomputes `nheadroom16` of the state 3× per
   sample** (amy.c/filters.c:584,587,590). This is already a tuned block-FP
   scheme and is on the "EQ per-block headroom" deferred list — noting only that
   it remains the dominant cost when EQ is engaged, in case that item is
   revisited.
