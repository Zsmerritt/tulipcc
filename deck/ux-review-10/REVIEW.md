# ux-review-10 — on-device UX crawl, round 1

- **Build under test:** deck-next @ 905e0ed1 (kbmgr lifecycle owner + per-key
  redraw merged), device TULIP4_R11, 1024x600, deployed to /user.
- **Session:** 2026-07-18 19:21–20:10 (device clock), COM11 via qexec/qput/qget.
- **Note on numbering:** the task brief said "UX7-N", but the repo's highest
  existing review dir is `deck/ux-review-9/`, so per the "highest N + 1"
  convention this is review 10 and findings are numbered **UX10-N**.
- Evidence screenshots in `shots/`. Findings are ranked; every suggestion
  carries a cost class (ZERO / CHEAP / EXPENSIVE per the review philosophy).
- The device WDT-crashed 3 times during this crawl. All three are recorded
  below with verbatim console/log capture. Config was backed up before and
  restored byte-for-byte after (sha8 fc0804c0), followed by a clean soft
  reboot; harness files /user/_drv.py and /user/_s.png were deleted.

---

## CRITICAL

### UX10-1 — WDT reboots during ordinary UI use (3 in one ~45-min session)
- **Screens:** instrument editor (Remove confirm), Apps > Keyboard, System > Settings.
- **Repro (observed):**
  1. **Crash #1 (19:44):** "Remove instrument" confirm modal open on
     layer_top → next harness exec ran `tulip.screenshot()` (a flash write)
     → Core 1 (USB-host core) panic, verbatim:
     `Guru Meditation Error: Core 1 panic'ed (Interrupt wdt timeout on CPU1)`,
     "Core 1 was running in ISR context", EXCCAUSE 6, deep repeating
     backtrace frames (0x42012a5f/0x4201a309 — recursive draw walk), full
     reboot. deck.log: `[1695] === deck boot === reset_cause=WDT`.
  2. **Crash #2 (19:59):** Apps > **Keyboard tile** tapped (orphan keyboard,
     see UX10-2). Deck idle, host port CLOSED (no serial interference
     possible) → WDT reboot within ~15 s. deck.log:
     `[1532] === deck boot === reset_cause=WDT` (no second boot line — see
     UX10-17).
  3. **Crash #3 (20:03):** System > **Settings** tapped → dual-core WDT
     during panel build, verbatim boot banner:
     `rst:0x8 (TG1WDT_SYS_RST)` … `W (38) boot.esp32s3: PRO CPU has been
     reset by WDT.` / `APP CPU has been reset by WDT.` deck.log:
     `[1549] === deck boot === reset_cause=WDT`. Intermittent: the same tap
     succeeded at 19:42 and again at 20:07.
- **deck.log tail (verbatim, session end):** three `=== deck boot ===
  reset_cause=WDT` entries between 19:21 baseline and 20:07.
- **Impact:** each crash is ~50 s of dead stage time. Common thread: a fresh
  full-width overlay (confirm modal / soft keyboard) or a heavy panel build;
  crash #1 adds a flash write while an overlay is up. This is the
  UX-REVIEW-6 H1 interrupt-WDT family, still alive.
- **Suggested fix:** treat as the round's top firmware/priority bug, not a
  polish item. Concrete threads to pull: (a) crash #2 is a clean,
  user-reachable repro (orphan keyboard, zero harness involvement) — start
  there; (b) audit what work runs on the cores while an overlay is up
  (partial-render copy? draw recursion in the backtrace); (c) crash #1
  says flash writes while an overlay renders are still lethal (matches the
  known mmap'd-PCM/flash-cache hazard — deckcfg.quiet gate may need to
  cover screenshot/other writers too).
