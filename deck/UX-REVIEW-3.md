# UX Review 3 — Tulip Deck (third re-audit on COM11)

A third harsh pass over the Tulip Deck touch UI (1024×600), graded against the
rubric in `UX-RESEARCH.md` (R1–R11). Conducted by driving the **live device**,
verifying each claimed fix on-screen, and hunting for what remains and what's new.
Screenshots (`s_*.png`) live in the session scratchpad.

Severity: **Critical** (breaks a task / crash) · **High** (frequent friction,
wrong mental model, **reads-as-broken**, or data/session loss) · **Med**
(noticeable, workaround exists) · **Low** (polish) · **Nit** (cosmetic).

**Bottom line up front:** most of the second-pass fixes landed cleanly — the
Reset confirm (H2), the styled screensaver dropdowns (M1), live values on the
Settings sliders (M2), the real patch names + "Juno-6 >" nav button (M5), the FX
label spellout (L1), the channel-map legend (L2), and the "(legacy)" badge on
Voices (M4) all verified on-device. **But the H1 fix is incomplete:** it was
applied inside `dk.frame`, so Settings and Files are now clean, yet **Calibrate
still draws its own title at x=40 and the Back pill covers it — the screen reads
"libration"** (`s_calib`). That is the very same reads-as-broken wayfinding
failure the prior audit rated High, still shipping on one screen. So this is
**not yet a clean pass**.

---

## 1. Verification of the 8 claimed fixes

| # | Claimed fix | Status | Evidence |
|---|-------------|--------|----------|
| **H1** | `dk.frame` header starts right of Back; standalone titles fully visible | **PARTIAL** | Settings title fully visible right of Back (`s_settings_top`); Files title + "/user" breadcrumb clear (`s_files`). **Calibrate still reads "libration"** (`s_calib`) — `calib.py:147` draws `"Touch calibration"` at `x=40` and `:120` `"Calibration result"` at `x=40`, both bypassing `dk.frame`'s `back_w+24` offset, so the Back pill overlaps them. Fix reached only `dk.frame`-based apps. |
| **H2** | System → Reset opens a confirm modal | **VERIFIED-FIXED** | `home._reset()` now calls `dk.confirm("Reset device?", …)` (`home.py:23–33`). On-device: modal with title, "This reboots now and drops the current session.", **Cancel** + **red Reset** (`s_reset2`). |
| **M1** | Screensaver Dim/Sleep dropdowns use the deck palette | **VERIFIED-FIXED** | `settings.py:152` now calls `dk.style_dropdown`. On-device both render SURFACE2-purple, white text, chevron — no olive/maroon (`s_settings_end`). |
| **M2** | Settings Volume/Brightness/Menu-size sliders show a live number | **VERIFIED-FIXED** | New `_val_slider` stacks a live teal readout under the title. On-device Volume "4", Brightness "5" (`s_settings_top`); Menu-button-size likewise. |
| **M5** | Rack rows show the real patch name; Patch nav shows the engine category | **VERIFIED-FIXED** | Rack row subtitle "Tulip · ch1 · **All Brass Set 1**" (`s_rack`); editor Patch row "A11 Brass Set 1" + nav button "**Juno-6 >**", no "Juno0" (`s_edit`). |
| **L1** | FX labels spelled out (liveness/damping) | **PARTIAL** | "**liveness**" / "**damping**" now spelled out (`s_fx`). But the values are still **unitless normalized 0.00–1.00**, not dB/ms — the second half of the original L1 (add units) is not done. |
| **L2** | MPE channel-map has a color legend | **VERIFIED-FIXED** | Legend row drawn right of the "Channel map" title: **master** (blue), **members** (teal), **conflict** (red) (`s_mpe`, `mpe.py:112–117`). Minor: the "**other**" (gray) legend label is low-contrast on the SURFACE card. |
| **M4** | Voices marked "(legacy)" in Apps | **VERIFIED-FIXED** | Apps tile reads "**Voices (legacy)**" and is de-emphasized gray, not an accent (`s_apps`, `home.py:113`). |

**Net:** 5 fully fixed, 2 partial (H1, L1), 1 fully fixed with a minor contrast
follow-on (L2). The two partials are the story: **H1 is the incomplete one that
still reads as broken.**

---

## 2. Remaining & new issues, ranked

### Critical
None. The watchdog reboot from pass 1 did **not** recur under a 6-iteration
rapid-nav stress loop (`reset_to_root → rack → edit → sound → back → back → sound
→ back`, ×6, one uninterrupted call → `STRESS_DONE_OK`, device stayed live).
*(Note: a single WDT reboot did fire while I ran an abnormal full-tree
`get_scroll_bottom()` introspection + scroll + screenshot in one blocking call —
not a user gesture. Real navigation is robust; flagged only for awareness.)*

