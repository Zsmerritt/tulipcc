# ux-review-7 — verification pass over the UX-REVIEW-6 fix round (2026-07-14)

Re-drove the physical Tulip (COM11) to verify commits `24b7f6ea`, `971ad092`,
`c5cb4253` against `../ux-review-6/findings.json`.

| File | What it is |
|---|---|
| `REVIEW.md` | Scorecard for every prior finding (fixed / partial / not fixed), new findings NEW-1…NEW-7, updated R1–R11 grades, verdict. |
| `findings.json` | **Start here if you're a fix agent**: `prior_findings_status` for every ux-review-6 ID + new findings with locations and fixes. |
| `shots/*.png` | 21 device screenshots `t01`–`t21`. |
| `deck_config.backup.json` | Device config as found at the start of this pass (contained the fix round's leftover test instrument; the true user state restored to the device is `../ux-review-6/deck_config.backup.json`). |

## Headlines

- **Round approved** — C1 fixed, S1–S3 landed cleanly, 2026 cheap tier landed,
  most fixes verified exactly as claimed. MIDI monitor verified decoding live.
- **Blocker 1 — NEW-1 (High):** keyboard-open on the patch picker still
  WDT-reboots intermittently (caught live; `reset_cause=WDT` in deck.log).
  The 32 KB internal-SRAM alloc failure fires on *every* keyboard open —
  likely the PARTIAL keyboard draw buffer from `cb5f7e3f`.
- **Blocker 2 — NEW-2 (Med, one-line):** M3 (green/blue switch) root-caused:
  style is correct, deferred-refill panels render stale pixels. Add
  `h.invalidate()` after `_fill` in `homeshell._schedule_refill._do`.
- Then one styling pass: NEW-3 (disabled alarm-hues), NEW-4 (placeholder
  contrast), NEW-5 (collapse picker header under keyboard).
- Audition-waveform skip: **endorsed**.

Device restored: single Juno-6 instrument ch1, MPE off, temp files removed,
rebooted and verified.
