# Engineering review — deck/ + fork C changes

Repo-only review (no device). Scope: all of `deck/`, the fork's AMY changes
(`amy/src/pcm.c`, `amy.c` AUX_REVERB, `delay.c`, `amy.h`), firmware glue
(`tulip/shared/amy_connector.c`, `modtulip.c`, `tsequencer.c`, `display.c`),
and the tools pipeline (`tools/drumsynth/`). Paths below are repo-relative.

---

## Executive summary

The deck is in better shape than most performance-UI codebases of this size:
the hot MIDI path has already been pushed mostly into C (`c_channels`), slider
drags are cache-only with commit-on-release, panel builds are chunked, and the
flash/PCM crash class was root-caused and fenced. The pure-logic/LVGL split
(`shellmodel.py`, `channels.py`, `amyparams.py` vs the panel modules) is the
right architecture and should be preserved.

The single most important finding is **E-1**: every callback handed to
`tulip.defer()` (and `tulip.midi_callback()`) is stored in a plain C global
that MicroPython's GC never scans. Lambdas and closures whose only reference
is that array get collected and their heap block reused — when the defer
fires, the C side calls whatever object now lives there. That is mystery #1
("`'float' object isn't callable`"), and it is also a latent cause of lost
deferred note-offs, dead meter/clock ticks, and (worst case) hard crashes.
The deck leans on `tulip.defer(lambda …)` in ~15 places, so this is systemic,
not cosmetic.

Second: the flash fence is **opt-in Python discipline** (E-2). `deckcfg` and
`decklog` fence their writes, but `files.py` delete, the firmware editor,
wordpad, screenshot writes, `midi_cc_file.json` saves and `tulip.upgrade` do
not — the original dual-core WDT crash is still reachable from a Files-panel
Delete tap while a PCM voice decays. The fence belongs one layer down, in C,
around the block-device write/erase.

Third: under the fork's build flags (`-DAMY_AUX_REVERB`, no
`AMY_MASTER_REVERB`) **bus 0 is reverberated twice per block** and the shared
reverb's delay lines advance twice per block (E-3) — audible as a wrong/short
room on the first instrument, plus a wasted second reverb per block on a core
that renders audio.

The four "mysteries" all have concrete answers (section at the end), and the
new serial-protocol work (a9def998) is reviewed at the bottom — short
version: **keeping the typed-message router host-side is the right call**;
a C-side console mux would put regression risk on the recovery path to save
zero cycles on the instrument.

---

## Findings

### E-1 — CRITICAL: `tulip.defer` / `tulip.midi_callback` callbacks are invisible to the GC (mystery #1)

**Where:**
- `tulip/shared/tsequencer.c:9-11` — `mp_obj_t defer_callbacks[DEFER_SLOTS]`,
  `defer_args[]` are plain C globals (BSS).
- `tulip/shared/modtulip.c:102` — `mp_obj_t midi_callback` (plain global).
- `tulip/shared/modtulip.c:119` — `amy_block_done_callback` (same).
- The only `MP_REGISTER_ROOT_POINTER` in the port is `native_code_pointers`
  (`tulip/esp32s3/main.c:486`). On the esp32 port, `gc_collect()` scans
  registered roots + task stacks only — **not** the C data/BSS segment.

**Failure mode:** any callback whose *only* strong reference is one of these
globals is collectable. Module-level functions survive (their module dict
holds them); lambdas and closures do not. After collection the heap block is
reused — commonly by a boxed float — and when
`tsequencer.c:24 mp_sched_schedule(defer_callbacks[i], …)` fires, Python
calls a float: `TypeError: 'float' object isn't callable`, printed by the
scheduler as an uncaught exception. It fires "once per config save / router
rebuild" because those are exactly the moments that allocate heavily and
trigger a GC pass while a defer is pending.

**Deck call sites at risk (all lambda/closure defers):**
- `deck/deckcfg.py:284` (deferred `_write` retry — a collected one silently
  drops a config save),
- `deck/forwarder.py:572,580` (preview note-off — collected = stuck preview
  note),
- `deck/homeshell.py:350,438,577,604` (panel refill, back-swap, meter tick,
  clock tick — collected = frozen meter/clock or a panel that never fills),
- `deck/instrument.py:265,286`, `deck/parameditor.py:328`, `deck/rack.py:568`,
  `deck/settings.py:276,542`, `deck/deckui.py:638`, `deck/midimon.py:142,210`.
- **Also** `deck/ui_patch.py:467`: `tulip.midi_callback(_drain)` stores a
  closure in the unrooted `midi_callback` global. If `_drain` is ever
  collected, *all* MIDI input dies (or calls garbage) until reboot. The stock
  firmware passes a module function here; the deck's override introduced the
  closure.

**Fix (firmware, small):** make the storage a GC root. Either

```c
// tsequencer.h / modtulip.c
MP_REGISTER_ROOT_POINTER(mp_obj_t defer_callbacks[DEFER_SLOTS]);
MP_REGISTER_ROOT_POINTER(mp_obj_t defer_args[DEFER_SLOTS]);
MP_REGISTER_ROOT_POINTER(mp_obj_t tulip_midi_callback_obj_ref);
```

and use `MP_STATE_VM(defer_callbacks)[i]` everywhere, or (no firmware
rebuild) add a Python-side registry: a `tulip.py` wrapper around `defer()`
that stores the callback in a module dict keyed by slot and removes it when
fired. The C fix is the right one — the Python one leaks entries when a defer
is dropped by `tsequencer_init()`.

**Until the fix lands:** pass module-level functions to `tulip.defer`, never
lambdas. (`screensaver.py` and `midimon.py` already do this correctly.)

