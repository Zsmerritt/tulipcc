# UX Review 5 — Tulip Deck (fifth re-audit on COM11)

A fresh harsh pass over the Tulip Deck touch UI (1024×600), graded against the
rubric in `UX-RESEARCH.md` (R1–R11). Conducted by driving the **live device**,
focused on the surfaces that are **new since UX-REVIEW-4** — the 3-tile Home, the
System/Apps submenu, the instrument **Type** mode-switch, the **type-scoped patch
picker** (favorites + search + stars), **Drums-as-an-instrument** (Kit row + kit
picker), and the **multitimbral per-instrument on/off** list — then sweeping the
rest and re-confirming the earlier fixes. Screenshots (`s_*.png`) live in the
session scratchpad.

Severity: **Critical** (breaks a task / crash) · **High** (frequent friction,
wrong mental model, **reads-as-broken**, or data/session loss) · **Med**
(noticeable, workaround exists) · **Low** (polish) · **Nit** (cosmetic).

**Bottom line up front:** the new architecture is a real step forward. Home is a
clean 3-tile launcher with **distinct** tile colors (the old Instruments/Drums
same-blue collision is gone), the **Type picker makes the engine explicit** (was
inferred from patch range — a genuine discoverability win), the patch picker is
**scoped per engine with working search, favorites-first ordering, and per-row
stars**, and **Drums is now a first-class styled instrument** with an in-palette
kit picker — retiring the black/tiny-knob legacy Drum-machine whiplash off the
main path (it now lives, badged "(legacy)", under System > Apps). The prior L-d
expression-card lie is **fixed**. The two findings worth acting on are both
**Medium** and both live on the new multitimbral/drum surfaces: a **Drums
instrument is mislabeled as a melodic patch** in the rack list, and **multiple
instruments on one device are all named "Tulip" with no way to rename and no
identity in the editor**. Everything else is Low/Nit. **Zero Critical, zero
High.**

---

## 1. Verification — prior fixes still hold + new-surface confirms

