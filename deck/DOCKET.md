# Deck docket (future work)

Parked items, not yet started. Captured from bench sessions.

## 1. Make Home the REAL root (relegate the REPL to a "Terminal" app)

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

## 2. Better multi-app management / navigation (rethink the switcher+power model)

We don't love the built-in "app switcher (shuffle) + power (quit)" task-bar model.
Explore a cleaner navigation system. **Design-language reference: KlipperScreen**
(3D-printer UI) — NOT a port, just borrow the *feel* and *screen management*:
- big flat touch targets, dark theme, generous spacing (deckui already leans this way);
- a **persistent top/side nav bar**: Home/back + a status area (e.g. active
  instrument, fleet, wifi/clock) always visible;
- **panel/screen-stack navigation** (push/pop) with a clear Back, instead of the
  opaque shuffle-cycle between apps;
- menus as full panels rather than the tiny corner launcher list.

Would replace/augment the LVGL task-bar switch/quit with a deck-owned nav shell.
Prototype a couple of layouts (nav-bar placement: top vs left) before committing.

## Constraints / notes
- **USB serial support must stay** on the AMYboards (no MIDI-only USB descriptor).
  So the fleet-over-hub scaling lever is the **hub**, not the board's endpoints:
  the ESP32-S3 USB host has ~8 DWC channels; the Tulip opens 2 (MIDI in/out) per
  board, but a **multi-level/cascaded hub** eats extra channels and blocks the 2nd
  board. Use a **single-level (single-chip) hub**. (Diagnosed from the enumeration
  log: `HCD DWC: No more HCD channels available`, hubs at addr 1 and 3.)
