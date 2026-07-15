# UX Review 6 — Tulip Deck (sixth live audit on COM11)

A fresh pass over the Tulip Deck touch UI (1024×600), driven on the **live
device**, graded against the rubric in `../UX-RESEARCH.md` (R1–R11). This pass
audits the changes landed since UX-REVIEW-5 by another agent — commits
`085c6392` (UX-REVIEW-5 fixes), `c92162fa` (Kits rename, auto-channel, logging,
bigger search), `cb5f7e3f` (keyboard flash fix), `97e2be9c` (perf: config cache,
commit-on-release sliders) — then sweeps the rest of the app. Per the owner's
brief, section 4 additionally grades the app against **2026 touch-UI paradigms**,
not just the rubric.

All screenshots live in `shots/` and are referenced by filename. The
machine-readable version of every finding is in `findings.json`.

Severity: **Critical** (breaks a task / crash) · **High** (frequent friction,
reads-as-broken, or data/session loss) · **Med** (noticeable, workaround exists)
· **Low** (polish) · **Nit** (cosmetic).

**Bottom line up front: this round is a net regression.** The UX-REVIEW-5 fixes
themselves are genuinely good — kit labels in the rack (`s15_rack_kit`), unique
instrument names + rename + editor identity (`s16_edit_tulip2`,
`s17_rack_2up`), auto-picked free MIDI channels, and a pleasant add→edit flow
all verified working on the device. But the same batch shipped **a Critical
regression: Settings no longer opens at all** (it crashes on build and silently
dumps you back — the single screen holding Wi-Fi, volume, brightness, screensaver
and calibration is unreachable), and this session **reproduced the
interrupt-watchdog reboot on an ordinary navigation path** (MPE → expression →
back → back → FX), which is almost certainly the user-reported "drops back to
Home" glitch — and the new persistent logging **cannot capture it**, because a
watchdog reset never gets to write a log line. The flagship rename/search
surfaces also render visibly broken text fields. Fix C1, H1, H2 before calling
this round done.

---

## 1. Verification — what the Gemini round actually fixed (confirmed live)

| Item | Status | Evidence |
|---|--------|----------|
| **M1 (rev-5): drum instrument mislabeled as melodic patch in rack** | **VERIFIED-FIXED** | `s15_rack_kit`: row reads "Tulip · ch1 · TR-808 kit". `shellmodel.instrument_sound()` branches on drums. |
| **M2 (rev-5): duplicate "Tulip" names, no rename, no editor identity** | **VERIFIED-FIXED** | `s17_rack_2up`: "Tulip" / "Tulip 2" distinct; editor breadcrumb shows the instrument's name (`s16_edit_tulip2`); Name field with rename exists (`s03_edit_top`) — though the field itself renders broken, see H2. |
| **Auto free-channel pick** | **VERIFIED** | Second instrument landed on Channel 2 automatically (`s16_edit_tulip2`). Device-move also re-picks (code: `rack._rebuild_edit`). |
| **Add → edit flow** | **VERIFIED, good** | "+ Add instrument" drops you directly into the new instrument's editor — right pattern. |
| **Bigger search field (rev-5 L1, partial)** | **PARTIAL** | Field is now 60 px (was 44) with a 72×60 keyboard button (`s06_patch`). But the placeholder is still clipped and low-contrast (H2), so the fix is half-done. |
| **Auto-keyboard on field focus** | **VERIFIED, with side effects** | Tapping the search field pops the keyboard (`s07_patch_kb`) — good discoverability. Side effects: M5 (keyboard buries the results list), L1 (unstyled firmware keyboard now on the main path), L10 (a 32 KB internal-RAM alloc failure logged on open). |
| **Favorites / search / stars** | **VERIFIED-HOLDS** | Live filter (`s08_search_kb`), star toggles orange without stealing selection (`s09_star`), favorites-first ordering (`s10_fav_order`), Favorites filter with clear active state (`s11_fav_filter`). |
| **Reset + Remove confirms** | **VERIFIED-HOLD** | `s30_reset_confirm`, `s19_remove_confirm` — named target, "can't be undone", red destructive button, dimmed backdrop. Textbook. |
| **Per-note expression panel (rev-5 fix)** | **VERIFIED-HOLDS** | `s23_mpe_expression`: descriptions are plain text, single real switch. |
| **Panel state across app switches** | **VERIFIED, nice** | Terminal round-trip returned to the same System panel (`s32_home_return`). |

