# Deck engineering review — round 1 (fresh eyes)

Scope: `deck/` (all), `tools/` pipeline (`drumsynth/`, `qexec.py`, `qget.py`,
`qput.py`), Python↔C boundary in `tulip/shared/` (`modtulip.c`,
`amy_connector.c`, `tsequencer.*`, `midi_router.h`). Repo-only; no device
access. `python -m pytest deck/test_deck.py -q` → 88 passed on this tree.
Deep AMY/DSP internals are the sibling reviewer's beat; boundary issues that
touch them are flagged, not chased.

## 1. Executive summary

This is an unusually well-instrumented codebase: the hot MIDI path is already
C-owned (route table + coalesced scheduler wakes in `amy_connector.c`),
flash-vs-PCM hazards are fenced, slider drags are cache-only with
commit-on-release, panel builds are chunked, and there is a real host test
suite. Most of the classic embedded-Python sins have already been engineered
out, and the comments document why.

What's left falls into three buckets:

1. **Resource-lifetime bugs the fast paths created.** The single-synth
   rebuild optimization (O-5) leaks AMY synth numbers on every in-place
   rebuild — a synth-kit swap burns ~19 of the 64-instrument budget, so 2–3
   kit auditions after boot silently break audio (F-1). The auto-allocator
   also collides with a channel-16 instrument (F-4).
2. **Boundary races in the defer/MIDI plumbing.** `tsequencer.c` silently
   drops a deferred callback when `mp_sched_schedule` fails, and downstream
   code assumes defers always fire — the config write chain wedges shut
   forever, preview notes stick, Back-navigation half-completes (F-3).
   Two smaller ordering races in the same plumbing (F-5, F-6).
3. **Small drift/robustness gaps**: touch calibration is saved but never
   restored at boot (F-2), a stale param key in the saved config can kill the
   whole router at boot (F-7), and the kit-generator toolchain has a dead
   writer (F-11).

Optimization headroom is modest because the big wins already landed; the two
that still pay are wiring the existing-but-unused `tulip.amy_send_batch` into
the rebuild path (~40–100 ms of audible rebuild gap back) and, longer-term,
moving layered-channel note dispatch into the C router (the last Python note
path). Details in §3. The Python/C split is fundamentally right; §4.

Finding count: **0 CRIT, 3 HIGH, 8 MED, 7 LOW.**
(F-1 is HIGH-verging-on-CRIT: it breaks core audio in a normal user flow,
but any topology edit / apply_all self-heals it.)

---

## 2. Findings

### F-1 HIGH — `rebuild_one` leaks AMY synth numbers; ~3 synth-kit swaps exhaust the 64-instrument budget

* Where:
  * `deck/forwarder.py:285-369` (`rebuild_one`), counter rewind only in
    `_release_synths` at `deck/forwarder.py:157-162` (i.e. only on full
    `_start_once`).
  * `tulip/shared/py/synth.py:44-46` — auto allocation `self.synth =
    PatchSynth.amy_synth_next; amy_synth_next += 1`; `release()`
    (`synth.py:137-143`) never returns the number.
  * `deck/drums_kit.py:88` — every SynthKit hit synth is
    `PatchSynth(num_voices=1, patch=slot)` → **always** auto-numbered, even
    when the kit synth itself is C-owned (`channel=` only reaches the kit
    synth, `drums_kit.py:97-99`).
* Failure scenario: `rack._set_kit` → `deckcfg.apply_instrument` →
  `forwarder.rebuild_one` (kit is not in `_sig`, so no full rebuild). Each
  synth-kit load allocates ~19 fresh hit-synth numbers starting from 16 and
  nothing rewinds the counter (`forwarder.py:159` runs only inside
  `_start_once`). forwarder's own comment (`forwarder.py:153-155`) states AMY
  caps instruments at 64: boot with one synth kit ends at ~35, first swap →
  ~54, second swap → ~73 > 64. AMY-side allocation failures are silent to
  Python (no exception → no `start()` fallback), so the symptom is pads going
  silent / wrong sounds until the user happens to trigger a full rebuild.
  Same leak, slower burn: any **layered** (non-C-owned) instrument leaks +1
  auto number per patch/voices edit via `rebuild_one`
  (`forwarder.py:325-333`).
