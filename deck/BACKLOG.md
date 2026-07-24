# Deck backlog

Non-blocking items captured for later, newest first.

## Design direction (agreed, to build)

### Instrument type picker — move "type" out of the patch picker
Today an instrument's engine is *inferred* from its patch number range. Make it an
**explicit type/mode** chosen in its own step (Juno-6 / DX7 / Piano / **Drums** / …):
- The type is where an instrument "becomes a kit vs a Juno." Mode-switch there and
  change what the editor shows (a Juno gets Sound tabs; a Drums gets the pad list).
- The patch picker then **only loads the selected type's patches** → less overhead,
  smaller memory, and faster patch-picker load (no 257-item build).
- Add a `type` field to the instrument model; Routing (or a Type row) sets it.

### Drums as an instrument style — SHIPPED (kit-swap); per-pad is the follow-up
- DONE: drums are an instrument **style** (`type='drums'`); a drum instrument is a
  `DrumSynth` loaded with a **kit** (whole-kit swap). All **7 kits already compiled
  into the firmware** (TR-808/909, Linn 9000, MR-12, Tokyo Synthetics, 80s Power,
  Percussion = `DrumSynth(patch=384..390)`) — the AMY worker confirmed no firmware
  change was needed; `drums.bin` is 98.3% of its partition (~64 KB free, so no new
  *samples* fit, but more kit *patches* over existing samples are ~free). Verified
  on-device: DrumSynth per kit, MIDI notes route + play, kit picker in the editor.
  Home "Drums" tile retired to System > Apps.
- **FOLLOW-UP: per-pad Tune / Decay / Level / Pan** (TR-8S style). Two possible
  mechanisms, both need an on-device **ear check** (I can't hear):
  (a) per-note override on the DrumSynth (a per-note `bp0`/`freq` send was *accepted*
  but its effect is unverified); or (b) one PCM voice per pad (`wave=PCM,
  preset=<drum>`, `freq`=tune, `bp0`=decay→length) — clean for the 808 (presets
  0-18), but the other kits map GM notes to gamma9001 presets 256-391 and that
  per-kit mapping isn't exposed to the deck yet. Decide the mechanism, then build.

## Side notes (from the user)

1. **FX per instrument vs per bus.** FX (reverb/chorus/echo/eq) are per-BUS in AMY;
   we model them per-device today. Per-instrument is *possible* (own bus per synth)
   but each active reverb/echo bus costs real DSP — N per-instrument reverbs would
   swamp the ESP32-S3 with several instruments. Recommended middle ground: a few
   shared **aux/send FX buses** (like a mixer) that instruments route to with a send
   level — flexible without N reverbs. Decision pending; no change yet.

2. **Return-to-Home "crash" — logging in place.** Reported: patch picker left idle a
   while, device dropped to Home without rebooting. **Screensaver ruled out** (only
   changes brightness, never navigates — and now validated: dim→sleep→wake works).
   `decklog.py` records to `/user/deck.log` + serial (survives reboots): the
   back/quit/home paths and panel-build exceptions log a traceback, and boot logs a
   marker. NEXT time it happens: `mpremote fs cat :/user/deck.log` — a `Back tapped` /
   `screen_quit_callback` / `panel build failed` line with no preceding `deck boot`
   pins the source.

3. **Auto-pick next free MIDI channel on Add / device change.** — DONE
   (deckcfg.next_free_channel; used on add + device change; device change keeps the
   custom name).

4. **On-screen keyboard.** (a) whole-keyboard flash on each keypress — addressed by
   switching to PARTIAL (tear-free) render only WHILE the keyboard is up, back to
   fast DIRECT on close (ui_patch); needs a visual confirm. Edge: closing via the
   keyboard's own close-key leaves PARTIAL on until the next toggle (self-corrects) —
   hook lv_soft_kb_cb if it matters; a true double-buffered DIRECT mode would fix
   flash globally but is a bigger firmware change. (b) auto-appear on focus — DONE.
   (c) bigger search field — DONE.
