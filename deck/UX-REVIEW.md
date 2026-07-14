# UX Review — Tulip Deck (live audit on COM11)

An independent, deliberately harsh UX audit of the Tulip Deck touch UI, graded
against the rubric in `UX-RESEARCH.md` (dimensions R1–R11). Conducted by driving
the **live device** (1024×600) and screenshotting every screen and state. This
is the "rip us a new one" pass: it front-loads what's broken. There is real
craft here too — see **§4 What's genuinely good** — but the brief was more
feedback, not reassurance.

Screenshots referenced as `sNN_*.png` live in the session scratchpad
(`…/scratchpad/`). Each was captured from the running app.

Severity: **Critical** (breaks a task / crash) · **High** (frequent friction,
wrong mental model) · **Med** (noticeable, workaround exists) · **Low** (polish).

---

## 1. Top issues, ranked

| # | Sev | Issue | Where |
|---|-----|-------|-------|
| 1 | **Critical** | **Rapid navigation hard-reboots the device.** Building the tabview-based Sound/FX editor while a panel-reveal rebuild is in flight starves the ESP32 interrupt watchdog → *Guru Meditation Error (Interrupt wdt timeout on CPU1)* → full reboot. Reproduced **twice** (see §2). | Sound/FX editors, back()+push |
| 2 | **High** | **Blind, thin sliders.** The generic ParamEditor renders **14 px** slider tracks with **no numeric value** — you drag filter cutoff / reverb level / pan with zero readout, on a resistive panel, far below the 44 px touch floor. | Sound `s05/s06`, FX `s07` |
| 3 | **High** | **Unstyled native widgets look broken.** `lv.tabview` tab bars render in default **maroon/olive**, `lv.dropdown` the same, clashing hard with the dark-blue system. Reads as a bug, not a design. | Sound/FX tabs, Settings dropdowns |
| 4 | **High** | **Back moves corners.** Top-**left** in shell panels, top-**right** in standalone apps (Settings/Files/Drums/Voices/Calibrate). Same label, opposite side — muscle memory breaks on every boundary crossing. | Everywhere |
| 5 | **High** | **"Basic" is empty.** The Sound editor's Basic Amp tab shows **one slider** on a full 1024×600 screen. Progressive disclosure has collapsed the useful set to almost nothing. | `s05` |
| 6 | **Med-High** | **Design-system whiplash.** Home is a polished dark-blue system; Drums is black+primary-red/yellow, Voices black+magenta, Terminal teal — three foreign palettes and control vocabularies one tap away. | `s16/s18/s19` |
| 7 | **Med** | **Ambiguous On/Off toggles.** A button whose label is its own state ("On"/"Off"). Is tapping "On" turning it on, or telling me it is on? State by color alone. | MPE, Settings, expression |
| 8 | **Med** | **Wayfinding truncated to nonsense.** Breadcrumb ellipsizes "Edit instrument"→"**Edit…**", "Per-note expression"→"**Per-note…**". | `s03/s15` |
| 9 | **Med** | **Destructive actions, one tap, no confirm.** System→Reset (reboots), editor→Remove instrument, Files→Delete all fire immediately; Delete isn't even red. | `s03/s09/s17` |
| 10 | **Med** | **Decorative color + low-contrast text.** Tile colors imply groupings that don't exist (Instruments≡Drums blue; Editor≡Wordpad green); the "active" tag is green-on-light-purple and nearly invisible. | `s01/s02` |

**Biggest, cheapest wins:** (a) restyle `tabview`+`dropdown` to the palette —
kills the single most "broken-looking" thing; (b) copy the **MPE screen's own
pattern** (value label under each control) into `ParamEditor._slider`, and fatten
the knob/hit area; (c) pick one Back corner; (d) widen the title zone; (e) make
Basic auto-include a group's controls when it would otherwise show ≤1.

---

## 2. Critical: the watchdog reboot (R11)

Twice during normal drill-in navigation the board threw
`Guru Meditation Error: Core 1 panic'ed (Interrupt wdt timeout on CPU1)` and
rebooted, losing the session:

