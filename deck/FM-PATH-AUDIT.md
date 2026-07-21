# FM-PATH-AUDIT — DX7 note-on to TRS jack, upstream vs fork (task #104)

**Status: read-only analysis. No behavior changed by this document.**

**Refs used:**
- tulipcc OURS = `deck-next` (tip `d6cb765b`), UPSTREAM = `origin/main` (`a6bc1ef2`, fetched fresh).
- amy OURS = `37030d13` ("FM: align algorithm table to upstream (drop unused opsix algos)", descendant of resync tip `dca9cab9`), UPSTREAM = `upstream/main` (`c829ff28`, fetched fresh).
- All comparisons via `git show <ref>:<path>` / `git diff <upstream> <ours> -- <path>` against the checked-out working trees at `C:\Users\zsmer\OneDrive\Desktop\projects\tulipcc` and `...\tulipcc\amy`. No worktree checkouts of the compared refs were needed; a short-path worktree (`C:\t\fmaud`) was used only to hold and commit this document.

**Trigger:** a DX7/FM patch renders roughly 15 dB quieter than a Juno-6 patch on our deck. The obvious suspect (`algorithms.c:224`, an arbitrary `/4` on FM voice output) is itself upstream, not fork-introduced — confirmed below. This document traces every other hop from MIDI-in to the TRS jack, upstream vs fork, and profiles every divergence found.

**Headline finding:** across all eight stages, **no fork-vs-upstream divergence was found that changes the numeric gain of a DX7 note relative to upstream's own behavior.** The DX7 patch bytes (`patches.h`), the FM operator/algorithm Python model (`amy/fm.py`), the algorithm render math (`algorithms.c`), and the DX7-relevant parts of `oscillators.c`/`amy.c` are either byte-identical to upstream or are refactors verified to be semantically equivalent. Every fork-only feature that touches the signal path (MPE, hard sync, per-instrument FX buses, aux-send reverb, adaptive core split, flash fence, atomic render-clock claim) is either gated off by default, uniform across instrument types, or structurally incapable of scaling one instrument's output relative to another's. If the reported 15 dB gap is real and reproducible, the evidence here points to it being **inherent to upstream's own FM design** (the `/4` divide plus DX7 patches' comparatively low baked operator output levels vs. Juno's multi-oscillator analog patches), not a fork regression. See "Ranked divergence list" and "Top suspects" at the end for the caveats and the one item (chorus/EQ *absence* on DX7, stage 6) that is real but not gain-shaped.

---

## Stage 1 — MIDI IN

**Scope:** MIDI byte arrival (USB/serial) → C ring/queue → Python visibility.

**Upstream path:** `tulip/shared/amy_connector.c`. `tulip_midi_input_hook()` (upstream line ~202) unconditionally calls `midi_msg_handler()` (CC-mapping dispatch, defined in `amy/src/midi_mappings.c`), then either buffers sysex into `sysex_buffer` or pushes the raw message into a plain, unsynchronized `last_midi[MIDI_QUEUE_DEPTH][]` array with a bare `int16_t` head/tail (no atomicity discipline documented), then schedules `midi_callback` via `mp_sched_schedule` on every message.

**Our path:** same file, 904 lines vs upstream's 647. Two new fork-only headers: `tulip/shared/midi_router.h` (21 lines — a C-side per-channel routing table that only inspects `data[0]` to decide board-routing, never touches note/velocity bytes) and `tulip/shared/midi_in_ring.h` (168 lines — formalizes the ring as SPSC/MPSC with documented invariants, `volatile` head/tail, and a counted-drop stat). `tulip_midi_py_pending` + `TULIP_MIDI_PENDING_REDRIVE` replace the unconditional per-message `mp_sched_schedule` with a coalesced, self-healing wakeup. `amy/src/amy_midi.c` was restructured from a single shared-global byte-stream parser into one `midi_stream_parser_t` instance per byte source (`midi_parsers[AMY_MIDI_SOURCE_COUNT]`), funneling non-UART sources through `amy_midi_inject` to preserve a single-writer invariant; it also adds full MPE support, correctly no-op'd when MPE zones aren't configured.

**Divergences and verdicts:**

