# Deck rework plan — navigation, instruments, home

Captured from the design discussion. Three phases; Phase 1 is independent and
built first.

---

## Phase 1 — Navigation model (build now)

**Goal:** every page has a Back; Back frees resources like the old quit did;
sound-producing pages can stay alive in the background with an explicit stop.

Investigation results (from `ui.py` / `tulip_graphics.py` / `sequencer.py`):
- **Switch (shuffle)** frees nothing — every app stays in `running_apps`, its LVGL
  tree stays allocated, its timers keep firing. Pure alt-tab.
- **Power (quit)** = `screen_quit_callback`: deletes the app's LVGL objects,
  removes it from `running_apps`, stops its frame-callback, `gc.collect()`. This
  is the one that frees memory + background CPU. Our `ui_patch` already retargets
  the quit landing to Home.
- **Re-entry** is handled by the firmware: `tulip.run(x)` with `x` already in
  `running_apps` just presents it (no re-run).
- **Drum emptiness**: `len(running_apps['drums'].drum_seq.events) == 0`.

**Behavior by page type** (all via `ui_patch`, so it survives `tulip.upgrade()`):

| Page | Back | Power |
|------|------|-------|
| Home (root) | — (no task bar) | — |
| REPL / Terminal | Home button (existing) | — (can't quit) |
| In-shell panels (Instrument/MPE/Fleet) | shell Back (pops panel; synth persists) | — |
| Config/utility standalone (Settings, Files, Voices, Editor, Wordpad, Tulip World, Keyboard) | **quit → free → Home** | — |
| Drum sequencer (sound-producing) | if `drum_seq.events` non-empty → **present Home, keep playing**; else → **quit** | **quit (stop beat + free)** |

**Implementation (`ui_patch.py`):**
- Extend the `draw_task_bar` monkeypatch: for standalone non-root, non-repl apps,
  **remove the shuffle button** and turn the quit button into a labeled
  **`‹ Back`** (its action stays `screen_quit_callback` = free + Home).
- A `KEEP_ALIVE = {'drums'}` set. For those, keep **both** a Back and a Power:
  - Back → if the app reports "busy" keep it alive (`tulip.run('home')` leaves it
    in `running_apps`); else quit. Busy check via a small per-app probe
    (`drums` → `drum_seq.events`), kept in `ui_patch` so `drums.py` is untouched.
  - Power → `screen_quit_callback`.
- Placement: standalone Back stays top-right (apps reserve that corner); panels
  keep their top-left shell Back. Different placement, same label — acceptable
  given borrowed apps can't move their content.

**Tests:** extend `test_deck.py` — Back-quits-non-keepalive, drums-back-keeps-when-
busy / quits-when-empty, keep-alive set membership, re-entry presents existing.

---

## Phase 2 — Instrument-first rack (spec, build after review)

**Paradigm shift:** from device-first "instances" to instrument-first objects.

**`deckcfg` schema:** replace `instances` with `instruments`, each:
`{ id, name, device, channel, patch, params, num_voices, mpe{enabled,members,bend,expression}, enabled }`
where `device` = `'internal'` or a board index. Add discovered `devices`
(id, name, kind, connected, voice_capacity) and `active_instrument`. **Migrate**
existing `instances` → one instrument each.

**Layering (chosen):** multiple instruments may share a channel — same
patch across devices = stack; different patches = layer. So the forwarder can no
longer lean solely on `midi.config`'s one-synth-per-channel routing; it must
distribute notes itself (generalize the existing `_StackEngine`):
- incoming ch C → all enabled instruments with channel C → each routed to its
  device (internal via its own synth / `amy.send`; board via `tulip.midi_out(msg, device=i)`).
- optional round-robin voice spreading for same-patch stacks.

**Voice budget:** track allocated voices per device (Σ instruments' `num_voices`)
vs device capacity — surfaced in the top bar + Devices screen.

**MPE:** per-instrument; an MPE instrument reserves a channel *range* (master +
members). The rack must show/allocate the range and warn on overlap.

**UI:**
- **Instruments** (was Instrument tile): a rack — list, add, tap-to-edit
  (device / channel / patch / params / MPE). Subsumes the old multi-mode.
- **Devices** (was Fleet tile): discovered Tulip + boards — connection, per-device
  voice load, rescan. Devices are discovered, not added.
- **Top bar → device meter strip**: `Tulip ● 18/32`, `A ● 10/32`, `B ○` —
  connection dot + voices used/capacity + MIDI-activity flash; tap → device
  detail. Justified by layering (voice starvation is now easy).

---

## Phase 3 — Home menu layering (build with/after Phase 2)

**Problem:** 13 flat tiles = kitchen sink. Introduce a shallow hierarchy.

**Reconsidered per button:** MPE → folds into instrument editing (drop tile).
Voices → superseded by the rack (move to an Advanced/Apps submenu). Keyboard →
contextual, not a home tile. Terminal/Reset/Calibrate/Time → System submenu.
Editor/Wordpad/Tulip World → Apps submenu.

**Proposed home (~6 tiles; 2 open sub-panels via the existing panel stack):**
1. **Instruments** (rack)
2. **Devices**
3. **Drums**
4. **Files**
5. **System** → Settings · Calibrate · Set time · Terminal · Reset · About
6. **Apps** → Editor · Wordpad · Tulip World · Keyboard · Voices (legacy)

Optionally group 1–3 under "Play" and 4–6 under "Setup" if we want one more tier,
but a flat 6 with two submenus is likely the sweet spot.

---

## Phase 4 — Screen dimming / sleep (module built + validated; wiring pending)

**Goal:** dim after an idle timeout, sleep (near-off) after a longer one; any
touch or incoming MIDI wakes it to full brightness. Both timeouts set in Settings
via dropdowns that include a **Never** option.

**`screensaver.py`** (new, done + validated on device): single idle source is
LVGL's input-inactivity timer, `lv.display_get_default().get_inactive_time()`
(touch resets it automatically); MIDI wake calls `.trigger_activity()` from a
`midi.add_callback`. A 300 ms tick reads `deckcfg` thresholds `dim_after` /
`sleep_after` (seconds; 0 = never) and steps brightness full → `DIM_LEVEL(2)` →
`SLEEP_LEVEL(1)` on phase changes only (so the Settings slider isn't fought while
awake). `start()`/`stop()`/`reload()`. Validated: `5 → 2 → 1 → wake → 5`.

**Caveat:** `tulip.brightness` clamps to 1–9, so "sleep" is brightness 1
(near-off), not a hard backlight cut. A true off = a small firmware change
(`display_brightness` allow 0 → full-off) via CI — do only if brightness 1 isn't
dark enough on the panel.

**Remaining wiring (fold into the worker's phases to avoid file collisions):**
- `deckcfg` DEFAULTS: add `'dim_after': 0`, `'sleep_after': 0`.
- `settings.py`: two dropdowns "Dim after" / "Sleep after" — Never / 15s / 30s /
  1m / 2m / 5m / 10m / 30m; call `screensaver.reload()` on change.
- `boot.py`: `screensaver.start()` after `deckcfg.apply()`.

## Sequencing & risk
- **P1 now** — self-contained, no schema change, immediate UX win.
- **P2** is the big one (schema + forwarder + two screens + meter strip); the
  panels' Back/Power and the home tiles for Instruments/Devices land here, so
  don't pre-build them in P1.
- **P3** rides on P2 (tiles change with the model).
- Everything stays in `/user` (survives `tulip.upgrade()`); `deckcfg` migration
  must be backward-compatible with existing `deck_config.json`.
