# SETTINGS-TABS — reorganization spec for the Settings panel

**Headline: a persistent Volume+Brightness strip pinned above a 3-tab left-rail
tabview (Network / Display / System), built with the FX editor's proven
`parameditor.build_tabbed` pattern.** The two most-used controls never go
behind a tab; everything else gets room to breathe instead of the current
12-item two-column cram; and tab switches repaint only the ~860px content area
right of the rail — the exact repaint profile the FX/Sound editors already
proved cheap in DIRECT mode.

This spec is written to be implemented as-is by the fix agent. Inventory
walked from `deck/settings.py _build()` (via `_build_cols`); layout targets
1024x600 with the 56px shell bar (`homeshell.BAR_H`), i.e. a 1024x544 panel.

---

## 1. Why tabs, and why this shape

The current panel (see `shots/v2_x5_settings.png`) packs 12 heterogeneous
rows into two fixed columns. It fits — but it's at capacity: the Wi-Fi card
already grew 18px once ("the password field grazed the bottom"), the clock
and Debug switches share a row purely for space, and any new setting forces
another squeeze. Density also flattens hierarchy: "Factory reset" sits
visually adjacent to "Sleep after".

Constraints honored:

- **DIRECT render:** `lv.tabview` with a left rail swaps one child tree per
  tap — one bounded repaint of the content area, no scrolling. This is the
  FX editor's exact pattern (`devices.fx_panel` → `parameditor.build_tabbed`),
  verified cheap on-device across reviews 8–9. No new widget types.
- **Volume/Brightness stay one tap:** they are NOT in any tab. They live in a
  strip that is always on screen, above the tabview. Opening Settings shows
  them immediately; no tab state can hide them.
- **No second nesting level:** every control sits directly on its tab page.
  Tabs are one level, exactly like FX's Reverb/Chorus/Echo/EQ.

## 2. Layout (pixels)

```
y=0    ┌───────────────────────────────────────────────────────┐
       │ shell top bar (56px, unchanged)                       │
y=56   ├───────────────────────────────────────────────────────┤
       │ PERSISTENT STRIP  (x=24, w=976, h=88)                 │
       │ [ Volume  card 480px ]  16px  [ Brightness card 480px]│
y=152  ├──────────┬────────────────────────────────────────────┤
       │ tab rail │ tab content page (~836 x ~384, flex column │
       │ 140px    │ of dk.row cards, 12px row gap)             │
       │ Network  │                                            │
       │ Display  │                                            │
       │ System   │                                            │
y=544  └──────────┴────────────────────────────────────────────┘
```

- **Strip:** two half-width cards side by side (row-flex container at
  `(24, 8)` inside the panel, `pad_column 16`). Each card reuses the existing
  `_val_slider` visual (title + teal mono value stacked left, fat slider
  right) with `slider_w ≈ 480-230`. Per X-8's direction, make BOTH sliders
  teal (`dk.TEAL`) — drop the green/orange split; orange stays reserved for
  warning states.
