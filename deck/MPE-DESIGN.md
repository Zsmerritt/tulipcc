# MPE + instrument modes — design notes

**Status (built):** the pass-through architecture below is implemented and
device-validated. `channels.py` is the pure per-device budget/allocator (unit
tested); `forwarder.py` creates each MPE instrument's synth at its zone master
channel, records the zone's member channels, calls `configure_mpe`, and skips
member channels in `_route` (AMY's C layer renders them). `mpe.py` shows a live
per-device **channel map** (zone master/members, other instruments, red conflict
cells) with an overlap warning. Confirmed on device: MPE synth number == master
channel (1), a non-MPE instrument coexists at an auto id (20), member set +
`MPE_MEMBER_CHANNELS` match the zone, no warnings. **Not yet exercised:** actual
per-note bend/pressure from a hardware MPE controller (needs the physical
controller), and render-side **remap (B)** (deferred — additive, see below).



## The two instrument modes (reconciled with the instrument-first model)

- **Mode 1 — single instrument:** the instrument targets **one device**. It can be
  either a **normal** single-channel instrument or an **MPE** instrument (a zone on
  that device). This is the current rack model (`instrument.device` = one device).
- **Mode 2 — stacked "super-instrument":** the instrument targets a **stack of
  devices** and works as ONE sound with **more voices than any single device** —
  notes round-robin across the stack, and **unison/detune** spreads each note into
  N detuned voices across the hardware. This is a model extension (`device` becomes
  a stack list + `unison`/`detune` settings); **not built yet**.

**MPE is Mode-1 only.** MPE polyphony is capped at **15 member channels** by the
MIDI spec, so stacking hardware can't raise it — Mode 2's whole point (more voices)
doesn't apply to MPE. So: **Mode 2 = max-voices + unison/detune (non-MPE); MPE = a
zone on a single device.** Both must exist; they don't combine.

## Two channel spaces (the correction)

There are **two** separate 16-channel spaces, and the earlier draft wrongly
collapsed them:

1. **Input space** — the MIDI coming from the controller/CME. **Shared now** (one
   stream, all devices see the same 16 channels); **per-device with the box**
   (each device gets its own stream — BOX-DESIGN.md).
2. **Render space** — **each device is its own AMY with its own 16 channels**, and
   hosts **up to 16 instruments** (one per channel, multitimbral). Device A's
   channel 3 has *nothing* to do with device B's channel 3 — they're different
   AMYs. So **channels on different devices never conflict.** The only cross-device
   coupling is today's *shared input*, which the box removes.

## "MPE on a busy device" — how we handle it

An MPE instrument needs a **contiguous channel block** on its device (master + N
members). Because each device is an independent AMY, this is a **per-device block
allocation**, not a global one:

- **On-device confirmed (this spike):** AMY happily places a zone at an **arbitrary
  master** (tested master = 1, 5, 9) **and a non-MPE synth keeps running on the same
  AMY**. So the forwarder can allocate the zone into whatever contiguous free slot a
  device has, leaving the device's other instruments on their channels untouched.
- **The forwarder decouples input ↔ render** (it already owns routing). An MPE
  instrument listens to an **input zone** (from the controller) and renders as an
  AMY zone on its device; the two ranges need not match — the forwarder can place
  the render zone wherever there's room.
- **Enabling MPE** → try to allocate a contiguous block on the instrument's device.
  If there's room (e.g. the "one Juno on the device" case — 15 free channels), it
  just drops in. If the device is too full/fragmented for the requested member
  count, the UI **warns and offers**: shrink the zone, move/remove a blocking
  instrument, or dedicate the device. No silent, fragile *cross-device* shuffling.
- **A per-device channel map** (16 slots, instruments as blocks, the zone as a
  range) makes all of this visible.
- **Cross-device is a non-issue on render** (independent AMYs). The only shared
  resource is **today's single input stream**: until the box, a full input zone
  claims the shared input, so run one full MPE zone at a time (or layered). The box
  gives each device its own input → fully independent MPE per device.

## Spike — how AMY MPE coexists with the forwarder (on-device confirmed)

Question: the forwarder **owns** the synths and released `midi.config`; AMY's MPE
routing goes through the synth/channel model — can internal MPE work?

Findings from `tulip/shared/py/midi.py` + `amy/amy/__init__.py`, **verified on COM11**:
1. **MPE is `amy.send(synth=master, mpe="num_members,bend")`** — the master *channel*
   IS the synth number (midi.py's `configure_mpe` comment: "master channel = synth").
   AMY's C layer routes member-channel notes to that synth with per-note expression.
2. `midi_event_cb` **early-returns for `MPE_MEMBER_CHANNELS`** — Python does nothing;
   AMY's C layer handles member channels.
3. **Confirmed:** `configure_mpe` accepts an **arbitrary master** (1/5/9 → members
   ascend from master+1), and a **non-MPE synth coexists** on the same AMY. So a
   device can carry MPE + normal instruments at once.
## What we build — DECIDED (bite the bullet: correct architecture now)

Single-dev project: we build the **correct general architecture now** and defer
only what is **purely additive** (adding it later needs no rework, so it isn't
debt). The dividing line:

**Build now (the architecture — no shortcuts):**
1. **Decouple input ↔ render in the forwarder, explicitly.** Every instrument
   carries **two bindings**: an **input binding** (a single channel, or an MPE
   *input zone* = master + N members) and a **render placement** (device + a single
   render channel, or a render *block* = master + N). The forwarder maps one to the
   other. There is **no 1:1 "channel == synth == input" shortcut** — that hardcode
   is exactly the debt we're avoiding, and everything below falls out of doing it
   right once.
2. **Per-device block allocator.** Enabling MPE asks the instrument's device for a
   contiguous free block of `members+1` channels; the device is an independent AMY,
   so this is local. Records the block; other instruments on that device keep their
   channels. Synth number of the MPE instrument **== its render master channel**.
3. **Per-device channel map (UI).** 16 slots per device, instruments as blocks, the
   MPE zone as a range. Enabling MPE with no contiguous room **warns and offers**
   (shrink zone / move a blocking instrument / dedicate device) — never silent
   cross-device shuffling.
4. **Render via pass-through** — zone placed at its block's master, member channels
   **skipped in `_route`** so AMY's C layer dispatches them. This is the
   **low-latency** path (no per-note Python on the hot MPE path) — the right default
   for a performance instrument. Per-bus FX (reverb/EQ) apply as normal.
5. **One lower zone**, **default full (15 members) but adjustable**, gated behind the
   global `mpe_enabled` setting.

**Deferred — additive, NOT debt (the decoupled model above absorbs it with zero
rework):**
- **Render-side remap (B).** Re-emitting a controller zone onto a render block at a
  *non-natural* master (needed only to pack MPE *beside* other instruments whose
  input channels sit inside the zone). It costs **per-note Python on the hot path**
  (latency/jitter) to buy a **niche** (MPE is channel-hungry — it wants a dedicated
  device far more often than it wants to share one). Because bindings are already
  decoupled, remap is a later *rendering strategy*, not a re-architecture. Ship
  pass-through; add remap only if a real setup demands MPE on a populated device.
- **Per-device input streams.** The transport that gives each device its own 16
  input channels lands with the **box hardware** (UART jumpers, ~next week). The
  model already assumes it; today everything degrades to the single shared input
  (one full MPE zone at a time, or layered). No rework when the hardware arrives.

## Decisions so far
- One zone (lower) to start. ✓
- Zone default = full (15 members), adjustable. ✓
- **Explicit input-binding ↔ render-placement decoupling built now** (no 1:1
  hardcode). ✓
- **Per-device block allocator + channel map built now; pass-through rendering.** ✓
- **Remap (B) deferred as an additive rendering strategy** (decoupling means no
  rework later). ✓
- Build ready for per-device box input, degrade to shared input now. ✓
- Mode 2 (stack/unison/detune) is a separate non-MPE feature to (re)build in the
  instrument-first model. ✓