- **Repro A:** from the instrument editor, `Back` then immediately open **Sound**
  (the shell rebuilds the revealed editor panel, then builds a 5-tab
  `lv.tabview`, each tab a `ParamEditor`, in one uninterrupted call).
- **Repro B:** `reset_to_root()` then open the **FX** editor in the same call.

Both share the signature: a **panel-reveal rebuild + a heavy tabview build with
no yield**, which blocks CPU1 long enough to trip the interrupt watchdog. When I
split the same steps across two calls (letting the LVGL render loop run between),
both screens built fine. So it is **timing/latency-sensitive**, not a hard
logic bug — but a user who double-taps Back, or drills in fast, can hit it. On a
performance instrument, an unprompted reboot mid-session is the worst possible
failure.

**Fix directions:** yield between the reveal-rebuild and the tabview build (defer
the tabview one tick); build tabs lazily (only the visible tab, populate others
on select); throttle Back so a second tap can't land mid-build; and consider
`vTaskDelay`/feeding the WDT around large LVGL tree construction.

---

## 3. Cross-cutting findings

**G-A · Blind thin sliders (High, R1+R5).** `deckui.slider` is 14 px tall and
`ParamEditor._slider` adds only a text label, no value. So across the entire
**Sound** and **FX** surface you adjust values you can't see, via a track thinner
than a finger is wide. The kicker: the **MPE screen already solves this** —
"15 channels", "+/- 48 semitones" live under its sliders. Port that pattern
everywhere and pad the knob hit-area to ≥44 px (or add +/- nudges for fine work).

**G-B · Unstyled LVGL widgets (High, R4+R7).** `lv.tabview` (Sound `s05/s06`, FX
`s07`) and `lv.dropdown` (Settings screensaver `s12/s13`, wave pickers) render in
LVGL's default theme — dark maroon active cell, olive inactive — against your
`BG=(32,32,64)` blue. It looks like a rendering fault. Style the tabview
`PART.ITEMS`/selected state and the dropdown main+list to `SURFACE/SURFACE2/
ACCENT` so borrowed widgets join the system.

**G-C · Back position flips (High, R3).** Shell panels put Back top-left;
standalone apps (`s11` Settings, `s17` Files, `s16` Drums, `s18` Voices, `s20`
Calibrate) put it top-right. The plan calls this "acceptable"; it isn't — it's a
per-transition re-orientation tax on the single most-used control. Pick one
corner and enforce it (the shell's top-left is the better choice; the standalone
apps are the outliers).

**G-D · Design-system whiplash (Med-High, R7).** The deck-native screens (Home,
Instruments, Devices, Settings, Files, Calibrate, MPE) form a coherent system.
But Drums (`s16`), Voices (`s18`), and the REPL (`s19`) are borrowed firmware
apps with entirely different palettes (black+red/yellow, black+magenta, teal),
control styles, and Back placement. Crossing Home→Drums feels like launching a
different product. At minimum reskin the highest-traffic one (Drums); ideally
give borrowed apps a thin deck chrome wrapper.

**G-E · Ambiguous toggles (Med, R5).** On/Off buttons (MPE, per-instrument MPE,
expression, Smooth UI, V-sync) encode state as label + color only, with the
label doubling as apparent action. Use a real switch widget (thumb + track) so
state and affordance are separate and instantly readable.

**G-F · Wasted panel (Med, R2).** Landscape real estate is squandered: Home
(`s01`) strands 6 tiles in the top-left with ~45% of the panel empty; Sound Basic
(`s05`) is one slider; Devices (`s08`) one row; editor rows (`s03`) put a 340 px
slider hard-right of an 800 px card leaving a dead gap. Use the width — 2-column
param layouts, larger/centered tiles, fill the vertical.

**G-G · Decorative color + contrast (Med, R6+R8).** Tile colors are assigned
arbitrarily: Instruments and Drums are the same `ACCENT` blue; Editor and Wordpad
the same green; the "connected"/Add/Settings green is reused everywhere — color
signals grouping that doesn't exist. And the "active" instrument tag (`s02`) is
green text on light-purple `SURFACE2`, effectively invisible. Either make color
semantic (+ add icons) or drop it to one accent; fix the contrast failures.

