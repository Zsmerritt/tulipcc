# ux-review-9 — full-app UX crawl (2026-07-16, overnight agent round)

Fresh senior-UX review of the deck on the physical Tulip (TULIP4_R11,
COM11), after a day of heavy churn: reverb-send semantics moved to the
Sound editor, slider touch-area fix, keyboard transition fix, Restart vs
Factory-reset split, welcome MPE opt-in, pad alternates picker.

| File | What it is |
|---|---|
| `REVIEW.md` | The reviewer's findings, severity-ranked, referencing shots. |
| `findings.json` | Machine-readable findings for the fix agent. |
| `shots/*.png` | Device screenshots captured during the crawl. |

## Device harness (NO-RESET — do not use raw mpremote, it power-cycles the deck)

All device access via the scratchpad tools (paths in the agent brief):

- `python qexec.py <script.py>` / `python qexec.py - "<code>"` — run
  MicroPython on the live deck without resetting it.
- `python qget.py /user/shot.png local.png` — fetch files (base64 over the
  same channel).
- Screenshot: `qexec - "import tulip; tulip.screenshot('/user/shot.png')"`
  then `qget`.
- Tap a widget: find it via the module's state (deck panels keep refs in
  module `_s` dicts) and `obj.send_event(lv.EVENT.CLICKED, None)`, or drive
  navigation through the deck's own API:
  `import homeshell; homeshell...` / `tulip.run('home')` etc.
- The shell singleton lives in the running modules; screens: home, rack
  (instruments), settings, files, padeditor, mpe, welcome.