## 2. Issues, ranked

### Critical

**C1 · Settings is dead — crashes on open, every time (R11/R9).**
Tap System → Settings: the screen flashes and you're back on the System grid
with **no error shown**. To the user this reads as a broken button; in reality
the panel build throws
`AttributeError: 'module' object has no attribute 'volume'` at
`settings.py:91` (`live=amy.volume` — this firmware's `amy` module has only
`MAX_VOLUME`; `tulip.volume` doesn't exist either; verified on-device). The perf
refactor (`97e2be9c`) moved the `amy.volume` reference from inside a drag
callback (where the latent break would only have hit the volume slider) to
**panel-build time**, killing the whole screen: Wi-Fi setup, volume, brightness,
fonts, screensaver, set-time, **touch calibration** and upgrade are all
unreachable from the UI. This cannot have been opened once on a device before
merging. *Fix:* use the current AMY volume API (resolve what the pinned amy
exposes — e.g. `amy.send(volume=…)`) and never evaluate optional attributes at
build time; guard with `getattr`. Add "open every screen once on-device" to the
merge checklist. Also: surface panel-build failures visibly (the shell's
in-panel "panel error" label never shows here because standalone apps quit
instead) — a silent bounce is the worst failure mode.

### High

**H1 · Interrupt-watchdog reboot on a plain navigation path — this is the
"drops back to Home" glitch, and the new logging can't see it (R11).**
Reproduced this session: editor → MPE → Per-note expression → Back → Back →
FX "Edit >" in quick succession → device **hard-rebooted** (uptime reset
confirmed; `deck.log` tail is nothing but `=== deck boot ===` markers). After
boot the user lands on Home — exactly the reported symptom `decklog.py` was
built to diagnose. Two structural problems: (a) the deferred-refill mitigation
in `homeshell._schedule_refill` reduces but does not eliminate the WDT trip —
heavy panel teardown+rebuild chains (MPE/FX editors) can still starve CPU1;
review 5 hit the same reboot once during a patch-search rebuild, so this is now
**two sightings in two sessions on two different heavy-rebuild paths**. (b) A
WDT reset never executes Python again, so file logging is blind to it *by
design*. *Fix:* log `machine.reset_cause()` (and the ESP-IDF reset reason) in
the boot marker so WDT resets are distinguishable from power-cycles in
`deck.log`; chunk the heavy editors' builds across ticks (build N cards per
`tulip.defer` slice) or feed the WDT during rebuild; consider pre-building the
FX/Sound panels once and re-parenting instead of rebuilding from scratch.

**H2 · Every text input renders its content visibly broken — on the round's
flagship features (R5/R8).** The new **Name** field shows "Tulip" **cut in half
at the field's bottom edge** in every editor (`s03_edit_top`, `s12_kit_editor`,
`s18_rename_kb` — worst with the keyboard up). The patch-search **placeholder**
("search patches") is clipped at the field's **top** edge and rendered in a dim
brownish-orange on purple that fails any contrast bar (`s06_patch`) — typed
text, oddly, sits fine (`s08_search_kb`). This is the same `tulip.UIText`
vertical-metrics problem review 5 flagged (L1); the field got bigger but the
text metrics were never fixed, and the Name field re-imported the bug into a
brand-new surface. Nothing here *functions* wrong — it just **looks broken on
the two features this round is proudest of**. *Fix once, in one place:* wrap
`UIText` creation in a `deckui` helper that (a) vertically centers the textarea
in its group (`align CENTER` / explicit y from font line-height), (b) sets a
legible placeholder color (`dk.MUTED` at minimum, on `SURFACE2`), and use it for
search, Name, and the Wi-Fi fields.