* Fix: recycle synth numbers. Cleanest: record the previously used synth
  number(s) in `_state['built'][iid]` and reuse them —
  `PatchSynth(channel=<old number>)` already forces an exact number
  (`synth.py:42-43`); plumb an equivalent `synth_numbers=` through
  `SynthKit.__init__` for the hit synths. Alternative: a free-list in
  `PatchSynth.release()`. Re-verify by asserting `amy_synth_next` is stable
  across N consecutive `rebuild_one` calls in a host test.

### F-2 HIGH — touch calibration is saved but never restored: `boot.py` hardcodes `touch_delta(1, 1, 0.8)`

* Where: `deck/boot.py:109-113` vs `deck/calib.py:92-99` (`_apply` saves
  `deckcfg.set_value('touch_delta', [nx, ny, cs])`). Nothing in the repo
  reads `cfg['touch_delta']` (verified by grep — the only consumers are the
  writer and the hardcoded boot call).
* Failure scenario: user runs the (carefully built) 5-point calibration,
  Apply works for the session, then every reboot silently reverts to the
  fixed `(1, 1, 0.8)` — calibration appears "broken after reboot".
* Fix in `boot.py`:
  ```python
  td = (cfg or {}).get('touch_delta') or (1, 1, 0.8)
  tulip.touch_delta(int(td[0]), int(td[1]), float(td[2]))
  ```

### F-3 HIGH — `tsequencer.c` drops deferred callbacks when `mp_sched_schedule` fails; downstream code assumes defers always fire

* Where: `tulip/shared/tsequencer.c:20-25` — the hook calls
  `mp_sched_schedule(defer_callbacks[i], defer_args[i])`, **ignores the bool
  return**, and clears the slot unconditionally. `mp_sched_schedule` returns
  false when the scheduler queue is full (depth 128,
  `tulip/esp32s3/mpconfigport.h:37`); the queue is shared with the per-frame
  `mp_schedule_lv` (`modtulip.c:1256-1259`, ~60/s), the MIDI drain, and
  sequencer callbacks, and it fills exactly when the MP task stalls (GC,
  fenced flash write, heavy panel build) — i.e. precisely when deck code is
  leaning on defers.
* Failure scenarios (all confirmed in code, all silent):
  * `deck/deckcfg.py:284-286,310-321` — `_write` sets
    `_state['write_chain'] = True` and relies on the deferred retry to clear
    it. One lost defer → `write_chain` stuck True → **every subsequent
    config save is silently skipped until reboot** (the guard at
    `deckcfg.py:284-285` returns early).
  * `deck/forwarder.py:713,721` — `preview()`'s deferred note-off lost →
    stuck sounding note (internal or on a board).
  * `deck/homeshell.py:449` — Back's deferred swap lost → popped panel never
    deleted, revealed panel stays HIDDEN with stale content.
  * `deck/rack.py:355`, `deck/parameditor.py:360`, `deckui.toast:678` —
    truncated lists / immortal toasts.
* Fix (C, 3 lines): only clear the slot when the schedule succeeded —
  ```c
  if (mp_sched_schedule(defer_callbacks[i], defer_args[i])) {
      defer_callbacks[i] = NULL; defer_sysclock[i] = 0; defer_args[i] = NULL;
  }   // else: slot stays armed; retried next tick
  ```
  Optionally add a `defers_dropped` counter for the debug bar. Python-side
  belt: `deckcfg._write` should also stamp a `write_chain_since` time and
  self-heal if the chain is older than, say, 5 s.

### F-4 MED — synth-number collision on channel 16: C-owned synth 16 vs first auto-allocated synth 16

* Where: `tulip/shared/py/synth.py:28` (`amy_synth_next = 16`),
  `deck/forwarder.py:159` (rewinds to 16), and the comment at
  `forwarder.py:484-486` which only claims "auto ids never collide with
  channels **1-15**".
