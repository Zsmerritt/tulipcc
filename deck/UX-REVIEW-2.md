# UX Review 2 — Tulip Deck (re-audit on COM11)

A second, deliberately harsh pass over the Tulip Deck touch UI, graded against
the rubric in `UX-RESEARCH.md` (R1–R11). Conducted by driving the **live device**
(1024×600), verifying each claimed fix on-screen, and hunting for what remains and
what's new. Screenshots (`sNN_*.png`) live in the session scratchpad.

Severity: **Critical** (breaks a task / crash) · **High** (frequent friction,
wrong mental model, reads-as-broken, or data/session loss) · **Med** (noticeable,
workaround exists) · **Low** (polish) · **Nit** (cosmetic).

**Bottom line up front:** the critical reboot is genuinely fixed and most of the
High-severity findings from the first pass landed well — value cards, styled tabs,
real switches, the confirm modal, the ACTIVE pill, uniform top-left Back, and two
strong new features (curated engine views + the MPE channel-map). But the Back
relocation introduced a **new regression** (Back overlaps the title on every
standalone app), and the confirm work **skipped the single most destructive
action** (System → Reset still reboots on one tap). So this is not yet a clean
pass.

---

## 1. Verification of the 8 prior top issues + 2 new features

| # | Prior issue | Status | Evidence |
|---|-------------|--------|----------|
| 1 | **Critical: WDT reboot on fast nav** | **VERIFIED-FIXED** | `homeshell.back()` now defers the panel refill to a later tick with a generation guard (`homeshell.py:228–277`). 6× rapid edit→Sound→back→Sound→back→FX→back in one uninterrupted call ran with no reboot (`STRESS_DONE_OK`); device stayed responsive (`s17`). |
| 2 | **High: blind thin sliders** | **PARTIAL** | Sound/FX now render "value cards": name + live teal readout + fat full-width slider (`s05`,`s06`,`s07`). But `dk.slider` used **directly** in Settings (Volume, Brightness, Menu-button-size) still has **no numeric readout** — still blind (`s08`). |
| 3 | **High: unstyled maroon tabview/dropdown** | **PARTIAL** | `lv.tabview` bar + tab buttons and the **ParamEditor** dropdowns are restyled to the deck palette (`s05`,`s07`). But the **Settings screensaver** Dim/Sleep dropdowns still render default **olive/maroon** (`s09`) — `settings.py:150` never calls `_style_dropdown`. Drums lane dropdowns also still olive (`s14`). |
| 4 | **High: Back flips corners** | **VERIFIED-FIXED** (but see H1) | Back is now top-left in shell **and** standalone (`s08` Settings, `s13` Files, `s14` Drums, `s15` Voices, `s16` Calibrate). Uniform blue pill. The relocation, however, introduced a new title-overlap bug (H1 below). |
| 5 | **High: "Basic" is empty** | **VERIFIED-FIXED** | Re-tiered. Basic tabs now carry 2–4 controls each (DCO=2, VCF=3, ENV=4, LFO=3, VCA=2). No more lone-slider screen (`s05`). Slightly sparse on 2-control tabs, but acceptable. |
| 6 | **Med: destructive, no confirm** | **PARTIAL** | Remove-instrument now fires a proper modal confirm (`s12`, Cancel + red Remove). Files delete arms to a red "Confirm?" on first tap (`files.py:112`). **But System → Reset still calls `machine.reset()` on one tap, no confirm** (`home.py:23`) — see H2. |
| 7 | **Med: ambiguous On/Off toggles** | **VERIFIED-FIXED** | Real `lv.switch` widgets (thumb+track, green when on) for V-sync, MPE gate, Smooth-UI, Enable-MPE, Enable-expression (`s09`, MPE panel). |
| 8 | **Med: near-invisible "active" text** | **VERIFIED-FIXED** | Filled green **ACTIVE** pill on the active rack row (`s02`), high contrast. |
| N1 | **New: curated editor views** | **VERIFIED** | Juno-6 view shows DCO/VCF/ENV/LFO/VCA with engine-native labels ("cutoff", "sub tune") and a "Juno-6 view" badge (`s05`,`s06`). DX7/Piano views are data-defined identically (`curated.py`). Genuinely good. |
| N2 | **New: MPE channel-map** | **VERIFIED** | 16-slot strip: master=blue, members=teal, "Zone fits" line + green status (`s10`); forcing a second instrument onto ch5 turns that cell **red** with an orange "Zone overlaps ch 5 — shrink it or move those" warning (`s11`). Excellent. |

**Net:** 4 fully fixed, 3 partial, 1 fixed-with-regression; both new features solid.

---

## 2. Remaining & new issues, ranked

### Critical
None. The watchdog reboot is fixed and did not recur under stress.