**G-H · Truncated wayfinding (Med, R3+R6).** `LEFT_ZONE=210` leaves ~94 px for
the title after the Back button, so "Edit instrument" and "Per-note expression"
ellipsize to "Edit…"/"Per-note…". The one label telling you where you are is
destroyed. Shrink Back to an arrow-only glyph and/or widen the title zone.

**G-I · Destructive without confirm (Med, R10).** Reset (`s09`), Remove
instrument (`s03`), and Delete (`s17`) all act on a single tap; Delete isn't even
colored as destructive. Add a confirm (modal is the correct use of modality here)
or hold-to-confirm.

**G-J · Hidden feature behind a global gate (Med, R9).** Per-instrument MPE only
appears in the editor when Settings→MPE is On — otherwise the entire feature is
invisible with no in-context hint, and the enabling toggle is buried at the
bottom of a long Settings scroll. Show a disabled "MPE — enable in Settings" row
in context.

**G-K · Inconsistent control vocabulary (Low-Med, R7).** "Pick one of N" is done
three ways: tiles (Home/submenus), horizontal category buttons + scroll (Patch),
left vertical tabs (Sound/FX). Value entry mixes sliders, steppers, and
button-groups within one screen (`s03`). Converge.

**G-L · Cards that lie about being tappable (Low-Med, R4).** Per-note expression
"Pressure"/"Slide" (`s15`) are read-only description cards styled exactly like
tappable rows; the Devices row (`s08`) is a full-width button whose only hint is
tiny "tap for FX" text. Interactive and static must look different.

---

## 4. What's genuinely good (keep / propagate)

- **A real, coherent design system** on the deck-native screens: dark rounded
  cards, generous 76–96 px rows, big Home tiles (200×96) — comfortably above the
  touch floor. This is the strong foundation the rest should live up to.
- **The device chip / voice-meter concept** (`s01` "Tulip 10/32", `s08` load
  bar): exactly the right glanceable fleet status for this product.
- **Live audition** on patch select (`forwarder.preview`) — correct, non-modal.
- **The MPE screen (`s14`) is the model:** every control shows its value, plus a
  plain-language status line ("master ch 1, member channels 2-16"). Copy this
  everywhere.
- **Files (`s17`)**: icons, sizes, breadcrumb, Up, persistent action footer — a
  well-built, legible screen.
- **Calibrate (`s20`)**: clean, deck-styled, clear "1/5" progress, always
  escapable.
- **Progressive disclosure (Basic/Advanced)** and the **panel-stack Back** model
  are the right architectural choices — they just need the execution fixes above.

---

## 5. Per-screen findings

### Home — `s01_home.png` (Med)
**Good:** big labeled tiles; live device chip w/ voice load; clock + wifi status.
**Bad:** 6 tiles clustered top-left, ~45% of the panel empty (G-F). Tile colors
decorative — Instruments and Drums identical blue (G-G). Only Devices has a
subtitle ("internal only"); the asymmetry looks unfinished. The lone center chip
reads as a stray pill and isn't obviously tappable; "10/32" (voices) is
unexplained. No icons.
**Fix:** enlarge/center the grid or add a second content column; give each tile a
stable icon + meaningful color; explain or drop the raw "10/32"; make the chip
read as a control.

### Instruments list — `s02_instruments.png` (Med; contrast High)
**Good:** clean list, active row highlighted, prominent Add.
**Bad:** the "active" tag is green on light-purple — **near-invisible** (R8).
Subtitle leaks the engine token **"Juno0"** instead of the patch's real name
(R6). Add's green is the same green as everything else. Acres of empty space.
**Fix:** move "active" to a solid pill or accent bar; show the patch name; badge
Add distinctly.