### E-2 — CRITICAL: flash-fence coverage is opt-in; unfenced writes keep the WDT crash reachable

**Where:** `deck/deckcfg.py:232-256` (`fenced_write`), `deck/decklog.py:71-78`
fence correctly. Not fenced: `deck/files.py:230` (`os.remove` from the Delete
button), `deck/settings.py:447` (factory-reset `os.remove`), the firmware
editor/wordpad saves, `tulip.screenshot`, `midi_cc_file.json` writes, any
user app writing to `/user`, and `tulip.upgrade`'s pre-write filesystem
activity.

**Why it matters:** the crash mechanism (flash program/erase suspends the
cache that `render_pcm`'s mmap'd fetches go through — `amy/src/pcm.c:44-51`,
`tulip/shared/modtulip.c:48-57`) doesn't care which Python module did the
write. Any unfenced write while a mapped-PCM voice renders is the documented
dual-core TG1WDT hard crash. Today that's one Files-panel tap away.

**Fix:** move the fence into C at the storage layer so it is automatic:
raise `amy_flash_fence`, delay ~2 render blocks, write, drop — inside the
esp32 partition/littlefs block-device write+erase hooks (the esp32 port's
`esp32_partition_write` path), not in Python. Then delete the Python-side
`fenced_write`/`quiet_now` machinery entirely (keep `quiet_now` only as a
fallback for pre-fence firmware if you must). This also removes the 60×250ms
retry chain and its defer traffic. Note `amy.h:1213-1221` already parks the
AMY tasks below the flash-guard IPC priority — with that plus a C-side fence,
the whole "is it safe to write" question disappears from Python.

### E-3 — HIGH: AUX reverb double-processes bus 0 and double-advances the reverb state

**Where:** `amy/src/amy.c:2007-2018` vs `amy/src/amy.c:2025-2064`, built with
`-DAMY_AUX_REVERB` only (`tulip/shared/tulip.mk:8`; `AMY_MASTER_REVERB` is
**not** defined anywhere).

The per-bus insert reverb block is compiled out only under
`#ifndef AMY_MASTER_REVERB` — so in this build it is **in**. Both it and the
aux block gate on the same `amy_global.bus[0]->reverb.level > 0` and use the
same `bus[0]->reverb.rev` instance. Whenever the room is on:

1. bus 0's block gets `stereo_reverb()` applied in place (insert), then
2. the aux block sums all buses (bus 0 now already wet) into `dry`/`aux`, and
3. `stereo_reverb_wet()` runs the *same* Stautner–Puckette network a second
   time in the same 256-sample block — its delay lines and filter state
   advance at 2× rate, halving the effective decay and coloring the tail.

Also `config_reverb` (`amy/src/amy.c:368-371`) clamps `bus = 0` only under
`AMY_MASTER_REVERB`, so a patch string carrying baked `h` params on a
non-zero bus configures a *second* per-bus reverb that the aux model never
intended to exist.

**Fix:** change both guards to
`#if !defined(AMY_MASTER_REVERB) && !defined(AMY_AUX_REVERB)` around the
per-bus reverb apply, and extend the `bus = 0` clamp in `config_reverb` to
`#if defined(AMY_MASTER_REVERB) || defined(AMY_AUX_REVERB)`. One reverb per
block, on the aux return only. (Bonus: `amy.c:444` defaults `reverb_send` to
1.0 per bus — deliberate for master-room compat, but it means any bus the
router hasn't explicitly set sends full-wet; the router papers over this at
rebuild (`forwarder.py:518`). Consider defaulting new buses to the deck's
policy (0.0) behind the AUX flag so un-managed synths stay dry.)

### E-4 — HIGH: RAM-patch slot map has no bounds — collisions after 5 melodic / 5 drum instruments

**Where:** `deck/synthkits.py:26-33` (slot constants), `deck/forwarder.py:328-330,397-419`.

The map is: 1024 audition, 1025..1029 melodic (5 slots), 1030 + 24·n per drum
kit; `amy_config.max_memory_patches = 128` (`tulip/shared/amy_connector.c:540`)
caps the pool at 1024..1151.

- `_next_melodic_slot` is unbounded: the **6th** gm/gm2 instrument stores its
  patch at slot 1030 — the first drum kit's *kit patch* slot. The kit then
  loads a melodic patch string as its silent placeholder (or vice versa,
  depending on rebuild order).
- `SLOT_KIT_STRIDE = 24` with kits of ~19 hits leaves only 4 spare; a future
  kit with >23 mapped notes silently bleeds into the next kit's window.
- The **6th** drum instrument (1030 + 120 = 1150 + 19 hits) walks past 1151
  into AMY's rejection range.

Nothing validates any of this; failures would present as "wrong kit sounds on
another instrument", the hardest class of bug to field-diagnose.

**Fix (cheap):** define `MAX_MELODIC_SLOTS`/`MAX_KIT_SLOTS` next to the
constants in `synthkits.py`, make `forwarder._start_once` refuse (log +
disable instrument) past the caps, and add one host test asserting the whole
map fits `< 1024 + 128` with worst-case counts. Longer term give slot
allocation one owner (a tiny `slots.py`) instead of arithmetic split across
`forwarder`, `synthkits`, `drums_kit`.

### E-5 — HIGH: littlefs "Corrupted dir pair at {0x1, 0x0}" (mystery #2) — root-pair abuse by design

`{0x1, 0x0}` is the **superblock/root-directory metadata pair** (blocks 0-1).
Two deck behaviors converge on it:

