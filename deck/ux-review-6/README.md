# ux-review-6 — live-device UX audit of the Tulip Deck (2026-07-14)

Sixth audit in the `deck/UX-REVIEW*.md` series, covering the changes since
UX-REVIEW-5 (commits `085c6392`, `c92162fa`, `cb5f7e3f`, `97e2be9c`). Driven on
the physical Tulip over COM11 with simulated taps + `tulip.screenshot()`.

## Contents (for agents applying fixes)

| File | What it is |
|---|---|
| `REVIEW.md` | The full human-readable review: verified fixes, ranked findings (C1…N7), R1–R11 grades, 2026-modernization gaps, structural recommendations (S1–S3), method. |
| `findings.json` | **Start here if you're a fix agent.** Every finding with severity, evidence screenshots, `location` (file/symbol), suggested fix, and status (new / new-regression / carried). Includes `fix_priority` ordering, `structural` moves, and `modernization_2026` items. |
| `shots/*.png` | 33 device screenshots (1024×600), `s01`–`s33`, referenced by every finding. |
| `deck_config.backup.json` | The device's `/user/deck_config.json` as it was before the audit (already restored to the device — kept for reference). |

## Headlines

- **C1 (Critical):** Settings crashes on open (`settings.py:91`, `live=amy.volume`
  — no such attribute on current firmware). The whole Settings surface is
  unreachable. Regression from the perf commit `97e2be9c`.
- **H1 (High):** Interrupt-WDT reboot reproduced on a normal nav path
  (MPE → expression → back → back → FX). This is the "drops back to Home"
  glitch; `decklog` structurally cannot capture it — log `machine.reset_cause()`
  in the boot marker.
- **H2 (High):** Name field text and search placeholder render vertically
  clipped (`tulip.UIText` metrics) — the round's flagship features look broken.
- The UX-REVIEW-5 fixes (kit labels, unique names, rename, editor identity,
  auto-channel) are **verified working** — keep them.
- Fix order: `C1 → H2 → M4 → M1 → M3 → M2 → Lows → S1/S2 → modernization`.

## Device state

The device was left exactly as found: config restored byte-for-byte, temp
driver files removed, rebooted to Home, verified. `/user/deck.log` was left
in place (its boot markers are H1 evidence).
