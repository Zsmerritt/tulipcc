# Deck docket (future work)

Parked items, not yet started. Captured from bench sessions.

## 1. Make Home the REAL root (relegate the REPL to a "Terminal" app) — DONE

Implemented in `ui_patch.py` (Home has no quit button; apps quit back to Home
falling back to the REPL; the REPL/Terminal gets a Home button). See the
"Home is the root" section of `README.md`. Original notes below.

Today the built-in REPL `UIScreen` is the structural root: it can't be quit, and
the power/quit button on any app (incl. Home) returns to the REPL — so the REPL
reads as "default" and Home as a secondary app.

Goal: **Home is the root** (no power/quit button — you don't close the root), and
the **REPL becomes a switchable "Terminal" app** with the power button. The REPL
must keep *running* (everything depends on it) — this is purely presentation:
- Home: hide the task-bar quit/power button (it's the root you return to).
- App quit: return to **Home** instead of the REPL (`screen_quit_callback`).
- REPL: reachable via the Home "Terminal" tile (already exists) and given a
  "back to Home" affordance instead of being the fallback.

Feasible at the **deck/ui_patch layer** (monkeypatch the task bar + quit target),
so it survives `tulip.upgrade()` — no firmware change needed. Verify Home can't be
orphaned (always a way back) and control-Q/control-Tab still behave sanely.

## 2. Better multi-app management / navigation — IN PROGRESS (top-bar + tile grid)

We don't love the built-in "app switcher (shuffle) + power (quit)" task-bar model.
**Chosen design (after prototyping left-rail vs top-bar): a top-bar + tile-grid
home shell**, KlipperScreen-flavored:
- **Top bar** (full width, ~56px): left = Home/breadcrumb + a `‹ Back` that
  appears only when drilled into a panel; center = **one tappable chip per fleet
  instance** (Tulip + each AMYboard), `name preset` with the active one
  highlighted, tap → that instance's Instrument panel; right = wifi + clock.
- **Tile grid** below (the launcher). Fleet tile shows a live board-count
  subtitle (`internal only` / `N boards`).
- **Firmware task bar fully hidden on Home** (no shuffle, no power) — the shell is
  the sole nav. `ui_patch.py` strips quit + alttab + launcher on the root.
- **Panel-stack navigation** (push/pop, one-level-at-a-time Back). Fleet-centric
  apps (Instrument, MPE, Fleet) open as **in-shell panels** so the bar + chips
  stay visible while editing; heavy/borrowed apps (Editor, Wordpad, Tulip World,
  Terminal, Keyboard) stay separate `tulip.run` UIScreens.

Files: `homeshell.py` (LVGL shell), `shellmodel.py` (pure, unit-tested:
`chip_specs`, `PanelStack`, push/rebuild logic), reworked `home.py` +
`instrument.py`. Status: M1 (shell) + M2 (Instrument panel + chip round-trip)
validated; M3 (Fleet + MPE panels + polish) in progress. Per-instance chips are
the answer to "show every board's instrument/preset equally, not just the Tulip."

## 3. Box build — hardware direction (for the eventual enclosure)

Goal: Tulip + up to ~2 AMYboards permanently connected in one box, screen
accessible, required ports on the outside. USB stays (per-board full 16-ch MIDI
stream for MPE in all modes + individual per-board parameter control; TRS can't do
per-board MPE, and enroll-then-unplug is out — enrollment is over the live USB via
per-device SysEx).

- **Data hub:** a **single-TT USB 2.0 hub** (single hub tier = fits the channel
  budget). Buying cheap off Amazon is fine over a custom PCB: look for **"USB 2.0"
  (not 3.x), 4-port** (4-port ≈ single chip = single-TT). Avoid 7/10-port towers
  (cascaded = the failure mode). Bus-powered is OK here because the boards draw
  power from pins, not the hub — downstream only needs VBUS-sense. Candidate:
  **SABRENT HB-MCRM** (4-port USB 2.0, bus-powered, captive USB-A). **Verify
  single-TT from the enumeration log** (one hub address; both boards
  `Claiming … MIDI device`) — can't tell from a listing.
- **Power:** one clean **5V** source. Prefer a **native-5V** feed (5V/3A USB-C PD
  head or a 5V barrel brick) — budget is ~2.5–3A. **Avoid bucking in the normal
  path** (cheap-buck switching noise couples into audio); keep a **20V PD head +
  buck as break-glass backup only**, and if used, filter it (LC + low-ESR caps,
  ferrite, star ground, or a post LDO on the analog rail).
- **Power topology:** 5V → Tulip 5V rail + boards' power pins; **USB host port →
  hub → boards for data only** (VBUS just for sense). Top USB-C stays free for dev
  — or use WiFi (Tulip World / local server) for updates so power never unplugs.
  Injecting 5V at the Tulip rail via a pin/pad bypasses USB input protection —
  solder-header-in-a-box move, not casual. (TODO: trace exact 5V input pad from
  the Tulip4 schematic if going the pin-inject route.)
- **Enclosure ports:** the top USB-C (charging/UART/power/REPL) must be exposed on
  the case — **panel-mount USB-C pigtail** (male→female, nut-mounted) or keystone
  coupler, kept short/shielded. Hub + boards live inside.
- **Ceiling:** the ESP32-S3 host has ~8 DWC channels → realistically **2–3 boards
  max** over one host. Beyond that needs a second transport (Alles/WiFi mesh) or a
  second host — design around it now.

## Constraints / notes
- **USB serial support must stay** on the AMYboards (no MIDI-only USB descriptor).
  So the fleet-over-hub scaling lever is the **hub**, not the board's endpoints:
  the ESP32-S3 USB host has ~8 DWC channels; the Tulip opens 2 (MIDI in/out) per
  board, but a **multi-level/cascaded hub** eats extra channels and blocks the 2nd
  board. Use a **single-level (single-chip) hub**. (Diagnosed from the enumeration
  log: `HCD DWC: No more HCD channels available`, hubs at addr 1 and 3.)
