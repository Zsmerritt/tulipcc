# REVIEW — SLICE B: MIDI + note lifecycle (fresh deep-scan)

## Exec summary

I traced an inbound MIDI message end-to-end: arrival on the AMY task
(`tulip_midi_input_hook`), the C channel router (board forwarding + the
"does Python even need this?" gate), the SPSC ring into Python, the coalescing
drain (`ui_patch._drain`), routing/sound/note-off/release in
`forwarder._route`, and the defer/scheduler plumbing (`tsequencer.c`,
`modtulip.c`). The code is unusually careful — the SPSC writer discipline
(single-owner head/tail, release-publish, drop-newest-on-full), the
`tulip_midi_py_pending` clear-then-recheck, the defer publish order + "clear
only when the scheduler accepted it", and the C-owned-channel bypass are all
correct on inspection, and I walked each claimed race to confirm it. Host tests
pass (93/93). I found no CRIT/HIGH functional defect in the traced path; the
findings below are one genuine but low-probability memory-ordering asymmetry, a
coalescing gap for order-sensitive CCs, and a handful of narrow
edge/robustness issues. The dominant design choice — solo/MPE channels are
played directly by AMY's C layer and never enter the Python queue at all — is
what keeps this path safe under load, and it holds up.

Severity counts: CRIT 0 · HIGH 0 · MED 2 · LOW 4

---

## Findings (most severe first)

### 1. [MED] SPSC reader has no ACQUIRE to pair the writer's RELEASE
`tulip/shared/amy_connector.c:243` (writer) vs
`tulip/shared/modtulip.c:333,341,352` (reader `tulip_midi_in`).

The writer publishes the payload then the tail with
`__atomic_store_n(&midi_queue_tail, next, __ATOMIC_RELEASE)` and a comment
(amy_connector.c:240-243) explicitly stating "volatile alone doesn't order the
non-volatile payload writes against it under -O3/LTO." The reader, however,
decides the queue is non-empty with a **plain** read of `midi_queue_tail`
(`if(midi_queue_head == midi_queue_tail)`, lines 333 & 341) and then copies the
non-volatile payload `last_midi[prev_head][i]` (line 352). A volatile read is
not a compiler acquire barrier for the surrounding non-volatile loads, so the
release/acquire pair is only half-built: under -O3/LTO the compiler is free to
hoist the payload copy above the tail comparison, i.e. read a slot the writer
has not finished filling.

Failure scenario: writer fills `last_midi[t]`, reader (having advanced up to
`t`) reads `midi_queue_tail`, sees the new value, but the reordered payload load
returns a stale/partial 3 bytes → a garbled or wrong-channel MIDI message
dispatched to `_route` (wrong note, wrong velocity, or a note-on that reads as
note-off). Manifestation is *unlikely* on the S3 because `last_midi` lives in
internal SRAM that is bus-coherent across the two cores (no per-core D-cache to
go stale), so this is a latent compiler-reordering hazard rather than an
observed bug — but it is exactly the hazard the writer side paid a release to
prevent, left unmatched on the reader.

Fix: make the emptiness check an acquire load:
`int16_t tail = __atomic_load_n(&midi_queue_tail, __ATOMIC_ACQUIRE);` and
compare `midi_queue_head == tail` in both the initial check and the recheck.

### 2. [MED] Coalescing drops order-sensitive RPN/NRPN data-entry CCs
`deck/ui_patch.py:414` (`_NO_COALESCE`) and the coalescing loop at
`deck/ui_patch.py:453-459`.

`_NO_COALESCE` correctly protects bank-select (0, 32), sustain (64) and the
channel-mode range (120-127), but omits the RPN/NRPN parameter machine: CC 98/99
(NRPN LSB/MSB), 100/101 (RPN LSB/MSB), and 6/38/96/97 (data entry / increment /
decrement). These are a *sequence* — a parameter is selected by 99/98 or 101/100
then written by 6/38 — and the drain coalesces per `(status, controller)`, so if
two data-entry values (or a select + a re-select) land in the same backlog only
the latest survives and the earlier one is dropped in place.

