# UX Review 9 — full app crawl, deck UI on the live device (COM11)

Reviewer brief: crawl every reachable screen of the deck UI, screenshot each,
and judge the Gemini-authored UX. Method: driven on the physical Tulip via the
no-reset harness (`qexec.py` / a file-mode `qget2.py` — see "Harness notes"),
screenshots to `/user/shot.png` fetched to `shots/`. 29 screenshots, `s01`–`s29`.
Panels were driven programmatically through the live `home._shell`
(push/reset_to_root) and, to review each engine editor without persisting
config, the single instrument's `type`/`kit`/`mpe` were changed **in RAM only**
(`deckcfg.set_instrument(..., flush=False)`, never `flush()`).

**Bottom line up front.** The Gemini UX is, for the most part, genuinely good
and coherent — a consistent two-column editor across all six engine types, a
single navigation chrome (Back + breadcrumb + chips) everywhere, well-gated
destructive confirms, masked Wi-Fi credentials, a one-screen Settings, an
excellent MPE channel-map visualization, and a soft keyboard that correctly
collapses its host header to show results while typing. I ran the full crawl
with **zero watchdog resets** (`reset_cause` stayed `2` throughout) and left the
device byte-identical to how I found it (on-disk `deck_config.json` diff empty).

But there is **one real functional bug**: the **Swap-hit picker is broken** — its
action row (Use selected / Kit default) is drawn behind the browser and is
invisible and unclickable, so you cannot actually swap a pad's hit. That's the
one blocker. Everything else is polish: RGB332 color muddiness on disabled
controls and favorite stars (the review-8 R-1 class, still unresolved), the
76-kit picker's lack of search/grouping, missing value readouts on the pad
sliders, and a Factory-reset button that looks as harmless as Restart.

So: **do not trust it blindly — it needs the Swap-hit fix before that feature
ships, plus a short polish pass — but the overall structure and most surfaces
are solid.** Nothing I found is a crash or a data-loss risk in normal use.

---

## Top findings by severity

### HIGH
- **X-1 · Swap-hit picker action buttons are hidden and unclickable.**
  `swap_panel` adds two `dk.row()`s directly to the pushed panel, which has no
  flex-column layout, so both land at `(0,0)` and overlap (verified on-device:
  child0 = action row 1024×64 @ (0,0), child1 = browser body 1024×448 @ (0,0);
  the opaque body draws last and covers the row). "Use selected" and "Kit
  default" — the only ways to commit or reset a swap — are invisible and their
  taps land on the body underneath. The feature is non-functional; only Back
  (cancel) works. `s15`. Fix: lay the parent out as a column (or set explicit
  y-positions), the pattern the other panels already use.

### MEDIUM
- **X-2 · Pad sliders have no value readouts.** Tune/Decay/Level/Snap show only
  a knob — no number or unit — unlike every other slider in the app (Sound/FX
  show live '%' readouts). You tune blind. `s14`.
- **X-3 · Kit picker doesn't scale to 76 kits.** No search (the Patch picker
  has one), no sampled/synth divider, and 69 synth kits each suffixed "(synth)"
  with heavy variant fan-out ("Acoustic B/C/D") — ~11 screens of near-identical
  rows to scroll. `s11`, `s12`.
- **X-4 · Disabled controls + favorite stars render muddy olive in RGB332
  (review-8 R-1, still present).** Files' Run/Edit/Delete bar is alpha-dimmed
  (`opa 102`) which quantizes GREEN/ACCENT to olive; favorite stars use
  `ORANGE=(228,132,44)` which quantizes to olive and sits near-isoluminant with
  the empty MUTED star — you can't tell starred from unstarred. `s23`, `s05`,
  `s22`. Fix with explicit flat disabled colors + an RGB332-safe star color.
- **X-5 · Factory reset looks identical to Restart.** Same neutral grey; the
  device-wiping action carries no warning weight while the rare 'Upgrade' button
  is the brightest thing in Settings. `s20`.

### LOW / NIT
- **X-6 (low) · Center device chip reads as a stuck progress bar** — brightest
  chrome element + a dark near-full inset track. `s01`, `s29`.
- **X-7 (low) · Pad 'Swap >' button is SURFACE2 on a SURFACE2 row** — no
  affordance. `s14`.
- **X-8 (nit) · Inconsistent slider accent colors** (green/orange/blue/teal) with
  no system; orange doubles as the meter's "hot" warning. `s20`, `s19`, `s04`.
- **X-9 (nit) · Welcome shows an orphaned '< Back' button** (verify on real
  first boot). `s27`.
- **X-10 (nit) · Welcome cards float title/subtitle to opposite edges.** `s27`.
- **X-11 (nit) · Double-space artifacts** ("ch1  A11", "BRASS  1", "  >"). `s01`,
  `s06`, `s02`.
- **X-12 (nit) · Submenu tiles reuse green for adjacent items.** `s25`, `s26`.
- **X-13 (low, firmware) · Recurring littlefs "Corrupted dir pair {0x1,0x0}"**
  console error on /user reads/writes. Non-fatal, no WDT, but flag to firmware —
  not deck-UI code.