| Item | Status | Evidence |
|---|--------|----------|
| **Back top-left + title clear, everywhere** | **VERIFIED-CLEAN** | Every panel + standalone: Instruments (`s_rack`), editor (`s_edit`), Type (`s_type`), Patch (`s_patch`), Kit (`s_kit`), System (`s_system`), Apps (`s_apps`), MPE (`s_mpe`), FX (`s_fx`), Sound (`s_sound`), Settings (`s_settings`), Files (`s_files`) — Back is uniformly top-left and never covers the title. |
| **Reset confirm modal** | **VERIFIED-HOLDS** | `s_reset`: "Reset device?" · "This reboots now and drops the current session." · Cancel (purple) + **red Reset**, dimmed backdrop. Cleared via `lv.layer_top().clean()` (did not tap red). |
| **Remove-instrument confirm** | **VERIFIED** (code `rack._remove`) | Two-step `dk.confirm("Remove instrument?", 'Delete "…"…')`. |
| **Styled dropdowns + segmented Basic\|Advanced** | **VERIFIED** | `s_sound`: DCO-wave dropdown styled; Basic/Advanced is now a **segmented toggle that shows the active state** (Basic filled blue) — resolves prior N-d "no active state". |
| **Value-readout sliders** | **VERIFIED** | Voices ("10 voices"), Settings Volume 4 / Brightness 5, MPE member/bend, Sound/FX readouts all present. |
| **L-d · Expression cards looked tappable** | **VERIFIED-FIXED** | `s_mpe_exp`: the Pressure / Slide descriptions are now **plain text** under a "What it does" header (no card chrome); only "Enable expression" is an interactive switch row. |
| **M-a · Drums design-system whiplash on the main path** | **RESOLVED (main path)** | Drums is now an instrument type with the **in-palette** kit picker (`s_kit`); the black/tiny-knob legacy app is moved to System > Apps and badged "Drum machine (legacy)" (`s_apps`). |
| **Type-scoped patch picker** | **VERIFIED** | `s_patch` (Juno-6) vs `s_dx7_patch` (DX7): the list rebuilds to only that engine's patches; subtitle reads "Juno-6 patches" / "DX7 patches". |
| **Favorites + search + stars** | **VERIFIED** | `s_patch_search` (live filter to "Trumpet" rows), `s_fav_empty` (helpful "No favorites in Juno-6 yet — tap the *" empty state), `s_fav_star` (starred patch turns **orange** and sorts to the top; selection stays highlighted blue; the star is a child button so it doesn't also select). |

---

## 2. Issues, ranked

### Critical
None.

### High
None.

### Medium

**M1 · A Drums instrument is mislabeled as a melodic patch in the rack list
(R6/R5).** `s_drum_edit`. Set an instrument to Type = Drums (kit TR-808) and its
rack row still reads **"Tulip  ch1  A11 Brass Set 1"** — a Juno brass patch name,
on a drum kit. Verified against source and by direct call:
`shellmodel.instrument_summary` (line 88–92) unconditionally formats
`patch_name(instr['patch'])` with **no drum-type branch**, and a drum instrument's
`patch` is 0 → `patches[0]` = "A11 Brass Set 1". Reproduced live:
`instrument_summary` returned `Tulip  ch1  A11 Brass Set 1` for a drums instrument.
This is 100 % reproducible on the primary instrument-management surface and gives
a flatly wrong mental model (the editor itself is correct — it shows "Kit
TR-808"). *Related latent bug:* `shellmodel.chip_label`/`patch_short` return
"Juno0" for a drum instrument (confirmed: `chip_label` → `Tulip Juno0`); it is not
rendered today because the top bar shows **device** chips, but it will surface if
per-instrument chips are ever restored. *Fix:* branch `instrument_summary` (and
`patch_short`) on `type == 'drums'` to show `drums_kit.kit_name(instr['kit'])`
(e.g. "TR-808").

**M2 · Multitimbral instruments on one device are indistinguishable — all named
"Tulip", no rename, no identity in the editor (R5/R6).** `s_rack2`. Add a second
internal instrument and the list shows **"Tulip" / "Tulip"** as the two bold
row titles; the only difference is the muted subtitle ("ch1 A11 Brass Set 1" vs
"ch2 BRASS 1"). `rack._add` names every new instrument after its device
(`sm.device_name`), and the editor (`_build_edit`) has **no name field** — there
is no way to rename, so N internal instruments are always "Tulip ×N". Compounding
it, the editor's title is the generic "Edit instrument" with **no active-instrument
identifier anywhere on the screen** (`s_edit`), so after tapping one of several
identical rows you can't confirm which one you're editing. This is friction on the
headline new multitimbral workflow (several instruments enabled at once). *Fix:*
auto-suffix duplicate names ("Tulip 1", "Tulip 2") or fold channel/type into the
row title; add a rename affordance; show the instrument name in the editor header.

### Low

**L1 · Patch-picker search field: placeholder low-contrast and text clipped at the
field's top edge (R8/R1).** `s_patch`, `s_patch_search`. The `tulip.UIText` search
box renders the "search patches" placeholder in a dim orange that is barely legible
on the purple field, and the text baseline sits **above** the field so it's
vertically clipped by the field's top border. (Matches BACKLOG item 4c "search
field too small for touch".) *Fix:* raise placeholder contrast, grow the field
height, and vertically center its text.

**L2 · Duplicate current-patch title in the picker (R2/R6, carried M-b).**
`s_patch`. The page title ("A11 Brass Set 1") repeats the highlighted selected row
verbatim. Under an active search it's worse — the title shows the *current* patch,
which may not even be in the filtered results (`s_patch_search` titles
"A11 Brass Set 1" over a Trumpet-only list). *Fix:* drop the redundant page title
(or make it a stable "Juno-6 patches" heading and let the row carry the selection).

**L3 · "Drum machine (legacy)" tile label is clipped on both ends (R6).** `s_apps`.
The label overflows the 200 px tile and center-clips to "…rum machine (legacy" —
the leading "D" and trailing ")" are cut. "Voices (legacy)" fits; this one is too
long. *Fix:* shorten to "Drums (legacy)" or wrap/shrink the tile font.

**L4 · System / Apps submenus cluster top-left; ~65 % of the panel is empty
(R2).** `s_system`, `s_apps`. The Home root is nicely centered (`s_home`), but the
drilled-in submenu grids are top-left-anchored `ROW_WRAP`, leaving most of the
1024×600 panel dead below the tiles ("false bottom"). *Fix:* center/enlarge the
submenu grids to match the root, or use the width.

**L5 · Files footer actions look enabled with nothing selected, and use an
off-palette color set (R5/R7, carried L-c).** `s_files`. Footer reads "nothing
selected" yet **Run** and **Edit** render full-color; **Delete** is only dimmed.
Worse, the three buttons are **navy / magenta / olive** — a borrowed palette that
doesn't match the deck accent/green/red vocabulary. *Fix:* dim/disable Run/Edit
until a selection exists, tint Delete red, and reskin to the deck palette.

**L6 · Changing Type silently discards the patch and Sound edits (R10).** `s_type`.
`rack._set_type` resets `patch` to the engine's first patch (and any dialed Sound
params are for the old engine), with no hint or confirm. Picking a genuinely
different engine legitimately means a new sound, so the reset is defensible — but
an unlabeled destructive side effect is a small safety gap. *Fix:* a one-line "this
resets the patch" hint on the Type picker, or a confirm when leaving a modified
sound.

**L7 · FX / Sound values are unitless and FX labels are terse lowercase (R6,
carried L-a).** `s_fx` (level 0.00 / liveness 0.85 / damping 0.50), `s_sound`
("DCO pulse width 0.50"). No dB/ms/%, and "0.50" showing at the slider's far-left
minimum reads as a mismatch. *Fix:* append engineering units; normalize label
casing.

### Nit

**N1 · Submenu tile-color reuse — Files and Settings are both green (R7).**
`s_system`. Two unrelated destinations share a color, implying a grouping that
doesn't exist. *Fix:* one semantic color per destination.

**N2 · Inactive left-tab labels are low-contrast on the purple SURFACE2 (R8).**
`s_fx` (Chorus/Echo/EQ), `s_sound` (VCF/ENV/LFO/VCA) render as dark text on a
mid-purple fill — legible but marginal at arm's length; the active tab (white +
blue underline) is fine. *Fix:* lighten inactive-tab text.