### High

**H1 · NEW REGRESSION — standalone Back button overlaps the screen title (R3/R6).**
`ui_patch` pins the standalone Back to `TOP_LEFT (0,0)` (`ui_patch.py:119–126`),
but every standalone app's `dk.frame` draws its title at `x=24, y=30`
(`deckui.py:85`). The button sits on top of the title. Result: Calibrate reads
"**libration**" (`s16`), and the "Settings" (`s08`) and "Files" (`s13`) titles are
entirely hidden behind the Back pill. Only the y=74 subtitle survives. This is the
exact "wayfinding destroyed / reads-as-broken" failure the first audit cared about,
re-created by the fix for issue #4.
*Fix:* when a standalone Back is present, offset the frame title to start right of
it (x≈170), mirroring what `homeshell._sync_chrome` already does (title → x=110
when Back is visible). One change in `dk.frame` (or have `ui_patch` shift the title).

**H2 · REMAINING — System → Reset reboots on one tap, no confirm (R10).**
`_reset()` calls `machine.reset()` directly (`home.py:23`); the System submenu
"Reset" tile is one tap from a full reboot that drops the live session. The confirm
infrastructure now exists (`dk.confirm`) and was wired to Remove-instrument — but
not to the single highest-stakes action on the device. On a performance instrument
an unprompted reboot is the worst outcome.
*Fix:* wrap `_reset` in `dk.confirm("Reset device?", "This reboots now and drops
the session.", ..., yes_text="Reset")`.

### Medium

**M1 · Settings screensaver dropdowns still render default olive/maroon (R4/R7).**
`s09`. `settings.py:150–154` builds raw `lv.dropdown`s without the palette styling
that `parameditor._style_dropdown` applies elsewhere, so Dim/Sleep look broken
against the blue system — the same complaint as issue #3, just in a spot the
restyle didn't reach. *Fix:* call `_style_dropdown` on them (or expose a
`dk.dropdown` helper and use it everywhere).

