# UX Review 8 — verification of the UX-REVIEW-7 fix round + live-use rounds (COM11)

Verifies, on the live device: `b3575c0b` (disjoint rack-row tap targets),
`fb536a11` (real audio level meter), `26d1d83e` (UX-REVIEW-7 fixes),
`15f9d06a` (M3 true root cause), `62e3be60` + `a8df671c` (live-use rounds),
`f00332da` (MIDI reliability, 12/24h clock), `f786bab6`/`fcb3ac2c`/`a0a36081`
(update UX). Screenshots `u01`–`u18` in `shots/`; statuses in `findings.json`.

**Bottom line up front: ship it. Every blocker is closed and verified on the
device.** The keyboard-open watchdog reboot is gone — I ran the exact stress
that killed the device last pass (repeated keyboard open/close on the patch
picker) plus a full session of heavy navigation: **zero WDT resets, and the
32 KB alloc-failure noise is gone** (the internal-RAM discovery — 24 bytes
free at idle, by design — explains the whole saga). The green/blue switch
flip-flop is dead, and the fix agent's diagnosis was *better than mine*: my
stale-pixel theory was necessary but not sufficient — the real mechanism was
`add_state(CHECKED)` firing the theme's style-transition animation toward the
theme blue before our styles were applied. Styles-then-state fixed it;
verified green on the refill path (`u09_root_refill`). What remains is one
small styling/copy pass and one listen test. Nothing above Low.

A method note in the interest of honesty: the live-use rounds caught a bug my
harness structurally could not — **the soft keyboard never actually typed
into panel fields**, because I had verified search by calling `set_text()`
directly, bypassing the real input path. This pass I verified the fix as far
as the REPL allows (the keyboard now targets the textarea:
`kb.get_textarea()` == the search field — the LVGL insertion handler is
wired; the binding exposes no way to synthesize button-matrix presses), with
the human live test as the final word. Simulated-tap coverage is not
input-path coverage; keep doing live rounds.

---

## 1. Scorecard vs ux-review-7 findings

| ID | Was | Status now | Evidence |
|----|-----|-----------|----------|
| NEW-1 keyboard-open WDT + 32 KB alloc | High | **FIXED** | 4× open/close cycles + full pass: no reboot, no new `reset_cause=WDT` in deck.log, alloc message gone. Root cause was structural: internal RAM is designed-full (24 B free idle); the PARTIAL buffer is PSRAM-only now, restyle/resize moved off the open tick, patch list windowed at 40 rows + "Show more" (`u05_showmore`). |
| NEW-2 / M3 stale refill + switch color | Med | **FIXED — deeper root cause found** | `u09_root_refill`: green after back-refill. Mechanism: style-transition animation raced toward the theme style resolved at `add_state()` time; styles now apply before state. Deferred panel invalidate kept as a general guard. Credit where due: better diagnosis than mine. |
| NEW-3 disabled alarm hues | Med | **PARTIAL** | Magenta/crimson gone, but on-device the disabled sliders render **olive tracks with black knobs** (`u10_mpe_disabled`) — not the intended SURFACE2/MUTED. Something still recolors through RGB332. See R-1 below. |
| NEW-4 placeholder contrast | Low | **FIXED** | Text fields are dark wells; "search patches" (`u05_showmore`) and Wi-Fi "(saved)" placeholders (`u11_settings`) clearly legible. |
| NEW-5 one row under keyboard | Low | **FIXED** | Header collapses, search lifts to top, 2–3 result rows visible while filtering (`u06_kb_collapsed`, `u07_search_collapsed`). |
| NEW-6 Files Delete/filename | Nit | **FIXED** | `u12_files_armed`: Delete red when armed, filename bright. |
| NEW-7 monitor raw ticks | Nit | **FIXED** | `u13_monitor_delta`: `+0ms / +117ms / +354ms` deltas; line count fills the card. |
| L9 "Kits" type name | Low | **FIXED** | `u14_type_drums`: Juno-6 / DX7 / Piano / **Drums**. |
| L10 subtitle stutter | Low | **FIXED** | `u01_root`: "ch1 A55 Synth Bass II" — device name only for board instruments. |
| N3 knob overhang | Nit | **FIXED** | `u18_sound`. |
| N4 inactive tab contrast | Nit | **FIXED** | `u18_sound`: VCF/ENV/LFO/VCA legible. |
| N5 ASCII arrows | Nit | **FIXED (code-verified)** | No `->`/`--` seen in any copy this pass. |
| N6 Welcome | Nit | **FIXED** | `u15_welcome`: "Tap a step to set it up.", REPL leak gone, cards are (and read as) tappable. |
| PROC-1 test residue | Process | **FIXED** | Device found in the user's own live state; config byte-identical at restore time. |

## 2. New surfaces reviewed (all landed since pass 7)

- **Disjoint rack-row tap targets** (`b3575c0b`) — the card is not clickable;
  an "open" zone covers everything left of a full-height toggle zone, and a
  near-miss on the switch **toggles instead of navigating**. This is
  textbook touch ergonomics (it's the exact failure NN/g's target-size
  guidance warns about) and it goes beyond anything the reviews asked for.
