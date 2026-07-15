# UX Review 7 — verification pass over the UX-REVIEW-6 fix round (COM11)

Verifies commits `24b7f6ea` (C1/H1/H2/M1–M4/L1 fixes), `971ad092` (S1–S3
structural rework), `c5cb4253` (2026 pass) against the findings in
`../ux-review-6/`. Everything below was re-driven on the **live device**;
screenshots in `shots/` (`t01`–`t21`), machine-readable statuses in
`findings.json`. The fix agent declared one deliberate skip — the audition
waveform in the patch picker — and I **endorse that skip**: an audio tap
through new C plumbing and cross-core buffer copies is a bad trade for a
decoration, and I'd skip the fake-envelope stand-in too.

**Bottom line up front: this is a strong round — most of it verified exactly
as claimed, and the structural rework (rack-as-home, two-column editors,
Settings/Files as shell panels) landed cleanly. Two things still block
"done": (1) the device still watchdog-reboots — I caught one live, now
provably tagged `reset_cause=WDT` by the new logging, triggered by opening
the keyboard on the patch picker; and (2) the green/blue switch flip-flop
(M3) is NOT fixed — and this pass root-caused it: the style is applied
correctly but deferred-refill panels render stale pixels until something
invalidates the screen. The fix is one line.** Everything else outstanding is
small (placeholder contrast, disabled-state colors, one-visible-result-row).

---

## 1. Scorecard vs ux-review-6 findings