---

## What I verified as GOOD (do not re-open)

- **Two-column instrument editor** is consistent and clean across **all six
  engines** — Juno-6, DX7, Piano, GM Bank, E-mu GM, Drums (`s02`, `s06`–`s10`,
  `s13`). Drums correctly swaps the right column (Kit + Pads/none) for the synth
  vs sampled case.
- **Reverb send** sits correctly in the Sound editor's VCA/Level tab as a proper
  value card (teal track, green '%' readout), not floating loose. `s04`.
- **FX (device) editor** — Reverb/Chorus/Echo/EQ left-tab rail, consistent with
  the Sound editor, clear "shared by N instruments" subtitle. `s17`.
- **MPE panel** — genuinely well done: enable toggle with a live
  master/member-channel summary, a channel-map strip (master/members/conflict
  legend), and a "Zone fits" readout. Off-state is a clear one-line pointer.
  `s18`, `s19`.
- **Settings** — everything on one screen, two columns, Wi-Fi status + IP live,
  credentials never rendered ("(saved)" + masked), 24h-clock toggle. `s20`.
- **Soft keyboard** — collapses the patch picker header on field focus and lifts
  search to the top so 2–3 results stay visible while typing. `s22` (the earlier
  `s21` non-collapsed shot was a harness artifact — my programmatic
  `open_keyboard_for` didn't fire the field's FOCUSED event that triggers the
  collapse; the real tap path works).
- **Confirm dialog** — modal scrim, "can't be undone" copy, Cancel-left /
  red-destructive-right. `s28`.
- **Files** — path breadcrumb, folder/file icons, right-aligned sizes,
  capability-gated actions (the olive dim is the only wart, X-4). `s23`.
- **Type / Kit / Patch pickers, Devices, System, Apps** — consistent full-width
  list / tile styles, clear selection highlighting. `s24`, `s11`, `s05`, `s16`,
  `s25`, `s26`.
- **Robustness** — full crawl (deep nav, tab switches, keyboard open/close,
  modal, engine switches, screen swap to Welcome and back) with **no watchdog
  reset**; disk config returned byte-identical.

---

## Screenshot index

| Shot | Screen |
|------|--------|
| s01 | Home (rack root) |
| s02 | Instrument editor — Juno-6 |
| s03 | Sound editor — Juno-6, Basic, DCO tab |
| s04 | Sound editor — Juno-6, VCA tab (reverb send) |
| s05 | Patch picker — Juno-6 |
| s06 | Instrument editor — DX7 |
| s07 | Instrument editor — Piano |
| s08 | Instrument editor — GM Bank |
| s09 | Instrument editor — E-mu GM |
| s10 | Instrument editor — Drums (sampled TR-808) |
| s11 | Kit picker (top) |
| s12 | Kit picker (sampled→synth transition) |
| s13 | Instrument editor — Drums (synth kit; Pads row) |
| s14 | Pads editor |
| s15 | **Swap-hit picker (broken — X-1)** |
| s16 | Devices panel |
| s17 | FX (device) editor — Reverb tab |
| s18 | MPE panel (gate off) |
| s19 | MPE panel (gate on) |
| s20 | Settings (both columns) |
| s21 | Keyboard (not collapsed — harness artifact) |
| s22 | Keyboard (collapsed, correct behavior) |
| s23 | Files |
| s24 | Type picker |
| s25 | System submenu |
| s26 | Apps submenu |
| s27 | Welcome (onboarding) |
| s28 | Confirm dialog (Remove) |
| s29 | Home (final, device restored) |

---

## Harness notes (for the next reviewer)

- **Root cause of the qget failures:** Git Bash's MSYS path conversion rewrites a
  POSIX arg like `/user/shot.png` into `C:/Program Files/Git/user/shot.png`
  before it reaches Python, so the device `open()` hit ENOENT. Prefix device
  commands with `MSYS_NO_PATHCONV=1` (or run from PowerShell). The stock
  `qget.py` also mangles its multiline read code through Windows subprocess arg
  passing — I added `qget2.py` (file-mode, LF endings) in the scratchpad; use it.
- **Base64 fetches occasionally drop a chunk** (corrupt PNG); re-run the fetch —
  the device file is intact. I validate each PNG with `PIL Image.load()`.
- **A stray leftover `qexec.py` (reverb-triage profiler) held COM11** at the
  start; its read loop self-releases at the 180s cap. Wait it out rather than
  killing another process.
- Config backup captured to `scratchpad/cfg_backup.txt` before touching anything;
  disk verified identical at the end.

## Verdict

Approve the structure; **block only on X-1** (Swap-hit picker) for the drum
synth-kit feature, and schedule the medium polish set (X-2..X-5) — most of which
is the recurring RGB332 color problem the fix loop has now bounced on three
times (review-8 R-1). The Gemini work is better than "needs a lot of work," but
it is not "perfect": ship the good surfaces, fix the one broken one.
