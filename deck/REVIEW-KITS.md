# REVIEW — SLICE D (KITS): param application, kit synthesis, GM tables, host tooling

## Exec summary
I traced the full slice: the pure param-apply path (`amyparams.synth_send_calls` /
the FX layering fns) into `forwarder._apply_params`/`_apply_device_fx`; synth-kit
construction (`synthkits.hit_patch_string` / `_xform_partials` / `SynthKit.__init__`
/ `retweak`) and its RAM-slot map against the forwarder's slot allocator; the pad
editor's live retweak + swap flow; the GM parallel-array tables (`gm.py`,
`gmbig.py`, `catalog.py`); and the offline generation pipeline (`ds2amy.py`,
`assemble2.py`) plus the no-reset serial transport (`qexec/qget/qput`). The slice
is in good shape — the slot-map bounds are correct (5 melodic + 5 kit windows fit
inside AMY's 1024..1151 pool with headroom, and ROLES caps a kit at 20 hits < the
24 stride), the send-only-stored-params contract and the patch-FX baseline layering
are sound and well-tested, and the GM `feedback=2` on one-shots is *not* a
machine-gun bug (pcm.c has an explicit one-shot loop guard, lines 418-428). The one
material defect is a robustness gap: a single unknown hit key aborts an entire kit
instrument. The rest are low-severity display/consistency/defensive items. Host
tests pass (93/93). **Counts: 0 CRIT, 0 HIGH, 1 MED, 5 LOW.**

---

## Findings (most severe first)

### 1. MED — One bad/missing hit key silently kills an entire synth-kit instrument
`deck/synthkits.py:201-207` (`hit_patch_string` raises `KeyError` when `_hit()`
returns `None`) consumed by `deck/drums_kit.py:80-93` (`SynthKit.__init__` loop),
which has **no per-hit guard**.

Trace: for each note the kit loads, `SynthKit.__init__` calls
`synthkits.hit_patch_string(hit_key, ...)`. If that key is absent from its pack file
(`_hit` returns `None`), `hit_patch_string` raises `KeyError`, which propagates out
of `SynthKit.__init__` → `make_synth`. In `forwarder.start()` (line 654) this is
caught per-instrument, so the **whole drum instrument goes silent** (only a console
print); in `rebuild_one` (`_rebuild_in_place`, line 343) it returns `False` and
forces a **full router rebuild** (release+recreate every synth, the ~80-200ms
interruption `rebuild_one` exists to avoid). Because the throw happens mid-loop,
hit synths already created for earlier notes are **orphaned** (never assigned to
`_state['synths']`, never released) and leak AMY voices until the next full rebuild.

Reachability: community/hybrid/donor kits deal hit keys across many packs
(`assemble2.py` `build_kit`/community/hybrid loops), and the on-device split layout
loads one `<pack>.json` on demand. A partial `qput` deploy (index.json pushed, a
referenced pack file not yet pushed) or any index/pack drift makes a referenced key
unresolvable — exactly the "went silent every boot" failure mode this module's own
comments repeatedly guard against elsewhere.

Fix: wrap the per-hit store/create in `SynthKit.__init__` in `try/except`, and on
failure skip the note (leave it unmapped) rather than aborting the kit — or
substitute a silent placeholder. That keeps the rest of the kit audible and avoids
the orphaned-synth leak.

### 2. LOW — No forwarder fallback for a gm2 program the emu4 font doesn't cover
`deck/gmbig.py:99-102` (`patch_string` raises `KeyError` for an uncovered program)
called at `deck/forwarder.py:626-627` with no guard. The emu4 font covers only 92
of 128 GM programs (`_PROGRAMS`). The UI picker *does* restrict gm2 selection to
`gmbig.programs()` (`instrument.py:64-66`), so this is unreachable through normal
use — but a hand-edited or migrated config with an uncovered gm2 `patch` yields a
`KeyError` caught at `forwarder.py:654`, leaving the instrument **silently mute**
(console print only). Fix: in the gm2 branch, fall back to the nearest covered
program (or program 0) when `has_program` is false, so a stale config degrades to
sound instead of silence.

### 3. LOW — `hit_name()` suffix stripping is too aggressive (collapses distinct names)
`deck/synthkits.py:97-107`. It strips any trailing `_<tail>` where `len(tail) >= 4`
and every char is in `0123456789abcdef`. Decimal digits are hex digits, so legit
numeric suffixes ("kick_1200", "snare_9090") and hex-word suffixes ("x_face",
"x_beef", "x_dead") get truncated, merging distinct hits to one display name.
`padeditor.py:224-230` then has to renumber the collisions. The generator only ever
appends **6-char** dedup hashes (`make_synthkits.classify` strips `\b[0-9a-f]{6}\b`),
so tightening the display strip to exactly 6-char all-hex tails (or requiring at
least one a-f letter) removes the false positives without losing the intended
cleanup.

