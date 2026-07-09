# Deck navigation redesign (DOCKET item 2)

A concrete proposal for replacing the firmware "shuffle + power" task-bar model
with a **deck-owned nav shell**: a persistent nav bar plus screen-stack
(push/pop) navigation with a clear Back. Design language borrowed from
**KlipperScreen** (3D-printer UI) — *not* a port: big flat touch targets, a dark
theme, generous spacing, one always-visible nav bar with a live status strip,
and menus as full panels instead of a tiny corner list.

The prototype lives in `deck/navshell.py`. This document is the design; the code
is the reference implementation of the pieces described here.

---

## 1. What we have today (and why it's weak)

The frozen `tulip/shared/py/ui.py` `UIScreen` draws a **task bar** into three
corner buttons on `screen.group`:

| Button | Symbol | Position | Action |
|--------|--------|----------|--------|
| alttab | SHUFFLE | top-right | cycle to the *next* running app (opaque) |
| quit   | POWER   | top-right (left of alttab) | quit app -> return to REPL |
| launcher | LIST  | bottom-right (REPL only) | tiny corner list of apps |

Problems:

- **Shuffle is a cycle, not a map.** You can't see where you are or where a tap
  goes; you just rotate through whatever happens to be running.
- **Quit means "close to REPL"**, so the REPL reads as the true root (DOCKET
  item 1 fixes the root; this item fixes the *navigation between* screens).
- **The launcher is a 195x140 corner list** — small targets, easy to miss on a
  1024x600 touch panel, and only on the REPL screen.
- **No persistent context.** Nothing tells you the active instrument, how many
  boards are in the fleet, wifi, or the time while you're inside an app.

We can't edit the frozen `ui.py`. We already monkeypatch the task bar from
`deck/ui_patch.py` (bigger buttons, a taller launcher). This proposal goes
further: draw a **full nav shell inside the deck's own `UIScreen.group`**, and
let an app optionally set `screen.hide_task_bar = True` to suppress the firmware
corner buttons entirely so the shell is the sole navigation.

---

## 2. Nav-bar placement: top bar vs left rail

I evaluated both. **Recommendation: a persistent LEFT RAIL.**

```
   TOP BAR (rejected)                 LEFT RAIL (recommended)
 +------------------------+        +------+-------------------+
 | Home  Back  ...  strip |  64px  | Home |  breadcrumb/title |
 +------------------------+        | Back |-------------------|
 |                        |        |      |                   |
 |        content         |        |      |     content       |
 |                        |        |------|                   |
 |                        |        |strip |                   |
 +------------------------+        +------+-------------------+
```

**Why the left rail wins on this device:**

1. **It dodges the firmware task bar.** The frozen alttab/quit buttons anchor to
   `TOP_RIGHT` and the launcher to `BOTTOM_RIGHT`. A top bar fights them for the
   same row; a left rail sits in a corner the firmware never uses, so the shell
   and the (optional) firmware buttons **coexist without overlap**.
2. **The screen is landscape (1024x600).** Vertical space is the scarce axis. A
   64px top bar costs ~11% of height; a 104px rail costs ~10% of width, of which
   there is far more to spare (920px of content remains).
3. **A tall column suits big stacked targets** — Home over Back over a vertical
   status strip — which is exactly the KlipperScreen feel (its action/status
   column also runs down a side).
4. **Thumb reach.** Held in two hands, the left rail sits under the left thumb
   for Home/Back while the right hand works the content — matching how the unit
   is handled at the bench.

Trade-off / cost of the rail: it eats horizontal width (worse if we ever add a
portrait mode), and a left-handed Back is slightly less conventional than a
top-left one. Both are acceptable here; the collision-avoidance with the frozen
task bar is the decisive factor. `RAIL_W` is a single constant in `navshell.py`
if we revisit.

---

## 3. The nav shell (persistent bar)

The rail (`navshell.NavShell`, `RAIL_W = 104`, full 600px height) holds:

- **Home** (top, accent) — resets the panel stack to the root panel. If already
  at the root and an `on_exit` hook is set, it hands off (e.g. back to the
  launcher / Home app per DOCKET item 1).
- **Back** (below Home) — pops one panel. **Hidden at the root** (you don't back
  out of a root), so it never dead-ends.
