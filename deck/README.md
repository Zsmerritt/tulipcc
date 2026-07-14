# Tulip Deck — a friendlier front-end for the Tulip Creative Computer

A small suite of touch apps that turn Tulip's bare Python-prompt boot into a
polished, navigable UI: a home-screen launcher, a device Settings panel, a
single-instrument picker, an MPE configurator, and a file browser — all sharing
one dark design system.

## Why it lives in `/user`

Tulip has three storage areas. `tulip.upgrade()` reflashes the firmware (OTA
partition) and the `/sys` folder (a separate "system" partition), but it **never
touches `/user`**. So everything here installs into `/user` and is wired up by
`/user/boot.py`. That means **the whole UI survives a firmware upgrade** — no
need to re-flash or re-edit frozen files. The frozen `ui.py` is left untouched;
`ui_patch.py` upgrades the task bar and launcher menu at runtime instead.

## Files

| File | What it is |
|------|------------|
| `deckui.py` | Shared design system: dark RGB332-safe palette, fonts, cards, rows, sliders, buttons, scroll columns. |
| `deckcfg.py` | Central config at `/user/deck_config.json`; `apply()` restores everything on boot. |
| `home.py` | The home screen: a `HomeShell` top bar over a tile grid of built-in + auto-discovered `/user` apps. Wires chip taps and the fleet-centric tiles to in-shell panels. |
| `homeshell.py` | The navigation shell: full-width **top bar** (Back/breadcrumb · per-instance instrument chips · wifi/clock) over a **push/pop panel stack**. The sole nav on Home. |
| `shellmodel.py` | Pure, LVGL-free logic behind the shell (unit-testable under CPython): chip labels/specs, fleet subtitle, and the `PanelStack` push/pop/rebuild bookkeeping. |
| `settings.py` | Wi-Fi, volume, brightness, terminal font, **menu button size**, smooth-UI / v-sync toggles, set-time, calibrate, upgrade. |
| `instrument.py` | Picks a synth patch per instance (Juno-6 / DX7 / Piano) with live preview. `panel()` (in-shell) + `run()` (standalone) entry points. |
| `mpe.py` | Enable/disable MPE, members, bend range, zone, and per-note expression (a pushed sub-panel). `panel()` + `run()` entry points. |
| `fleet.py` | Multi/Stack mode, add/remove boards, per-instance channels, detune/unison, voice priority, rescan. `panel()` + `run()` entry points. |
| `calib.py` | Touch calibration that uses the deck's own LVGL touch path (not the stock blocking `calibrate` script), so taps register and it's always escapable. Persists the delta via `deckcfg`. |
| `files.py` | Touch file browser for `/user` — Run / Edit / Delete. |
| `welcome.py` | First-boot onboarding (shown once). |
| `ui_patch.py` | Runtime patch: makes Home the root and **strips the firmware task bar on Home** (no quit/shuffle/launcher). Apps quit back to Home; the REPL/Terminal keeps a Home button. |
| `boot.py` | Startup glue. Wrapped so a failure can never block the REPL. |

## Deploy

From this folder, with the Tulip connected over USB:

```powershell
./deploy.ps1                # or: ./deploy.ps1 -Port COM7
```

This copies every `*.py` into `/user`. Reboot the Tulip (or `run('home')`).

Requires `mpremote` (`pip install mpremote`).

## Config

Everything the apps change is stored in `/user/deck_config.json` and re-applied
by `boot.py` on the next boot. Delete that file to reset to defaults. To re-run
onboarding: `run('welcome')`.

## The navigation shell

Home is a **top bar over a panel stack** (`homeshell.py`), not the old corner
launcher:

- **Top bar** — left: a `‹ Back` button that appears only once you've drilled into
  a panel, plus a breadcrumb; center: **one tappable chip per fleet instance**
  (Tulip + each AMYboard), showing `name preset` with the active instance
  highlighted — tap a chip to open that instance's Instrument panel; right: a
  wifi indicator + clock. The chips are the answer to showing every board's state
  equally, and they update live as the fleet changes.
- **Panel stack** — the fleet-centric apps (**Instrument, MPE, Fleet**) open as
  **in-shell panels** (`app.panel(parent, shell)`), so the top bar + chips stay
  visible while you edit; **Back** pops one level at a time (e.g. MPE → per-note
  expression sub-panel → back → MPE → back → grid). Heavier/borrowed apps
  (Editor, Wordpad, Tulip World, Terminal, Keyboard) still launch as separate
  UIScreens via `tulip.run`, and each panel app keeps a standalone `run()`
  fallback reachable from the REPL launcher menu.
- The pure stack/chip logic lives in `shellmodel.py` so it unit-tests under
  CPython (`test_deck.py`); `homeshell.py` does the LVGL drawing on top.

## Home is the root; the REPL is a "Terminal" app

The deck boots into **Home**, and Home is the structural root: it has no
power/quit button (you don't close the root), and quitting any other app — via
its power button or `control-Q` — returns to **Home**, not the REPL.

The Python REPL keeps running (everything depends on it) but is now just a
switchable **Terminal** app. Reach it from Home's **Terminal** tile (or the
shuffle button / `control-Tab`); get back via the REPL task bar's **Home**
button, its launcher menu's **Home** entry, or `control-Tab`. The REPL still
can't be quit — and neither can Home — so there's always a way to both.

This is done at runtime in `ui_patch.py` (monkeypatching the frozen `ui.py`),
so it survives `tulip.upgrade()`.