### Instrument editor — `s03_editor.png` (Med)
**Good:** logical rows; channel stepper; clear nav rows to Patch/Sound/FX.
**Bad:** breadcrumb truncated to "**Edit…**" (G-H). Patch row shows the sound
**twice** with two different names — label "A11 Brass Set 1" and button "Juno0 >"
(G-G/R6). Four different control types stacked (button/stepper/slider/nav) (G-K).
With one device, the Device "button" does nothing. Voices slider has no end
value. Remove (destructive) sits below the fold with no confirm (G-I).
**Fix:** fix the breadcrumb; one patch name; a real value on the voices slider;
confirm Remove.

### Patch picker — `s04_patch.png` (Med)
**Good:** category tabs, clear selection highlight, **live audition** on tap.
**Bad:** 128 Juno patches in a flat scroll — no search, filter, favorites, or
recently-used (R2). Current patch name duplicated (page title + highlighted row).
A third distinct nav metaphor (G-K).
**Fix:** add search/scroll-to-letter and a favorites/recent band; drop the
duplicate title.

### Sound editor — Basic — `s05_sound_basic.png` (High)
**Good:** left-tab grouping; a Basic/Advanced disclosure exists.
**Bad:** **broken maroon/olive tab bar** (G-B). Basic Amp tab = **one "level"
slider** on the whole screen (G-F, R9) — Basic is too sparse to be worth the
mode. Slider 14 px, **no value** (G-A). "Advanced" toggle label is state-vs-
action ambiguous (G-E).
**Fix:** style the tabs; when a group has ≤1 basic control, promote a few more so
Basic is useful; add value readouts; clarify the toggle.

### Sound editor — Advanced — `s06_sound_adv.png` (High)
**Good:** more tabs appear (Osc B, Filter Env) — the disclosure structure is
right.
**Bad:** same broken tabs, same blind 14 px sliders (G-A/G-B). "pan" sits
centered with no way to know it's 0/center. Each ~800 px card holds a 340 px
right-aligned slider → large dead gap (G-F). Terse lowercase labels.
**Fix:** as above; 2-column layout to use the width; show center/detented values.

### FX editor (per device) — `s07_fx.png` (High)
**Good:** excellent context header ("FX bus: Tulip / shared by 1 instrument");
per-bus left tabs.
**Bad:** broken tabs; blind sliders (no dB/ms/level readouts) (G-A/G-B);
cryptic abbreviations "live"/"damp".
**Fix:** style tabs; value readouts with units; spell out labels.

### Devices — `s08_devices.png` (Med)
**Good:** connection status, voice-load bar, "tap for FX" hint, Rescan.
**Bad:** the whole card is a button but the only affordance is tiny text (G-L);
device→FX is a non-obvious mental model. Rescan is `SURFACE2` and reads as
disabled. Lots of empty space.
**Fix:** add a chevron/"FX ›" affordance to the card; give Rescan a real button
style.

### System submenu — `s09_system.png` (Low)
**Good:** Reset correctly red; three clear tiles.
**Bad:** only 3 items — a whole extra tap/level for thin grouping. Reset fires on
one tap, no confirm (G-I). Settings green reused.
**Fix:** confirm Reset; consider surfacing these without a submenu, or fold
Set-time/Calibrate (currently inside Settings) up here for consistency.

### Apps submenu — `s10_apps.png` (Low)
**Good:** consistent with Home tiles.
**Bad:** Editor and Wordpad share green (G-G). Otherwise fine.

### Settings — `s11/s12/s13` (Med-High)
**Good:** comprehensive; card grouping for Wi-Fi; live apply; toast confirms.
**Bad:** Back is **top-right** here (G-C). Wi-Fi placeholder text is barely
legible and the second field **clips the card's bottom edge** (layout bug).
**Terminal font Small/Medium/Large shows no active state** — you can't tell the
current size (R5). On/Off toggles ambiguous (G-E). Screensaver **Dim/Sleep
dropdowns render in broken maroon** (G-B). It's one long undifferentiated scroll
of heterogeneous rows. "Upgrade" (a heavyweight, risky action) is a small purple
button beside Set-time/Calibrate with no extra weight or confirm.
**Fix:** unify Back corner; highlight the active font; fix the Wi-Fi card
clipping and contrast; style dropdowns; section the page; guard Upgrade.