### High

**H1(cont.) · Calibrate title is destroyed by the Back button — reads
"libration" (R3/R6).** The H1 fix was placed in `dk.frame` (title → `back_w+24`,
`deckui.py:88–93`), which correctly clears Settings and Files. But `calib.py`
does not use `dk.frame`: it draws `"Touch calibration"` at `x=40, y=34`
(`calib.py:147`) and `"Calibration result"` at `x=40, y=130` (`:120`). The
top-left Back pill (~150 px wide) sits on top of both, so the touch-setup screen
a user reaches *because their touch is miscalibrated* is titled "**libration**"
(`s_calib`). This is the exact reads-as-broken wayfinding failure the prior pass
graded High; it now survives on the one standalone screen that hand-rolls its
header. The task isn't blocked (dots are tappable, Cancel/Back work), but a
shipping title reading "libration" is a High-visibility defect.
*Fix:* start both calib labels at `x≈168` (mirror `dk.frame`'s `back_w+24`), or
route calib's header through `dk.frame`. One-line change.

### Medium

**M-a · Design-system whiplash persists — Drums & Voices (R7).** Drums
(`s_drums`) is still black + primary red/orange/yellow, ~40 px arc Vol/Pitch/Pan
knobs (below the touch floor), olive lane dropdowns, and a lone red power glyph
top-right. Voices (`s_voices`) is black + magenta + teal with tiny list targets.
Only the top-left blue Back was harmonized; both interiors remain a foreign
product. Voices is at least now badged "(legacy)" at the entry tile (M4), but
Drums is a primary Home tile. *Fix:* reskin Drums to the deck palette (highest
traffic); replace the arc knobs with sliders or pad their hit-areas ≥44 px.

**M-b · Patch picker: 128 patches in a flat scroll; title duplicated (R2/R6).**
`s_editor`. Engine tabs (Juno-6/DX7/Piano) now read as human categories — good —
but there's still no search / scroll-to-letter / favorites / recents, and the
current patch name appears **twice** (page title "A11 Brass Set 1" *and* the
highlighted row). *Fix:* add scroll-to-letter + a recents band; drop the
duplicate page title (the highlighted row already names it).

### Low

**L-a · FX/Sound values are unitless; sound labels terse lowercase (R6).**
`s_fx` shows 0.00 / 0.85 / 0.50 with no dB/ms; `s_sound_adv` shows "sub wave",
"sub width", "sub tune 440" (Hz unstated) in terse lowercase. Labels are now
spelled out (L1) but engineering units and Title-case are still missing.
*Fix:* append units and normalize label casing.

**L-b · Home reuses tile colors and wastes the panel (R2/R7).** `s_home`:
Instruments and Drums are the **same** ACCENT blue; 6 tiles cluster top-left with
~55 % of the panel empty. In Apps, Editor and Wordpad share the same green
(`s_apps`). Color implies groupings that don't exist. *Fix:* unique semantic
color + a stable icon per tile; center/enlarge the grid or add a second content
column (recent patches / a level strip).

**L-c · Files actions look enabled with nothing selected (R5).** `s_files`:
"nothing selected" in the footer, yet **Run** (green) and **Edit** (blue) are
full-color and **Delete** rests neutral (not red). They no-op, but nothing
signals it, and Delete doesn't read as destructive at rest (it does arm to a red
"Confirm?" on tap). *Fix:* dim/disable Run/Edit/Delete until a selection exists;
tint Delete red.

**L-d · Per-note expression cards still look tappable but aren't (R4).**
`mpe.py:273–280` renders "Pressure" and "Slide (CC74)" as full `dk.row` cards
identical to interactive rows, but they're read-only descriptions.
*Fix:* strip the card chrome from the static descriptions, or make them
configurable.

**L-e · Files has two upward affordances (R3).** `s_files`: a blue **Back**
(top-left) and a purple **Up** (right of the title) do different things (pop panel
vs. parent folder). Reasonable but a documented duality. *Fix:* keep both but
visually subordinate "Up", or fold directory-up into a breadcrumb.

### Nit

**N-a · Value-card slider knob overflows the card's left corner at min.**
`s_sound`, `s_sound_adv`, `s_fx`: at a min-value position (pulse width / sub width
= 0.50 = min; FX level = 0.00) the ~52 px white knob pokes over the card's left
rounded corner. The big target is correct (R1); the overflow is cosmetic.
*Fix:* inset the slider a few px, or shrink the visible knob while keeping the
padded hit-area.