**N3 · Min-value slider knob overflows the card's left corner (R1, carried N-a).**
`s_fx` (level 0.00), `s_sound` (pulse width at min). The big hit target is correct;
the ~52 px knob poking over the rounded corner is cosmetic. *Fix:* inset the
slider a few px.

**N4 · Wi-Fi card: fields cramped against the lower edge, placeholders barely
legible (R8, carried N-c).** `s_settings`. Unchanged across five audits. *Fix:*
grow the card; darken placeholders.

---

## 3. Updated per-dimension grades (R1–R11)

| # | Dimension | Grade | Δ vs pass 4 | Rationale |
|---|-----------|-------|------|-----------|
| R1 | Touch ergonomics | **B+** | ↑ | Drums is now a styled instrument (64 px kit rows) instead of the ~40 px-knob legacy screen on the main path. Fat sliders, 52 px steppers, real switches, 76 px list rows. Search field is the one small/cramped target. |
| R2 | Hierarchy & density | **B** | ↑ | Home root centered, 3 clean tiles. But System/Apps submenus cluster top-left (~65 % empty); duplicate title in the patch picker. |
| R3 | Navigation consistency | **B+** | = | Back uniformly top-left, titles/breadcrumbs clear, drill-stack consistent through the new Home>System>Apps nesting. Files Back+Up duality persists (Low). |
| R4 | Affordance & feedback | **A-** | ↑ | Switches, two confirm modals, styled tabs/dropdowns, **segmented Basic\|Advanced with active state**, live audition on patch **and** kit select, star toggles. Expression cards no longer lie. |
| R5 | State clarity | **B** | = | Value readouts everywhere; per-instrument green/gray on-off switches; favorites star + type/kit selection highlighted. Dragged by the drum mislabel (M1) and duplicate names / no editor identity (M2). |
| R6 | Labeling & language | **B-** | = | Scoped patch names, kit names, curated labels. But a drum instrument is mislabeled as a melodic patch (M1), "Drum machine (legacy)" clipped (L3), unitless values, lowercase FX labels. |
| R7 | Consistency of system | **B** | ↑ | Drums reskinned into the deck palette; legacy drums/voices moved off-path + badged. Remaining: Files footer off-palette (L5), submenu tile-color reuse (N1). |
| R8 | Contrast & legibility | **B-** | = | Teal readouts + styled dropdowns hold. Search placeholder (L1), inactive-tab text (N2), Wi-Fi placeholder (N4) remain. |
| R9 | Discoverability | **A-** | ↑ | **Type picker makes the engine explicit** (was inferred); scoped pickers, favorites + live search, curated Advanced disclosure, legacy apps badged. |
| R10 | Safety | **B+** | = | Reset + Remove confirmed. Type-change silently resets the patch/sound (L6) is the one unguarded side effect. |
| R11 | Robustness | **A-** | = | 4-iteration rapid rack→edit→back loop left the device live (`STRESS_OK`). One transient interrupt-watchdog reboot fired during a screenshot; **not reproducible** (the same search rebuild succeeded on retry) — a watchdog trip, not a UI defect. |