1. **Every hot file lives in the `/user` root.** In littlefs, each commit to
   a file's parent directory rewrites that directory's metadata pair — for
   root files that is the *anchored* superblock pair, which (unlike normal
   dir pairs) can never be relocated by wear-leveling. `deck_config.json`
   whole-file rewrites (every toggle/favorite/instrument edit) and
   `deck.log` per-line append-open-close (`decklog.py:67-76` — one commit
   *per log call* on fence firmware, plus an `os.stat` and rollover check
   per call) concentrate erase cycles exactly there.
2. **The pre-fence crash history.** A power-cut/WDT mid-program on blocks 0/1
   leaves one half of the pair with a bad CRC. littlefs keeps running off the
   good half but re-reports the corrupt one on every traversal — which
   matches "repeatedly on /user operations". The config json being only 564 B
   (see `deck/device-backup/deck_config.json`) means it is inlined in the
   metadata pair, so config writes are *pure* metadata-pair traffic.

**Mitigations, in order of value:**
1. One-time repair: back up `/user`, reformat, restore. The message never
   heals by itself if the bad half is genuinely worn.
2. **Move hot-write files into a subdirectory** (`/user/var/deck_config.json`,
   `/user/var/deck.log`): commits then land on a relocatable child pair, not
   the anchored root pair. This is the single highest-leverage change.
3. Batch the log: buffer lines and flush on a 2-5 s / 2 KB threshold (the
   `_PENDING` machinery already exists — today it flushes per call whenever a
   fence is available). Drop the per-call `os.stat` (track size in RAM).
4. Atomic config writes: `json.dump` to `PATH + '.new'`, then `os.rename`
   (atomic in littlefs). This protects against torn writes; note it slightly
   *increases* metadata commits, so pair it with (2).
5. Confirm the mount's `block_cycles` is the MicroPython default (100), not
   -1, so non-root pairs relocate before they wear out.

### E-6 — MEDIUM: `deckcfg.load()` returns the live cache — aliasing is the API

**Where:** `deck/deckcfg.py:163-201,293-295`.

Every caller gets *the* cached dict. All setters mutate it in place, which is
what makes `flush=False` drags work — but it also means any caller that
mutates the returned dict (or the `instruments()` list) without calling
`save()` has silently changed state that the *next unrelated* `save()` will
persist. Nothing enforces the discipline; it works today because every
mutation site happens to go through the setters. As the module count grows
this is the classic source of "config changed and nobody knows who did it".

**Fix:** document the contract at `load()` (one line: "treat as read-only;
mutate only via setters"), and in `test_deck.py` add a canary: load, deep-copy,
run a panel-build-ish read path, assert no mutation. Full copy-on-read is not
worth the RAM/GC cost on-device — the contract + canary is the right size.

### E-7 — MEDIUM: `midimon` keeps its MIDI callback (and a 150 ms defer chain) alive after the panel closes

**Where:** `deck/midimon.py:119-144,168-212`.

`_close()` runs only when `_render()` throws — and `_render()` runs only when
`_s['dirty']` is set. Leave the panel while paused (or with no MIDI traffic)
and: the tick chain keeps re-deferring every 150 ms forever, `_s['open']`
stays True, and `_cb` keeps running **per MIDI message** (append + bytes()
copy) on the hot path indefinitely. Re-opening the panel bumps `sid` and
strands the old chain, but the callback cost persists until a message finally
arrives and `_render` touches the dead label.

**Fix:** probe liveness unconditionally in `_tick` (e.g.
`_s['lbl'].get_text()` in a try, or check `lbl.is_valid()`), or register an
`lv.EVENT.DELETE` handler on the label that calls `_close()`. Same pattern
worth auditing anywhere a panel registers an external callback
(`screensaver.note_activity` is global-by-design, fine).

### E-8 — MEDIUM: shellmodel/product tables have drifted — wrong labels for GM and synth-kit instruments

**Where:** `deck/shellmodel.py:23-34,88-96`, `deck/home.py:248-253`.

- `instrument_sound()` ignores types `gm`/`gm2`: a GM instrument with program
  5 shows the *Juno* patch-5 name in every rack row and the home footer
  (`home.py` repeats the same `patches[...]` lookup).
- `_KIT_NAMES` duplicates `drums_kit.KITS` and knows nothing about synth kits,
  so any `'synth:…'` kit renders as "TR-808 kit" in the rack row.
- `patch_short()` labels GM patches "Juno%d".
- The `0..127 juno / 128..255 dx7 / 256 piano` boundary set now lives in four
  places: `shellmodel.py:10-11`, `amyparams.engine_of`, `deckcfg.type_of_patch`,
  `instrument._TYPE_RANGE` + `rack._TYPE_FIRST_PATCH`.

**Fix:** one `catalog.py` (pure, host-testable) owning: engine ranges, type →
first patch, kit-id → display name (sampled + synth), and
`sound_label(instr)` that dispatches on `type`. Everything else imports it.

### E-9 — MEDIUM: 32 shared defer slots are a global failure domain

**Where:** `tulip/shared/tsequencer.h:6`, `modtulip.c:224-238`.

The deck's steady state holds 3-4 slots (meter, clock, screensaver,
write-chain), and bursts add panel fills, toasts, preview note-offs,
tab-fill chains and debounced searches. Exhaustion raises
`ValueError('No more defer slots')` at the *caller* — and several deck
`except Exception` handlers respond by permanently disabling their feature
(`screensaver.py:93-95` stops the screensaver until reboot;
`homeshell._start_meter/_start_clock` stop their ticks). Nothing ever
retries.