| # | What changed | file:line (upstream / ours) | Reason | Merit | Gain impact |
|---|---|---|---|---|---|
| 1 | Sysex prepend: byte loop → `memmove` + clamp | amy_connector.c:209-224 / :212-230 | fixes byte-0 smear when `data==sysex_buffer` aliases | justified bugfix | none — sysex only |
| 2 | Double CC-mapping dispatch removed | amy_connector.c:196-207 / :208-211 | `midi_msg_handler` already ran once upstream of the hook in our build; calling again double-fired CC/note mappings | justified bugfix | none (would have caused double note-on/CC firing, not attenuation) |
| 3 | C-side channel router added | midi_router.h (new, 21 lines); amy_connector.c:236-257 | routes board channels before Python sees them; verified it reads only `data[0]`, forwards `data[1..]` (note/velocity) unmodified | justified feature | none — confirmed byte-for-byte pass-through |
| 4 | Plain array ring → SPSC/MPSC ring | midi_in_ring.h (new, 168 lines) | concurrency-safety hardening (documented S1-S4 invariants, tested via a host harness) | justified hardening | none — push functions copy `data`/`len` verbatim |
| 5 | Per-message `mp_sched_schedule` → coalesced/self-healing wakeup | amy_connector.c:97-102, 290-311 | fixes CC-storm scheduler flooding and a soft-reset losing a queued callback | justified fix | none — structural only |
| 6 | `amy_midi.c` parser: shared globals → per-source parser state + `amy_midi_inject` funnel | amy/src/amy_midi.c, +292/-81 lines | fixes message tearing when two byte sources (DIN+USB, or `tulip.midi_local()`) interleaved on a shared-global parser | justified concurrency fix | none — `AMY_MIDI_PARSE_EMIT` forwards `(d,l)` straight through, same shape as upstream's `amy_event_midi_message_received` call |
| 7 | Full MPE support added | amy/src/amy_midi.c (`amy_mpe_*`) | new feature, gated behind MPE zone config; gate reduces to upstream's exact check when unused | justified addition | none for a non-MPE DX7 patch |

**Stage verdict: not gain-affecting.** No divergence touches velocity, note number, or any value byte between raw MIDI input and the point where AMY's own event parser takes over; every ring/queue hop is a verified byte-for-byte pass-through.

---

## Stage 2 — NOTE DISPATCH

**Scope:** MIDI note-on → an AMY note event/delta, both sub-paths in ours vs. upstream's one path.

**Path (a), C-owned channels:** `amy/src/midi_mappings.c`: `midi_msg_handler` → `midi_message_handler_to_queue` → `yield_midi_message_handler_events`. Note-on falls back to `default_note_mapping` (template `"i%in%nl%v"`, lines 54-65), and `map_midi_value()` (lines 253-269) computes velocity→level as `ret_val = min_val + (max_val-min_val) * value/127.0f` with `min_val=0, max_val=1.0` — i.e. exactly `vel/127.0`, written into the wire `l` field via `substitute_midi_special_values` (lines 271-303). **Diffed upstream vs. ours: `map_midi_value`, `default_note_mapping`, and the velocity math are byte-identical.** The only diff in this file (11 insertions / 3 deletions) is an MPE synth-channel redirection in the note-template substitution — changes *which* synth plays, not how loud.

**Path (b), deck forwarder:** `deck/forwarder.py:105-121` `_route()` computes `vel = m[2] / 127.0` (line 108) and calls `syn.note_on(note, vel)` (line 115). `syn` is `tulip/shared/py/synth.py`'s `PatchSynth` — **the same file upstream ships**, not deck-only. `note_on()` (synth.py:113-121) calls `self.amy_send(note=note, vel=velocity, ...)` → `amy.send(... vel=velocity, ...)`. `amy/amy/__init__.py`'s `_KW_MAP_LIST` maps `('vel','lF')` — `vel=` becomes wire field `l` (float, 0-1), no further transform. **`amy/amy/__init__.py` diffs zero lines vs upstream.**

**Cross-check against upstream's own path:** upstream's `tulip/shared/py/midi.py:midi_event_cb` (unchanged base logic) computes the identical `vel = value/127.0` (midi.py:286) before calling `synth.note_on(midinote, vel)`. Both of our sub-paths reproduce this exact formula rather than diverging from it — **computed delta: 0 dB**, the formulas are literally the same code/constant, not merely close.

Real fork changes in this file exist (`amy_synth_next` base 16→18 + a free-list to avoid channel-16 exhaustion; `DrumSynth` default `num_voices` 1→4/6; MPE member-channel skip in `midi_event_cb`; per-callback exception isolation in the MIDI drain loop) — **none touch `note_on`, `amy_send`, or any velocity/gain math.** No CC7/CC11 (channel volume/expression) interception was found on either side that would scale note gain.