Failure scenario: a controller (or the deck's own MPE pitch-bend-range setup)
forwarded to a board sends RPN 0 select + data-entry = 48; a later stray
data-entry in the same drain supersedes it, so the board receives the wrong bend
range, or a select whose matching data-entry got collapsed. Narrow (RPN pairs
rarely co-occur in one high-rate backlog), which caps it below the bank-select
case, but it is the same class of bug the existing list already guards.

Fix: extend the set —
`_NO_COALESCE = frozenset((0, 6, 32, 38, 64, 96, 97, 98, 99, 100, 101) + tuple(range(120, 128)))`.

### 3. [LOW] Drop-NEWEST on a full queue discards note-ons in favor of stale CC
`tulip/shared/amy_connector.c:230-245`.

When the ring is full the writer drops the *newest* message. Coalescing of the
high-rate CC/bend/pressure streams happens only on the **drain side**, after
dequeue — so if the Python drain stalls (GC, a long panel build) the 1024-deep
ring fills with *raw, un-coalesced* CC/bend, and the message most likely to be
dropped at the tail is the fresh note-on the user just played. This inverts the
desirable policy (never lose a note; the CC flood is exactly what coalescing
would have collapsed). Impact is low in practice: depth is 1024 and the common
case (a solo/MPE instrument) is C-owned and bypasses this ring entirely, so only
a stalled *layered*-channel or tap scenario reaches it.

Fix (if ever hit): keep note/note-off undroppable — on full, scan back for a
coalesceable status (0xB0 non-`_NO_COALESCE` / 0xD0 / 0xE0 / 0xA0) to overwrite
instead of dropping the incoming message when it is a note-on/off.

### 4. [LOW] Board bytes can be dropped during the route-table upload window
`tulip/shared/modtulip.c:419-427` + `tulip/shared/amy_connector.c:214` +
`deck/forwarder.py:94-95`.

`tulip_midi_routes_fn` lowers `tulip_midi_route_active=0` for the microseconds it
rewrites the table. While it is 0 the C hook skips the whole router block
(amy_connector.c:214) and forwards **no** board bytes, falling through to
queue+schedule Python. But Python's `_route` still sees the *previous* upload's
`_state['c_router'] == True` (it is only re-set at the end of
`_upload_c_routes`, forwarder.py:866, and never set False in between) and so
zeroes `boards` at line 95 — meaning neither C nor Python forwards, and a
board-directed message that arrives inside the rewrite window is lost. Only
occurs during a rebuild (config/instrument change), not during steady play, so
impact is a momentary dropped note/CC to a board on reconfigure.

Fix: forward boards from the C hook even while `route_active==0` (do the board
loop before the active gate), or double-buffer the table so it is never
observed half-written.

### 5. [LOW] Re-triggered (ch,note) without an intervening note-off orphans layered voices
`deck/forwarder.py:122`.

`_state['notes'][(ch, note)] = played` overwrites any prior entry for the same
`(channel, note)`. If a layered channel receives two note-ons for the same note
with no note-off between (legato overlap, a stuck/duplicated controller message),
the first `played` list of instrument ids is forgotten; the later note-off pops
only the second set, leaving the first set of internal voices sounding until a
rebuild. Only affects *layered* (2+ internal) channels — solo/MPE are C-owned and
handled by AMY — so it is an edge case.

Fix: accumulate rather than replace, e.g. extend/merge the existing list, or
release the previous set before overwriting.

### 6. [LOW] `activity()` double-counts layered-channel traffic
`deck/forwarder.py:829` returns `tulip.midi_activity() + _state['seen']`.

The C counter `tulip_midi_activity` is bumped for every non-sysex message
(amy_connector.c:211, before the router), and `_state['seen']` is bumped again in
`_route` for every message that reaches Python — so layered-channel messages are
counted twice. Harmless: the value is only consumed as a monotonic "did it
change since last poll?" flicker signal for the top-bar chips, and double-count
does not break "delta > 0 == activity." Noted only so a future absolute-count use
does not trust it.

---

## Ranked optimization notes (perf-relevant path)

The hot path is already well optimized (C-owned channels never touch Python; CC
storms coalesce; rebuilds batch into one MP->C call). Residual, in value order:

1. **Acquire-load the tail (finding 1)** — free; also lets the compiler keep the
   payload copy correctly ordered without pessimizing.
2. **Coalesce-aware drop (finding 3)** — only matters under drain stall, but it
   is the difference between losing a note and losing a redundant CC.
3. The drain builds a fresh Python `list` + `dict` per invocation
   (`ui_patch.py:444-445`); on a large coalesced backlog that is real GC
   pressure. A reusable module-level scratch buffer/dict (cleared, not
   reallocated) would trim per-drain allocation — worth it only if drain-time GC
   ever shows up in the audible-jitter budget.