### Medium

**M1 · Rename writes the whole config file to flash on every keystroke
(R11).** `rack.py:_name_cb` fires on `VALUE_CHANGED` (each character) and calls
`deckcfg.set_instrument`, which unconditionally `save()`s — one full JSON
serialize + flash write per keypress, on the same UI tick as the keyboard event.
The **same commit series** (`97e2be9c`) introduced cache-then-flush specifically
because "a flash write per VALUE_CHANGED tick stalls both cores and wears the
config sector" — the new rename feature violates the new rule. *Fix:* update the
RAM cache with `flush=False` per keystroke (chips can refresh from cache) and
commit once on `DEFOCUSED`/`READY` (keyboard close).

**M2 · MPE panel: sub-controls look fully live while the master switch is off
(R5/R4).** `s22_mpe`: "Enable MPE" is off, yet Member channels, Pitch bend,
Listen channel and Per-note expression render at full color and full
interactivity; the only status cue is a tiny muted "MPE off" in the bottom-left
corner. A 2026-baseline panel dims/disables dependent controls (LVGL:
`add_state(lv.STATE.DISABLED)` + a disabled style) or collapses them under the
gate. *Fix:* disable + 40 % opacity on the four dependent cards while the gate
is off; move the "MPE off/on" status up next to the switch.

**M3 · Same control, two colors: the rack's enable switch is green on a fresh
build and blue after a Back-refill rebuild (R5/R7).** Fresh push: green
(`s02_rack`, `s20_rack_after_remove`); after `back()`'s deferred
`_schedule_refill` rebuild: blue (`s15_rack_kit`, `s17_rack_2up`) — with
`rack.py:85` passing `color=dk.GREEN` unconditionally both times. Green is the
deck's "enabled" vocabulary (wifi dot, connected dot), so the blue variant
breaks state-color meaning on the app's most important toggle. Likely in the
deferred-refill path the CHECKED-state indicator style isn't applied (timing of
`add_state` vs style?) and LVGL's theme default (blue) shows through. *Fix:*
find why `set_style_bg_color(c(color), PART.INDICATOR | STATE.CHECKED)` loses in
the refill path (set the style on `PART.INDICATOR` unconditionally as a
fallback), and add a regression check to `test_deck.py`'s shellmodel layer if
possible.

**M4 · Search-as-you-type with the keyboard up shows exactly ONE result row
(R2/R1).** `s07_patch_kb`, `s08_search_kb`: the auto-popped keyboard covers the
bottom ~55 % of the screen and the results list does not resize, leaving a
single visible row between the field and the keys ("trum" → you can see A13
Trumpet but not B82 Piccolo Trumpet, `s09_star`). The whole point of live search
is watching the list narrow as you type. *Fix:* when the soft keyboard is up,
shrink `listbody` to the space above it (ui.py exposes the keyboard height;
re-grow on close). One line of layout beats a great feature you can't see.

### Low

**L1 · The firmware soft keyboard is now design-system whiplash on the main
path (R7).** `s07_patch_kb`, `s18_rename_kb`: olive/khaki keys, different
radius, different font — jarring against the deck palette, and `autoshow`
means every search/rename now routes through it. It also logged
`alloc failed size 32768 … internal 8bit` when opening (see L8). *Fix:* restyle
`ui.lv_soft_kb` from `deckui` when the deck is active (bg `SURFACE`, keys
`SURFACE2`, accent CHECKED) — LVGL keyboards take styles like any widget.