**Fix:** short term, bump `DEFER_SLOTS` to 64 (32 pointers of RAM). Real fix:
the periodic consumers (meter/clock/screensaver/midimon) shouldn't ride the
defer pool at all — use `lv_timer` or one C-side 100 ms "deck tick" that
Python subscribes to (see migration map #3). That removes ~90% of defer
traffic and the E-1 exposure with it.

### E-10 — MEDIUM: forwarder rebuild is a sledgehammer, and it's on every edit path

**Where:** `deck/forwarder.py:286-550`; called via `deckcfg.apply_all()` from
`rack.py` (channel step, device change, enable toggle, voices release, type,
kit), `instrument._select_patch`, `mpe.py` (every switch/slider release),
`settings._mpe_switch`, `devices._rescan`.

Every rebuild releases *all* synths, re-stores patches, re-creates every
instrument, replays FX and MPE zones. With a synth drum kit configured, that
is ~19 × (store_patch + PatchSynth + `sleep_ms(2)`) ≈ 40-80 ms *minimum* of
serial AMY wire traffic and Python — per channel-stepper tap, times N kits.
The reentrancy guard (`rebuilding`/`rebuild_queued`, forwarder.py:293-303) is
correct, and `_route` degrades gracefully mid-rebuild (empty routes = dropped
messages, not crashes) — but rapid edits still audibly interrupt everything
that's sounding, including instruments the edit didn't touch.

**Fix (design direction, not now):** per-instrument apply. `deckcfg` already
knows which instrument changed; teach forwarder a `rebuild_one(iid)` that
releases/rebuilds only that synth and re-asserts only its bus. Keep the full
rebuild for device/MPE topology changes. This also decimates slot-map churn
(E-4) and littlefs-adjacent stalls.

### E-11 — LOW/MEDIUM: `last_midi` ring buffer has a writer/reader race

**Where:** `tulip/shared/amy_connector.c:50-56,166-181`.

Written from AMY's MIDI context, read by the MP task (`tulip.midi_in`). On
queue-wrap the *writer* advances `midi_queue_head` — mutating the reader's
cursor unsynchronized (classic SPSC violation; `int16_t` indices are at least
atomic on Xtensa word stores, but the head bump can interleave with a
concurrent read of the same slot). Worst case a message is torn/duplicated
during floods — rare, plausible during MPE storms. Fix: drop-newest instead
of drop-oldest (writer never touches head), or a real single-producer ring
(power-of-2 size + monotonic indices).

### E-12 — LOW: per-tick audition machine-guns hits in the pad editor

`deck/padeditor.py:107-122` — `_apply()` ends with `_audition(note)` and runs
on every slider `VALUE_CHANGED` tick, so a drag fires dozens of hits and
`retweak()` (store_patch + 2 amy.sends) per tick. Keep retweak per tick (live
audition of the *next* natural hit is the point), but only trigger the note on
release, or rate-limit to ~5 Hz.

### E-13 — LOW: envelope thinning keeps the wrong points

`tools/drumsynth/ds2amy.py:65-86` — the docstring says "middles by biggest
level steps" but the code sorts middles by `-abs(p[1])` (highest absolute
level). Long-decay envelopes lose their body points and collapse toward the
attack; part of why converted hits read thinner/quieter than their sources
(feeds mystery #4). Fix: sort by `abs(step to previous kept point)` as
documented, or better, by trapezoid-area error.

### E-14 — LOW: `settings._amy_volume` / `deckcfg.apply._volume` call whatever `amy.volume` is

`deck/settings.py:18-30`, `deck/deckcfg.py:626-634`. Today `amy` has no
`volume` attribute so the `amy.send(volume=)` branch runs. If upstream amy
ever grows a module-level `volume` *value* (it has grown module state
before), `vol(v)` raises TypeError — swallowed in settings, swallowed in
apply's loop, and volume silently stops applying. Make the probe
`callable(vol)` instead of `is not None`. (This pattern was investigated as
mystery #1's cause; it is innocent today but it is the same disease —
attribute-probing another module's API. Prefer explicit versioned probes.)

### E-15 — LOW: error-swallowing breadth

`except Exception: pass` appears ~120 times across `deck/`. The pattern is
deliberate (a performance instrument must not crash), but many sites swallow
*programming* errors, not environmental ones — e.g. every FX apply in
`forwarder._apply_device_fx`, every `_apply_params` send. Suggested contract:
environmental guards stay silent; anything that indicates a code bug logs via
`decklog.dbg()` (debug-gated, so zero cost in performance mode). The two
existing patterns to copy are `_synth_err` (first-failure-per-instrument,
forwarder.py:63-73) and `_fill`'s on-screen panel error (homeshell.py:382).

### E-16 — LOW: test coverage gaps vs `test_deck.py`

`test_deck.py` (1278 lines) covers deckcfg migration/instrument API,
forwarder routing/layering/note-off, ui_patch quit topology, channels, and
amyparams — the right pure-logic set. Missing:
- `synthkits.hit_patch_string` override math (`_xform_partials`,
  `_capped_gain` cap behavior, snap-on-noise-only) — pure, easy;
- `deckcfg._write` retry-chain semantics (coalescing, cap, `write_chain`
  reset) with a fake `tulip.defer`;
- the slot map (E-4) as an invariant test;
- kit loudness gate (mystery #4 below);
- `decklog` rollover.

---

## Where the cycles go — prioritized optimizations

Budget context for every number below: ESP32-S3 @ 240 MHz, dual core. AMY
renders 256-sample blocks at 44.1 kHz stereo (`amy/src/amy.h:80,89`) — a
block every **5.8 ms**, ~172 blocks/s, render task pinned core 0, fill-buffer
task core 1 (`amy.h:1223-1224`), both at `MAX-3`. The MicroPython/LVGL/UI
world shares what's left. Three currencies matter: **core-1/0 DSP cycles**
(audio headroom = polyphony), **MP-task milliseconds** (UI latency, and —
because MIDI dispatch is scheduled onto the MP task — note-to-sound latency
for anything not C-owned), and **GC pressure** (each heap alloc advances
toward a collection; a gc_collect over a multi-MB PSRAM heap is a 30-100 ms
stall of the MP task, which is exactly the "missed note / late note" window).

Ranked by impact-per-effort:

### O-1. Aux-reverb guard fix (E-3) — DSP cycles, 2 lines
`stereo_reverb`/`stereo_reverb_wet` is the most expensive per-sample FX in
AMY: per sample it runs 6 early-reflection delay taps, 4 main delay lines,
4 one-pole LPFs and the 4×4 mixing matrix (`amy/src/delay.c:340-414`) —
order 50-70 fixed-point ops/sample, ×512 samples (stereo block) ≈ **~30 k
ops per block, ~5 M ops/s ≈ 2-4% of a core** per instance. Today, with the
room on, it runs **twice** per block (insert on bus 0 + aux return). The
2-line preprocessor fix buys that back *and* fixes the audible double-room.
Effort: trivial. Risk: none (dead code under the deck's flags).

### O-2. Finish the C MIDI path (migration #1) — MP-task ms + GC, medium
What Python still pays per MIDI message today (all on the MP task, scheduled
from `tulip_midi_input_hook`):
- `tulip.midi_in()` allocates a fresh bytes object per message;
- `ui_patch._drain` (ui_patch.py:423-466): status decode, coalescing dict +
  batch list (two more allocs per message), then a call *per registered
  callback* — currently 2-3 (`forwarder._route`, `screensaver.note_activity`,
  `midimon._cb` when open, plus `midi.midi_event_cb`);
- `forwarder._route`: ~10-30 dict/set ops even on the early-out paths.

At MicroPython call overhead (~5-15 µs/call on the S3) the full chain is
**~150-400 µs per message**. An MPE controller streaming pressure+bend+CC at
~300 msg/s costs **5-12% of the MP core continuously**, plus ~3 heap allocs
per message — thousands of allocs/minute driving the GC toward its next
multi-10-ms pause. Notes on layered channels and *all* board-bound traffic
ride this path, so a GC pause here is audible timing jitter.

The C layer already owns solo internal channels (`c_channels`). Finish it: a
C routing table (`channel → {board mask, synth list, mpe-member flag}`) that
`forwarder._start_once` uploads after each rebuild; C forwards board bytes
(`tulip_send_midi_out_device` is already C), plays layered internals, bumps
an activity counter, and only escalates to Python what the UI actually needs
(midimon tap when open). Python's per-message cost drops to **zero** in the
common case. Effort: medium (~150 lines C + a `tulip.midi_routes(...)`
binding + forwarder changes, Python fallback kept for desktop). This is the
single biggest latency/jitter win available.

### O-3. Coalesce cheaply until O-2 lands — 10 lines each, today
Two immediate trims to the same hot path:
- `screensaver.note_activity` (screensaver.py:66-74) runs per message and
  calls `lv.display_get_default().trigger_activity()` through the MP binding
  (~20-50 µs) — under an MPE stream that alone is ~1-2% of the MP core. Set
  a plain Python flag in the drain instead; let the existing 300 ms
  screensaver tick call `trigger_activity()` once if the flag is set.
  Cost: 10 lines. Saves an LVGL round-trip per message.
- `midimon` leak (E-7): after leaving the panel, `_cb` keeps paying
  bytes-copy + list-append per message forever. Fixing the teardown is a
  perf fix, not just hygiene.

### O-4. Fence into the C write path (E-2) — MP-task ms + audio gaps
Beyond the crash-class closure: every fenced write today costs
`sleep_ms(12)` **on the MP task** with mapped-PCM voices muted
(`deckcfg.py:249-255`) — a config save is ~12-15 ms of frozen UI and a 12 ms
PCM hole; every decklog line on fence firmware pays the same 12 ms again
(`decklog.py:71-76`). In debug mode a router rebuild logs 1-2 lines → each
rebuild silently costs an extra ~25 ms + two PCM holes. A C-side fence around
the actual block-device write shrinks the window to the real program/erase
time (~1-4 ms per 4 KB page-program burst) instead of a fixed 12 ms guess,
and batching (O-6) amortizes what's left. Effort: ~40 lines C, deletes ~60
lines of Python retry machinery (and its defer/GC traffic).

### O-5. Per-instrument router apply (E-10) — perceived latency, medium
Full rebuild cost, measured from the code: per synth-drum kit, 19 ×
(`store_patch` — AMY parses a ~200-char wire string — + `PatchSynth` create +
`deferred_init` sends + `sleep_ms(2)` yield) ≈ **80-120 ms**; melodic
instruments ~5-15 ms each; plus FX re-baseline sends for every bus. Every
channel-stepper tap, enable toggle, voice-slider release and MPE switch pays
the whole bill and briefly interrupts every sounding instrument.
`rebuild_one(iid)` cuts an edit to the ~5-15 ms (or ~100 ms for the one kit)
that actually changed, and stops the audible dropout on untouched
instruments. Effort: medium — the state to keep coherent is `routes`,
`c_channels`, `fx_targets`, slot assignment (fixes E-4 pressure too).

### O-6. Batch decklog + move hot files off the root pair (E-5) — flash time
Each `decklog.log()` on-device write is: `os.stat` (a littlefs metadata
walk), open-append-close (a metadata-pair commit ≈ one 4 KB program,
periodically a compaction = erase+rewrite of the pair ≈ **several ms with
the flash cache suspended** — during which *both* cores stall on any cache
miss), plus the 12 ms Python fence (until O-4). Buffering lines and flushing
on a 2-5 s / 2 KB threshold turns N commits into 1 and divides root-pair
wear by N. Effort: small (the `_PENDING` buffer already exists — change the
flush policy).

### O-7. One tick source instead of defer chains — GC + reliability
The meter (10 Hz), clock (0.03-0.5 Hz), screensaver (3.3 Hz) and midimon
(6.7 Hz) each re-arm via `tulip.defer`, allocating a closure/slot per tick —
~20 allocs/s at steady state, all E-1-exposed, all competing for 32 global
slots (E-9). One `lv_timer` (or a single C 100 ms tick calling a
module-level dispatcher) removes the allocation churn and the exhaustion
failure mode. The per-tick *work* is already cheap (meter tick ≈ 100-300 µs;
`amy_level` is a C peak-hold read, `modtulip.c:372-386`). Effort: small.

### O-8. `kit_panel` builds ~80 rows in one tick (`rack.py:297-351`)
~80 × (button + label + 6-8 style calls) through the MP-LVGL binding ≈
**40-80 ms in a single LVGL event tick**, plus one full flex relayout — the
same single-tick shape that produced the interrupt-WDT reboots the codebase
already fought (`parameditor.build_tabbed`'s chunking comment,
UX-REVIEW-6 H1). Reuse `instrument.py`'s `_WINDOW`-batched builder (40 rows +
"Show more") or defer-fill after the first screenful. Effort: small.

### O-9. `gmbig.py` tables — heap + import time
~24 KB of Python source parsed on first import into ~1000+ nested tuples/
dicts: on MicroPython that's roughly **60-120 KB of heap** plus a
100-300 ms compile on-device, and every one of those objects joins the GC
scan set forever after (longer collections). Freeze it as `.mpy` (kills the
compile) or better pack to a `bytes` blob + `struct.unpack_from` accessors
(kills ~90% of the heap objects). Same treatment applies to `gm.py`'s
tables at smaller scale. Effort: small-medium.

### O-10. `render_pcm` inner-loop dispatch (`amy/src/pcm.c:364-401`)
The per-sample loop re-tests `preset->channels == 2` and the L/R/mix wave
variant on every sample — 2-3 branches × 256 samples × every PCM voice.
Hoisting to per-block dispatch (mono/stereo × L/R/mix specializations, the
`render_lut` family idiom) saves ~5-10% of PCM render cost per voice; with
6-10 sample voices sounding (a busy kit) that's ~0.5-1% of core 0 back, and
it composes with every future PCM use. Effort: medium; upstreamable.

### O-11. AUX mix loop hoisting (`amy/src/amy.c:2037-2048`) — minor
The dry/send accumulation loops over `bus ≤ highest_bus` per sample with
`amy_global` pointer chases inside. Hoisting `fbl[0][bus]` base pointers and
the two scale vectors into locals before the sample loop is worth ~10-20% of
that loop's cost (compiler may already do some of it; check the disassembly
before bothering). Only worth touching if O-1's win isn't enough headroom.

**Non-optimizations** (measured judgment, don't spend time): `deckcfg.load()`
caching already fixed the config-parse hot spot; `json.dump` of a 0.5-2 KB
config is not worth a custom format; `star_src` rasterizes once and caches;
`_discover_user` is cached on the dir listing; the slider drag paths are
already cache-only with commit-on-release — leave them alone.

---

## Python → C migration map (ranked)

The owner's direction: frontend stays Python, backend logic migrates to C.
Where it pays:

1. **MIDI drain + coalescing + routing table** (`ui_patch._drain`,
   `forwarder._route`) — the last per-MIDI-event Python. The C layer already
   plays C-owned channels; what remains in Python is: queue drain, the
   coalescing of pressure/bend/CC backlogs, board forwarding
   (`tulip.midi_out(msg, device)` per message), layered-channel fan-out, and
   the activity counter. All of it is table-driven — expressible as a small C
   routing table (`channel → {synth ids, board mask, mpe member set}`) that
   forwarder *writes* on rebuild and C consumes per event. Python then sees
   only what the UI needs (a counter, monitor taps when midimon is open).
   Payoff: zero-alloc, zero-GC note path even for layered channels and
   boards; immunity to MP-task stalls for *every* instrument, not just solo
   ones. Effort: medium (new modtulip binding + table struct + forwarder
   changes; the Python fallback stays for desktop).
2. **Flash fence at the storage layer** (E-2). Effort: small. Payoff:
   correctness + deletes Python machinery.
3. **Timer/defer infrastructure** (E-1 + E-9 + opt #4): GC-rooted defer
   arrays, plus one periodic tick source. Effort: small. Payoff: reliability
   of everything that ticks.
4. **MIDI activity + level taps**: `tulip.amy_level()` exists; add
   `tulip.midi_activity()` (monotonic counter bumped in
   `tulip_midi_input_hook`) so the 10 Hz meter stops importing forwarder and
   reading Python state. Effort: trivial.
5. **Kit note-map + hit-synth instantiation** (`drums_kit.SynthKit.__init__`)
   — currently ~19 store_patch + PatchSynth creations with 2 ms yields, per
   kit, per rebuild. A C-side "load kit from slot base" that registers the
   io note maps in one call would cut kit-switch latency from ~100 ms to ~ms.
   Effort: medium. Do it *after* #1 and E-10; per-instrument apply may make it
   unnecessary.

Where it is **not** worth it:

- **Config (`deckcfg`)**: JSON at this size is fine; migration/merge logic
  changes often and benefits from host tests. Keep Python.
- **Patch-string generation** (`synthkits.hit_patch_string`, `amyparams.*`):
  string assembly, runs on user edits only, heavily unit-tested on host.
  Keep Python.
- **All LVGL panel code**: churn-heavy frontend; the fix for panel cost is
  build discipline (windowing, chunked tab fills — both already in place),
  not C.
- **shellmodel/channels pure logic**: the host-testability is worth more than
  the microseconds.
- **The serial console router** (a9def998's deferred C-side mux): zero
  on-device cycles at stake and the risk lands on the recovery path — see
  the serial-protocol review section below. The recorded decision to keep it
  host-side Python is the correct application of this map's own criteria.

---

## The four mysteries

### 1. `TypeError: 'float' object isn't callable` on save / rebuild

**Root cause: E-1.** `tulip.defer` stores the callback `mp_obj_t` in
`defer_callbacks[]` (`tulip/shared/tsequencer.c:9`), which is not a GC root
on the esp32 port. The deferred *lambdas* used by the save path
(`deckcfg.py:284`) and by the shell's tick/refill closures
(`homeshell.py:350,438,577,604`, `rack.py:568`) can be their block's only
reference. A config save / router rebuild allocates enough to trigger
`gc_collect()`, the pending lambda is freed, its block is reused by a boxed
float, and when `tsequencer.c:24` schedules it, calling it raises exactly
`TypeError: 'float' object isn't callable` — printed once, by the scheduler,
per collected callback. The candidates suggested in the brief
(`fenced_write`/`quiet_now` attribute probing, time shadowing, `amy.volume`)
are all innocent — verified: `amy` has no `volume` attribute, `quiet_now`
only calls real functions, and the probe pattern in `settings._amy_volume`
takes the `amy.send` branch.
**Fix:** register the arrays (and `midi_callback` /
`amy_block_done_callback`, `modtulip.c:102,119`) as MP root pointers; until
then, only module-level functions into `defer`.

### 2. littlefs `Corrupted dir pair at {0x1, 0x0}`

**See E-5.** `{1,0}` is the anchored superblock/root pair. All the deck's
hot-write files live in the `/user` root, so every config save and every
per-line log append commits to that unrelocatable pair; the corruption itself
most plausibly dates from the pre-fence era of hard crashes mid-write (the
exact failure the fence was built for), and littlefs re-reports the bad half
of the pair on every traversal forever. Repair = backup/reformat/restore;
prevent = move `deck_config.json` + `deck.log` into a subdirectory, batch log
flushes, and use tempfile+rename for the config. Write-throttling alone
cannot help the root pair because *any* root-file commit lands on it.

### 3. AMY_DEBUG profiler prints "luus"

`amy/src/amy.h:473-479` formats with `%10"PRIu64"us`. The ESP-IDF toolchain
builds newlib with **nano formatting** (`CONFIG_NEWLIB_NANO_FORMAT=y` on this
board config), and nano `printf` has no 64-bit integer support: it consumes
`%10ll` as an unknown conversion and emits the residual `"lu"` literally,
followed by the real `"us"` suffix → `luus`, and the argument is skipped.
**Portable fix** (works on nano, glibc, MSVC, clang): don't format 64-bit
integers at all —

```c
fprintf(stderr, "%40s: %10lu calls %10luus total [%6.2f%% wall %6.2f%% render] %9luus per call\n",
    profile_tag_name(tag), (unsigned long)profiles[tag].calls,
    (unsigned long)profiles[tag].us_total, ...,
    (unsigned long)(profiles[tag].us_total / profiles[tag].calls));
```

`us_total` as `unsigned long` (32-bit here) wraps at ~71 minutes of *counted*
time — far beyond any profiling session; if that bothers you, print
`us_total / 1000` as ms. (Alternative: `CONFIG_NEWLIB_NANO_FORMAT=n`, but
that costs ~25-40 KB of flash and only fixes this board.)

### 4. Near-silent tr909 4th-variant hits

Three compounding causes, all systematic:

1. **Dedupe-hash duplicates inflate the variant fan-out.** The harvest keeps
   per-tree duplicates as `name_<hash>.ds`; `assemble2.build_kit`
   (`assemble2.py:375-405`) treats pool *length* as variant depth
   (`depth = min(4, min(len(roles[r]) for r in CORE))`). The tr909 snare pool
   is `[snare, snare2, snare2_db9df0, snare_7dbbaa]` — verified **only two
   distinct sounds** (`snare_7dbbaa.ds` is byte-identical to `snare.ds`,
   `snare2_db9df0` to `snare2`). So "TR-909 mda D" exists only because
   duplicates padded the pools, and its hit selection is an arbitrary
   re-deal: variant 3 indexes every *other* role pool at `3 % len`, pulling
   whichever 4th file sorted in — with no loudness relationship to variant A.
2. **No loudness control anywhere in the pipeline.** `.ds` → 2-osc AMY
   conversion preserves whatever amplitude/spectral energy survives:
   `ds2amy.convert` maps General `Level` (attenuation only, line 114),
   section levels /128, and thins envelopes with the E-13 bug (body points
   dropped → shorter audible hits); heavily band-passed noise
   (`filter_type` BPF, resonance-scaled from `dF`) can lose most of its
   energy relative to its amp const. `synthkits.KIT_GAIN = 2.5` +
   `_AMP_CAP = 2.5` (`synthkits.py:137-148`) is a *fixed* gain: it lifts
   everything equally and caps the loud ones, but can never equalize a hit
   whose quietness is spectral or temporal.
3. **Nothing verifies the shipped kits.** The generated `synthkits_data` is
   committed and flashed; the first render of a given hit happens on
   hardware, by ear.

**Validation gate (proposed):** in `assemble2.py`, after building `kits`,
render or estimate every mapped hit and (a) reject or (b) normalize:

- *Ground truth (preferred):* render each hit's wire string 250 ms at vel 1.0
  through libamy on the host (the repo already builds amy for CI), take
  peak + RMS. Assert every kit-mapped hit has RMS within e.g. 15 dB of its
  kit's median and above an absolute floor; print offenders with their pool
  index. Add the same as a `test_deck.py` test over `synthkits_data` so a
  regenerated corpus can't regress silently.
- *Normalization:* rather than reject, scale each hit's amp consts so its
  rendered peak lands in [0.5, 1.0] before `KIT_GAIN` (store the factor in
  the generated JSON). Then `KIT_GAIN` becomes a policy trim, not a rescue.
- *Fan-out hygiene:* dedupe pools by content hash of the converted `oscs`
  (not filename) before computing variant depth — tr909 then correctly
  yields A/B only.

---

## Serial protocol review (commit a9def998: qexec.py, qget.py, SERIAL-PROTOCOL.md)

**The recorded architecture decision — typed-message router stays host-side
Python, C-side console mux deferred — is correct. Endorse it.** This is the
mirror image of the MIDI-path question, and the same impact-per-effort lens
gives the opposite answer:

1. **There are no cycles to win.** The console is a dev/deploy channel:
   events are seconds apart, latency budgets are human-scale, and none of it
   touches the audio/UI runtime. A C mux would be the first migration-map
   entry with a payoff of zero on-device cycles. Migration to C should buy
   audio headroom or MP-task milliseconds; this buys neither.
2. **The risk lands on the recovery path.** A mux at the USB-CDC/REPL layer
   sits inside the one channel used to un-brick the device. Any framing bug
   there converts "deploy tool got confused" (retry) into "cannot reach the
   REPL" (reflash over the boot button). The deck has already been burned by
   exactly this class of coupling (DTR/RTS reset races, per MEMORY /
   qexec's raison d'être).
3. **The protocol is still evolving, and the deploy channel is the
   bootstrap.** Host tools iterate per-invocation for free; a C mux iterates
   per firmware flash — *through the very channel being changed*. Wrong
   place for churn.
4. **Tag + nonce + checksum already gives end-to-end correctness** at the
   only point that can verify it (the receiver). A C mux would add framing
   but could not replace the checksum; it would be a second mechanism, not a
   substitute.

The doc's revisit trigger (device-initiated push to a persistent host
session) is the right one. Add a second: **if deploys move off mpremote to a
framed push** (the KNOWN GAP row), the receive-side state machine on the
device is the moment a small C channel handler earns its keep — but note
`flash_stream.py` already runs a lockstep framed transfer in on-device
Python acceptably, so even that is not a foregone conclusion.

Code review of the new tools (all minor):

- `qget.py:26` — nonce is milliseconds-derived. Serial exclusivity makes
  collisions near-impossible, but `os.urandom(4).hex()` is strictly better
  and the same length. One line.
- `qget.py:49-53` — lines matching neither tag nor end-marker are silently
  ignored; a *missing* end-marker is caught by the checksum (`want is None`
  → loud fail, exit 2, nothing written). Good failure design; exit codes 1
  (transport) vs 2 (integrity) are distinct and scriptable.
- `qexec.py:42` — a single `\r\x03` interrupt assumes an idle REPL. A busy
  MP task (mid-GC, mid-panel-build) can eat the first ^C; mpremote sends two
  with a pause for this reason. Cheap robustness: `\x03` twice, 100 ms apart.
- `qexec.py:51-53` — 256 B chunks with 10 ms sleeps caps code upload at
  ~25 KB/s. Fine for snippets (qget's payload code is ~300 B); if a tool
  ever pushes large scripts, switch to raw-paste mode (`\x05A\x01`) which
  has real flow control, rather than tuning the sleep.
- `qexec.py:83-87` — raw-REPL framing parse (`split(b'OK',1)`,
  `split(b'\x04')`) assumes the script's stdout never contains `\x04`. True
  for the typed-line convention (payloads are base64/text lines) — worth one
  sentence in SERIAL-PROTOCOL.md's conventions: "typed payloads must be
  line-oriented text; binary goes base64" (qget already complies).
- `SERIAL-PROTOCOL.md` audit table: honest about the remaining gap
  (mpremote `fs cp` untagged, mitigated by sha-retry). Agreed that a
  `FWD:`-framed push or Wi-Fi deploy is the eventual fix; the sha loop is
  adequate meanwhile.

One structural suggestion: `qexec.py` hardcodes `PORT = 'COM11'`; the other
flash tools presumably do too. Lift to an env var (`DECK_PORT`) with the
COM11 default so the toolset survives a port renumber without a five-file
edit.

## Maintainability notes (beyond the numbered findings)

- **The `_s`/`_w` module-dict singleton pattern** is used by 10 panels. It is
  cheap and works with the shell's builder-function contract, but state
  lifetime is "whatever the last panel left", and three modules (`instrument`,
  `mpe`, `files`) remember to `.clear()` while others don't. Minimum fix: a
  documented convention that `panel()` resets its module state dict first
  (one line each). A tiny `class Panel` base would also give E-7's teardown
  hook a natural home (`on_delete`).
- **`ui_patch`'s monkeypatching of frozen `ui.py`** is well-documented
  (coupling note at `ui_patch.py:31-36`) and the right call given the
  survive-upgrade constraint — but add a boot-time probe that verifies the
  patched attribute names exist and logs loudly when they don't, so a
  firmware bump degrades visibly instead of silently.
- **`forwarder` + `deckcfg.apply_all`** is the one place module coupling is
  inverted: config applies by importing the router, the router reads config,
  the UI calls both. Fine at this size; if a second output backend ever
  appears (e.g. network AMY), introduce an `apply(iid)` interface first
  (E-10) rather than a second `apply_all` clone.
- The FX layering model (defaults < patchfx < user, `amyparams.py:416-517`)
  is genuinely good — pure, tested, and it solved a real class of bugs. Keep
  new FX semantics there, not in forwarder.
