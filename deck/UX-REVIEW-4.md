# UX Review 4 — Tulip Deck (fourth re-audit on COM11)

A fourth harsh pass over the Tulip Deck touch UI (1024×600), graded against the
rubric in `UX-RESEARCH.md` (R1–R11). Conducted by driving the **live device**,
verifying the one outstanding High fix on-screen, hunting for regressions it may
have introduced, and re-confirming the previously-High items. Screenshots
(`s_*.png`) live in the session scratchpad.

Severity: **Critical** (breaks a task / crash) · **High** (frequent friction,
wrong mental model, **reads-as-broken**, or data/session loss) · **Med**
(noticeable, workaround exists) · **Low** (polish) · **Nit** (cosmetic).

**Bottom line up front:** the single High from pass 3 is **fixed and verified on
the device**. Calibrate no longer hand-rolls its title at x=40 — `calib.py` now
offsets both the "Touch calibration" header and the "Tap the dot" prompt to
`ui_btn*2.4 + 24` (mirroring `dk.frame`), so the header sits fully clear of the
top-left Back pill on **both** the capture screen (`s_calib`) and the result
screen (`s_calib_result`). No new regression: the change is isolated to two label
x-coordinates in `calib.py` and touched nothing else. Every standalone Back/title
pair (Settings, Files, Calibrate, Welcome) is clean, and the Reset confirm modal
holds. **Zero Critical, zero High remain.** What's left is the same Medium-and-
below backlog carried from pass 3 (Drums/Voices whiplash, flat patch list,
unitless values, tile-color reuse, a few polish nits) — none of it reads-as-
broken and all of it has a workaround.

---

## 1. Verification — the pass-3 High and the standalone Back/title items

| Item | Status | Evidence |
|---|--------|----------|
| **H1 (cont.) · Calibrate title clipped to "libration"** | **VERIFIED-FIXED** | `s_calib`: "**Touch calibration**" renders in full, right of the Back pill; prompt "Tap the dot (1/5)" below it; purple dot + Cancel present. `calib.py:150–157` computes `_tx = int(deckcfg.get('ui_btn',60)*2.4)+24` and places both labels there. |
| **Calibrate — result screen** | **VERIFIED-CLEAN** | `s_calib_result`: header still clear of Back; "Calibration result" at x=40,y=130 sits **below** the 64-px Back pill (no overlap); Apply (green) / Cancel. |
| **Settings — Back/title** | **VERIFIED-CLEAN** | `s_settings`: "Settings" + "device configuration" fully right of Back (via `dk.frame`). |
| **Files — Back/title** | **VERIFIED-CLEAN** | `s_files`: "Files" + "/user" breadcrumb + "browse /user" + purple "Up" all clear of Back. |
| **Welcome (`run('welcome')`) — Back/title** | **VERIFIED-CLEAN** | `s_welcome`: "Welcome to Tulip" fully visible; Wi-Fi / Instrument / MPE cards + "Get started". |
| **H2 · Reset confirm modal** | **VERIFIED-FIXED** | `s_reset`: "Reset device?" · "This reboots now and drops the current session." · Cancel (purple) + **red Reset**. |

**Regression check for the calib fix: NONE.** The edit is confined to two label
x-offsets in `calib.py`; `dk.frame`, `deckui`, and every other screen are
untouched and render identically to pass 3.

---

## 2. Remaining & new issues, ranked

### Critical
None. Rapid-nav stress (`reset_to_root → rack → instrument → sound → back×3`, ×5,
one uninterrupted call → `STRESS_OK`) left the device live — no watchdog reboot.

### High
None.

### Medium

**M-a · Design-system whiplash — Drums & Voices (R7).** `s_drums` is still black +
primary red/orange/yellow step buttons, ~40 px arc Vol/Pitch/Pan knobs (below the
44 px touch floor), olive lane dropdowns, and a lone red power glyph top-right;
only the top-left blue Back is harmonized. Drums is a **primary Home tile**, so
this is the highest-traffic offender. `s_voices` is black + olive + magenta + teal
with tiny list targets; it is at least badged "(legacy)" at its Apps tile (M4,
still holds) and off the main path. *Fix:* reskin Drums to the deck palette first;
replace arc knobs with sliders or pad hit-areas ≥44 px.

**M-b · Patch picker: 128 patches in a flat scroll; title duplicated (R2/R6).**
`s_edit`. Engine tabs (Juno-6 active blue / DX7 / Piano) read as human categories
— good — but there is still no search / scroll-to-letter / favorites / recents,
and the current patch name appears **twice**: page title "A11 Brass Set 1" *and*
the highlighted row "A11 Brass Set 1". *Fix:* add scroll-to-letter + a recents
band; drop the duplicate page title.

### Low

**L-a · FX/Sound values unitless; FX labels terse lowercase (R6).** `s_fx` shows
`level 0.00 / liveness 0.85 / damping 0.50` with no dB/ms; labels are spelled out
(pass-2 L1) but lowercase. `s_sound` shows "DCO pulse width 0.50" (normalized, no
unit). *Fix:* append engineering units; normalize label casing.

**L-b · Home reuses tile colors and wastes the panel (R2/R7).** `s_home`:
**Instruments and Drums are the same ACCENT blue**, implying a grouping that
doesn't exist; 6 tiles cluster top-left with ~55 % of the panel empty. *Fix:*
unique semantic color + stable icon per tile; center/enlarge the grid or add a
second content column.

**L-c · Files actions look enabled with nothing selected (R5).** `s_files`:
footer reads "nothing selected", yet **Run** (green) and **Edit** (blue) are
full-color and **Delete** rests neutral (not red). *Fix:* dim/disable Run/Edit/
Delete until a selection exists; tint Delete red.