---

## 4. Coverage & method

Driven live on COM11 (1024×600). Verified on-device: Home 3-tile launcher
(`s_home`, `s_final`); Instruments list 1-up (`s_rack`) and multitimbral 2-up
(`s_rack2`); instrument editor top (`s_edit`) + bottom (`s_edit2`); Type picker
(`s_type`); scoped patch picker Juno-6 (`s_patch`) + DX7 (`s_dx7_patch`); patch
search filter (`s_patch_search`); favorites empty state (`s_fav_empty`) + starred
ordering (`s_fav_star`); Drums editor — Kit row, no Sound, FX present
(`s_drum_edit2`) — and the drum instrument's mislabeled rack row (`s_drum_edit`);
Kit picker over all 7 kits (`s_kit`); System submenu (`s_system`); nested Apps
(`s_apps`); MPE overview (`s_mpe`) + per-note expression (`s_mpe_exp`); per-device
FX bus (`s_fx`); Sound curated editor (`s_sound`); Reset confirm modal (`s_reset`);
Settings (`s_settings`); Files (`s_files`). R11 checked with a 4-iteration rapid-nav
loop. The `shellmodel.instrument_summary`/`chip_label` mislabels (M1) were confirmed
both from source and by direct on-device calls.

Runtime config was temporarily changed to reach these screens (type dx7/drums, a
starred patch, a second instrument, `mpe_enabled` on) and **fully restored**:
single instrument (id 0), `type='juno6'`, `patch=0`, enabled, channel 1,
`mpe_enabled=False`, instrument MPE off, favorite 5 cleared, device returned to
Home. The session screenshot `/user/s.png` was created and deleted (pre-existing
unrelated PNGs in `/user` were left untouched). Brightness untouched. No app source
under `deck/` was modified.

---

## 5. Verdict

**ONLY MINOR ISSUES REMAIN.** Zero Critical, zero High. The new surfaces — 3-tile
Home, explicit Type mode-switch, engine-scoped patch picker with search/favorites/
stars, Drums-as-an-instrument with an in-palette kit picker, and the multitimbral
per-instrument on/off list — are well built and verified on the device, and the
prior expression-card lie is fixed. The two items worth fixing next are both
**Medium** and both new: **M1** (a Drums instrument reads "A11 Brass Set 1" in the
rack list — a one-line `instrument_summary` drum branch) and **M2** (multiple
instruments on one device are all "Tulip", with no rename and no editor identity).
Neither breaks a task or reads as a crash. The fix loop can stop; schedule M1 and
M2 as the top of the backlog.
