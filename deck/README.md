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
| `deckui.py` | Shared design system: dark RGB332-safe palette, fonts, cards, rows, sliders, buttons. |
| `deckcfg.py` | Central config at `/user/deck_config.json`; `apply()` restores everything on boot. |
| `home.py` | Full-screen launcher grid. Auto-discovers runnable apps in `/user`. |
| `settings.py` | Wi-Fi, volume, brightness, terminal font, **menu button size**, set-time, calibrate, upgrade. |
| `instrument.py` | Picks the single synth patch for MIDI channel 1 (Juno-6 / DX7 / Piano), with live preview. |
| `mpe.py` | Enable/disable MPE and edit members, bend range, zone and per-note expression. |
| `files.py` | Touch file browser for `/user` — Run / Edit / Delete. |
| `welcome.py` | First-boot onboarding (shown once). |
| `ui_patch.py` | Runtime patch: Home-as-root task bar (no quit button on Home; apps quit back to Home), bigger switch/quit buttons + launcher menu with the deck apps. |
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
