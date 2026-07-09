# Plan: multi-AMYboard control from Tulip Deck

Goal: let the Tulip main board drive itself **plus** any number of attached
AMYboards, auto-detecting how many are present and scaling the UI to match.
Two performance modes, full per-board control, optional unison detune.

## Constraints discovered in firmware (why the design is what it is)

- **AMYboards are USB-MIDI devices.** Notes/CC/PC/pitch-bend play the loaded
  synth; deep control is SysEx (mfr `00 03 45`), incl. `zP<python>`.
- **`tulip.midi_out()` broadcasts** to every USB-MIDI + TRS output. There is
  **no "send to device N"** in firmware (`tulip_send_midi_out` takes bytes only).
- **MIDI-in has no source id** (`tulip_midi_input_hook(data, len, is_sysex)`),
  so we can't tell which cable a message came from.
- **BIG ONE: the firmware claims a single USB-MIDI device.** `usb_host.c` keeps
  one global `Device_Handle_midi` + one `MIDIOut` endpoint, overwritten on every
  MIDI enumeration (usb_host.c:130/170/247). So with two AMYboards on a hub only
  the last-enumerated one is reachable for output. **Multi-board over USB needs
  firmware work regardless of MPE** (see "Multi-device USB" below).
- A board that needs deep parameter control (not just patch) needs a **companion
  sketch** that maps channel-scoped CCs / SysEx to AMY params -- our unit of
  "full control".

## Multi-device USB (firmware) -- the real enabler, and the MPE fix

To reach N boards (and to give each a full 16-channel space for its own MPE
zone), extend the USB-MIDI host to track and address devices individually:

- `usb_host.c`: arrays `Device_Handle_midi[]`, `MIDIOut[]`, `MIDIIn[]`; claim
  each MIDI interface into a free slot instead of overwriting; a per-device
  `tulip_send_midi_out(buf, len, device_index)`.
- `modtulip.c`: expose `tulip.midi_out(bytes, device=N)` (default = all/first).
- Deck: each instance carries a `device` index; the forwarder emits per device.
  Already plumbed -- `forwarder._emit(data, device)` uses the 2-arg form and
  falls back to broadcast on today's single-device firmware.

Result: each AMYboard = its own 16 MIDI channels = **per-board MPE**, no channel
collisions. This is "advertise multiple connections" done host-side. USB cable
numbers (CN) only help a single multi-port device, not separate boards.

Interim (no firmware): one board reachable, so MPE = Tulip's own AMY + that one
board; "one MPE instrument at a time".
- MPE needs the MPE firmware (present in your fork; not on the stock board).
- I2C is audio-in today; control-over-I2C is future firmware work. USB for now.

## Data model (`deckcfg`)

Replace the single-instrument fields with a dynamic list of **instances**:

```
{
  "mode": "multi" | "stack",
  "active_instance": 0,            # which one the UI is editing
  "instances": [
    { "name": "Tulip", "kind": "internal", "channel": 1,
      "patch": 0, "num_voices": 10, "mpe": false, ... },
    { "name": "Board A", "kind": "amyboard", "id": "...", "channel": 2, ... },
    { "name": "Board B", "kind": "amyboard", "id": "...", "channel": 3, ... }
  ],
  "detune": { "enabled": false, "spread_cents": 8, "per_instance": [...] },
  ...device settings...
}
```

Instance 0 is always the internal Tulip AMY. AMYboard instances are added/removed
by discovery. Per-instance settings are identical in shape so the same UI edits
any of them.

## Discovery & enrollment (`amyfleet.py`)

1. **Count boards** — broadcast a SysEx ping (`zIZ`); each board replies `OK`.
   Count replies in a short window ⇒ number of boards present. Re-run on demand
   and at boot; the instance list extends/contracts to match.
2. **Assign channels (enrollment)** — because we can't single out a board, use a
   one-at-a-time enrollment backed by the companion sketch: an *unassigned* board
   claims the next free channel and then **ignores further enrollment**; already
   assigned boards ignore it. So plugging boards in one at a time gives each a
   stable, unique channel. The assignment is stored on the board (persisted in
   its sketch) and mirrored in `deckcfg`.
3. **Identify** — each board reports its channel/id on ping so Tulip can map
   config to the right physical board across reboots.

## The forwarder + note tracking (`forwarder.py`)

`midi.add_callback(route)` runs alongside Tulip's internal synth.

- **Note table**: `active_notes[(src_channel, note)] -> [ (instance, out_note) ]`
  so every note-off / aftertouch / per-note bend goes to exactly the instance(s)
  that took the note-on.
- **Mode 1 (multi)**: message on an instance's channel → internal instance plays
  locally; AMYboard instance ⇒ `midi_out` the message (that board plays, others
  ignore by channel). Straight pass-through, per channel.
- **Mode 2 (stack)**: one incoming note is *allocated*:
  - **default = round-robin** across enabled instances for maximum polyphony
    (note-on picks the next instance; note-off routed back via the note table);
  - **detune enabled = unison**: the note fans out to *all* enabled instances,
    each transposed by its detune offset (AMY float notes = cents).