| ID | Was | Status now | Evidence |
|----|-----|-----------|----------|
| C1 Settings dead | Critical | **FIXED** | `t10_settings`, `t11_settings_bottom` — opens as a shell panel (S3), full content, volume slider present; `_amy_volume()` fallback deployed. |
| H1 WDT reboots | High | **PARTIAL** — observability fixed, reboots not gone | `reset_cause=WDT` logged live this session (worked exactly as designed). But a fresh WDT reboot fired on keyboard-open in the patch picker (see **NEW-1**). Chunked tab builds held: Sound/FX opened without incident this pass. |
| H2 clipped text fields | High | **MOSTLY FIXED** | Name field renders centered (`t02_editor`); typed search text clean (`t05_search_kb`). Residual: search + Wi-Fi **placeholders still dim** and marginal at arm's length (`t04_patch_kb`, `t10_settings`) — see NEW-4. |
| M1 rename flash-per-keystroke | Med | **FIXED (code-verified)** | Cache-per-keystroke + flush on defocus/ready in deployed source. Not keystroke-driven live, but the code path is the one specified. |
| M2 MPE dead-looking-alive controls | Med | **FIXED**, with a residual | `t07_mpe_off`: dependent cards dim + DISABLED, status beside the switch. Bonus beyond spec: **Channel map with live conflict detection** (`t08_mpe_on` flags ch 2 red + "Zone overlaps ch 2" warning) — genuinely great. Residual: the disabled recolor produces **alarm hues** (see NEW-3). |
| M3 green/blue switch flip-flop | Med | **NOT FIXED — root-caused this pass** | `t01_root` fresh = green; `t16_root_after_refill` = blue **with the fixed two-selector style deployed and the live widget's resolved indicator color reading green (63,191,127)**. Forced `lv.screen_active().invalidate()` → renders green (`t17_after_invalidate`). So: panels rebuilt in `_schedule_refill`'s deferred tick draw stale pixels; the widget-level style fix cannot work. **Fix: `h.invalidate()` after `self._fill(h, b)` in `homeshell._schedule_refill._do`.** May also cure other subtle post-Back staleness. |
| M4 one result row under keyboard | Med | **PARTIAL** | List now resizes above the keyboard and scrolls (`t05_search_kb` scrollbar) instead of being occluded — real improvement. But **still exactly one visible row**, because the header (big title + current + Favorites + search) eats the rest. Collapse the title/Favorites block while the keyboard is up → 3 rows visible (see NEW-5). |
| L1 keyboard whiplash | Low | **FIXED** | `t04_patch_kb` — keyboard fully restyled into the deck palette. Night and day. |
| L2 stale duplicate picker title | Low | **FIXED** | `t04_patch_kb`: stable "Juno-6 patches" + "current: A11 Brass Set 1". |
| L3 legacy tile clipped | Low | **FIXED** | `t20_apps`: "Drums (legacy)" fits. |
| L4 submenu top-left cluster | Low | **FIXED** | `t09_system`, `t20_apps`: centered grids, icons. |
| L5 Files footer | Low | **FIXED**, two nits | `t12_files`/`t13_files_sel`: shell chrome, Back consistent, buttons disabled until selection, filename shown, Run green / Edit accent on select. Nits: **Delete renders muted purple, not the destructive red**, and the footer filename is placeholder-dim. |
| L6 silent type reset | Low | **FIXED** | `t06_type`: "Changing type resets the patch and sound edits." |
| L7 unitless / min-reads-broken | Low | **FIXED** | `t18_sound`: "50 %" mono readout + **min/max microlabels (50 %…99 %)** at track ends. Exactly right. |
| L8 32 KB alloc failure on keyboard open | Low | **NOT FIXED** — now implicated in NEW-1 | Fired on every keyboard open again this pass. |
| L9 "Kits" as a Type name | Low | **NOT ADDRESSED** | `t06_type` still Juno-6/DX7/Piano/**Kits**. Standing recommendation: type = "Drums", patch row = "Kit …". |
| L10 "Tulip / Tulip ch1…" stutter | Low | **NOT ADDRESSED** | `t01_root` subtitles still lead with the device name under an identical row title. |
| N1 wrong clock | Nit | **FIXED** | `t01_root`: "--:--". |
| N2 backdrop tile-soup | Nit | **IMPROVED** | `t19_reset_backdrop`: uniform dark; one teal ghost tile survives (acceptable). |
| N3 knob overflows card corner | Nit | **NOT FIXED** | `t18_sound` min-knob still pokes over the card edge. |
| N4 inactive tab contrast | Nit | **NOT FIXED** | `t18_sound` VCF/ENV/LFO/VCA unchanged. |
| N5 ASCII `->` / `--` in copy | Nit | **NOT FIXED** (and the new conflict warning adds another "--") | `t08_mpe_on`, `t21_welcome`. |
| N6 Welcome fake-button cards | Nit | **NOT ADDRESSED** | `t21_welcome` unchanged. |
| N7 submenu label color drift | Nit | **RESOLVED** | `t09_system` labels read clean white with icons. |
| S1 rack-as-home | Structural | **DONE** | `t01_root`: rack is the root, Devices/System footer with icons, status line in former dead space, Back correctly absent at root. |
| S2 two-column editors | Structural | **DONE** | `t02_editor`: editor fits with zero scrolling; MPE is paired cards + full-width channel map (`t08_mpe_on`). |
| S3 Settings/Files as shell panels | Structural | **DONE** | `t10_settings`, `t12_files`: uniform Back/breadcrumb; a future build failure now lands on the shell's visible panel-error surface. |
| MOD (2026 pass) | — | **LANDED (cheap tier)** | MIDI monitor live-verified decoding notes/CC/bend with counter (`t15_midimon_live`); icons on rows/tiles; root status line; mono value readouts; 1 px card edges. Chip voice-meter/activity code present (`forwarder.live_voices()`/`activity()` respond; idle = 0 during this bench pass). Waveform skip endorsed. |

## 2. New findings (this pass)

**NEW-1 (High) · Keyboard-open on the patch picker can still WDT-reboot the
device — now with proof.** First attempt this session: focus search →
auto-keyboard → device rebooted; `deck.log` shows `=== deck boot ===
reset_cause=WDT` (the new logging earning its keep). Second attempt: survived.
Intermittent ≈1-in-2 on this bench. The constant companion: `alloc failed
size 32768 … internal 8bit` fires on **every** keyboard open (L8). Strong
hypothesis: the keyboard-flash fix (`cb5f7e3f`) switches to PARTIAL rendering
with an **internal-SRAM draw buffer while the keyboard is up** — that 32 KB
internal alloc is failing, dropping the render into a slow fallback exactly
when the keyboard build + M4 list-resize churn peaks, opening the WDT window.
*Fix directions:* (a) make the 32 KB buffer allocation succeed (reserve it at
boot instead of allocating on open) or degrade gracefully; (b) stop rebuilding
the full patch list on keyboard open/resize — resize the existing `listbody`
only; (c) window the patch list (build ~20 rows, fill on scroll).

**NEW-2 (Med) · Deferred-refill panels render stale pixels — root cause of M3,
one-line fix.** Style state on rebuilt widgets is correct (probed live:
indicator = green) but the screen shows the pre-style pixels until a manual
`invalidate()` (`t16` vs `t17`). Add `h.invalidate()` after `self._fill(h, b)`
in `homeshell._schedule_refill._do`. Then remove nothing — the two-selector
switch styling can stay as belt-and-braces.

**NEW-3 (Med) · Disabled-state recolor produces alarm hues.** Disabled MPE
sliders render **magenta / dark-red / olive** tracks (`t07_mpe_off`) — a
disabled pitch-bend slider reading crimson is the danger color vocabulary on
an inactive control. Same mechanism muddies the Files footer's disabled
buttons (`t12_files`) and breaks the channel-map legend↔chip color match
while disabled. LVGL's DISABLED state gray-mixes the base color and RGB332
quantizes the result into these hues. *Fix:* explicit disabled styles —
track/bg = `SURFACE2`, text = `MUTED` — instead of relying on the theme's
color mix.

**NEW-4 (Low) · Placeholder contrast is still below arm's-length legibility**
in patch search (`t04_patch_kb`) and both Wi-Fi fields (`t10_settings`).
The H2 helper centered the text but placeholders remain dim. One shade fixes
all three (placeholder ≥ `MUTED`; fields sit on `SURFACE2`, so a step lighter).

**NEW-5 (Low) · Keyboard mode should collapse the picker header.** With title
+ current + Favorites + search retained, the resized list shows one row
(`t05_search_kb`). Hide the title/Favorites block while the keyboard is up;
~3 rows become visible and live-search starts feeling live.

**NEW-6 (Nit) · Files: Delete should be red when enabled** (`t13_files_sel`
renders it muted purple — destructive actions own red in this system), and
the footer filename is placeholder-dim.

**NEW-7 (Nit) · MIDI monitor timestamps are raw boot-ticks** ("796943" —
`t15_midimon_live`). Relative seconds (or delta-ms between events, which is
what MIDI debugging actually needs) would read better in the same width.

**Process note:** the fix round was verified by its author with test state
left on the device (a second "Tulip 2" instrument was still configured when
this pass started). Restored to the true pre-review state this pass. Bench
verification should end with the same restore step the review passes use.

## 3. Grades (R1–R11), Δ vs UX-REVIEW-6

| # | Dimension | Grade | Δ | Driver |
|---|-----------|-------|------|--------|
| R1 | Touch ergonomics | **B+** | = | Holds; keyboard row target sizes fine. |
| R2 | Hierarchy & density | **A-** | ↑↑ | Two-column editors, centered submenus, root shows state, no false bottoms left on main paths. |
| R3 | Navigation consistency | **A-** | ↑ | One chrome everywhere now (S3); Back uniform including Files/Settings. |
| R4 | Affordance & feedback | **A-** | ↑ | Disabled states exist (M2), conflict warnings, pressed-sink; residual NEW-3 hues. |
| R5 | State clarity | **B+** | ↑↑ | Range-labeled readouts, live status line, channel map; dragged by M3 staleness (NEW-2) and L10. |
| R6 | Labeling & language | **B+** | ↑ | L3/L6/L7 fixed; "Kits" (L9) and the name stutter (L10) remain. |
| R7 | Consistency of system | **B+** | ↑↑ | Keyboard restyled, Files reskinned, one palette; NEW-3 disabled hues are the residual. |
| R8 | Contrast & legibility | **B** | ↑ | Mono values help; placeholders (NEW-4) and inactive tabs (N4) still thin. |
| R9 | Discoverability | **A-** | = | MIDI monitor is a discoverability gift; Settings reachable again. |
| R10 | Safety | **A-** | ↑ | Confirms + type-reset hint + Files Run/Edit gating. |
| R11 | Robustness | **C+** | ↑ | Settings revived, WDT observability landed, chunked builds held — but a WDT reboot still fired on a main path (NEW-1) and the 32 KB alloc fails on every keyboard open. |

## 4. Verdict

**Approve this round — it did what it said, and the structural work is
genuinely good.** The gap to "done" is narrow and specific: **NEW-1** (make
keyboard-open stop being a coin-flip reboot: fix the 32 KB internal-SRAM
alloc and stop full list rebuilds on resize) and **NEW-2** (one-line
`invalidate()` in the deferred refill — closes M3 for real). Then NEW-3/4/5
in one small styling pass. The remaining carried items (L9 naming, L10
stutter, N3–N6) are a half-day of polish. After NEW-1 and NEW-2, R11 stops
being the story of every review, and the next pass should be the first with
nothing above Low.

Method & restore: driven exactly as pass 6 (mpremote on COM11, tap
simulation, screenshots; one action per exec around heavy panels). Device
restored to the true pre-review state: single Juno-6 instrument ch1, MPE gate
off, temp files removed, rebooted and verified.