* Failure scenario: an instrument on MIDI channel 16 is C-owned (solo → `c_own`,
  or MPE upper zone, which the MPE UI actively promotes:
  `deck/mpe.py:141` "Zone: upper" at ch 16). Its AMY synth number is 16.
  The first layered instrument (or any SynthKit's first hit synth) also gets
  auto number 16 → both parties send `num_voices=`/`patch=` to the same AMY
  synth; last writer wins, notes cross-fire.
* Fix: start the auto allocator at 17 (`synth.py:28,36` and
  `forwarder.py:159`), and while there reserve one more id for F-8's scratch
  synth (below).

### F-5 MED — `tulip.defer` publishes the callback before its deadline/arg: cross-core early-fire race

* Where: `tulip/shared/modtulip.c:252-254` — writes `defer_callbacks[index]`
  (line 252) **before** `defer_args[index]` (253) and `defer_sysclock[index]`
  (254). The consumer `tulip_amy_sequencer_hook` (`tsequencer.c:21`) runs on
  the AMY task on the other core and tests
  `defer_callbacks[i] != NULL && get_ticks_ms() > defer_sysclock[i]`.
* Failure scenario: hook samples the slot between the two writes:
  `defer_sysclock[i]` is still 0 (cleared on last fire) so the compare passes
  → callback fires **immediately** (not after `ms`) and with the **previous
  occupant's arg**. Window is tens of ns against a 5.8 ms sampling period —
  rare, but `tulip.defer` runs thousands of times per session and the symptom
  (a slider commit or note-off firing early with a stale arg) is
  undiagnosable in the field. None of these fields are volatile, so the
  compiler may also reorder the stores.
* Fix: write `defer_sysclock` and `defer_args` first, then the callback
  pointer, with the pointer store last (make `defer_sysclock` volatile or add
  a compiler barrier before publishing the callback).

### F-6 MED — MIDI queue: pending-flag clear race strands a message; head/tail not volatile

* Where: reader `tulip_midi_in` (`tulip/shared/modtulip.c:323-336`) clears
  `tulip_midi_py_pending = 0` **after** deciding the queue is empty; writer
  (`tulip/shared/amy_connector.c:222-226`) enqueues, then skips scheduling
  when `tulip_midi_py_pending` is still 1.
* Failure scenario (interleaving): (1) reader sees `head == tail`, (2) writer
  enqueues + sees `pending==1` → no schedule, (3) reader sets `pending=0`,
  returns None, drain exits. The message sits in the queue until the *next*
  MIDI event arrives — user-visible as "pressed a key, nothing, pressed
  again and both fired" (the exact symptom class `ui_patch.py:410-418`
  fixed for the exception case).
* Fix in `tulip_midi_in`: clear pending first, then re-check:
  ```c
  if (midi_queue_head == midi_queue_tail) {
      tulip_midi_py_pending = 0;
      if (midi_queue_head == midi_queue_tail) return mp_const_none;
  }
  // pop path...
  ```
  (Any writer that saw `pending==1` enqueued before the clear, so the
  re-check finds its message.) Also declare `midi_queue_head/tail`
  volatile (`amy_connector.c:56-57`); the SPSC comment (E-11) promises the
  discipline but the compiler was never told. Related, acknowledged in the
  code ("Step on the head, hope no-one notices", `modtulip.c:327`): head is
  advanced *before* `mp_obj_new_bytes` copies the slot; a GC pause inside
  that allocation plus a ≥1024-message flood can tear the slot. Depth 1024
  makes this academic today; copying to a stack buffer before advancing head
  closes it for free while you're in the file.

### F-7 MED — one unknown stored param name kills the whole router at boot (`KeyError` escapes)

* Where: `deck/amyparams.py:351` — `ap = PARAM_BY_NAME[name]['apply']` raises
  `KeyError` for any stored param the schema no longer knows. Call chain:
  `forwarder._apply_params` calls `synth_send_calls(params)` at
  `forwarder.py:181` (the per-send try only wraps `amy.send`,
  `forwarder.py:182-187`), and `_start_once` invokes `_apply_params` at
  `forwarder.py:603` — **outside** the synth-creation try (508–577). The
  exception unwinds `_start_once` → `start()` → `boot.py:104-107` prints
  "forwarder failed" → no router, no sound, every boot.
* Trigger: config written by a newer deck (e.g. `piano_quality`, added
  recently) then code downgraded; or any hand-edited/corrupt `params` key.
  The codebase is otherwise scrupulous about config forward-compat
  (migrations in `deckcfg.load`), which makes this gap stand out.
* Fix: in `synth_send_calls`, `d = PARAM_BY_NAME.get(name)` and `continue`
  (optionally log once) when None. One-line host test: params
  `{'no_such_param': 1}` must not raise.

### F-8 MED — pad-audition scratch synth 15 collides with a C-owned instrument on channel 15

* Where: `deck/synthkits.py:238-247` — `audition(..., synth=15)` does
  `num_voices=0` then re-patches synth 15. A solo internal instrument on MIDI
  channel 15 is C-owned with synth number 15 (`forwarder.py:485-487`).