### 4. LOW — gm2 has no looped/one-shot EG distinction (gm.py does)
`deck/gmbig.py:94-102` returns the sustain-loop EG `A5,1,60000,0.85,220,0` for
**every** program, whereas `deck/gm.py:126-140` branches on `PRESET_LOOPED` and
gives one-shots a long 4000 ms release so they ring to their natural end. gm2 has
no `PRESET_LOOPED` table, so one-shot SFX/percussion in the emu4 font get the short
220 ms release path. Given `feedback=2` sustain-through-release plus the pcm.c
one-shot loop guard this is likely benign, but it is an unverified sound-quality
asymmetry — flag for a listen test. If it matters, add a per-program looped flag to
gmbig (or derive release from `_NZONES`/root, which are already carried).

### 5. LOW — `fx_calls()` is dead in the production apply path
`deck/amyparams.py:505-519`. The forwarder applies FX via `fx_send_strings`
(chorus/echo) + `fx_eq_string` + the reverb room string (`_apply_device_fx`,
`forwarder.py:233-254`); `fx_calls` is referenced only by `test_deck.py`. It also
still iterates `reverb` in `FX_BUSES`, which would emit an `amy.reverb()` bus call
that contradicts the "reverb is the shared master room, never per-bus" model
documented two functions above. Harmless today (nothing calls it) but a live trap —
either delete it or align it with the room model.

### 6. LOW — `assemble2._hit_features` misreads duration for short bp0 strings (offline)
`tools/drumsynth/assemble2.py:31-34`. `dur = float(bp[-4])` assumes a bp0 of the
form `t,l,t,l,t,l` (release pair last), so `[-4]` is the main decay time. For any
4-element bp0 — and the code's own default `'0,1,200,0'` — `[-4]` indexes element 0
(`'0'`), scoring duration as ~0. This only feeds the coarse content-classification
fallback (`classify_content`) for community/hybrid role assignment and never runs
on-device, so impact is limited to occasional role misclassification of hits whose
converted envelope is short. Fix: parse even-index time slots and take the max (as
`_scale_times` already does), rather than a fixed negative index.

---

## Perf / optimization notes (kit build is the hot path in this slice)
Weighed against the 5.8 ms AMY block / GC-jitter constraint. None are correctness
issues; ranked by value:

1. **Coalesce the kit-build wire traffic.** `SynthKit.__init__` emits, per kit,
   ~20 `store_patch` + ~20 `PatchSynth` creates + ~20 `midi_note_cmd` sends + the
   base patch — plus a `sleep_ms(2)` yield per note (`drums_kit.py:83`), i.e. ~40 ms
   of deliberate UI stall *on top of* the message traffic. `rebuild_one` wraps its
   rebuild in `_AmyBatch`, but `start()` builds each SynthKit before the FX pass;
   confirm the kit-build path runs inside a batch, and consider yielding every Nth
   note instead of every note (the 2 ms×20 = 40 ms stall is the larger cost now that
   the JSON parse was split out).
2. **`retweak` per drag tick** (`padeditor._apply`, non-commit branch) issues
   `store_patch` + two `amy.send` every `VALUE_CHANGED`. The audition is already
   rate-limited to ~5 Hz; the retweak itself is not. It's deliberate (keeps the next
   natural hit live) and store is RAM-only (no flash fence), so this is acceptable,
   but rate-limiting retweak to match the audition (or to the last tick of a drag)
   would cut the mid-drag message storm with no audible loss.
3. **`synth_send_calls` / the FX string builders are pure and cheap** — no action;
   they allocate small transient dicts/lists per apply, which is fine at apply
   cadence (not per-block).

## Cross-slice interface (forwarder slot allocation — shared with MIDI)
Verified sound. `SLOT_AUDITION=1024`, melodic `1025..1029` (`MAX_MELODIC_SLOTS=5`),
kits `1030 + 24n` for n=0..4 (`MAX_KIT_SLOTS=5`). Kit 4 base=1126, +20 hits = 1146 ≤
1151 (`SLOT_LIMIT-1`), so no overrun into AMY's rejection range, and the
`_next_kit_slot`/`_next_melodic_slot` guards (`forwarder.py:591`, `617`) refuse
loudly at the cap. The `str`/`int` key duality between `padeditor` (writes `str`
keys for `hits`/`hit_swaps`) and `SynthKit`/`drums_kit` (reads `sw.get(note) or
sw.get(str(note))`) is handled consistently on both sides.