- Suppress internal double-sounding for channels owned by AMYboard instances.

## Full per-board control (companion sketch, `amyboard-deck-sketch.py`)

Uploaded to each board via the control API (`zT`/`zP`). Responsibilities:
- listen on its enrolled channel; ignore other channels;
- enrollment handshake (claim channel, persist, then ignore enrollment);
- map a documented **CC set** (and/or channel-tagged SysEx) to AMY params:
  patch, num_voices, detune, filter freq/reso, amp/filter envelopes, pan, fx.
- reply to ping with its id + channel.

Tulip's per-instance UI writes params by sending those CCs on the board's
channel (routed by the forwarder), giving identical control whether the instance
is the internal Tulip AMY or a board.

## UI changes (all in the existing deck apps)

- **Instance selector** — a segmented control (Tulip / Board A / Board B / …),
  built from the live instance list, at the top of Instrument & MPE. Selecting
  repoints the same screen at that instance. Auto-sizes to detected boards.
- **Mode switch** — Multi vs Stack (in Settings or a new "Fleet" app).
- **Detune section** (Stack mode) — enable, spread (cents), auto-spread vs manual
  per-instance offset, voices-per-instance, live total-voice readout.
- **Fleet status** — a small screen: boards detected, channels, "rescan",
  "enroll next board", firmware/version per board.
- Home gets a **Fleet** tile; the instance selector shows in Instrument/MPE.

## MPE across the fleet (phased — hardest part)

MPE spreads one instrument across member channels, which collides with using
channels to address boards. Plan: MPE stays on the internal instance first
(works with your MPE firmware). Multi-board MPE is a later phase — likely each
MPE board owns a *contiguous channel block* (master + its members) and the
forwarder maps per-note member channels within that block. Deferred until the
single-board pieces are solid.

## Phasing (build order; earlier phases testable without boards)

0. **Config model** → N dynamic instances + mode (backward-compatible migrate
   from the current single-instrument config). *(No hardware needed.)*
1. **Instance selector UI** in Instrument/MPE + a Fleet app (static list first).
2. **Forwarder + note table**, Mode 1 routing. *(Test with a virtual 2nd instance.)*
3. **Mode 2**: round-robin allocation + Detune section (unison). *(Testable.)*
4. **Discovery**: SysEx ping-count + Fleet status; auto-extend instance list.
5. **Companion sketch** + enrollment + full CC/SysEx param control. *(Needs boards.)*
6. **Multi-board MPE.**

## Audio (how sound leaves the fleet)

- **USB carries MIDI, not audio** — the Tulip isn't a USB-audio host, so audio
  can't be summed over USB. Options for Mode 2's "one jack":
  - **Analog summing** (passive mixer / summing cable of the jacks) — works now,
    any number of boards. Recommended for Mode 2.
  - **I2C audio-in** — Tulip can take one board/AMYchip's audio in over I2C
    (`amy.send(wave=amy.AUDIO_IN0)`) and route it out its own jack; ~one stereo
    pair, nascent on hardware. Future path for a single board.
- Mode 1 (each board its own jack) needs nothing special.

## MPE is per-device

MPE support depends on each device's firmware. Confirmed: the AMYboard on the
Tulip has MPE; the **Tulip main board does not yet** (`configure_mpe`/`mpe=`
absent). So the MPE screen detects support and warns/degrades gracefully per
instance. An AMYboard with MPE firmware can do MPE via forwarded MPE MIDI even
while the Tulip's own instance can't (until the Tulip is flashed).

## Status (implemented so far)

- **Phase 0 DONE** — instance-based config model (N instances, mode, detune),
  backward-compatible migration from the single-instrument config.
- **Phase 1 DONE** — instance selector in Instrument & MPE; Fleet app (mode
  switch, add/remove boards, per-instance channel, detune section).
- **Phase 2 DONE** — forwarder with note→instance table; Mode 1 channel
  pass-through. Wired into boot.py.
- **Phase 3 DONE** — Mode 2 round-robin allocation + unison detune (internal via
  AMY float notes, boards via static pitch-bend offset). Smoke-tested on device.
- **Phase 4 NEXT (needs hardware)** — discovery. The SysEx `OK` reply is
  intercepted by Tulip's own AMY layer, so auto-count needs a firmware hook or
  the companion sketch replying in a way Tulip surfaces; boards are added
  manually in Fleet until then.
- **Phase 5 (needs boards)** — companion sketch: channel enrollment + full
  CC/SysEx param control.
- **Phase 6** — multi-board MPE.

## Open decisions (please confirm before Phase 4+)

- OK to write & upload a **companion AMYboard sketch** (required for full control
  + auto-enroll)? Or must boards stay on their stock sketch (limits us to
  patch-via-Program-Change + note-level detune)?
- **Enrollment**: one-at-a-time auto-claim (above) acceptable, or do you prefer
  to pre-assign each board a fixed channel yourself?
- **Playing/selecting while performing**: in Multi mode, does your controller's
  MIDI channel pick the instance, or is there always one "active" instance that
  your keys play?
- **Channel defaults**: Tulip=1, boards=2,3,4,…? Any channels reserved (10=drums)?