* Failure scenario: user has an instrument on ch 15; opens the pad editor /
  swap panel while the router kit isn't live (`padeditor._audition` falls
  back to `synthkits.audition`, `padeditor.py:73-84`) → tapping a hit
  **destroys and re-patches the ch-15 instrument**.
* Fix: don't hardcode a number inside the channel range. Reserve an id above
  the channels (e.g. make the auto allocator start at 18 per F-4 and use 17
  as the audition scratch), or route auditions through a
  `PatchSynth`-allocated scratch created once.

### F-9 MED — MIDI drain coalescing reorders/loses semantically ordered CCs (bank select, sustain)

* Where: `deck/ui_patch.py:440-459` — supersede-in-place (`batch[i] = m`,
  line 452-455) keeps the **newest value at the oldest position**, and CC
  coalescing is applied to all controllers.
* Failure scenarios:
  * Bank select: backlog `CC0=1, PC=5, CC0=2, PC=9` coalesces to
    `CC0=2, PC=5, PC=9` — PC 5 resolves against the wrong bank
    (midi.py mirrors bank state at `tulip/shared/py/midi.py:290-306`).
  * Sustain (CC64): a down→up→down triple in one backlog collapses to the
    final "down" — the transient release is lost, notes hang under the pedal.
  * Generally: a CC that arrived *after* a note-on is now applied *before*
    it.
* Fix: exempt CC0 (bank), CC32 (bank LSB), CC64 (sustain) and CC120-127
  (channel-mode) from coalescing — one small frozenset test in the drain
  loop; keep coalescing for the high-rate continuous controllers it was built
  for (pressure/bend/mod/CC74).

### F-10 MED — `qput.py`: large unfenced write, and the temp file lives in the `/user` root

* Where: `tools/qput.py:110` — `tmp = remote.rsplit('/', 1)[0] +
  '/.qput_tmp'` → for the usual `/user/foo.py` target the temp file is
  `/user/.qput_tmp`, i.e. littlefs commits land on the **root metadata pair**
  — the exact superblock-wear pattern deckcfg E-5 (`deck/deckcfg.py:24-28`)
  exists to avoid, repeated on every deploy. And the big payload write
  (`qput.py:121-128`) runs **outside** the flash fence — the fence is only
  raised around the rename (`qput.py:132-139`). On manual-fence firmware,
  the repo's own invariant (`deck/deckcfg.py:216-226`, memory of a live
  repro) says a flash write during mmap'd-PCM rendering is a dual-core WDT
  crash; the temp-file write is the largest single flash write in the
  workflow.
* Fix: `tmp = '/user/var/.qput_tmp'` (mkdir var first), and wrap the write
  loop in `flash_fence(1)/(0)` (or probe `flash_fence_auto` like deck code
  does and skip on auto-fence firmware).

### F-11 MED — `make_synthkits.py` still writes the dead single-file `deck/synthkits.json`; docstrings point at the wrong output

* Where: `tools/drumsynth/make_synthkits.py:17` (`OUT = ...deck/synthkits.json`)
  and `:204-205` (writes it). The device code reads only the split layout
  `synthkits_data/index.json` + per-pack files (`deck/synthkits.py:20`), which
  only `assemble2.py` produces (and which even deletes `mk.OUT` at
  `assemble2.py:511`). `assemble2.py:9`'s docstring *also* claims "Writes
  deck/synthkits.json".
* Failure scenario: a future contributor regenerates kits with
  `make_synthkits.py` (the module whose name says "make synthkits"), sees a
  fresh JSON, deploys — and nothing changes on device; worse, a stray
  `synthkits.json` re-appears in `deck/`.
* Fix: strip `main()`/OUT from `make_synthkits.py` (keep it as the harvest +
  ROLES + TR808 library `assemble2` imports), or make it emit the split
  layout; correct both docstrings.

### F-12 LOW — Home footer status line shows the Juno patch name for GM instruments

* Where: `deck/home.py:254-266` — `sound = patches[instr.get('patch', 0)]`
  for every non-drums type; a `gm`/`gm2` instrument's `patch` is a GM program
  number, so the footer prints e.g. "A11 Brass Set 1" for GM program 4. This
  is exactly the drift class `catalog.py` (E-8) was created to end.
* Fix: `sound = catalog.sound_label(instr)` (drops the local drums branch
  too).