**L-d · Per-note expression cards look tappable but aren't (R4).** `s_mpe_exp`:
"Pressure" ("channel pressure -> voice level") and "Slide (CC74)" ("timbre slide
-> filter cutoff") render as full `dk.row` cards **identical** to the interactive
"Enable expression" switch row above them, but they are read-only descriptions
with no control. *Fix:* strip the card chrome from the static descriptions, or
make them configurable.

**L-e · Files has two upward affordances (R3).** `s_files`: a blue **Back**
(top-left, pops the panel) and a purple **Up** (right of the title, parent
folder). Reasonable but a documented duality. *Fix:* subordinate "Up" visually or
fold directory-up into the breadcrumb.

### Nit

**N-a · Value-card slider knob overflows the card's left corner at min.**
`s_sound` (DCO pulse width 0.50 = min) and `s_fx` (level 0.00): the ~52 px white
knob pokes over the card's rounded left corner. The big target is correct (R1);
the overflow is cosmetic. *Fix:* inset the slider a few px, or shrink the visible
knob while keeping the padded hit-area.

**N-b · "other" channel-map legend label low-contrast (could not reproduce this
pass).** `s_mpe_map`: with the default zone the legend shows only **master**
(blue), **members** (teal), **conflict** (red), all readable on the BG; no "other"
(gray) state was present to re-test. Left on the watchlist; still worth a filled
chip per legend word.

**N-c · Wi-Fi card: fields cramped, placeholder low-contrast (R8).** `s_settings`:
the SSID/password fields sit tight against the card's lower edge and the
placeholder text ("network name" / "password") is barely legible — carried over
from all three prior audits. *Fix:* grow the Wi-Fi card height; darken
placeholders.

**N-d · Terminal-font Small/Medium/Large shows no active state (R5).**
`settings.py:116–118` — all three buttons are `bg=dk.SURFACE2` with no indication
of the current size. *Fix:* highlight the active size (ACCENT fill).

---

## 3. Updated per-dimension grades (R1–R11)

| # | Dimension | Grade | Δ vs pass 3 | Rationale |
|---|-----------|-------|------|-----------|
| R1 | Touch ergonomics | **B** | = | Fat sliders (22 px track, ~52 px knob), value cards, 52 px steppers, real switches. Drum ~40 px arc knobs + Voices list targets still small (borrowed apps). |
| R2 | Hierarchy & density | **B-** | = | Sound/FX full-width value cards. Home still ~55 % empty; patch list flat; Settings one long scroll. |
| R3 | Navigation consistency | **B+** | ↑ | **Calibrate overlap fixed** — Back is now uniformly top-left with a fully-visible title on every standalone screen; breadcrumbs full. Only the Files Back+Up duality (Low) drags it. |
| R4 | Affordance & feedback | **B+** | = | Switches, confirm modal, styled tabs + dropdowns, ACTIVE pill, live audition. Expression cards still lie (L-d). |
| R5 | State clarity | **B** | = | Value readouts on Settings sliders + MPE/Sound/FX. Terminal-font active state missing; Files actions look enabled. |
| R6 | Labeling & language | **B-** | = | Curated engine-native labels, real patch names, spelled-out FX labels. Remaining: unitless FX/Sound values, terse casing, duplicate patch title. |
| R7 | Consistency of system | **B-** | = | Shell fully styled (tabs, dropdowns, switches). Drums/Voices interiors still full palette whiplash. |
| R8 | Contrast & legibility | **B-** | = | ACTIVE pill, teal readouts, styled dropdowns hold up. Wi-Fi placeholder + field clipping remain. |
| R9 | Discoverability | **B+** | = | Curated views, Advanced disclosure with active state, in-context MPE pointer, Voices marked legacy. |
| R10 | Safety | **B+** | = | Reset confirmed (H2), Remove-instrument modal, Files two-tap Delete. |
| R11 | Robustness | **A-** | = | 5-iteration rapid-nav stress produced no reboot; device stayed live. |

---

## 4. Coverage & method

Driven live on COM11 (1024×600). Verified on-device: Calibrate capture
(`s_calib`) + result (`s_calib_result`); Welcome (`s_welcome`); Settings top
(`s_settings`) + mid (`s_settings_b1`) + bottom incl. styled screensaver
dropdowns, MPE switch, System row (`s_settings_b2`); Files (`s_files`); Reset
confirm modal (`s_reset`); Home (`s_home`); Instruments rack (`s_rack`); patch
picker (`s_edit`); Sound — Juno-6 view (`s_sound`); per-device FX (`s_fx`); MPE
overview (`s_mpe`) + channel-map legend (`s_mpe_map`); per-note expression editor
(`s_mpe_exp`); Drums (`s_drums`); Voices (`s_voices`). R11 checked with a
5-iteration rapid-nav stress loop.

Runtime config was temporarily changed to reach MPE (`mpe_enabled` on + instrument
0 MPE on) and **fully restored**: `mpe_enabled` False, instrument 0 MPE disabled,
single instrument (id 0), patch 0, device returned to Home. The session
screenshot `/user/s.png` was deleted. Brightness untouched. No app source under
`deck/` was modified.

---

## 5. Verdict

**ONLY MINOR ISSUES REMAIN.** Zero Critical and zero High. The last outstanding
High — Calibrate's hand-rolled title clipped to "libration" — is fixed and
verified on the device (header fully visible, no Back overlap, no regression
elsewhere). Everything that remains is Medium (Drums/Voices design-system
whiplash; flat 128-patch picker with a duplicated title) or below. The fix loop
can stop.
