# Tulip "deck" UI — UX Review (fresh crawl)

> **Round 2 verification (v2):** every finding re-verified live on-device after the
> fix round; per-finding `status` + `v2_evidence`/`v2_note` are in `findings.json`,
> screenshots in `shots/v2_*.png`. Verdicts: **FIXED** F-1, F-2, F-5, F-6, F-7,
> F-8, F-11, F-13, F-15 · **NO-CHANGE-NEEDED** F-9 (starred = bright yellow,
> verified) · **PARTIAL** F-10 (status string clamps to "3-16 (14 of 15 fit)";
> the channel-map footer still says "Zone fits: … + 15 members") ·
> **REGRESSED** F-3 (active tab now renders the LVGL default-theme MAROON with
> teal text instead of accent+white — `_paint_active_tab` sets local colors at the
> default state selector, which the theme's exact CHECKED-state match outranks;
> set them at `lv.STATE.CHECKED` too) · **NOT-FIXED** F-4 (deckui's DISABLED
> slider styles are deployed but mpe.py still dims dependent cards via
> `set_style_opa(102)` — the 40% blend on RGB332 is the olive/black source),
> F-12 (deferred), F-14, F-16 (not in this round's scope).

Reviewer: senior UX designer (touchscreen / machine-controller displays).
Target: the `deck/` home-shell app on the physical Tulip (TULIP4_R11), 1024x600
RGB332 on an ESP32-S3 in DIRECT render mode. Reviewed live over the qexec/qget
serial harness; every finding cites the screenshot(s) in `shots/` that show it.

**Compute-cost lens:** this device spends most of its cycles on real-time audio.
Every finding below carries a cost note, and each suggested fix is the cheapest
option that solves the problem. Genuinely nicer-but-expensive ideas are listed
separately at the bottom as "costed ideas — likely reject" rather than as
findings. Notably, *most* of the real findings here are data/logic/color bugs
whose fixes cost **zero** additional render or per-frame work.

## Surfaces crawled
Welcome; Home root (rack); Instrument editor (drums-synth, Juno-6 synth, GM);
Type picker; Kit picker (sampled + synthesized); Pads editor; Pad Swap picker
(empty + populated); Sound editor (Basic + Advanced); FX device editor; Patch
picker (Juno-6, GM); MPE (gate off, per-instrument row, disabled panel, enabled
panel, gated-off message); Settings (persistent strip + Network / Display /
System tabs); Files (list, selected, delete-confirm); on-screen keyboard;
Remove-instrument confirm dialog; Devices; System submenu; Apps; MIDI monitor.
Screenshots are numbered in crawl order (`shots/01_...` … `shots/32_...`).

## What's working well
- **Remove-instrument confirm** (`27`): clear title, real consequence text
  ("This can't be undone"), destructive action red vs neutral Cancel, dimmed
  scrim. Model modal.
- **Welcome onboarding** (`29`): numbered steps, plain-language copy, one primary
  "Get started" CTA.
- **On-screen keyboard** (`26`): large keys, function keys color-differentiated
  from letters, sensible symbol row.
- **Two-column landscape editor** (`02`, `09`): identity/routing left, sound
  right — fits without scrolling, uses the 1024px width well.
- **Live value readouts** on virtually every slider (Sound, FX, MPE, Settings),
  with min/max microlabels — no "blind" sliders.
- **FX editor context line** "shared by 2 instruments on Tulip" (`13`) — sets the
  right mental model for a device-level bus.

---

## Findings (by severity)

### HIGH
**F-1 — Synth-kit drums mislabeled "TR-808 kit" on the Home rack row.**
`shellmodel.instrument_sound()` looks the kit up in an **int-keyed** dict
(`_KIT_NAMES`), but synth kits are **string** keys (`'synth:tr909_d'`), so the
lookup always misses and prints the `'TR-808 kit'` default. The result is visible
on the root screen the performer glances at most: the row says **"ch1 TR-808
kit"** while the very same screen's footer says **"TR-909 mda D (synth)"** and the
editor's Kit field agrees with the footer (`01`, `32`, `02`). The one wrong label
is the most-seen one.
*Fix:* in `instrument_sound()`, when the kit isn't an int in `_KIT_NAMES`, resolve
via `drums_kit.kit_name(kit)` (what the editor already uses).
*Cost:* one branch in a per-row-build function; **zero** per-frame cost.

### MEDIUM
**F-2 — Kit picker doesn't reveal the current selection on open** (`04`, `05`).
84 kits; the current one is highlighted but lives far off-screen at the bottom of
the "Synthesized" section — opening the picker shows no sign of what's loaded.
*Fix:* `selected_btn.scroll_to_view(lv.ANIM.OFF)` once after build. Use
`ANIM.OFF` (instant jump) — an animated scroll would repaint every frame.
*Cost:* one one-shot scroll call at build; negligible.

**F-3 — Tabbed panels invert visual hierarchy** (`11`, `13`, `14`, `16`).
On Sound / FX / Settings the **selected** tab renders dark/recessed (only a thin
blue underline) while **inactive** tabs render as lighter raised lavender buttons
— the eye goes to the wrong place, and on the Settings System tab the active tab
is nearly invisible against the background. The intended `STATE.CHECKED = ACCENT`
highlight in `parameditor._style_tabview` isn't rendering on this build.
*Fix:* on the tabview's `VALUE_CHANGED`, explicitly recolor the ~4 tab buttons
(active = ACCENT + white, others = SURFACE2 + placeholder) instead of relying on
the CHECKED style.
*Cost:* tab switches already repaint the content page; recoloring ~4 buttons is
negligible.

**F-4 — MPE sliders render in default-theme olive/mustard when disabled** (`18`
vs `19`). With per-instrument MPE off (gate on), the dependent sliders fall back
to LVGL's default olive track + black knob — reads as broken. The deck slider
style has no DISABLED-state colors.
*Fix:* give the slider style explicit **flat** disabled colors at full opacity
(muted track/knob). Do not alpha-dim — the codebase already learned (files.py
X-4) that opacity dimming quantizes to olive on RGB332. Or dim only the card
container.
*Cost:* static style setup; zero per-frame cost.

**F-5 — GM patch names stored pre-truncated to 12 chars** (`28b`).
`gm.NAMES` literally contains `'Electric Gra'`, `'Honky-Tonk P'`, `'Tine
Electri'`, `'FM Electric '`. This is a **data** problem, not layout — the row
label auto-sizes and has hundreds of unused px.
*Fix:* store full names in `gm.NAMES`; labels already ellipsize where genuinely
needed.
*Cost:* a few hundred bytes of flash/RAM; zero render cost.

**F-6 — Files "Delete" never shows red on selection** (`24` vs `25`).
On selecting a file, Run→green and Edit→blue, but Delete stays neutral lavender;
red only appears at the second "Confirm?" tap. `_set_btn` caches each button's
enabled color on first call, and the build-time `_set_actions(False)` captures
delbtn's creation color (SURFACE2); `_select` sets RED then immediately re-enables
delbtn, restoring the cached SURFACE2 and wiping the red. The destructive action
has no distinct affordance until after you've already tapped it once.
*Fix:* create the Delete button with `bg=dk.RED` so the cache captures RED (like
Run=GREEN/Edit=ACCENT), or special-case delbtn.
*Cost:* none.

### LOW
**F-7 — Pad Swap picker: no selected-pack highlight; empty hits column has no
prompt** (`07`, `08`). The right column is a big empty void until you tap a pack,
and the tapped pack gets no highlight. "Use selected" is green even with nothing
selected.
*Fix:* highlight the tapped pack (reuse the existing 2-object recolor), add one
muted "Pick a pack" placeholder label. *Cost:* 2-object recolor + one static
label; negligible.

**F-8 — Pad Swap hit names leak hash suffixes** (`08`): `brush1_139732`,
`jazzkick_24e847`, `brush2_5d9f12`.
*Fix:* strip a trailing `_<hex>` in `synthkits.hit_name()` for display.
*Cost:* one string op per visible row; negligible.

**F-9 — Patch favorite stars don't show state** (`10`, `28b`): every row shows an
identical filled dark star; can't tell what's favorited. (No favorites were set
during review, so on/off couldn't be A/B'd — **verify**.)
*Fix:* ensure `dk.star(on)` vs `(off)` differ strongly (solid orange vs hollow).
*Cost:* color/glyph only; none.

**F-10 — MPE member-channel math overflows past ch16 with no warning** (`19`):
master ch2 + 15 members shows "member channels **3-17**" (ch17 doesn't exist) yet
claims "Zone fits". `_members_str` doesn't clamp `hi = master + n` to 16 and
`zone_fits` false-positives.
*Fix:* clamp to `min(16, master+n)` and raise the existing overlap warning.
*Cost:* pure logic; none.

**F-11 — Files can delete core system modules with only the inline two-tap**
(`23`): boot.py / amyparams.py / home.py etc. are listed and deletable; deleting
boot.py bricks the device.
*Fix:* filter known deck modules out of the listing (the set already exists in
`home._discover_user`), or route system-.py deletion through the stronger
`dk.confirm` modal. *Cost:* one set lookup per entry; negligible.

**F-12 — Confirm modal lives on `layer_top()`, untied to the panel lifecycle**
(`27`, `28`): it survived programmatic navigation and floated over an unrelated
screen. Normal use exits via its buttons, but any async screen change
(screensaver wake, app switch) could strand it.
*Fix:* parent the scrim to the active screen/panel, or dismiss open confirms on
navigation/screensaver. *Cost:* none.

### NIT
**F-13 — Legacy tools in Apps** (`31`): "Drums (legacy)" / "Voices (legacy)"
duplicate the Instruments UI. Drop from the default Apps list. *Cost:* none.

**F-14 — MPE gated-off panel is an empty dead-end** (`20`): tiny top-left message,
no button to reach the setting it names. Center it + add an "Open Settings"
button. *Cost:* one label + button; negligible. (Transient state — low priority.)

**F-15 — Clock never auto-syncs despite Wi-Fi connected** (`14`, `01`): top bar
reads "--:--" while Settings shows "connected 192.168.4.250"; time only sets on a
manual "Set time now"/Connect.
*Fix:* call `deckcfg.sync_time()` once after a successful boot reconnect.
*Cost:* one NTP request at boot; not a per-frame cost.

**F-16 — Files disabled buttons look like normal secondary buttons** (`23`):
disabled Run/Edit/Delete (SURFACE2 + muted) match many enabled secondary buttons
elsewhere, so "disabled" doesn't read as disabled. Lowest priority. *Cost:* none.

---

## Costed ideas — likely reject (better UX, not worth the cycles)
- **Animated scroll-to-selection / animated tab transitions.** Any tweened motion
  repaints large regions every frame in DIRECT mode. Use instant `ANIM.OFF`
  jumps (see F-2) instead.
- **Thumbnails / waveform previews in the kit & swap lists.** Decoding/pushing
  per-row imagery on an 84-item list is real CPU + PSRAM churn during scroll.
  Text rows are the right call here.
- **Fuller empty-state illustrations** (Devices, MIDI monitor, gated MPE). Nicer,
  but extra draw surface for screens users pass through quickly — keep the cheap
  one-line prompts.

## Notes on method / device hygiene
- All navigation and screenshots were driven over `tools/qexec.py` /
  `tools/qget.py` (never a default serial open — that power-cycles the device).
- One temporary instrument ("REVIEW-TMP") was created to exercise the synth
  editors/pickers and MPE, then **removed**; the device was returned to its
  original single-instrument (Tulip9) Home state (`32`), the global MPE gate was
  toggled on for review and **restored to off**, and no files under /user were
  modified except the transient `shot.png`.
- Reset cause read as WDT (3) at session start (a pre-existing reboot from before
  this session); the device was responsive throughout and did not watchdog during
  the crawl.