### F-13 LOW — "Reset patch" doesn't clear `hit_swaps`

* Where: `deck/rack.py:581-588` clears `params`, `reverb_send`, `hits` but
  not `hit_swaps`, although the button's stated contract is "clear this
  instrument's sound-design overrides … per-pad tweaks", and swaps are edited
  from the same pad editor. Fix: add
  `deckcfg.set_instrument(rid, 'hit_swaps', {}, flush=False)`.

### F-14 LOW — `shellmodel.py` still duplicates the patch-boundary facts catalog owns

* Where: `deck/shellmodel.py:8-57` — `_JUNO_END/_DX_END`, `patch_short`,
  `patch_category`, `patch_name`. Only `patch_name`'s fallback uses
  `patch_short` internally; no production caller remains (grep: tests only).
  This is the E-8 drift pattern half-cleaned. Fix: delete the three helpers
  (or re-route through `catalog`) and update tests.

### F-15 LOW — `tulip.bg_bitmap` get path allocates a user-sized VLA on the MP task stack

* Where: `tulip/shared/modtulip.c:1041` — `uint8_t bitmap[w*h*BYTES_PER_PIXEL]`
  with w/h straight from Python; `bg_bitmap(0, 0, 1024, 600)` is a ~600 KB
  stack allocation → overflow. Not deck-path, but it's a one-line
  `malloc_caps` fix while the file is open.

### F-16 LOW — route-table upload is not atomic vs the input hook

* Where: `tulip/shared/modtulip.c:392-406` writes 16 entries while
  `amy_connector.c:188-198` reads them from the AMY task. A message arriving
  mid-upload can be routed with a half-old table (one message misforwarded /
  double-notified during a rebuild). Benign in practice; if it ever matters,
  bump a generation counter or clear `tulip_midi_route_active` around the
  loop.

### F-17 LOW — Files panel does 2–3 littlefs `stat`s per entry per refresh

* Where: `deck/files.py:161-191` — `_is_dir` (stat) per entry, then another
  stat for size per file, on top of `listdir`. littlefs metadata walks are
  the slow operation the codebase elsewhere batches (decklog O-6). With a
  large `/user` the panel-open stall is user-visible. Fix: use
  `os.ilistdir()` (one pass gives type + size) — small, contained.

### F-18 LOW — Debug-mode clock cadence only applies after re-subscribe

* Where: `deck/homeshell.py:626` — `_clock_ms()` is evaluated once when
  `_start_clock` subscribes; toggling Debug in Settings doesn't re-arm, so
  the "live" RAM readout updates every 30 s until the screen is next
  re-activated. Fix: have `settings._debug_switch` call
  `home._shell._start_clock()` after `refresh_status()`, or re-register on
  each `_tick`.

---

## 3. Optimization list (ranked by impact ÷ effort)

Budget recap: AMY renders a 256-sample block every **5.8 ms**; the MP task
shares core time with LVGL; a GC pause is audible timing jitter. The prior
rounds already took the big wins (C router O-2, single-synth rebuild O-5,
log batching O-6, shared ticker O-7, chunked fills O-8), so the residual list
is short and honest.

1. **Wire `tulip.amy_send_batch` into the rebuild path.** The C entry point
   exists and is loaded (`modtulip.c:534-555`) but has **zero Python
   callers** (grep). Today every AMY message pays
   `amy.message()` (kwargs walk + priority sort + string concat,
   `amy/amy/__init__.py:205-228`) plus one MP→C call
   (`_boot.py:113` routes `override_send` to `tulip.amy_send`). A router
   rebuild emits ~50–150 messages (kit = ~19 × (store_patch + num_voices +
   patch) + FX re-baseline); at an estimated 0.3–0.5 ms per message that is
   the bulk of the observed 80–200 ms rebuild gap. Collect wire strings
   (`amy.message(**kw)` is already separable from `send_raw`) and flush one
   newline-joined batch per rebuild phase. Est. **40–100 ms per rebuild
   returned** (audible: shorter interruption of other sounding instruments);
   effort S–M, Python-only. Keep `SynthKit`'s deliberate `_yield(2)`
   (`drums_kit.py:83`) between hit *creations*, but the stores can batch.