- **Status strip** (pinned to the bottom of the rail) — see below.

Both nav buttons are 88x68 two-line tiles (icon over label) — comfortably above
the ~88px big-target floor deckui already uses for tiles.

To the right of the rail:

- A **64px header** showing the **breadcrumb trail** (small, muted) over the
  **current panel title** (large, white), with a thin divider.
- The **content area** (920x536), which hosts the panel stack.

---

## 4. Status strip contents

A compact vertical stack at the bottom of the rail, always visible (guarded so
it degrades gracefully on a bare board / host mocks):

| Field | Source | Example |
|-------|--------|---------|
| Active instrument | `deckcfg.get_instance(active_index())` name + patch | `Board B` / `p12` |
| Fleet size | `deckcfg.num_instances()` | `Fleet: 2` |
| Wi-Fi | `tulip.ip()` -> online/offline (green when up) | `online` |
| Clock | `time.localtime()` HH:MM, refreshed ~every 30s via `tulip.defer` | `14:07` |

`refresh_status()` re-derives all of them; `set_status(**fields)` lets an app
override any field (e.g. show a transport state). This is the "always know the
active instrument / fleet / wifi / clock" requirement from the docket.

---

## 5. Screen-stack navigation model

The shell owns an explicit **panel stack** (a Python list), replacing the opaque
shuffle cycle:

- `push(builder, title)` — creates a full-size panel in the content area, hides
  the current top, and calls `builder(parent, shell)` to fill it. `parent` is a
  scrollable flex column (same feel as `dk.scroll_body`). Returns the panel.
- `pop()` / `back()` — deletes the top panel and un-hides the one beneath.
- `reset_to_root()` — pops everything above the first panel (what Home does).

**Single Back + a read-only breadcrumb.** The primary affordance is one Back
button on the rail (mirrors control-friendly, low-ambiguity KlipperScreen). The
header additionally renders the full trail (`Home / Fleet / Board B`) so a deep
stack still tells you where you are — but the crumbs are *display only*, not tap
targets, to keep the touch model dead simple (one Back, one Home). If we later
want tappable crumbs, the stack already has every title/panel to jump to.

Panels are cheap `lv.obj` containers; hiding (not deleting) the parent on push
keeps state and makes Back instant. Deleting on pop frees memory (a `gc.collect`
hook could be added if churn matters).

---

## 6. How the existing deck apps fit

Two adoption levels — the shell is **incremental**, not a rewrite:

**A. Shell-per-app (drop-in).** Any current app keeps its `run(screen)` and just
builds its first screen as a root panel:

```python
import navshell
def run(screen):
    shell = navshell.NavShell(screen, root_title="Fleet")
    shell.push(build_fleet_root, "Fleet")
    screen.present()
```

Sub-screens that today rebuild the whole `screen.group` (e.g. `mpe.py` /
`instrument.py` swap content when you pick a different instance; `fleet.py`
rebuilds on add/remove board) become **`shell.push(...)` panels** with a real
Back instead of ad-hoc rebuilds. Mapping:

