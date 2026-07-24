# ux-review-11 — round 2: verification pass + new ground

- **Build under test:** ux-round1 @ b00fe8e8 ("deck: implement UX round-1
  findings"), device TULIP4_R11, 1024x600, COM11.
- **Session:** 2026-07-18 20:51–21:20 (device clock).
- **Config:** backed up (sha8 fc0804c0, byte-identical to round 1's),
  restored byte-for-byte at the end + clean reboot; harness files
  (/user/_drv.py, /user/_s.png) deleted. Throwaway artifacts (instrument
  "Tulip 2", preset "Tulip") created and fully removed during the session.
- **WDT count this session: ZERO** — including 3 Settings opens, ~10
  keyboard open/close cycles, 4 confirm modals, and a boot ^C test.
  (Anecdotal — UX10-1 is separately owned; no screenshots were taken with
  a modal up, per the round-1 harness rule.)

---

## Fix verification (disposition per finding)

| Finding | Verdict | Evidence |
|---|---|---|
| UX10-2 Apps Keyboard tile removed | **VERIFIED** | Apps grid = Editor / Wordpad / Tulip World only; grid intact. `shots/02-apps-no-keyboard.png` |
| UX10-3 no zombie keyboard after tab-switch close | **VERIFIED** | kb up on Wi-Fi password → Display tab: kb object gone AND screen fully repainted. `shots/05-no-zombie-after-tabswitch.png` |
| UX10-4 picker relayout on kb hide key | **VERIFIED** | hide key → `collapsed False, list_h 360`, header/Favorites restored. `shots/07-picker-restored-after-hidekey.png` |
| UX10-5 password field clears echo strip | **VERIFIED** | field scrolled to y=208..255, echo strip top 266 → fully visible; eye btn (840,209,64,44) fully visible. `shots/04-password-clears-strip.png` |
| UX10-6 Reset patch confirm + toast | **VERIFIED** (real oracle) | planted `params={'test_marker':0.5}` → Cancel kept it intact → Reset cleared to `{}` + "Patch reset" toast. Modal copy: "Reset patch? / Clears this instrument's sound edits…" |
| UX10-7 ✓ key closes keyboard | **VERIFIED** | OK press → kb gone, kbmgr state clean |
| UX10-8 eye refreshes echo immediately | **VERIFIED** | eye on → echo `'xy'` same tick; eye off → `'••'` same tick (no keystroke needed) |
| UX10-9 debug tiles appear in place | **VERIFIED** | Debug ON → Profiler+Logs rows present ~1s later without leaving Settings |
| UX10-10 debug readout clears | **VERIFIED** (twice) | Debug OFF → readout label empty within 0.8s |
| UX10-11 sound editor keeps tab | **VERIFIED** | VCF → Advanced → still cutoff/resonance; → Basic → still VCF |
| UX10-12 disabled nav button reads disabled | **VERIFIED** | MPE off: "Edit >" label clearly muted (state DISABLED + visual). `shots/06-mpe-disabled-btn.png` |
| UX10-13 boot survives ^C in Wi-Fi join | **VERIFIED** | host sent ^C at T+8.14s → boot printed "wifi failed:" (BaseException catch) → "[10518] boot: reset cause hard" → boot completed. Full log: `boot_ctrlc.log`. Note: the interrupted join leaves Wi-Fi down until next boot (expected; a follow-up clean reboot restored it) |
| UX10-14 root status line | **VERIFIED** | "vol 1 \| net 192.168.4.250" — duplication gone. `shots/01-home-status-line.png` |
| UX10-15 system-file delete reason | **VERIFIED** | status line: "amyfleet.py - system file", Delete disabled |
| UX10-16 profiler bar min width | **STILL-BROKEN** | bars at 3%/7% still render as circles: clamp is `_BAR_H+2` (20px) at radius `_BAR_H//2` (9) → a 20x18 pill ≈ a dot; AND the track is `bg=dk.BG` = invisible on the BG page, so there is no bar context at all. `shots/03-profiler-bars.png`. See UX11-1 |
| UX10-1 WDT crashes | **NOT IN SCOPE** (separately owned) | zero WDTs observed this session; the crash-#2 repro (Keyboard tile) no longer exists |
| UX10-17 log half-entries | doc-only | n/a |

---

## New findings (UX11-N)

### UX11-1 (LOW) — Profiler load bar still reads as a dot (UX10-16 residual)
- **Screen:** Settings > System > Profiler (debug).
- **Repro:** core loads 3%/7% draw as green circles; no visible track.
- **Root cause:** min-fill clamp `_BAR_H+2` (20px) vs radius `_BAR_H//2`
  (9px) — a 20x18 rounded rect is visually a circle. The track behind it is
  `dk._flat(track, bg=dk.BG)` — identical to the page background, invisible.
- **Fix:** track bg `SURFACE2` (one style call, makes the bar extent
  visible and low fills read against it), and/or radius 4 + min fill
  `2.5*_BAR_H`. Cost: ZERO.
- **Evidence:** `shots/03-profiler-bars.png`.

### UX11-2 (LOW) — Top-bar voice chip stale after an instrument type change
- **Screen:** instrument editor / top bar.
- **Repro:** throwaway 2nd instrument (Juno default, 10 voices) → chip
  "Tulip 20/32" (correct) → Type changed to Drums, Voices row shows
  "1 (fixed by kit)" → chip still "Tulip 20/32" through kit browse, pads,
  swap (several minutes). Corrected only when the instrument was removed
  (back to 10/32).
- **Fix:** the type-change path (rack `_set_type`/apply) should call
  `shell.refresh_chips()` like add/remove/rename do. Cost: ZERO.
- **Evidence:** `shots/08-drums-editor.png` (Voices "1 (fixed by kit)" with
  chip 20/32 in the same frame).

### UX11-3 (LOW) — Swap-hit picker: "Use selected" enabled with nothing selected
- **Screen:** Pads > Swap.
- **Repro:** panel opens with the green "Use selected" button fully enabled
  before any hit is chosen (`has_state(DISABLED)` False). Same
  looked-tappable-but-isn't pattern the Files action bar fixed.
- **Fix:** build it disabled+dimmed; enable on first hit selection.
  Cost: ZERO.
- **Evidence:** `shots/11-swap-picker.png` (empty right pane, bright green
  button).

### UX11-4 (LOW) — Swap-hit list doesn't mark the pad's current hit
- **Screen:** Pads > Swap, pack open.
- **Repro:** Kick currently plays `acoustic/jazzkick`; the acoustic pack
  list shows `jazzkick` styled exactly like every other row — no
  current-selection highlight, unlike the patch picker's blue current row
  (and the kit picker's highlighted TR-808).
- **Fix:** apply the same current-row accent when the pack containing the
  active hit is open. Cost: ZERO.
- **Evidence:** `shots/12-swap-hits-grid.png` vs `shots/09-kit-picker.png`.

### UX11-5 (NIT) — Preset detail shows a raw patch number
- **Screen:** Presets > Open >.
- **Repro:** subtitle "Juno-6 - patch 0" for a preset whose patch is
  A11 Brass Set 1. `catalog.sound_label` exists precisely for this (the
  Home footer used it after review F-12).
- **Fix:** reuse catalog.sound_label in the detail subtitle. Cost: ZERO.
- **Evidence:** `shots/14-preset-detail.png`.

### UX11-6 (NIT) — Pad editor value legibility
- **Screen:** Pads.
- **Repro:** Tune value "0" is tiny, dim purple on the row; Decay/Level/
  Snap "100%" small dim green; the current sound name ("jazzkick") renders
  in placeholder-muted color although it is a real value (round-1's
  UX-REVIEW-7 NEW-4 fixed this exact class in search fields).
- **Fix:** value labels → WHITE/TEXT (or the sound editor's LED style for
  consistency); sound name → TEXT color. Cost: ZERO.
- **Evidence:** `shots/10-pad-editor.png`.

### UX11-7 (NIT) — Swap/kit list rows carry a triangular tab artifact
- **Screen:** Pads > Swap (packs + hits), also visible on kit rows.
- **Repro:** every row shows a small dark triangular tail hanging off its
  bottom-left corner — looks like a rendering glitch, present in every
  screenshot of these lists.
- **Fix:** eyeball the row style (looks like an lv style/radius artifact on
  these buttons only — the patch picker rows don't have it). Cost: ZERO.
- **Evidence:** `shots/11-swap-picker.png`, `shots/12-swap-hits-grid.png`,
  `shots/09-kit-picker.png`.

---

## New ground covered (round-1 gaps)
- **Drums flow (throwaway instrument):** Add → Type Drums (Kit TR-808
  default, Voices "1 (fixed by kit)", Sound row correctly absent for a
  sampled kit) → Kit picker (search field, current kit highlighted;
  `Acoustic` row = `synth:acoustic`) → Pads editor (pad grid, per-pad
  Tune/Decay/Level/Snap, Reset pad) → Swap-hit picker (pack list → hits →
  audition tap → "Use selected" applied `hit_swaps {'36':'acoustic/brush1'}`,
  auto-return to Pads, pad card marks the swap "brush1 *") → Remove
  instrument (chip back to 10/32). All functional; findings UX11-2/3/4/6/7.
- **Presets (throwaway):** Save (prefilled name) → "Saved \"Tulip\"" toast +
  row; Open > detail (Recall/Rename/Delete); Recall → "Recalled \"Tulip\""
  toast + auto-pop to list; Delete → confirm ("Delete preset? … cannot be
  undone.") → empty state back, `presets.list_presets() == []`.
  Finding UX11-5. Rename UI present but not exercised (field+button only).
- **MIDI monitor decode:** injected note-on/off, CC74, bend +2048, program,
  polytouch at the callback layer → all decoded correctly with delta-ms
  stamps, "7 msgs" counter, Clear → "0 msgs" + waiting state.
  (Transport/router path NOT exercised — no external MIDI source.)
  `shots/15-midimon-decode.png`.
- **Screensaver:** with 20 min of real input inactivity banked, setting
  `dim_after=10` + reload flipped phase full→dim within one 300ms tick;
  `trigger_activity()` (touch-equivalent) restored full instantly;
  config restored to Never. Sleep phase untested (same `_apply_phase`
  path; SLEEP_LEVEL=0 kills the backlight — risky to drive remotely).

## Still not covered
- Terminal/Editor/Wordpad/Tulip World app switches, Touch Calibrate
  (forbidden/no safe return), Wi-Fi Connect, firmware actions (forbidden).
- Real-finger touch, live external MIDI, audio-audible verification.
- Screensaver sleep phase + MIDI-wake path (code-read only).
- Preset rename commit path.

## Restoration record
- Config: byte-identical restore (sha8 fc0804c0) BEFORE the final reboots.
- UX10-13 test intentionally interrupted the post-restore boot's Wi-Fi
  join (that boot came up offline — expected); a second clean reboot
  followed and the deck is at Home with Wi-Fi up, chip "Tulip 10/32",
  instrument "Tulip ch4 A11 Brass Set 1" — the exact pre-session state.
- /user/_drv.py and /user/_s.png deleted (verified by os.remove + later
  import failure in round 1's pattern).