2. **Move layered-channel note dispatch into the C router** (the follow-on
   the O-2 comments already sketch). Layered channels (2+ internals on one
   channel) are the only remaining Python note path
   (`forwarder._route`, `forwarder.py:79-142`); per the code's own
   measurements that costs 150–400 µs + ~3 heap allocs per message
   (`amy_connector.c:60-66`) and, worse, exposes note timing to MP-task
   stalls. Extending `tulip_midi_route_t` with up to ~4 synth ids per channel
   and having the hook synthesize the `note_on/off` into AMY directly makes
   layered rigs as stall-immune as solo ones. Est. removes **5–12 % of the MP
   core under a controller stream** and all note-jitter for layered users;
   effort M (C, plus note-table ownership moves to C or is dropped —
   `live_voices()` fallback would need the C counter). Coordinate with the
   sibling C review.
3. **Deploy deck modules as `.mpy` (or freeze the stable ones).** `gmbig.py`
   (24 KB), `amyparams.py` (22 KB), `deckui.py` (26 KB), `forwarder.py`
   (34 KB) are parsed from source on-device at import. Cross-compiling to
   `.mpy` at deploy time cuts boot/first-open import latency (rough est.
   200–500 ms across the set) and heap churn at the moment boot is also
   starting the router. Effort S (deploy.ps1 + mpy-cross), zero code change.
4. **`files.py` single-pass `ilistdir`** (see F-17): S effort, removes a
   visible panel-open stall on full filesystems.
5. **Micro (only if touching anyway):** `ticker._fire` allocates
   `list(subs)` every 100 ms tick (`deck/ticker.py:39`) — cache the snapshot
   and rebuild it only on subscribe/cancel; `forwarder._route` could bind
   `_state` fields to locals at the top. Each worth µs; none worth a commit
   on its own.

**Boundary flag for the sibling C review:** `tulip_midi_input_hook`
(`amy_connector.c:188-199`) calls `tulip_send_midi_out_device` →
`send_usb_midi_out` inline in the hook. If that hook runs on the render
task, a slow USB host transaction spends render budget; consider a small
outbound ring drained off-task. Not verified from this side of the boundary.

## 4. Python→C migration assessment

The stated policy (frontend Python, backend C) is being executed well; the
map below is what round 1 supports changing.

**Already in the right place (C), keep:** route table + board forwarding +
activity counter (`midi_router.h` / `amy_connector.c`), coalesced scheduler
wake, flash fence + auto-fence, `amy_send_batch` (needs callers, §3.1),
`piano_partials` cap, defer/sequencer timing source.

**Move to C (ranked):**
1. Layered-note dispatch (§3.2) — the only remaining latency-sensitive
   Python.
2. Nothing else clears the bar. Everything else on the MP task is either
   event-driven UI or ≤10 Hz housekeeping.

**Keep in Python (explicitly — do NOT move):**
* `forwarder` topology/rebuild logic, `deckcfg`, `amyparams`, `catalog`,
  `channels`, `shellmodel`, `curated` — cold-path, migration-heavy,
  covered by the 88 host tests; C would freeze iteration speed for zero
  audible gain.
* `synthkits` string transforms (`hit_patch_string`, `_xform_partials`) —
  ~1–3 ms per pad-editor drag tick, rate-limited already
  (`padeditor.py:124-131`); fine.
* All LVGL panel code, `ticker`, `screensaver` (its per-message hot path is
  already one dict store, `screensaver.py:66-75`), `decklog` (batched),
  `midimon` (store-only callback, render at 6.7 Hz).
* Host tools (`qexec`/`qget`/`qput`, drumsynth pipeline) — host-side by
  nature; the protocol hardening (nonce tags, checksums, raw-paste flow
  control) is genuinely good.

## 5. Re-verification notes for round 2