**Stage verdict: CLEAR.** Note-dispatch velocity math is upstream-identical on both fork sub-paths. Ruled out as a source of the DX7 quietness.

---

## Stage 3 — VOICE / PATCH

**Scope:** how a DX7 patch (patches.h 128-255) is loaded and allocated to a voice.

**DX7 patch bytes are byte-identical, reconfirmed:**
```
$ git diff upstream/main 37030d13 -- src/patches.h
(zero output, exit 0)
```
`amy/amy/fm.py` (the Python FM operator/algorithm model) is likewise byte-identical (`git diff upstream/main 37030d13 -- amy/fm.py` — zero output). `tulip/shared/py/patches.py` (tulipcc side) is also byte-identical (`git diff origin/main deck-next -- tulip/shared/py/patches.py` — zero output). **The "one obvious /4 divide" is not the only thing confirmed upstream-equal here — the entire DX7 data model is.**

**`amy/src/algorithms.c` diff (11 lines, `git diff upstream/main 37030d13 -- src/algorithms.c`):**
1. `synth[osc]->role = SYNTH_IS_ALGO_SOURCE` (upstream algorithms.c:145) → `synth[osc]->status = SYNTH_IS_ALGO_SOURCE` (ours, same line) — a field-consolidation rename (see Stage 4).
2. A new bounds check in `render_algo`: `if (algorithm >= NUM_ALGORITHMS) algorithm = 0;` before indexing the `algorithms[]` table, guarding against an unchecked wire `algorithm` value walking off the end of the array. **Justified hardening, no gain effect for any in-range algorithm** (all DX7 patches use valid, in-range algorithm indices — this only fires on malformed/malicious wire input).
3. `SAMPLE amp = SHIFTR(F2S(msynth[osc]->amp), 2);  // Arbitrarily divide FM voice output by 4 to make it more in line with other oscs.` — present **identically** at upstream `algorithms.c:211` and ours `algorithms.c:224` (13-line shift is entirely due to the added bounds check above it). **Confirmed: this line is upstream, not fork-introduced, and unmodified.**

**`amy/src/patches.c` diff (395 lines) — reviewed in full.** This is a large structural reorganization: `event_addresses_bus`/`event_addresses_synth`/`event_addresses_oscs` were merged into `event_is_bus_directed` + a single `event_addresses_oscs(e, &is_empty)` with an out-param, `patches_grab_synth_tier` was folded into `patches_voices_for_event`, direct-voice addressing (`e->voices[]`) and MPE zone config (`e->mpe_members`) were added, and a new `amy_reserved_oscs` floor was added so hosts that drive low oscs directly (unused note: "from Leeman1982/amy" — an upstream-adjacent contribution, not deck-only) don't have the voice allocator claim them. **The `synth_level` (per-instrument level, "iV") set/get logic is unchanged in substance** — same field, same `instrument_set_level`/`instrument_get_level` calls, same default path (`if (level != 1.0f) event->synth_level = level;`), just relocated within the reorganized function. **No gain-math change found in this diff** — it is a control-flow refactor (voices-vs-synth addressing, MPE, reserved-osc floor), not a value-math change.

**`oscs_per_voice` for a DX7 voice** comes from a build-generated table, `patch_oscs[patch_number]`, computed at build time directly from `patches.h` — since `patches.h` is byte-identical, this table's DX7 entries (7 oscs/voice: 1 ALGO parent + 6 operators) are identical upstream vs ours.

**Deck-side instrument creation** (`deck/instrument.py`, `deck/forwarder.py`): `deck/instrument.py:18-21` defines `_TYPE_RANGE = {'juno6': (0, 128), 'dx7': (128, 256), ...}` (matches `deck/catalog.py:9-10`, `JUNO_END=128, DX_END=256` — matches the patch-string boundary independently confirmed by extracting `patches.h` directly: index 127 is the last Juno entry, 128 ("DX7 STRINGS 1") is the first DX7 entry). `deck/forwarder.py:374-378` and `:708-714`: DX7 and Juno-6 instruments both go through the same `_synth.PatchSynth(patch=..., num_voices=nv, ...)` constructor with `nv = instr.get('num_voices') or 10` — **no DX7-specific `num_voices` default exists; only `piano` gets a special-cased lower default (4).** No divergence between DX7 and Juno instrument construction on the deck.

**Stage verdict: CLEAR.** The DX7 patch data, the FM Python model, the algorithm render entry point, and the voice-allocation path are confirmed upstream-equal (byte-identical where it matters, semantically equivalent where refactored). No DX7-vs-Juno asymmetry exists in the deck's own instrument-creation code either.