### MPE editor — `s14_mpe.png` (Low — the good one)
**Good:** **every control shows its value**; plain-language status summary;
clear green On. This is the template.
**Bad:** sliders still 14 px thin (G-A touch side); toggle ambiguity (G-E);
reachable only via the hidden global gate (G-J).
**Fix:** fatten slider hit-areas; that's about it.

### Per-note expression — `s15_expr.png` (Med)
**Good:** clear intent text.
**Bad:** "Pressure" and "Slide (CC74)" are **dead read-only cards that look
tappable** (G-L) — users will tap and get nothing. Only one real control
(Enable). Thin dead-end panel. Breadcrumb truncated (G-H); title duplicated.
**Fix:** make description cards visually static (no card chrome), or make them
actually configurable; merge this thin panel back into MPE.

### Drums — `s16_drums.png` (Med — borrowed app)
**Good:** functional 16-step grid; Back+Power pair for the keep-alive model.
**Bad:** total palette break (black + primary red/yellow) (G-D); vol/pitch/pan
**knobs ~40 px** are fiddly on touch (R1); the per-lane dropdowns are the broken
maroon style; two exit buttons crammed in the top-right corner.
**Fix:** reskin to the deck palette; larger knob targets or replace with sliders.

### Files — `s17_files.png` (Low-Med — one of the best)
**Good:** icons, sizes, breadcrumb, Up, persistent action footer, on-theme.
**Bad:** Run/Edit/Delete look **enabled while "nothing selected"** — Delete
especially should be disabled (R5); Delete isn't red despite being destructive
(G-I inconsistency); "Up" and "Back" are two different upward affordances.
**Fix:** disable actions until a selection exists; color Delete as destructive +
confirm.

### Voices (legacy) — `s18_voices.png` (Med)
**Good:** dense power-user access to channels/synths/patches/poly/arp + keyboard.
**Bad:** it **duplicates the new Instruments rack's job** in a completely
different, off-theme (black + magenta), dense UI with tiny list targets (G-D,
R7) — two parallel instrument editors is confusing. It's badged "legacy" in the
plan but sits one tap deep in Apps with no such marker on-screen.
**Fix:** clearly mark it "Advanced/legacy," or hide behind an advanced flag, so
it isn't mistaken for the primary editor.

### Terminal / REPL — `s19_repl.png` (Low)
**Good:** keeps a Home button **and** launcher menu bottom-right — a consistent,
always-available escape, matching the documented model.
**Bad:** teal terminal theme is yet another palette (G-D); it's inherently a
power surface. (The binary garbage in the capture is my own `mpremote` paste
traffic, not the app.)
**Fix:** acceptable as-is; optionally theme the console colors toward the deck.

### Calibrate — `s20_calib.png` (Low — good)
**Good:** clean, on-theme, clear "1/5" progress, escapable via Cancel **and**
Back.
**Bad:** the first target is dead-center rather than a corner; "Cancel" (bottom)
and "Back" (top-right) are two differently-labeled/placed escapes (G-C flavor).
**Fix:** start at a corner; pick one escape affordance.

---

## 6. Method & coverage

Driven live on COM11 (1024×600). Screens captured: Home; Instruments list;
instrument editor; Patch picker; Sound editor (Basic + Advanced); per-device FX;
Devices; System submenu; Apps submenu; Settings (top/mid/bottom incl. brightness,
Terminal-font, render toggles, screensaver dropdowns, MPE toggle, System
actions); MPE editor; Per-note expression; Drums; Files; Voices; Terminal/REPL;
Calibrate. MPE was toured with `mpe_enabled` temporarily on and **restored to
off**; brightness and all params/FX left untouched; device left on **Home**. No
app source was modified. The **watchdog reboot (§2) was encountered twice** as a
side effect of scripted rapid navigation and is reported as a robustness finding.