| App | Root panel | Pushed panels |
|-----|-----------|---------------|
| home | app grid | (launches apps; or becomes the shell's root menu) |
| settings | settings list | Wi-Fi, Calibrate, Set-time as drill-in panels |
| instrument | instance list | per-instance patch/voices/channel |
| mpe | instance list | per-instance MPE editor |
| fleet | mode + board list | per-board channel / detune |
| files | `/user` listing | Run / Edit / Delete confirm as panels |

**B. Shell-as-home (fuller).** Home hosts one `NavShell` whose root panel is the
launcher grid; tiles `push` the target app's root panel into the *same* stack.
Then Home/Back/status persist across the whole deck and the firmware shuffle is
never needed. This is the end state; it pairs with DOCKET item 1 (Home-as-root)
and would be wired in `home.py` + `ui_patch.py` — **owned by the parallel item-1
agent**, so this proposal deliberately leaves those files untouched and ships the
reusable shell they can adopt.

---

## 7. Relationship to the firmware task bar

Three ways the shell and the frozen task bar can relate:

1. **Coexist (default, `own_taskbar=False`).** The rail is drawn in the
   top-left/left region the firmware never uses; the firmware alttab/quit/launcher
   stay in their corners. Nothing breaks; you gain the rail. Good for trying it
   app-by-app.
2. **Shell owns nav (`own_taskbar=True`).** The app sets
   `screen.hide_task_bar = True` before `present()`, so the firmware draws no
   corner buttons and the rail is the only navigation. Cleanest look.
3. **Patched task bar (existing `ui_patch.py`).** Independently, `ui_patch.py`
   already relocates/enlarges the corner buttons; the shell doesn't depend on it
   and doesn't edit it.

Keyboard: `control-Tab` (switch) and `control-Q` (quit) still work via the
firmware callbacks — the shell is additive and never removes them.

---

## 8. Wireframes

### 8a. Home (shell root = launcher grid)

```
+--------+-----------------------------------------------------------+
| [Home] |                                                           |
|        |  Home                                                     |
| (Back  |  ------------------------------------------------------   |
|  hidden|  +----------------+ +----------------+ +----------------+ |
|  at    |  |  Instrument    | |      MPE       | |     Fleet      | |
|  root) |  +----------------+ +----------------+ +----------------+ |
|        |  +----------------+ +----------------+ +----------------+ |
|        |  |     Files      | |    Settings    | |    Terminal    | |
|--------|  +----------------+ +----------------+ +----------------+ |
| Board B|                                                          |
| p12    |                                                           |
| Fleet:2|                                                           |
| online |                                                           |
| 14:07  |                                                           |
+--------+-----------------------------------------------------------+
   104px                          920px
```

### 8b. An app with the nav bar (Fleet, one level deep)

```
+--------+-----------------------------------------------------------+
| [Home] |  Home / Fleet                                             |
|        |  Fleet                                                    |
| [Back] |  ------------------------------------------------------   |
|        |  Mode:  ( Multi )  ( Stack )                              |
|        |  +------------------------------------------------------+ |
|        |  | Board A   ch 2                          [ edit >  ]  | |
|        |  +------------------------------------------------------+ |
|        |  | Board B   ch 3            (active)      [ edit >  ]  | |
|--------|  +------------------------------------------------------+ |
| Board B|  [ + Add board ]   [ - Remove ]                          |
| p12    |                                                           |
| Fleet:2|                                                           |
| online |                                                           |
| 14:07  |                                                           |
+--------+-----------------------------------------------------------+
```

### 8c. A menu panel pushed two levels deep (full panel, not a corner list)

```
+--------+-----------------------------------------------------------+
| [Home] |  Home / Fleet / Board B          <- breadcrumb trail      |
|        |  Board B                                                  |
| [Back] |  ------------------------------------------------------   |
|        |  MIDI channel        [ - ]   3   [ + ]                    |
|        |  Voices              [ - ]  10   [ + ]                    |
|        |  Detune spread       |=========o------------|            |
|        |                                                           |
|        |  Tap [Back] to return to Fleet, [Home] to the launcher.   |
|--------|                                                           |
| Board B|                                                           |
| p12    |                                                           |
| Fleet:2|                                                           |
| online |                                                           |
| 14:07  |                                                           |
+--------+-----------------------------------------------------------+
```

---

## 9. Prototype (`deck/navshell.py`) — what it provides

- `NavShell(screen, root_title=, on_home=, on_exit=, own_taskbar=, auto_clock=)`
  builds the rail + header + content area on a normal deck `UIScreen`.
- `push(builder, title)`, `pop()`, `back()`, `reset_to_root()` — the panel stack.
- `set_status(**fields)` / `refresh_status()` — the live status strip.
- A built-in `run(screen)` demo: a root menu whose tiles push detail panels that
  can drill deeper, exercising Home/Back, the breadcrumb, and the status strip.

Built entirely on `deckui.py` (`dk.*` helpers, dark palette, montserrat 12/18/24
fonts) and raw `lvgl`, matching deck conventions. All state/hardware lookups are
guarded so it renders on a bare board or under the host test mocks.

**To demo:** deploy the deck and `run('navshell')` (or from a script:
`import navshell; navshell.run(tulip.UIScreen('navshell'))`). Tap the tiles to
push panels, Back to pop, Home to reset. LVGL-9 API assumptions are noted at the
top of the module.
