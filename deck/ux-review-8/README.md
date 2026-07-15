# ux-review-8 — verification of the UX-REVIEW-7 fix round + live rounds (2026-07-15)

Re-drove the physical Tulip (COM11, new firmware with `tulip.amy_level()`)
against `../ux-review-7/findings.json` plus the two live-use rounds and the
update-UX work.

| File | What it is |
|---|---|
| `REVIEW.md` | Full scorecard, new-surface reviews, remaining findings R-1…R-5, grades, verdict. |
| `findings.json` | **Start here if you're a fix agent**: statuses for every prior ID + 5 small remaining findings. |
| `shots/*.png` | 18 device screenshots `u01`–`u18`. |
| `deck_config.backup.json` | User's live config as found (returned byte-identical). |

## Headlines

- **Approved — the fix loop has converged.** Zero WDT resets under the exact
  stress that rebooted the device in pass 7; alloc noise gone; M3 dead
  (style-order root cause, verified green on the refill path); every review-7
  item fixed except one styling residual.
- Real audio chip meter verified end-to-end (`tulip.midi_local` chord →
  `amy_level` 0.076 → meter lit).
- Remaining: R-1 disabled controls still render olive/black on-device,
  R-2 possible first-note drop after boot (listen test), R-3/R-4/R-5 copy
  and layout nits. Nothing blocks daily use.
- Method lesson kept in the report: simulated taps missed the
  keyboard-never-typed bug that a human caught — keep alternating agent
  passes with live human rounds.