**L2 · Picker page title duplicates the selection and goes stale under filter
(R2/R6, carried rev-5 L2).** `s06_patch` titles "A11 Brass Set 1" over a list
whose selected row says the same; under a "trum" filter the title still says
"A11 Brass Set 1" over a Trumpet-only list (`s08_search_kb`). *Fix:* make the
big title the stable "Juno-6 patches" heading; let the highlighted row carry the
selection.

**L3 · "Drum machine (legacy)" tile still clipped at both ends (R6, carried
rev-5 L3, untouched).** `s28_apps`: reads "…rum machine (legacy…". The rename
commit renamed the *type* to Kits but left this. *Fix:* "Drums (legacy)".

**L4 · System / Apps submenus still cluster top-left; ~70 % of the panel is
dead (R2, carried rev-5 L4, untouched).** `s27_system`, `s28_apps` vs the
centered Home root (`s01_home`). *Fix:* apply the root's centered flex align to
`home._submenu_builder`.

**L5 · Files app unchanged: enabled-looking footer actions with nothing
selected, off-palette navy/magenta/olive buttons, and a divergent Back style
(R5/R7, carried rev-5 L5).** `s29_files`. Also note its Back is a flush square
top-left unlike the shell's rounded Back. *Fix:* disable Run/Edit until a
selection exists; reskin footer to deck accent/green/red; reuse the shell Back
chrome.