* F-1: assert `PatchSynth.amy_synth_next` unchanged across two consecutive
  `rebuild_one` calls (host-testable with the test suite's tulip/amy stubs).
* F-3: grep tsequencer.c for the `mp_sched_schedule` return being consumed.
* F-2/F-7/F-12/F-13: single-file greps at the cited lines.
* F-5/F-6: check store order in `tulip_defer` and clear-then-recheck in
  `tulip_midi_in`, plus `volatile` on head/tail.
* Tests: `python -m pytest deck/test_deck.py -q` (88 passing baseline).

---

## 6. Previously-raised items — verdict

### P-1 — `gmbig.py`/`gm.py` table representation (heap + GC-scan cost; proposed bytes-blob pack)

**Verdict: DON'T build the bytes-blob + `struct.unpack_from` machinery. DO a
two-step restructure (evict dead fonts, parallel-array the survivor) that
captures ~95 % of the same win with none of the tooling risk.**

Measured against the current tree (counts from importing the actual modules):

* `gmbig.py` materializes **824 two/three-tuples, ~571 dict entries across 11
  dicts, and 253 WAVES strings**. On a 32-bit MP heap (16-byte GC blocks:
  3-tuple ~32 B, 2-tuple ~16 B, short string ~32 B, dict tables ~8 B/slot
  with growth slack) that is **~45-60 KB and ~1,100-1,200 heap objects** —
  the earlier claim's 60-120 KB is the right order of magnitude but at/above
  the high end. It is resident only after a `gm2` instrument first exists
  (imports are lazy), but `sys.modules` keeps it forever after.
* `gm.py` is **not** part of the problem and should be left alone: its four
  tables are already parallel lists of small ints (`gm.py:13-64`), and
  MicroPython small ints are pointer-tagged — a 128-int list is **one** heap
  object + ~0.5 KB storage. Only `NAMES` (128 strings, ~4-5 KB) allocates
  per-entry, and the UI needs those strings regardless (`gm.name` serves both
  `gm` and `gm2` pickers; `catalog.py:35-44`, `gmbig.py:886-888`).
* **GC-scan claim: real but second-order.** MP GC sweep cost scales with heap
  *size* (whole-ATB scan), not liveness — extra live data doesn't touch it.
  Only the mark phase pays: ~1,100 extra objects at PSRAM latencies is on
  the order of **~1 ms added per full collection**, against Tulip full-GC
  pauses that are already tens of ms. The tables add a few percent to a
  pause; they don't create one — and the C-owned note path (O-2) is what
  actually insulates note timing from those pauses. "Permanently joins the
  scan set" is accurate; "audible jitter driver" overstates it at this scale.
* **`.mpy` deployment (my §3.3) does not address this — confirmed.** It
  removes parse/compile time and the transient parse-tree peak, but the
  constant tuples/dicts are still materialized on the heap at import. (Only
  *freezing* into the firmware image would put them in ROM and out of the
  mark set — semantically defensible since `PRESET_BASE` must match
  `src/pcm_gm_big.h` anyway, but it conflicts with the deck's
  /user-survives-`tulip.upgrade()` deploy model, so not recommended now.)
* **The dominant waste is dead data, not representation.** `gm2` serves only
  `FONT = 'emu4'` (`gmbig.py:874`); the accessors (`programs`,
  `has_program`, `patch_string`, `gmbig.py:877-895`) touch nothing else, and
  `patch_string` uses only the `preset` field of the 3-tuple. Measured:
  **732 of 824 tuples (89 %) — the merged/emu8/bank4 fonts, all DRUMS, all
  WAVES — are runtime-dead**, kept only "REPL/patch-string accessible until
  their maps pass a listen test" (`gmbig.py:870-872`).

Recommended shape, in order:

1. **Evict the unverified fonts from the runtime module** — move
   merged/emu8/bank4 + DRUMS + WAVES to `tools/` data (or a `gmbig_extra.py`
   imported only from the REPL). Zero format risk, no regeneration tooling,
   removes ~89 % of the objects (~40-50 KB) in one mechanical edit.
2. **Store the surviving emu4 map in `gm.py`'s existing parallel-array
   style** — three 128-slot tables (`PRESET` as an int list or two-byte pack
   with an absent sentinel, `ROOT`/`NZONES` as `bytes`). ~3 heap objects,
   ~1.5 KB, human-diffable, a pattern the codebase already trusts, and a
   one-line round-trip assert in `test_deck.py` (unpacked tables == the
   tools-side source dict) covers the regeneration risk.

Why not the bytes-blob: it optimizes the wrong axis. Steps 1+2 already reach
~50 KB → ~2 KB and ~1,200 → ~10 objects; a packed blob with
`struct.unpack_from` accessors saves nothing further worth having, while
adding a binary format + regeneration step to a mapping whose own docstring
says "order-reconstructed — trust but verify" (`gmbig.py:1-11`) and which is
still churning as fonts pass listen tests. Silent field-offset mistakes in
exactly this kind of hand-regenerated blob are how presets shift by one.
Revisit only if all four fonts must ever ship at runtime (~4x today's live
data) — by which point the maps should be listen-verified and stable enough
to freeze a format around.