**M2 · Settings Volume/Brightness/Menu-size sliders are blind (R5).**
`s08`. These use `dk.slider` directly with no value label, so you set volume 0–11
and brightness 1–9 with zero readout — the very problem the value cards solved in
Sound/FX. *Fix:* show the current number (a small right-aligned label updated in
the callback, like MPE's "15 channels").

**M3 · Design-system whiplash persists (R7).**
Drums (`s14`) is still black + primary red/orange/yellow with olive lane dropdowns
and ~40px arc knobs; Voices (`s15`) is black + magenta + teal with tiny list
targets. Only the top-left blue Back was harmonized; everything inside remains a
foreign product. *Fix:* reskin at least Drums (highest-traffic) to the deck
palette; replace the small drum knobs with sliders or enlarge the hit areas.

**M4 · Voices is an unmarked parallel instrument editor (R7/R9).**
`s15`. It duplicates the rack's job in an off-theme, dense UI and carries **no
on-screen "legacy/advanced" marker**, sitting one tap deep in Apps. Users can
mistake it for the primary editor. *Fix:* badge it "Advanced/legacy" or hide it
behind an advanced flag.

**M5 · Patch name is still doubled and leaks the engine token (R6).**
`s03`: the row shows "Patch **A11 Brass Set 1**" and, right beside it, a button
"**Juno0 >**". The rack row subtitle likewise reads "Tulip ch1 **Juno0**" (`s02`).
"Juno0" is an internal index, not a name. *Fix:* show the real patch name in both
places; make the nav button read the category ("Juno-6 >") rather than "Juno0".

**M6 · Patch picker: 128 patches in a flat scroll, no search/favorites; title
duplicated (R2).** `s04`. Same as the first audit — the current name appears both
as the page title and as the highlighted row. *Fix:* add scroll-to-letter/search +
a recents/favorites band; drop the duplicate title.

### Low

**L1 · FX/Sound labels terse and unitless (R6).** `s07`: "live", "damp" (should be
"liveness", "damping"); reverb/echo values show no dB/ms. Sound labels are terse
lowercase. *Fix:* spell out and add units.

**L2 · MPE channel-map has no color legend (R5).** `s10`/`s11`: blue=master,
teal=member, gray=busy, red=conflict is never explained; a first-timer must infer
it. *Fix:* add a one-line legend under the strip.

**L3 · Home tiles reuse colors, no icons (R6/R7).** `s01`/`s17`: Instruments and
Drums are the **same** accent blue; in Apps, Editor and Wordpad share the same
green (`home.py:101`). Color implies a grouping that doesn't exist. *Fix:* unique
semantic color + a stable icon per tile, or drop to a single accent.

**L4 · Home wastes the panel (R2).** `s01`: 6 tiles clustered top-left, ~55% of a
1024×600 panel empty. *Fix:* center/enlarge the grid or add a second content
column (recent patches, a transport/level strip).

**L5 · Files actions look enabled with nothing selected (R5).** `s13`: Run/Edit/
Delete are full-color and Delete rests as neutral SURFACE2 while "nothing selected"
— they no-op, but nothing signals that. *Fix:* dim/disable until a selection
exists.

**L6 · Per-note expression cards still look tappable but aren't (R4).**
`mpe.py:267–274` renders "Pressure" and "Slide (CC74)" as full `dk.row` cards
identical to interactive rows, but they're read-only descriptions. *Fix:* strip the
card chrome from static descriptions, or make them configurable.

### Nit

**N1 · Value-card slider knob is oversized and grazes the card edge.** `s05`/`s07`:
the ~52px white knob at a min-value position (e.g. "DCO pulse width" = 0.50 = min,
reverb "level" = 0.00) pokes against/over the card's left rounded corner. Big touch
target is good (R1); the overflow is cosmetic. *Fix:* inset the slider a few px or
shrink the visible knob while keeping the padded hit area.

**N2 · ACTIVE pill text hugs the pill edges** (`s02`) — minor padding tweak.

**N3 · Wi-Fi password field grazes the card bottom; placeholder text low-contrast**
(`s08`, R8) — carried over from the first audit.

---

## 3. Updated per-dimension grades (R1–R11)

| # | Dimension | Grade | Rationale |
|---|-----------|-------|-----------|
| R1 | Touch ergonomics | **B** | Fat sliders (22–26px track, ~52px knob), value cards, 52px steppers, real switches. Drum knobs (~40px) + Voices list targets still small. |
| R2 | Hierarchy & density | **C+** | Sound/FX now full-width (dead-gap gone); MPE strip uses the width. Home still ~55% empty; patch list flat; Settings one long scroll. |
| R3 | Navigation consistency | **B-** | Big win: Back uniformly top-left + breadcrumb no longer truncates ("Edit instrument" full). Dragged down by H1 (Back overlaps standalone titles) and Files' Back+Up duality. |
| R4 | Affordance & feedback | **B** | Real switches, confirm modal, styled tabs, ACTIVE pill, live audition. Expression cards still lie; Devices row still a whole-card button. |
| R5 | State clarity | **B-** | Value readouts on Sound/FX/MPE; switches. But Settings sliders blind, Terminal-font shows no active state, Files actions look enabled when none selected. |
| R6 | Labeling & language | **C+** | Curated engine-native labels are a real gain; breadcrumbs fixed. "Juno0" token still leaks (rack + patch button); FX abbreviations terse. |
| R7 | Consistency of system | **C+** | Deck-native screens cohesive; tabs/ParamEditor dropdowns styled. Drums/Voices full palette whiplash; Settings dropdowns unstyled; Voices duplicate editor. |
| R8 | Contrast & legibility | **B-** | ACTIVE pill fixed the worst failure; teal readouts readable. Wi-Fi placeholder low-contrast; olive dropdowns remain. |
| R9 | Discoverability | **B** | Curated views + Advanced disclosure clear; MPE-off now shows an in-context "enable in Settings" pointer (`mpe.py:191`) — fixes the old hidden-gate gripe. Voices' unmarked-legacy still hurts. |
| R10 | Safety | **C+** | Remove-instrument modal + Files two-tap are good. But the highest-stakes action (Reset → reboot) is still unguarded (H2). |
| R11 | Robustness | **A-** | Deferred refill + generation guard; rapid-nav stress produced no reboot. The former Critical is resolved. |

---

## 4. Coverage & method

Driven live on COM11 (1024×600). Verified: Home (`s01`,`s17`); Instruments list
(`s02`); instrument editor (`s03`); Patch picker (`s04`); Sound editor — Juno-6
Basic (`s05`) + Advanced (`s06`); per-device FX (`s07`); Settings top (`s08`) +
bottom (`s09`); MPE editor + channel-map (`s10`) + conflict/overlap (`s11`);
Remove-instrument confirm modal (`s12`); Files (`s13`); Drums (`s14`); Voices
(`s15`); Calibrate (`s16`). Curated DX7/Piano views were verified by construction
in `curated.py` (Juno-6 exercised live). WDT robustness verified by a 6-iteration
rapid-navigation stress loop.

Runtime config was temporarily changed to reach MPE (mpe_enabled on, a second
instrument added to force a conflict) and **fully restored**: single instrument
(id 0), `mpe_enabled` False, instrument MPE disabled, patch 0, device returned to
the Home root. All device-side screenshot PNGs deleted from `/user`. Brightness
untouched. No app source under `deck/` was modified.