- **Cost:** bugfix (n/a).
- **Evidence:** `shots/32-after-orphan-kb.png` (aftermath of #2: deck
  stranded on REPL screen), task logs with full Guru dump (#1) and boot
  banner (#3); deck.log lines quoted above.

### UX10-2 — Apps > "Keyboard" tile opens an ORPHAN keyboard that bypasses kbmgr (and preceded crash #2)
- **Screen:** System > Apps.
- **Repro:** tap the Keyboard tile. `home.py _APPS` wires it to raw
  `tulip.keyboard` — not `kbmgr`. Result: `ui.lv_soft_kb` exists,
  `kbmgr.is_open()` False, **no textarea bound**.
- **Impact:** (1) With no ta, LVGL inserts nothing; keys go through
  `ui.lv_soft_kb_cb → tulip.key_send()` into the REPL *under* the UI —
  invisible typing; (2) every navigation guard (`homeshell push/back →
  kbmgr.close()`) is a no-op because kbmgr has no binding, so the keyboard
  can outlive navigation, exactly the lifecycle hole kbmgr was built to
  close; (3) the deck WDT-rebooted ~15 s after this tile was tapped
  (crash #2, UX10-1) with the port closed — best available repro for the
  crash family.
- **Suggested fix:** remove the tile from the deck's Apps grid (its purpose
  on a REPL screen doesn't exist inside the shell), or route it through
  kbmgr with an explicit target. One-line change either way.
- **Cost:** ZERO.
- **Evidence:** on-device state dump (`kb True kbmgr_open False`);
  `shots/31-apps.png`; crash aftermath `shots/32-after-orphan-kb.png`.

---

## HIGH

### UX10-3 — Zombie keyboard stays painted after tab-switch close (Settings)
- **Screen:** Settings, any tab switch while the soft keyboard is up.
- **Repro:** Network tab → focus Wi-Fi password (keyboard opens) → tap
  "Display" tab. kbmgr closes the keyboard correctly (`ui.lv_soft_kb` None,
  tree has no keyboard object, layer_top empty) — but the keyboard stays
  **painted** over the bottom half of the screen. Persisted 60+ s across two
  screenshots; only a forced `lv.screen_active().invalidate()` cleared it.
- **Impact:** half the screen is a ghost keyboard; taps there land on
  invisible Display-tab controls beneath it. Reads as a hard hang to a user.
- **Suggested fix:** in `kbmgr._hide_overlay()` (and/or deckui
  `close_keyboard`), after `tulip.keyboard()` tears the overlay down,
  invalidate the vacated rect (or the screen) once. One invalidate per
  close, no per-frame cost.
- **Cost:** ZERO (one-off invalidate).
- **Evidence:** `shots/20-settings-display.png` + `shots/21-zombie-kb-persist.png`
  (zombie, 60 s apart), `shots/22-after-invalidate.png` (recovered).

### UX10-4 — Patch picker stuck in collapsed keyboard layout after the keyboard's own hide key
- **Screen:** instrument editor > Patch Browse (patch picker).
- **Repro:** open search keyboard (header collapses, list shrinks above the
  keyboard — correct) → close the keyboard with the **keyboard-glyph hide
  key on the keyboard itself** → keyboard closes, but the picker stays in
  collapsed mode: header/Favorites hidden, list still ~300 px with the
  bottom half of the screen dead. Recovery only via the panel's own kb
  toggle button or refocusing the field.
- **Root cause:** `instrument.py _layout_list()` is triggered only by
  `_kb_layout_cb` (panel kb button + search-ta FOCUSED/DEFOCUSED). The
  firmware hide key path (`ui.lv_soft_kb_cb` → delete) never notifies the
  panel. kbmgr *knows* the close happened (its DELETE hook runs) but has no
  way to tell listeners.
- **Suggested fix:** give kbmgr a tiny close-listener hook (list of
  callbacks invoked at the end of `_teardown_binding`), have the picker
  register its relayout there (and Settings could reuse it for UX10-3's
  invalidate). Pure logic.
- **Cost:** ZERO.
- **Evidence:** `shots/06-patch-selected-trumpet.png` (stuck state) vs
  `shots/07-patch-fav-star.png` (restored after panel kb button toggle).

### UX10-5 — Wi-Fi password field ends up hidden UNDER the echo strip while typing
- **Screen:** Settings > Network > Wi-Fi password (fresh kbmgr code).
- **Repro:** focus the password field → kbmgr scrolls the card so the field
  clears the *keyboard* top edge (300 px), but the echo strip occupies a
  further 34 px above that edge — the field lands behind the strip with
  only ~20 px peeking out. The eye button is also half-covered.
- **Root cause:** `kbmgr._ensure_visible()` computes overlap against
  `kb_top - margin` and ignores the echo strip height it itself adds
  (`_build_echo` eh=34).
- **Impact:** you type "blind" into a field you cannot see; with the eye
  toggled the reveal happens in the hidden field (and the echo stays
  masked — UX10-8), so the eye appears broken exactly when you need it.
- **Suggested fix:** when the echo strip is (or will be) shown, use
  `kb_top - eh - margin` as the clearance line in `_ensure_visible`.
  One constant in existing math.
- **Cost:** ZERO.
- **Evidence:** `shots/19-password-echo-reveal.png` (field clipped under
  strip; echo `•y` correct).

### UX10-6 — "Reset patch" wipes all sound-design edits with no confirm and no feedback
- **Screen:** instrument editor, left column.
- **Repro:** tap "Reset patch" → params, reverb send, per-pad hits AND pad
  swaps are cleared immediately (`rack.py _reset_patch`); layer_top stays
  empty (no confirm, no toast — verified on-device); the panel silently
  redraws.
- **Impact:** one stray tap destroys potentially hours of sound design.
  Contrast: removing an instrument and resetting the device both get
  `dk.confirm`; deleting a *file* takes two taps. This is the same
  severity of loss with zero guard, and no acknowledgment even on
  intentional use.
- **Suggested fix:** `dk.confirm("Reset patch?", "...", yes_text="Reset")`
  (component already exists), plus the existing toast pattern on completion.
- **Cost:** ZERO.
- **Evidence:** code path + on-device tap with empty layer_top; screen in
  `shots/02-instrument-editor.png`.

---

## MEDIUM

### UX10-7 — The keyboard's ✓ (checkmark) key is dead
- **Screen:** every soft-keyboard use (verified in rename + patch search).
- **Repro:** press ✓ → nothing. `ui.lv_soft_kb_cb` handles
  NEW_LINE/BACKSPACE/KEYBOARD/'1#'/plain ASCII; `SYMBOL.OK` falls through
  every branch, and nothing listens for LVGL's READY event. Keyboard stays
  up, no commit signal. (ui_patch's `_filtered_cb` passes only
  KEYBOARD/CLOSE to the original close path — OK isn't in that set either.)
- **Impact:** the most prominent "I'm done" affordance on the keyboard does
  nothing; users will assume the rename/search didn't take.
- **Suggested fix:** in kbmgr's `_kb_event_cb` (already hooked to
  VALUE_CHANGED), detect `SYMBOL.OK` and call `close()` — it already has
  the deferred, self-delete-safe teardown. Also fires the ta's DEFOCUS
  commit path indirectly (rename flush) since close detaches the binding;
  if not, send READY to the ta explicitly.
- **Cost:** ZERO.

### UX10-8 — Eye toggle doesn't refresh the echo strip until the next keystroke
- **Screen:** Settings > Network Wi-Fi password + echo strip.
- **Repro:** with `•••` in the echo strip, tap the eye (field unmasks —
  `get_password_mode()` False) → echo still shows bullets; only the next
  keystroke repaints it as cleartext. Same stale state re-masking.
- **Root cause:** `_update_echo` reads live password mode, but nothing calls
  it on the eye toggle; kbmgr has no public refresh entry point.
- **Suggested fix:** expose `kbmgr.refresh_echo()` (call `_update_echo`)
  and invoke it from settings' `_toggle_eye`/`_mask_pw`. Two lines.
  (Fixing UX10-5 makes this visible and worth doing together.)
- **Cost:** ZERO.
- **Evidence:** on-device sequence: pwmode False + echo `'••'`, then next
  keystroke → `'xyz'`.

### UX10-9 — Debug-mode tiles (Profiler/Logs) don't appear until Settings is reopened
- **Screen:** Settings > System.
- **Repro:** toggle Debug ON → no new rows appear (verified: labels absent);
  they exist only after leaving Settings and reopening the System tab.
  Nothing tells the user extra tools now exist or where.
- **Suggested fix:** either rebuild the System tab page on debug toggle
  (same rebuild the tab switch already does), or append the two rows
  live in `_debug_switch`. Alternatively add "adds Profiler + Logs below"
  to the switch subtitle.
- **Cost:** ZERO (build-time only).
- **Evidence:** `shots/23-settings-system.png` (Debug off state),
  `shots/24-profiler.png`, `shots/25-logs.png` (tiles working after reopen).

---

## LOW

### UX10-10 — Debug status-bar readout lingers after Debug is turned off
- **Screen:** top bar, everywhere.
- **Repro:** Debug off at 19:55 (`debug mode off` in deck.log) → `WDT py
  9238K int 41K` still painted in the top bar at 19:57+ (2+ min). Only the
  session's reboot cleared it.
- **Suggested fix:** `_debug_switch(False)` → `refresh_status()` should
  set the readout label to '' / hidden explicitly (it currently seems to
  only stop updating).
- **Cost:** ZERO.
- **Evidence:** label text captured on-device at 19:57 with debug False.

### UX10-11 — Sound editor forgets the active tab when switching Basic/Advanced
- **Screen:** instrument editor > Sound.
- **Repro:** VCF tab → tap "Advanced" → view rebuilds on DCO tab.
- **Suggested fix:** carry the active tab index across the view rebuild.
- **Cost:** ZERO.
- **Evidence:** `shots/10-sound-vcf.png` → `shots/11-sound-advanced.png`.

### UX10-12 — Disabled nav buttons don't look disabled (MPE "Per-note expression")
- **Screen:** MPE panel with "Enable MPE" off.
- **Repro:** the "Edit >" nav button carries LV `DISABLED` state (real touch
  is correctly ignored) but paints exactly like every enabled button —
  sliders in the same panel do gray out.
- **Suggested fix:** add flat DISABLED colors to `dk.button` like the ones
  `dk.switch` got (same UX-REVIEW-6 NEW-3 treatment).
- **Cost:** ZERO.
- **Evidence:** `shots/27-mpe-panel.png` (compare grayed sliders vs
  normal-looking Edit > button).

### UX10-13 — boot.py's Wi-Fi join dies on a stray serial ^C, killing the whole deck UI
- **Screen:** boot (dev-adjacent but real).
- **Repro (observed live):** WDT reboot → a harness ^C arrived during
  `tulip.wifi()` in `_boot` → `KeyboardInterrupt` (not an `Exception`
  subclass) escaped the `except Exception` guard → boot.py aborted →
  deck stranded on the bare REPL screen until manually rebooted. Any
  console glitch during the ~10 s Wi-Fi join window can do this.
- **Suggested fix:** wrap the wifi/sync block (or `_boot` itself) in
  `except BaseException` with a log line, or disable KeyboardInterrupt
  (`micropython.kbd_intr(-1)`) across boot.
- **Cost:** ZERO.
- **Evidence:** `shots/32-after-orphan-kb.png` — traceback visible on the
  deck's own screen (`boot.py:148 → _boot:73 → tulip.py:388 wifi →
  KeyboardInterrupt`).

---

## NIT

### UX10-14 — Home root status line duplicates the rack row above it
- "Tulip  A11 Brass Set 1 | vol 1" sits directly beneath a rack row and
  footer that already show name, sound and (in Settings) volume. Dead rows
  on a mostly-empty root. Either drop it or make it earn the space (e.g.
  add IP/clock-sync/next-free-channel info). Cost: ZERO.
  Evidence: `shots/01-home-rack.png`.

### UX10-15 — Protected files: Delete silently disabled with no reason shown
- Files > select `amyfleet.py` → Delete stays disabled (good) but the
  status line shows only the filename; nothing says "system file". A
  suffix in the status line ("amyfleet.py — system file") explains it.
  Cost: ZERO. Evidence: on-device state; `shots/29-files.png`,
  `shots/30-files-delete-confirm.png` (normal delete arming for contrast).

### UX10-16 — Profiler load bar renders as a dot at low percentages
- Core-load bars at 5-7% draw as a circle/dot (fill width < corner
  radius), reading as a bullet not a bar. Clamp min fill or reduce radius.
  Cost: ZERO. Evidence: `shots/24-profiler.png`.

### UX10-17 — deck.log boot entries: interrupted boots leave half-entries
- Crash #2's boot logged only `=== deck boot === reset_cause=WDT` and no
  `boot: reset cause` line (boot.py died mid-way, UX10-13) — worth knowing
  when reading logs: a lone boot line means boot didn't finish. Doc-only.

---

## Keyboard-manager shakeout results (fresh code — what PASSED)
- Echo strip appears for rename/search (echo=True) and always for password
  fields; tracks typing and backspace exactly; masked echo shows
  `•…•` + last-char reveal on new keystrokes only, re-masks ~1.2 s later
  (verified live); backspace/mode switches never reveal.
- Live password-mode detection works (eye state read per keystroke).
- Patch search: header collapses, field lifts above the keyboard, list
  filters live (debounced) while typing — good performance feel.
- Back (shell nav) with keyboard up: keyboard auto-closes, no crash, state
  fully reset — the structural DELETE-hook guard works.
- Settings tab-switch with keyboard up: kbmgr state closes cleanly, no
  crash (but see UX10-3 zombie paint).
- Rapid open/close toggling (4x fast): stable, consistent state.
- close() idempotence: verified (repeat closes are no-ops).
- Per-key redraw (5b68224b): **not verifiable with still screenshots** —
  key-flash is transient. No whole-matrix flashing artifacts were seen in
  any capture; needs an eyeball/video pass to truly confirm.

## Also verified working (no findings)
- Instrument editor: stepper (instant label update), voices slider, Device
  chooser, Type selector list (6 engines, destructive-change warning copy).
- Patch browser: selection highlight + current-label update, favorites
  star toggle + Favorites filter, star art.
- Add instrument: sensible defaults, lands directly in the editor, chip
  voice count updates (10/32 → 20/32); Remove + confirm modal + cancel all
  correct, root refreshes.
- Devices: connected state, "tap for FX" hint, voices bar, Rescan; row
  opens the shared FX panel.
- FX editor: per-bus tabs, "shared by N instruments" subtitle.
- Presets: clear empty state with guidance copy.
- Files: two-tap delete confirm (arms/disarms on selection change),
  action buttons properly disabled when nothing selected, folder icons,
  sizes.
- MIDI monitor: Pause/Resume relabel, Clear, waiting state (no live MIDI
  source available to exercise decode).
- Settings: persistent Volume/Brightness strip (instant readout update,
  deferred flash write), Wi-Fi saved-credential placeholders (never
  prefilled — good), 24h clock switch, screensaver dropdowns, render
  switches, terminal font picker, System rows.
- Profiler / Logs (debug): build-once + ticker update, log tail view.
- Confirm modals (Remove/Reset): copy, colors, Cancel behavior.

## Not covered (honest gaps)
- Terminal tile / REPL app switch and return (no safe harness path back;
  needs a hands-on pass).
- Touch Calibrate, Editor, Wordpad, Tulip World (full app switches).
- Screensaver dim/sleep firing (needs idle time).
- Live MIDI input through the monitor; audio-audible checks.
- Drums/synth-kit pad editor + kit picker and preset save flow (require
  changing the lone instrument's type / writing user files — too
  destructive for a review pass; next round should stage a second
  instrument for this).
- Wi-Fi Connect (credential mutation forbidden), firmware Upgrade / Safe
  update / Factory reset (forbidden).
- Real-finger touch latency/gestures (all taps were synthetic LVGL events).

## Harness notes for the next agent
- After ANY crash: wait ≥60 s before the next qexec — its ^C lands in
  boot.py's Wi-Fi join and strands the deck at the REPL (UX10-13).
- Never `tulip.screenshot()` while a layer_top modal is up (crash #1).
- After a reboot, /user/_s.png still holds the PREVIOUS screenshot — a
  qget "success" can be stale evidence; compare sha8s (this session's
  15-remove-instrument-confirm.png was exactly that and was deleted).
- Debug mode's console chatter corrupts qexec/qget stream framing —
  keep Debug OFF except when testing it.
- Restored: config byte-identical (fc0804c0) + soft reboot + clean boot to
  Home verified; /user/_drv.py and /user/_s.png removed.