**N-b · "other" channel-map legend label is low-contrast.** `s_mpe`: the gray
"other" legend word (GRAY 92,94,112 on SURFACE 64,64,96) nearly disappears while
master/members/conflict read clearly. *Fix:* lighten the "other" swatch label or
add a small filled chip beside each legend word.

**N-c · Wi-Fi card: fields cramped, placeholder low-contrast (R8).** `s_settings_top`:
the SSID/password fields sit tight against the card's lower edge and the
placeholder text is barely legible — carried over from both prior audits.
*Fix:* grow the Wi-Fi card height or shrink field spacing; darken placeholders.

**N-d · Terminal-font Small/Medium/Large shows no active state (R5).**
`s_settings_top` (bottom edge): the three buttons are identical SURFACE2 with no
indication of the current size. *Fix:* highlight the active size (ACCENT fill).

---

## 3. Updated per-dimension grades (R1–R11)

| # | Dimension | Grade | Δ vs pass 2 | Rationale |
|---|-----------|-------|------|-----------|
| R1 | Touch ergonomics | **B** | = | Fat sliders (22 px track, ~52 px knob), value cards, 52 px steppers, real switches. Drum ~40 px knobs + Voices list targets still small (borrowed apps). |
| R2 | Hierarchy & density | **B-** | ↑ | Sound/FX now full-width value cards — the old dead-gap is gone. Home still ~55 % empty; patch list flat; Settings one long scroll. |
| R3 | Navigation consistency | **B-** | = | Back uniformly top-left; breadcrumbs full. Dragged by the Calibrate title overlap (H1 cont.) and the Files Back+Up duality. |
| R4 | Affordance & feedback | **B+** | ↑ | Switches, confirm modal, styled tabs **and** dropdowns, ACTIVE pill, Advanced now shows an active state, live audition. Expression cards still lie. |
| R5 | State clarity | **B** | ↑ | Value readouts now also on Settings sliders (M2), plus MPE/Sound/FX. Terminal-font active state still missing; Files actions look enabled. |
| R6 | Labeling & language | **B-** | ↑ | "Juno0" token gone (M5); FX labels spelled out (L1); curated engine-native labels. Remaining: unitless FX/Sound values, terse casing, duplicate patch title. |
| R7 | Consistency of system | **B-** | ↑ | All dropdowns now styled (Settings included, M1); tabs styled; Voices badged legacy. Drums/Voices interiors still full palette whiplash. |
| R8 | Contrast & legibility | **B-** | = | ACTIVE pill, teal readouts, styled dropdowns hold up. Wi-Fi placeholder + field clipping remain; "other" legend label low-contrast. |
| R9 | Discoverability | **B+** | ↑ | Curated views, Advanced disclosure with an active state, in-context MPE pointer, Voices marked legacy. |
| R10 | Safety | **B+** | ↑↑ | The highest-stakes gap is closed: Reset now confirmed (H2), alongside Remove-instrument modal + Files two-tap. |
| R11 | Robustness | **A-** | = | Rapid-nav stress produced no reboot. One crash only under abnormal full-tree introspection, not a user path. |

---

## 4. Coverage & method

Driven live on COM11 (1024×600). Verified on-device: Home (`s_home`); Instruments
rack (`s_rack`); instrument editor (`s_edit`); Patch picker (`s_editor`); Sound —
Juno-6 Basic (`s_sound`) + Advanced (`s_sound_adv`); per-device FX (`s_fx`);
Settings top (`s_settings_top`) + bottom incl. styled screensaver dropdowns +
MPE + System (`s_settings_end`); Reset confirm modal (`s_reset2`); MPE editor +
channel-map legend (`s_mpe`); Apps submenu (`s_apps`); Files (`s_files`); Drums
(`s_drums`); Voices (`s_voices`); Calibrate (`s_calib`). R11 checked with a
6-iteration rapid-nav stress loop.

Runtime config was temporarily changed to reach MPE (mpe_enabled on + instrument
MPE on) and **fully restored**: `mpe_enabled` False, instrument MPE disabled,
single instrument (id 0), patch 0, device returned to Home. All session
screenshot PNGs deleted from `/user`. Brightness untouched. No app source under
`deck/` was modified.

---

## 5. Verdict

**HIGH/CRITICAL ISSUES REMAIN** — no Critical, but **one High**: the H1 fix is
incomplete, so **Calibrate still reads "libration"** (Back covers a
hand-rolled title). It's a one-line fix (`calib.py` — offset the two title
labels to `x≈168`, or route them through `dk.frame`). Everything else remaining
is Medium or below. Land that single fix and the next pass should read
**ONLY MINOR ISSUES REMAIN**.