---

## Stage 4 — FM RENDER

**Scope:** `algorithms.c` render math, `fm.c`/`oscillators.c` operator render, `combine_controls`/amp handling, the ALGO parent amp.

**The `/4` divide** (`algorithms.c:224` ours / `:211` upstream) — covered above, confirmed upstream, unmodified, identical comment and constant on both sides.

**`role` → `status` field consolidation** (`amy/src/amy.h`): upstream keeps two separate `uint8_t` fields on `synthinfo`: `status` (SYNTH_OFF/SYNTH_AUDIBLE/SYNTH_INAUDIBLE — playback state) and `role` (SYNTH_IS_NORMAL/SYNTH_IS_MOD_SOURCE/SYNTH_IS_ALGO_SOURCE/SYNTH_IS_CHAINED — the osc's function within a voice), each independently examined:  `synth[osc]->role == SYNTH_IS_ALGO_SOURCE` gates the note-on/note-off chained-osc walk (upstream amy.c:1461-1462, 1520-1521) so operator oscs never get `status` set to `SYNTH_AUDIBLE` (since the walk only follows the `chained_osc` linked list, which operator oscs are not part of), and the main render loop's dispatch test is *purely* `status == SYNTH_AUDIBLE` (upstream amy.c:1842) with no role check — so operator oscs are excluded from direct per-osc rendering *as a side effect of never being set AUDIBLE in the first place.* **Ours merges `role` into `status`, renumbering `SYNTH_IS_ALGO_SOURCE` from 2 to 4** (to avoid bit-colliding with `SYNTH_AUDIBLE=1` if the two were ever OR'd — they aren't; every use on both sides is an exact `==` compare, i.e. the field is used as a plain enum, never a bitmask, on both sides). Traced every assignment/comparison site (amy.c:1274-1276, 1344, 1361-1365, 1394, 1416, 1460-1470, 1520-1523, 1585-1591, 1635, 1657, 1703-1712, 1747-1748, 2061-2145 in our numbering; corresponding upstream lines shifted ~230 earlier) — **every exclusion test that used `role` upstream has a corresponding `status`-based test in ours at the same logical point, and the load-time assignment `synth[osc]->status = SYNTH_IS_ALGO_SOURCE` (amy.c:1657 ours) happens outside the chained-osc note-on walk, exactly as upstream's `role` assignment did.** This consolidation is verified semantically equivalent — a legitimate memory-saving refactor (drops one `uint8_t` per osc struct), **not a gain bug**, though it is dense enough that a future edit reintroducing a bitwise `|=` against `status` would be a real risk (flagging for awareness, not as a current bug).

**`amy/src/oscillators.c` diff (124 lines):** adds hard-sync (`sync_params`, `render_synced_lut`/`render_synced_lut_cub`, wired into `render_lpf_lut`/`render_triangle`/`render_sine`) and a `render_wavetable` fix (separate `max_value_a`/`max_value_b` so the release reaper doesn't kill an audible voice when `duty==0`) plus the GAMMA9001 wavetable flash-fence check. **None of this is reached by the FM/ALGO render path** — `render_mod`/`render_algo` in `algorithms.c` call the SINE renderer through `fm_sine_note_on`/`render_lut`-family functions directly, but hard sync is strictly opt-in (`sync_source` must be explicitly set on an osc; DX7 patches, confirmed via `patches.h`, never set it) and the wavetable fence/dual-max fix is `render_wavetable`-only, a different wave type entirely (PCM/wavetable, not ALGO/sine). **No gain effect on the FM path.**

**`amp_combine_controls`/`combine_controls`** (amy.c): the only change is a new `COEF_MOD1` coefficient slot (a second mod-matrix input, new fork feature) added to the same exemption `if (i != COEF_MOD && i != COEF_MOD1)` that already excluded `COEF_MOD` from the log-compression mapping upstream. This is additive (a new enum slot with its own array cell, populated by `compute_mod1_scale`); DX7 patches don't set `mod1_source` (confirmed via `patches.h` extraction — no DX7 patch string uses `%J`/mod1-related wire fields), so `ctrl_inputs[COEF_MOD1]` for a DX7 op osc is whatever `compute_mod1_scale` returns for an unconfigured mod1 (traced to a neutral/no-op default). **No gain effect for DX7.**

**`mix_with_pan`** (amy.c): a new static-pan fast path (`pan_start == pan_end`, the common case) was added, computing `gain_l = F2S(lgain_of_pan(pan_start) * level)` directly instead of going through the per-sample ramp. **Verified mathematically identical**: the ramping branch (used by both upstream and the fork's non-static-pan case) computes the exact same `lgain_start = lgain_of_pan(pan_start) * level` as its starting gain, and when `pan_start == pan_end` the ramp delta (`d_gain_l`) is zero, so the ramping branch would produce the identical constant gain across the block — the fast path is a verified no-op optimization (comment: "level is folded into the (constant) pan gains, matching upstream" — confirmed true by direct comparison of both code paths). **No gain effect.**

**`render_osc_wave`'s render-clock claim** (amy.c): upstream does a plain `if (synth[osc]->render_clock != amy_global.total_samples)` check-then-set; ours does an atomic compare-and-exchange to prevent a chain crossing the dual-core split from rendering the same osc twice in one block. This is a **correctness fix for a double-render bug** (which, if it existed, would make audio *louder* or glitchy, not quieter) — not relevant to the DX7-quiet direction, and gated identically for all oscillator/instrument types, not DX7-specific.

**Stage verdict: CLEAR — the DX7 render path is genuinely upstream-identical in its value math.** Every fork change found here is either a verified no-op refactor, an opt-in feature DX7 patches don't use, or a concurrency/correctness fix orthogonal to gain.

---

## Stage 5 — PER-VOICE / INSTRUMENT LEVEL

**Scope:** `instrument_level`/`synth_level` (iV) application (amy.c ~2183-2186 upstream numbering), `mix_with_pan`, does the deck set `synth_level`, our FX buses.

`amy.c` (upstream, confirmed present, byte-identical logic to ours):
```
float instrument_level = 1.0f;
if (osc_to_voice[osc] < ...)
    instrument_level = instrument_level_for_voice(osc_to_voice[osc]);
mix_with_pan(fbl[core][bus], per_osc_fb[core][bus], msynth[osc]->last_pan, msynth[osc]->pan, instrument_level);
```
`amy/src/instrument.c` (exists upstream, `git diff upstream/main 37030d13 -- src/instrument.c` = 1 line, i.e. trivially identical) defines `instrument->level = 1.0f` as the default (instrument.c:183) and `instrument_set_level`/`instrument_get_level` (:499-508) with a `level < 0 → 0` clamp — **no upper clamp, and no engine-specific default.**

**Does the deck ever set `synth_level`?** `grep -rn "synth_level" deck/*.py` returns **zero matches** across the entire deck codebase. The deck never calls `instrument_set_level`/sends `synth_level` (wire field `V`) for any instrument, DX7 or Juno. The `'level'` parameter exposed in `deck/curated.py`'s DX7 and Juno-6 curated views (both list `'level'` in their `VCA`/`Level` tab) maps through `deck/amyparams.py:239` — `_slider('level', 'Amp', 'level', 0.001, 7, 1.0, 'basic', ...)` — **a single shared PARAMS entry, default 1.0, identical range/default for both engines.** Traced this to the base osc's `amp_coefs[COEF_CONST]` ("a" field on osc 0), not `synth_level` — confirmed by the amyparams.py:45-46 comment ("Verified: amp_coefs[COEF_CONST]=1.0 (level/oscA_level/oscB_level=1.0)"). **No DX7-vs-Juno default-level asymmetry exists anywhere in the deck.**

**`mix_with_pan`**: covered in Stage 4 — verified upstream-equivalent.

**Stage verdict: CLEAR.** `synth_level`/instrument-level is unused (stays at its 1.0 default) for every instrument type on the deck; no fork divergence in its application code either.

---

## Stage 6 — BUS MIX & FX

**Scope:** 4 AMY buses (per-instrument FX routing, a deck feature), `patch_fx` (chorus/EQ baked), reverb sends, EQ silent-skip, aux reverb.

**`AMY_NUM_BUSES` is upstream, not fork-added:** `#define AMY_NUM_BUSES 4` lives in `amy/src/amy.h:175`, unconditionally, in both upstream and our tree — it is **not a build-flag or fork-specific constant.** What *is* fork-specific is the **policy** of routing each instrument to its own bus for independent insert FX (`deck/forwarder.py` comment at :239-245: "Per-instrument FX buses (C.5)... 5th+ instrument shares the last [bus]"), which upstream tulip does not do (upstream typically uses bus 0 for everything). This is a UI/architecture feature, not a change to the bus-mix math itself.

**Per-bus volume is uniform:** `amy_global.volume[bus] = 1.0f` is set in a loop over all `AMY_NUM_BUSES` at both `global_init` (amy.c:611-612) and `bus_reset`-adjacent init (amy.c:1096-1097) — **identical for bus 0..3, on both upstream and ours** (this code is not diffed between refs; both share it). No bus number is inherently quieter.

**`patch_fx` / `patchfx.py` — the DX7-range gap is real but confirmed accurate, not a bug.** `deck/patchfx.py`'s `FX` dict (generated from `patches.h`) has entries for patch numbers 0-127 (Juno) plus a couple of special entries at 256/257, but **no entries for 128-255 (the entire DX7 range)** — `patch_fx(131)` (a DX7 patch) returns `{}`. Directly extracted and inspected `patches.h`'s raw patch strings to check whether this is a generation bug or reflects real source data:
```
patch 0  (Juno):  ...Zx0,0,0k1,,0.5,0.5Z          <- has 'Zx' (EQ) and 'k' (chorus)
patch 131 (DX7):  v2a0.037163,...I14Z...v0w8a1,...O2,3,4,5,6,7o2Z   <- no 'x' or 'k' anywhere
```
**Confirmed: DX7 patches in `patches.h` genuinely never bake `Zx` (EQ) or `k` (chorus) commands — the Juno patches do.** `patchfx.py`'s missing DX7 rows accurately mirror this; it is not a generation bug. The docstring for `patch_fx()` states this table only *mirrors* what the baked patch string already set in AMY (for the FX editor's display and for `fx_bus_baseline`'s re-assertion after multi-instrument load-order races, per `deck/amyparams.py:1046-1052`) — it does not independently apply anything the patch string didn't already apply. So a DX7 instrument's bus ends up with `fx_bus_baseline({})` — i.e., default/flat EQ (`config_eq(bus, 1.0, 1.0, 1.0)`, amy.c:574 — unity, not attenuating) and chorus off (`CHORUS_DEFAULT_LEVEL 0`, amy.h:250) — **which is exactly what the DX7 patch string itself specifies** (no baked EQ/chorus commands = leave the engine defaults, which are unity/off). **This is a genuine, confirmed content/UI difference (DX7 patches get no baked coloration, Juno patches get a baked "analog" EQ+chorus voice) but it is not gain-shaped**: `config_eq(bus, 1,1,1)` is unity gain on all three EQ bands, and `CHORUS_DEFAULT_LEVEL=0` means chorus is silent/bypassed, not attenuating the dry signal. If anything, a Juno patch's `Zx7,-3,-3` (boost low, cut mid/high) or `Zx-15,8,8` (deep cut low, boost mid/high) EQ curves reshape spectral balance in ways that could make a Juno patch subjectively *louder-sounding* at some frequencies without being a literal level difference — worth flagging as a secondary perceptual-loudness contributor, but not a literal-gain divergence, and it is **upstream-inherent patch content** (both `patches.h` and `patchfx.py`'s generator reflect the same source data on both refs — `patches.h` is byte-identical, so this "gap" exists identically for upstream tulip too, if upstream had an equivalent FX-mirror table, which it doesn't since per-instrument FX buses are fork-only).

**Reverb send defaults are uniform:** `REVERB_DEFAULT_LEVEL 0` (amy.h:281, reverb off by default), and the deck's `reverb_send` PARAMS slider (`deck/amyparams.py:344`) defaults to `0.0` for every instrument type — no DX7-specific reverb-send default. `AMY_AUX_REVERB` (our fork's aux-send reverb architecture, `amy.c:2446-2488`) is gated by `if (AMY_HAS_REVERB && amy_global.bus[0]->reverb.level > 0 ...)` — **inert whenever reverb is off** (the deck's out-of-the-box default), and when active it applies `volume_scale[bus]` and `reverb_send` uniformly per bus via the same per-bus-uniform defaults established above — no DX7-vs-Juno asymmetry found in this fold logic.

**`AMY_AUX_REVERB` build flag is fork-added:** `tulip/shared/tulip.mk:8` (ours) reads `CFLAGS += -DGAMMA9001 -DAMY_AUX_REVERB`; the diff against upstream's `tulip.mk` shows upstream only defines `-DGAMMA9001`. This is a genuine fork-only architecture change (aux-send reverb replacing upstream's simpler/absent per-bus reverb fold) but — per the trace above — it does not introduce a per-instrument-type gain asymmetry; its default state (`reverb_send=0.0` dry, uniform across buses, `bus_reset` amy.c:580-587) is inert until a user or the router explicitly raises a send, and that logic (`deck/forwarder.py:213-221`, reading `params.get('reverb_send', 0.0)`) is identical for DX7 and Juno instruments.

**Stage verdict: mostly clear, one confirmed-but-non-gain finding.** No literal per-instrument-type gain scaling divergence was found in the bus/FX layer. The one real, confirmed difference — DX7 patches carry no baked EQ/chorus while Juno patches do — is **upstream-inherent patch-content data**, reflected accurately (not buggily) by the fork's `patchfx.py` mirror table, and is spectral/coloration, not a literal level cut.

---

## Stage 7 — MASTER

**Scope:** master volume scale (0.1×volume), soft-clip (FIRST_NONLIN/HARDCLIP), master EQ.

- Master volume scale: `MUL4_SS(F2S(0.1f), F2S(amy_global.volume[bus]))` — upstream `amy.c:2067`, ours `amy.c:2444`. **Identical.**
- Soft-clip stage (positive/negate → `S2L` → `FIRST_NONLIN`/`FIRST_HARDCLIP`/`clipping_lookup_table`) — upstream `amy.c:2087-2113`, ours `amy.c:2552-2582`. **Byte-identical logic**; `FIRST_NONLIN`/`FIRST_HARDCLIP` (`clipping_lookup_table.h:5,8`) and `SAMPLE_MAX` (`amy.h:314`) diff empty between refs. No separate master EQ stage exists in either build (only per-bus EQ, Stage 6 territory).
- `AMY_HPF_OUTPUT` (a one-pole output HPF, code present identically at `amy.c:2078-2085` upstream / `:2542-2550` ours, with a comment citing "large low-frequency excursions from some FM patches") — **checked the actual build flags: `AMY_HPF_OUTPUT` is not defined in either upstream's or our `tulip.mk`/`esp32_common.cmake`.** Gated off on both sides. Not a live divergence, but notable that upstream's own code anticipated FM needing output HF/LF treatment and it's dormant on both refs.

**Stage verdict: CLEAR.** Master-stage math is upstream-identical; no build-flag divergence either.

---

## Stage 8 — OUTPUT

**Scope:** `amy_fill_buffer` output loop, the ESP `>>1` (-6 dB) shift, i2s.c DMA, codec/DAC → TRS jack.

- Final int16 cast including the platform `uintval >>= 1` (-6 dB) shift — upstream `amy.c:2107-2113`, ours `amy.c:2574-2582`. **Identical**, applied unconditionally to the fully-mixed sample after all buses are summed — **uniform across every instrument/oscillator type**, not conditional on synth engine. If this were wrong it would flatten *everything* equally, which contradicts a DX7-specific gap.
- `amy_peak_hold` meter tap (ours only, amy.c:2570) reads `uintval` **before** the platform shift for correct metering scale — read-only instrumentation, doesn't touch the sample path.
- `src/i2s.c` diff (178 lines) is entirely the adaptive core-split controller (`amy_split_index`, moving the core-0/core-1 osc-render boundary to balance render time, replacing the static `AMY_OSCS/2` split) plus a documented voice-alignment snap so the split index never lands mid-voice — the snap exists specifically to *prevent* a chain-tail osc (unenveloped, full-scale) from rendering raw into the mix if a voice were split across cores. This is the "voice-align split fix" referenced in the task brief; **it is a bug-prevention mechanism, not a bug source**, and — if it were ever defeated — the failure mode is a transient full-scale glitch on any multi-osc chain (Juno's algo voices are also chains), not a steady 15 dB attenuation, and not DX7-specific.
- No codec/DAC driver with a gain/volume register exists in either repo's I2S-adjacent code (`tulip/**` has no `codec`/`es8311`/`es8388`/`pcm5102` files); the AMYboard's DAC path has no software-controllable gain stage to diverge on.
- `amy_fill_buffer`'s final divide/shift/cast to int16 output is otherwise identical line-for-line between upstream and ours.

**Stage verdict: CLEAR.** Everything from bus-sum onward is bit-identical to upstream or is a same-for-every-instrument scheduling optimization; even a latent bug in the adaptive core split (none found) could not explain a DX7-only gap since it treats all oscillator chains identically.

---

## Ranked divergence list (by plausibility as a DX7-quietness cause)

All items below were investigated and **none were confirmed as a live gain-affecting bug.** Ranked by how close each one came to being a plausible suspect before being ruled out, most-plausible first:

1. **Stage 6 — DX7 patches carry no baked EQ/chorus, Juno patches do.** Confirmed real (verified directly against `patches.h` patch strings), confirmed upstream-inherent (not a fork content gap — `patches.h` is byte-identical to upstream), confirmed unity-gain in its defaults (`config_eq(bus,1,1,1)`, `CHORUS_DEFAULT_LEVEL=0`). **Not a literal gain cause, but the closest thing to a "real, measurable difference" found in the whole trace** — a Juno patch's baked EQ curve reshapes its spectrum in ways that could read as louder on some meters/monitors even at equal RMS. Recommend: if the reported gap survives an RMS-matched or A-weighted re-measurement, re-examine here first, but this is upstream's own patch design choice, not a fork defect.
2. **Stage 4 — the `role`→`status` field consolidation.** Verified semantically equivalent by tracing every read/write site, but it is the single most structurally risky refactor found on the FM path (a future edit that treats the merged field as a bitmask instead of an enum would break the algo-source exclusion silently). Currently correct; flagged for awareness, not as a live bug.
3. **Stage 3 — `patches.c`'s 395-line reorganization.** Large diff, but every gain-relevant code path (`synth_level` set/get, `oscs_per_voice` sourcing) traced through unchanged in substance. No bug found, but the sheer diff size means this is the file most worth a second pair of eyes if a future regression appears.
4. Everything else audited (Stages 1, 2, 5, 7, 8, and the remainder of Stage 4/6) is either byte-identical to upstream or a verified no-op/orthogonal change.

## Top 3 suspects for the DX7 quietness (final assessment)

Given that **no fork-introduced gain divergence was found anywhere in the signal path**, the three most plausible explanations, ranked:

1. **It's inherent to upstream's own FM design and reproduces on stock upstream tulip too.** The `/4` divide (`algorithms.c:224`) is arbitrary by its own comment ("Arbitrarily divide FM voice output by 4 to make it more in line with other oscs") and DX7 operator patches bake comparatively modest output levels on non-carrier operators (e.g. patch 131's operators range `a0.037163` to `a2`) compared to a Juno patch stacking 2-3 full-amplitude additive oscillators via `chained_osc`. This is upstream's own tuning choice, confirmed byte-identical on our fork. **This is the leading hypothesis** — if true, the fix (if wanted) would need to touch upstream-shared code (`algorithms.c`'s `/4`, or per-patch operator levels in `patches.h`), which is out of scope for this read-only audit per the task brief.
2. **A perceptual/spectral effect, not a literal gain difference** (Stage 6, item 1 above): Juno patches carry a baked EQ+chorus voicing that DX7 patches don't, which could read as "louder" on some measurement even without a literal amplitude difference. Worth re-measuring the actual complaint (RMS/peak in a DAW, not by ear) to distinguish this from hypothesis 1.
3. **A DX7 patch selected for comparison has an unusually quiet FM algorithm/operator config within upstream's own design space** — i.e. not every DX7 patch is 15 dB quieter than every Juno patch; the specific patches compared may not be representative. This audit didn't have device access to A/B specific patches (per the read-only, no-device-access constraint), so this couldn't be ruled in or out — flagging as an open question for whoever reproduces the complaint on hardware.

**No fork-vs-upstream divergence claims the title of "smoking gun."** The honest conclusion of this audit is that the DX7 FM render path — patch data, Python FM model, and C render math — is genuinely upstream-identical, and the quietness complaint, if real, is very likely a property of upstream AMY's own FM voicing design rather than something introduced by the fork's MIDI/note-dispatch/bus/FX/output changes.

## Caveats / uncertainty

- This audit is code-comparison only; no device was used to actually measure the reported 15 dB gap, confirm which specific DX7 patch(es) and Juno patch(es) were compared, or rule out a user-side variable (patch selection, playback velocity, per-instrument level slider left at a non-default value in a saved rack config rather than the engine default traced here). Recommend a controlled A/B (same velocity, same num_voices, RMS-matched measurement) as a follow-up before pursuing any code change.
- Stage 4's `role`→`status` consolidation was verified by tracing every call site found via `grep`, not by a formal proof or by building/running the code; a call site missed by grep (e.g. behind a macro) would not have been caught.
- The `amy_reserved_oscs` feature (Stage 3) and `AMY_AUX_REVERB` (Stage 6) are both fork-only architecture additions that are currently inert/uniform by default; this audit did not exhaustively fuzz every possible deck configuration state (e.g. a saved rack with a non-default `reverb_send` or a partially-migrated FX config) that could interact with them.