**L6 · Type change still silently discards patch + Sound edits (R10, carried
rev-5 L6, untouched).** `s05_type` has helper text ("What engine this
instrument plays.") but no reset warning. *Fix:* one hint line: "Changing type
resets the patch and sound edits."

**L7 · Values still mostly unitless; params whose minimum is nonzero read as
broken sliders (R5/R6, carried rev-5 L7).** `s25_sound_basic`: "DCO pulse width
0.50" with the knob hard-left *is correct* (the param's range is 0.50–0.99) but
is indistinguishable from a broken slider. `s24_fx`: level/liveness/damping
unitless. "sub tune 440 Hz" shows units are already supported. *Fix:* percent
display for duty-style params ("50 %"), min/max microlabels at the track ends,
units (dB/ms/%) everywhere `amyparams` knows them.

**L8 · 32 KB internal-SRAM allocation failure logged opening the keyboard on
the patch picker (R11).** `alloc failed size 32768, heap_caps_malloc, internal
8bit` — the keyboard still appeared (fallback path), but the hot path is one
failed alloc away from visible breakage, and this headroom pressure is the same
soil H1 grows in. *Fix:* worth a heap audit of the picker path (it rebuilds the
full patch list per keystroke — consider windowing the list).

**L9 · "Kits" is the wrong word for an instrument *Type* (R6).** `s05_type`:
the list reads Juno-6 / DX7 / Piano / **Kits** — three engines and one plural
noun describing a *patch collection*. The editor row "Kit TR-808" (`s12_kit_editor`)
is right; the type name should have stayed **Drums** (the thing the instrument
*is*), with "Kit" as its patch-slot label — which is exactly how review 5's M1
fix labeled the rack row ("TR-808 kit"). Half of this rename improved clarity,
the other half traded a category name for a collection name.

**L10 · Rack rows stutter the default name: "Tulip — Tulip ch1 …" (R6).**
`s02_rack`, `s17_rack_2up`: the bold title is the instrument name (default:
device name) and the subtitle begins with the device name again. *Fix:* drop
the device prefix from the subtitle when it equals the row's device, or only
show device on non-internal instruments.

### Nit

**N1 · The top-bar clock confidently shows the wrong time (05:52) on a device
that has never set its clock.** A wrong clock is worse than none. *Fix:* show
"--:--" until time is set (NTP or Settings), or hide it.

**N2 · Reset-confirm backdrop dimming makes half the tiles vanish.**
`s30_reset_confirm`: under the 200-alpha overlay, the green/purple tiles blend
to invisible navy while Terminal/Reset survive — an RGB332 blend artifact that
makes the dimmed background look glitched. *Fix:* higher-opacity backdrop (or
full-black at ~230) so nothing half-survives.

**N3 · Min-value slider knob overflows the card's rounded corner (carried).**
`s24_fx` level 0.00. *Fix:* inset track a few px.

**N4 · Inactive left-tab labels low-contrast on purple (carried).** `s24_fx`
Chorus/Echo/EQ, `s25_sound_basic` VCF/ENV/LFO/VCA. *Fix:* lighten inactive text.

**N5 · ASCII arrows and double-hyphens in UI copy.** "channel pressure ->
voice level" (`s23_mpe_expression`), "Drum kit -- swaps all the sounds at once"
(`s13_kit_picker`). The font has real glyphs; if not, reword ("Drum kit: swaps
all sounds at once").

**N6 · Welcome step cards look tappable but aren't (R4).** `s33_welcome`: three
big colored numbered cards read as buttons; only "Get started" is. Also the
footer leaks REPL syntax (`run('welcome')`) into onboarding copy.

**N7 · Submenu tile label color drifts cream/yellow vs the root's white
(R7).** Compare `s27_system` labels with `s01_home` — palette mapping
difference worth a look while touching tile code.

---

## 3. Per-dimension grades (R1–R11), Δ vs UX-REVIEW-5

| # | Dimension | Grade | Δ | Rationale |
|---|-----------|-------|------|-----------|
| R1 | Touch ergonomics | **B+** | = | Search field/keyboard button grown to 60 px; fat sliders, 52 px steppers, 76 px rows hold. Keyboard-buried results (M4) is layout, not target size. |
| R2 | Hierarchy & density | **B-** | ↓ | Submenus still cluster top-left; picker title duplication; keyboard leaves one visible result row. |
| R3 | Navigation consistency | **B+** | = | Back uniform in the shell; panel state survives app switches (nice). Files' divergent Back persists. |
| R4 | Affordance & feedback | **B** | ↓ | Confirms/switches/stars hold; but MPE dead-looking-alive controls (M2) and Welcome's fake-button cards. |
| R5 | State clarity | **C+** | ↓ | Clipped name/placeholder text (H2), green/blue switch flip-flop (M3), live-looking disabled MPE controls (M2), min-value sliders reading broken (L7). |
| R6 | Labeling & language | **B-** | = | Kit labels in rack fixed (+); "Kits" as a type (L9), legacy tile clip (L3), name stutter (L10), unitless values (−). |
| R7 | Consistency of system | **C+** | ↓ | The unstyled firmware keyboard is now *on the main path* via autoshow (L1); Files unchanged; switch color inconsistency (M3). |
| R8 | Contrast & legibility | **B-** | = | Placeholder contrast still failing (H2); inactive tabs, Wi-Fi card unverifiable (Settings dead). |
| R9 | Discoverability | **B+** | ↓ | Auto-keyboard on focus is a genuine win; but the entire Settings surface is undiscoverable because it crashes (C1). |
| R10 | Safety | **B+** | = | Both confirms hold. Type-change reset still unhinted (L6); per-keystroke flash writes (M1) are a wear risk, not a data risk. |
| R11 | Robustness | **D** | ↓↓ | Settings hard-crashes on open (C1); WDT reboot reproduced on a normal nav path (H1); 32 KB alloc failure on the keyboard path (L8). Stress loop ×3 passed *when paced*, but "don't navigate briskly" is not a contract. |

## 4. Does this feel like 2026? (requested calibration)

Measured against current touch-appliance practice (KlipperScreen-class printer
UIs, modern POS, Teenage Engineering / Polyend-generation instrument UIs,
Material 3 / HIG expressive-motion baselines), the deck is a **competent
2019-era flat utility UI**. Clean IA, decent targets — but it would not read as
a 2026 product. Concretely, in priority order:

1. **Zero motion.** Panels, modals and the keyboard *teleport*. There is no
   push/pop slide, no fade, no pressed-state transition, no animated switch
   thumb. Motion is not decoration; it's how modern UIs explain hierarchy
   (where did this panel come from, where does Back go). LVGL 9 does this
   cheaply (`lv_obj` translate/opa animations, ~150–200 ms ease-out; honor a
   "reduce motion" toggle in Settings). This is the single biggest
   modernity gap.
2. **No iconography.** Every tile, row and button is text-only (`s01_home`,
   `s27_system`). 2026 glanceable UIs pair a glyph with every destination
   (Instruments ♪, Devices ⌁, System ⚙, Files 📁) — icons are what make a
   3-foot UI readable at arm's length while playing. LVGL symbols are already
   in the build (Back chevron, keyboard glyph); an 8-glyph pass over
   tiles/rows would transform scanability.
3. **The UI never shows sound.** This is a musical instrument whose home
   screen is static text. Nothing meters, nothing pulses on MIDI-in, the chip
   shows "10/32" as dead numerals. A 2026 instrument UI shows a live voice
   meter in the chip, a MIDI-activity flicker on the instrument row, an
   audition waveform in the patch picker. Even one live element (chip voice
   meter — the data already updates `voices used/capacity`) would change the
   perceived generation of the product.
4. **Flat-on-flat depth.** Cards are single-tone fills with no elevation
   language — the 2025-26 correction to flat design restores soft shadows /
   1 px borders so controls read as controls (the audit rubric's own §4).
   One shadow token on cards + pressed "sink" would do it.
5. **Typography has one voice.** Everything is the same family/weight at 2–3
   sizes; hierarchy comes only from position. A weight axis (semibold titles,
   regular body, mono for values — values especially: `0.50`, `440 Hz`,
   `ch2` want tabular figures) is standard 2026 practice.
6. **Empty space without status.** Home's dead lower half (`s01_home`) could
   carry the modern "appliance dashboard" layer: now-playing patch, last MIDI
   device seen, volume — glanceable state, not decoration.

None of this contradicts the embedded constraints (RGB332, two cores, LVGL):
items 1, 2, 4, 5 are style/animation passes with near-zero RAM cost; item 3
reuses data the shell already polls.

## 5. Structural recommendations — the bigger moves

Per the owner's brief: nothing here is constrained to polish-sized changes, and
scrapping everything was on the table. My judgement after driving every screen:
**the bones are good — keep the shell** (top-bar + panel stack, device chips,
the confirm vocabulary, the unit-tested `shellmodel` split). A from-scratch
rewrite would burn the parts that already test well and re-roll the dice on
stability (see H1). But two structural changes are worth real work:

**S1 · Make the rack the home screen — the launcher is spending the best
screen on navigation instead of state.** The #1 task on a performance
instrument is "get to my instrument's sound, fast." Today that's Home →
Instruments → row → Browse → patch: **four taps and three panels before
anything sounds different**, and the root screen (`s01_home`) spends 1024×544
of prime real estate on three abstract category tiles and dead space. Meanwhile
the rack (`s17_rack_2up`) — the screen that actually shows the instrument
state a performer glances at — is one level down. Proposal: **Home IS the
rack.** Instrument rows (with patch names, enable switches, live voice/MIDI
activity) fill the root; "+ Add instrument" lives at the bottom as today;
Devices and System become two small fixed tiles or top-bar icons (the top bar
already has the device chips — Devices is arguably *already* in the bar). This
deletes one entire navigation level from the main loop, gives the dead Home
space a job (state), and costs little: `rack.panel` already exists as the
builder — it becomes the root panel instead of the tile grid.

**S2 · The editors are phone-portrait forms stretched across a landscape
panel — go two-column.** Every editor row (`s03_edit_top`, `s22_mpe`) is a
full-width card with a label hard-left and a lone control hard-right, leaving
up to ~700 px of empty card between them, and forcing vertical scrolling to
reach Remove (`s04_edit_bottom`). This is the exact anti-pattern in
`UX-RESEARCH.md` §2 (porting portrait onto a wide panel). On 1024×600 the
instrument editor fits **entirely without scrolling** as two columns: left =
identity/routing (Name, Device, Channel, Voices), right = sound (Type, Patch,
Sound, FX, MPE), Remove full-width at the bottom. Same for MPE and Settings
(when it's revived). The row/card primitives in `deckui` don't change — only
the panel layouts.

**S3 (smaller, optional) · Fold Settings into the shell; retire standalone
chrome for first-party tools.** Settings (and Files) are the last first-party
surfaces living as separate `tulip.run` apps with their own chrome — which is
why Files has a divergent Back (L5) and Settings can die without the shell's
panel-error safety net (C1 bounced silently instead of showing the shell's
"panel error" label). Splitting Settings into shell panels (Network / Display /
Audio / System) fixes the chrome fork, gives its crash a visible error surface,
and makes its sections reachable in the same push/pop vocabulary as everything
else. Files can follow later; the borrowed heavy apps (Editor, Wordpad, Tulip
World) rightly stay standalone.

What I would *not* do: rebuild the patch picker again (tabs-by-engine +
search + favorites is the right shape — it needs M4/H2/L2, not a rethink), or
replace LVGL/the panel stack, or split the deck into more apps.

## 6. Coverage & method

Driven live on COM11 (1024×600) via `mpremote` + a temporary on-device driver
(`/user/_drv.py`, removed after): LVGL-tree walking, real `lv.EVENT.CLICKED`
dispatch on the actual widgets, `tulip.screenshot()` pulls. Captured: Home
(`s01`), rack 1-up/2-up (`s02`,`s17`), editor top/bottom (`s03`,`s04`), Type
(`s05`), patch picker + keyboard + search + star + favorites
(`s06`–`s11`), Kits editor + picker (`s12`,`s13`), Devices (`s14`), rack kit
row (`s15`), Tulip 2 editor (`s16`), rename w/ keyboard (`s18`), remove confirm
(`s19`), post-remove rack (`s20`), MPE row/panel/expression (`s21`–`s23`), FX
(`s24`), Sound Basic/Advanced (`s25`,`s26`), System (`s27`), Apps (`s28`),
Files (`s29`), Reset confirm (`s30`), Terminal (`s31`; the garbage text is my
own raw-REPL traffic, not a defect), post-Terminal state retention (`s32`),
Welcome (`s33`). Settings could not be captured — it crashes on open (C1),
traceback captured over serial. Robustness: one WDT reboot reproduced (H1),
then a paced 3× rack→edit→back→back loop passed (`STRESS_OK`, uptime
continuous).

Device state was fully restored: config file restored byte-for-byte from
`deck_config.backup.json` (single Juno-6 instrument, ch1, MPE gate off,
favorites cleared), `/user/_drv.py` and `/user/_s.png` deleted, device
rebooted to Home and verified. `deck.log` was left in place (its boot markers
are evidence of H1). No repo source was modified.

## 7. Verdict on the Gemini round

**Ship the review-5 fixes, hold the perf round.** The M1/M2 fixes are real,
verified, and thoughtfully done (auto-channel and add→edit weren't even asked
for). But the batch fails the basic bar of "every screen opens": Settings is
dead on current firmware (C1) — a build-time crash that one smoke tap would
have caught — the watchdog reboot the logging round was supposed to explain is
still reproducible and still invisible to that logging (H1), and both marquee
text-input features render visibly broken (H2). Priority order for the fix
agent: **C1 → H2 → M4 → M1 → M3 → M2**, then the carried Lows (L2–L7 are all
small), then the structural moves in section 5 (S1 rack-as-home, S2 two-column
editors) and the 2026 pass in section 4 (motion → icons → live meters).