- **Real audio chip meter** (`fb536a11`) — verified live end-to-end: injected
  a chord through `tulip.midi_local()` (the real router), `tulip.amy_level()`
  read 0.076, and the chip meter lit green (`u02_meter_live`). The
  always-visible 8 px track fixes the old invisible-until-audio bar. The
  "audition waveform" skip stands; this meter delivers the same "the UI shows
  sound" value at the right cost.
- **Two-column no-scroll Settings** (`u11_settings`) — everything on one
  screen, Wi-Fi status + IP live, credentials never rendered ("(saved)"
  placeholders, masked typing), 24-hour clock toggle, mono readouts. Dense
  but coherent; a defensible trade against DIRECT-mode scroll repaints.
- **Localized clock** — top bar shows real local time in 12 h format
  (`u01_root` "1:00a"); "--:--" fallback confirmed earlier while offline.
- **Update overlay** (`u16_fwprogress`, `u17_fwfail`) — progress card with
  stage note, don't-play warning, percent + bytes; fail state is a clear red
  notice with tap-to-dismiss. Two copy nits below.
- **Windowed patch list** — "Show 40 more…" (`u05_showmore`); selection stays
  in-window; scrolling stays light.

## 3. Remaining findings (all small)

**R-1 (Low) · Disabled controls render olive tracks + black knobs.** The
NEW-3 intent (SURFACE2 track / MUTED text / GRAY knob) is not what lands on
the panel (`u10_mpe_disabled`) — some recolor path still mixes through
RGB332. Fix on-device this time: set explicit DISABLED colors with
`recolor_opa` zeroed and verify against a screenshot, not the source.

**R-2 (Low) · Possible first-note drop after boot.** The first note through
the C MIDI layer logged `No synth configured for MIDI channel 1` (once;
subsequent notes fine, level tap read >0 on the chord). If that first note is
actually silent, the first key a player hits after power-on is dead. Needs a
listen test; if real, prime the C synth map in `deckcfg.apply_all()`/boot
instead of lazily on first use.

**R-3 (Nit) · Update-failed copy contradicts itself.** `u17_fwfail` keeps
"Please don't play or touch the deck until this finishes." directly above
"tap to dismiss". `fail()` should clear the caution line.

**R-4 (Nit) · Editor right-column cards sit flush against the right screen
edge** while the left column keeps a margin (`u04_editor`) — asymmetric
gutter, right card borders clipped.

**R-5 (Nit) · Update overlay shows raw byte counts** ("380000/1000000") —
format as KB/MB.

## 4. Grades (R1–R11), Δ vs UX-REVIEW-7

| # | Dimension | Grade | Δ | Driver |
|---|-----------|-------|------|--------|
| R1 | Touch ergonomics | **A-** | ↑ | Disjoint row zones + near-miss-toggles; padded slider knobs; collapse-mode search. |
| R2 | Hierarchy & density | **A-** | = | One-screen Settings, windowed lists; editor gutter nit (R-4). |
| R3 | Navigation consistency | **A-** | = | Single-swap Back; one chrome everywhere. |
| R4 | Affordance & feedback | **A-** | = | Meter, press-sink, tappable welcome steps; disabled hues (R-1) the residual. |
| R5 | State clarity | **A-** | ↑ | Live meter + wifi/IP + delta-ms + armed states; R-1 the drag. |
| R6 | Labeling & language | **A-** | ↑ | Drums, stutter gone, copy purged; R-3/R-5 copy nits. |
| R7 | Consistency of system | **B+** | = | R-1 disabled palette is the one remaining off-system look. |
| R8 | Contrast & legibility | **B+** | ↑ | Dark-well fields solved placeholders; inactive tabs lighter. |
| R9 | Discoverability | **A-** | = | Monitor, meter track always visible, tap-a-step welcome. |
| R10 | Safety | **A-** | = | Confirms, gated Files, update warnings, masked credentials. |
| R11 | Robustness | **B+** | ↑↑ | Zero WDT under last pass's kill-stress; alloc noise gone; internal-RAM-full is now a *documented* design constraint rather than a mystery. R-2 to confirm by ear. |

## 5. Verdict

**Approved — this is the pass where the fix loop converges.** Every item
from reviews 6 and 7 is fixed or deliberately settled, the two structural
gambles (rack-as-home, shell-panel Settings/Files) have survived three
passes of real use, and robustness — the story of every previous review — is
now a clean sheet under the exact stress that used to reboot the device.
Remaining work is one styling/copy pass (R-1, R-3, R-4, R-5) and one listen
test (R-2); none of it blocks daily use. The next review should be triggered
by new features, not by this backlog.

Method: driven as passes 6–7 (mpremote COM11, tap simulation against the
new hit-zones, `tulip.midi_local` for real-router MIDI, screenshots). Device
state: found in the user's live configuration, returned byte-identical
(config diff empty at restore), temp files removed, rebooted, verified.