- **Tabview:** `parameditor.build_tabbed(parent, tabs, make, x=8, y=104,
  w=1008, h=_panel_h()-112, tab_bar=140)` where `_panel_h()` is the existing
  helper (544). Exactly the Sound editor's call shape (`rack._render_sound`).
  Since the pages hold plain `dk.row` cards rather than ParamEditor defs,
  either (a) generalize `build_tabbed` to accept `(label, builder_fn)` pairs,
  or (b) write a 20-line local `_settings_tabs()` that clones its tabview
  setup + deferred-fill loop. (a) is preferred — the deferred one-tab-per-tick
  fill is the part that must be kept (it's the WDT-safety pattern; see
  build_tabbed's comment block).
- Tab pages: `set_flex_flow(COLUMN)`, `pad_row 12`, scroll disabled — each
  page below fits with slack; nothing needs to scroll.

## 3. Tab contents (complete inventory mapping)

Every control from `_build()` is accounted for below. Rows keep their current
implementations (same callbacks, same live/commit split) — this is a MOVE, not
a rewrite, except where noted.

### Persistent strip (always visible)
| Control | From | Notes |
|---|---|---|
| Volume slider + live readout | left col item 2 | `_val_slider`, live `_amy_volume`, commit on release. Color → TEAL. |
| Brightness slider + live readout | left col item 3 | `_val_slider`, live `tulip.brightness` + `_reload_saver` commit. Color → TEAL. |

### Tab 1 — **Network** (default tab)
| Control | From | Notes |
|---|---|---|
| Wi-Fi card (status+IP, ssid, password, Connect, eye, keyboard) | left col item 1 | Move the whole `wcard` unchanged (214px). Full 836px width available — widen the text fields to ~420px; the Connect/eye/kb cluster stops crowding the fields. |
| Set time | System row button | MOVED here: it requires Wi-Fi (its own toast says "Need Wi-Fi") and semantically it is "time comes from the network". Full-width row: label "Time" + `Set time now` button + the 24-hour toggle (next line). |
| 24-hour clock switch | left col item 4 (shared row) | Joins Set time in a "Time" group — clock format lives with clock setting. Unshares the awkward clock+Debug row. |

Default tab = Network because it's the panel's one glanceable status
("connected 192.168.4.250") and the most common first-run task. Page total:
~214 + 60 + 60 + gaps ≈ 350px < 384. Fits, no scroll.

### Tab 2 — **Display**
| Control | From | Notes |
|---|---|---|
| Dim after (dropdown) | right col item 3 | Unchanged. Inside the tab page these rows sit high enough that `dd.set_dir(lv.DIR.TOP)` is no longer needed — open direction can be default/auto (compute from position, or just leave TOP; either works). |
| Sleep after (dropdown) | right col item 4 | Unchanged. |
| Smooth UI (partial buffer) switch + subtitle | right col item 1 | Unchanged (88px row). |
| V-sync (tear-free) switch | right col item 2 | Unchanged. |
| Terminal font Small/Medium/Large | left col item 5 | Unchanged. It's a display/legibility setting. |

Page total: 60+60+88+60+56 + gaps ≈ 372px ≤ 384. Fits, no scroll. If a future
row overflows, the page is already a flex column — enable `scroll_dir(VER)`
on THIS page only (a one-page scroll is still far cheaper than the old
full-panel scroller).

### Tab 3 — **System**
| Control | From | Notes |
|---|---|---|
| MPE global gate switch | right col item 5 | Keep the switch; ADD the one-line MUTED subtitle "per-note expression; shows MPE controls on instruments" (the gate currently explains nothing — cheap discoverability win). |
| Debug switch | left col item 4 (shared row) | Joins System: it's diagnostics (top-bar RAM readout, logging), not a clock setting. Own row with a MUTED subtitle "status-bar RAM readout + verbose log". |
| Calibrate | System row button | Row: label "Touch" + `Calibrate` button. |
| Upgrade | System row button | Row: label "Firmware" + `Upgrade` button (stays SURFACE2 per X-5). |
| Restart | Power row | Keep in one "Power" row… |
| Factory reset | Power row | …with Factory reset LAST, right-aligned, keeping its X-5 red border/text + two-tap arm. The tab gives it breathing room; do NOT let any other row sit below it. |

Page total: ~76+76+64+64+64 + gaps ≈ 380px ≤ 384. Fits.

## 4. Promotions / demotions (explicit)

- **PROMOTED:** Volume, Brightness — out of the grid entirely, pinned
  above the tabs, visible in every tab state. (Owner constraint; also usage
  reality: they're the two controls a performer touches mid-set.)
- **PROMOTED (visibility):** Wi-Fi status line — as the default tab's first
  card it remains the first thing seen on open, same as today.
- **DEMOTED:** Upgrade, Calibrate, Restart, Factory reset — one tab away.
  Correct: all four are rare, careful actions; none belongs at the same
  visual level as daily controls. Factory reset keeps its two-tap arm + red
  treatment, so the demotion adds a third safety layer (deliberate tab visit).
- **MOVED for semantics:** Set time + 24-hour clock unify under Network/Time;
  Debug joins System. This kills the panel's one incoherent row (clock+Debug
  sharing a card purely for space).
- **UNCHANGED:** nothing is removed; every current capability keeps a home.

## 5. Implementation notes for the fix agent

1. Restructure `settings._build(body, right, cw, screen)` into per-group
   builders: `_build_wifi(page, screen)`, `_build_time(page, screen)`,
   `_build_display(page)`, `_build_system(page, screen)`, plus
   `_build_strip(parent, cw)` for Volume/Brightness. Each takes a flex-column
   parent and adds `dk.row` cards at `lv.pct(100)` width — the existing row
   code moves verbatim.
2. `panel(parent, shell)` becomes: strip at `(24, 8)`; tabview at
   `(8, 104)` via the generalized `build_tabbed` with
   `[("Network", ...), ("Display", ...), ("System", ...)]`. Keep
   build-first-tab-synchronously + defer-the-rest (the WDT-safety property).
3. The legacy standalone `run()` path (launcher entry, taller header) should
   reuse the same group builders stacked in its existing single
   `dk.scroll_col` — zero duplicate control code, no tabview needed there.
4. Keyboard: the Wi-Fi fields are now inside a tab page. `dk.close_keyboard()`
   must fire on tab CHANGE as well as panel pop — add the tabview's
   `VALUE_CHANGED` event → `dk.close_keyboard()`, otherwise switching tabs
   with the keyboard up strands it over a hidden textarea (the exact
   use-after-free family that hard-crashed Wi-Fi settings before; see
   `homeshell.back()`'s comment).
5. Toasts (`dk.toast(screen, ...)`) keep working unchanged — they attach to
   the screen, not the panel.
6. Repaint cost check (acceptance): switching tabs must repaint only the
   content page (~836x384); the strip and rail must not flash. Verify
   on-device with the debug meter, same as the FX editor was.
7. Acceptance screenshots: one per tab + one mid-drag on Volume proving the
   strip stays live in every tab state; keyboard-up-then-tab-switch must not
   crash (point 4).

## 6. What I deliberately did NOT do

- **No 4th tab.** An "Audio" tab (Volume + MPE) was tempting, but Volume is
  constraint-pinned to the strip and a one-switch tab is waste. MPE sits fine
  in System with a subtitle.
- **No always-visible Wi-Fi status chip in the strip.** The top bar already
  shows wifi-on/offline globally; duplicating IP in the strip spends the
  strip's space on read-only info. The default tab shows it instead.
- **No dropdown-to-segmented conversions or other control swaps.** "No new
  widget types" is the constraint and the existing controls all work; this is
  a reorganization, not a redesign.
