# ux-review-12 — round 3: final verification of UX11-1..7

- **Build under test:** ux-round2 @ 61a1c52b ("deck: implement UX round-2
  findings"), device TULIP4_R11, COM11.
- **Session:** 2026-07-18 21:30–21:45 (device clock). WDT count: **zero**.
- **Config:** backed up (sha8 fc0804c0, still byte-identical to rounds 1/2),
  restored + clean reboot at the end; /user/_drv.py + /user/_s.png deleted;
  throwaway instrument "Tulip 2" and preset "Tulip" created and fully
  removed.

## Verdicts

| Finding | Verdict | Evidence |
|---|---|---|
| UX11-1 profiler bars | **VERIFIED** — track now visible (SURFACE2 full width), 1%/4% core loads render as short green bars, not circles; memory bars gain context too | `shots/07-profiler-bars-fixed.png` (vs r2 shot 03) |
| UX11-2 voice chip accounting | **VERIFIED** — chip: 10/32 → add Juno throwaway 20/32 → type→Drums **11/32** immediately (editor row "1 (fixed by kit)" agrees) → remove → 10/32 | on-device chip text at each step; also visible in `shots/01-kit-picker-noshadow.png` top bar |
| UX11-3 "Use selected" gating | **VERIFIED** — builds DISABLED + visually dimmed; enables on hit selection (state checked on open) | `shots/03-swap-current-highlight.png` (dimmed button) |
| UX11-4 current-hit highlight | **VERIFIED** — opening the acoustic pack marks the pad's current hit (`jazzkick`) in the ACCENT blue used by the patch/kit pickers | `shots/03-swap-current-highlight.png` |
| UX11-5 preset detail sound name | **VERIFIED** — subtitle "Juno-6 - A11 Brass Set 1" (was "patch 0") | `shots/06-preset-detail-soundname.png` |
| UX11-6 pad editor legibility | **VERIFIED** — Tune/Decay/Level/Snap readouts WHITE + mono, hit name in TEXT color | `shots/02-pad-editor-readable.png` |
| UX11-7 triangular row artifact | **IMPLEMENTED, ARTIFACT PERSISTS — finding was MISDIAGNOSED (mine, round 2)** — see UX12-1 | `shots/04-triangle-compare-r2-vs-r3.png`, `shots/05-kitrows-compare-r2-vs-r3.png` |

### UX11-7 detail (honest correction)
The round-2 diagnosis ("LVGL button drop-shadow") was wrong. The implemented
fix (dk.button zeroes shadow_width/shadow_opa) is in place and confirmed
live (`get_style_shadow_width(0) == 0`, `shadow_opa == 0` on a hit row) and
is harmless/correct per the flat-design policy — but the visible "triangle"
is **not a shadow**. Magnified crops show it is the dark column background
showing through the small inter-row gap, funneled by adjacent rows' corner
radii, quantized on RGB332 — i.e. a spacing/contrast artifact, present in
round 2 and round 3 alike (kit rows are pixel-identical across rounds at
3x magnification; the swap picker's tighter lists still show the funnels).

## New finding

### UX12-1 (NIT) — Swap/pack list row gaps read as dark "funnel" notches
- **Screen:** Pads > Swap (pack + hit columns); faintly on any tight list
  over a contrasting column background.
- **Root cause (corrected):** rows nearly touch (2-4px flex gap); the two
  adjacent rounded corners carve the dark column bg into a horizontal
  wedge that reads as a glitch at 1:1 scale. Not a shadow.
- **Fix options (any one):** widen the row gap to ~8px; or set the pack/hit
  column bg to SURFACE (lower contrast against SURFACE2 rows); or drop the
  row radius to ~4 on these narrow lists. Cost: ZERO.
- **Evidence:** `shots/04-triangle-compare-r2-vs-r3.png` (magnified).

## Convergence
14 of the 15 round-1 findings and 6 of the 7 round-2 findings are verified
fixed on-device; the one remainder (UX11-7/UX12-1) is a NIT with a corrected
one-line-class fix, and UX10-1 (WDT family) is separately owned under #95 —
zero WDTs occurred across rounds 2 and 3 (~75 min of continuous UI driving)
after the Keyboard-tile repro was removed. **Loop declared CONVERGED** aside
from that single NIT, which can ride along with any future change.

## Restoration
- Config restored byte-for-byte (sha8 fc0804c0) before the final reboot;
  verified after boot. Debug flag (left on in RAM by a failed switch-coord
  lookup) cleared by the restore+reboot.
- /user/_drv.py, /user/_s.png deleted; throwaway instrument + preset gone.
- Final state: Home, Wi-Fi up, chip "Tulip 10/32", instrument
  "Tulip ch4 A11 Brass Set 1", no debug readout.
