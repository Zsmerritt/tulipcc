# Deck UI Decoupling — Architecture & Phased Plan (task #86)

**Status:** DESIGN ONLY. This document introduces no code changes. It is the
blueprint an implementer follows next.

**Branch:** `ui-decouple-design`, cut from `deck-next` (d6cb765b).

---

## 1. Purpose & strategic intent

The deck (`deck/*.py`) is today a rich alternative UI that assumes it is
running on **our fork's** Tulip firmware. The goal is to make it a
**self-contained, opt-in UI that rides on STOCK amy + STOCK tulipcc** — cleanly
separable so it is *not chained to the fork's firmware*.

The user frames the codebase as **two major parts: the UI vs. everything else.**
The enabling firmware hooks (render_cyc, the C MIDI router/tap, flash-fence,
display tuning, amy-send-batch, etc.) are being upstreamed as PRs so that *any*
stock Tulip can host a rich UI. This document plans how the deck's Python
separates from fork-specific firmware dependencies, so the UI can run — with
graceful feature degradation — on an unmodified `shorepine/tulipcc` build.

This is **not** a plan to merge the deck into shorepine mainline. It is a plan
to make the deck *separable* and *stock-runnable*.

---

## 2. Executive summary — how close is the deck to stock-runnable?

**Very close.** The headline finding is that the deck already degrades on stock
firmware in almost every path — the coupling debt is *scattered ad-hoc guards*,
not hard breakage.

- The deck references **40 distinct `tulip.*` symbols** (non-test). **28 are
  STOCK** tulipcc API; only **12 are fork-only** C bindings.
- Of the 12 fork-only bindings, **~10 are already guarded** in the Python with
  `hasattr(tulip, ...)` / `getattr(tulip, ..., None)` / `try/except`. The deck's
  authors have been writing degradation by hand, per call site, for months.
- Two more fork-only surfaces live outside the `tulip` C module:
  `midi.configure_mpe` + `midi.MPE_MEMBER_CHANNELS` (fork's MPE support) and
  `amy.override_send` (fork amy monkeypatch, already `hasattr`-guarded).
- The **firmware-flashing tooling** (`flash_*.py`, `boardfw.py`, `update.py`,
  `fwprogress.py`, `flashlib.py`) is fork-firmware *provisioning*, not UI. It is
  "everything else" and should be excluded from the portable UI layer entirely.

**So the deck is ~90% stock-runnable today by accident.** What's missing is not
compatibility code — it's *structure*: the degradation logic is smeared across
~8 files (`deckcfg`, `flashmode`, `forwarder`, `homeshell`, `settings`,
`screensaver`, `amyfleet`, `profilerdata`) with subtly different idioms. The
decoupling's real deliverable is to **centralize every fork-only call behind one
thin shim (`deckhw.py`)** so degradation is uniform, testable, and the UI can
query a single capability object instead of probing `tulip` inline.

**Top 3 hardest dependencies** (biggest functional cost on stock, detailed in §5):

1. **`amy_level()`** — output peak-level meter. *No upstream PR.* Powers the
   homeshell level meters **and** the deckcfg flash-safety quiet-gate. Losing it
   doesn't just blank a meter; it weakens the audio-activity signal the
   flash-write crash guard relies on. Most consequential because it is entangled
   with the flash-fence crash-safety story.
2. **The C MIDI router** (`midi_routes` / `midi_activity` / `midi_in_drops`) —
   covered by `pr-tulipcc-midi-router`, but it is the single biggest *behavioral
   cliff*: on stock the forwarder falls back to a pure-Python MIDI tap that is
   slower and drops events under load. The fallback works; it is just materially
   worse.
3. **`render_cyc()`** — per-oscillator render-cycle profiling
   (`pr-tulipcc-render-cyc`). The entire Profiler app is inert without it
   (graceful fallback = shows nothing). Grouped with the other no-PR probes
   `flash_freq` and `piano_partials`.

---

## 3. Coupling table — every deck ↔ firmware dependency

Classification method: each `tulip.*` symbol used by `deck/*.py` was checked
against the **stock** binding universe (`origin/main` = `shorepine/tulipcc`),
combining the C binding table (`tulip/shared/modtulip.c`) with the frozen Python
layer (`tulip/shared/py/*.py`). "PR branch" names the upstreaming vehicle among
the `pr-tulipcc-*` branches.

### 3a. STOCK `tulip.*` — no action needed (28 symbols)

These resolve on any stock Tulip and stay exactly as-is:

`amy_ticks_ms`, `app`, `board`, `brightness`, `color`, `current_uiscreen`,
`defer`, `edit`, `exists`, `ip`, `is_folder`, `key_send`, `keyboard`,
`lv` (LVGL, `import lvgl as lv` in stock `ui.py`), `midi_callback`, `midi_in`,
`midi_out`, `run`, `screen_size`, `sysex_in`, `tfb_font`, `touch`,
`touch_callback`, `touch_delta`, `UIText`, `upgrade`, `version`, `wifi`.

Note `board()` is stock and used only for DESKTOP-vs-device gating
(`home.py:26`, `ui_patch.py:211`).

### 3b. FORK-ONLY `tulip.*` — must go behind the shim (12 symbols)

| Deck symbol | Firmware dependency | Stock? | Covered by PR | Already guarded in deck? | Main call sites |
|---|---|---|---|---|---|
| `render_cyc` | per-osc render-cycle counters | fork-only | `pr-tulipcc-render-cyc` | yes (`profilerdata` fallback) | `profiler.py`, `profilerdata.py` |
| `midi_routes` | C MIDI router config | fork-only | `pr-tulipcc-midi-router` | yes (`hasattr`, `_has_c_router`) | `forwarder.py:1027,1169` |
| `midi_activity` | C router activity counter | fork-only | `pr-tulipcc-midi-router` | **partial** (raw in `screensaver.py:89`) | `forwarder.py:1020`, `screensaver.py:89` |
| `midi_in_drops` | C router drop counter | fork-only | `pr-tulipcc-midi-router` | yes (`hasattr`) | `forwarder.py:1093` |
| `num_midi_devices` | USB-MIDI device count | fork-only | `pr-tulipcc-num-midi-devices` | yes (`hasattr` in fwd; raw+try in `deckcfg`/`amyfleet`) | `forwarder.py:59`, `deckcfg.py:738`, `amyfleet.py:81` |
| `flash_fence_auto` | auto flash-write fence | fork-only | `pr-tulipcc-flash-fence` | yes (`hasattr`) | `deckcfg.py:340,348` |
| `flash_fence` | manual flash fence | fork-only | `pr-tulipcc-flash-fence` | yes (`getattr`) | `deckcfg.py:351` (not called bare) |
| `display_partial` | partial-render toggle | fork-only | `pr-tulipcc-display-tuning` | **via try/except** | `deckcfg.py:869`, `settings.py:553` |
| `display_vsync` | vsync toggle | fork-only | `pr-tulipcc-display-tuning` | **via try/except** | `deckcfg.py:868`, `settings.py:560` |
| `amy_send_batch` | batched AMY wire send | fork-only | `pr-tulipcc-amy-send-batch` | yes (`hasattr` + `amy.override_send`) | `forwarder.py:542,554` |
| `amy_level` | output peak-level meter | fork-only | **NO PR** | yes (`hasattr` in homeshell; raw+try in deckcfg) | `homeshell.py:137,260,536`, `deckcfg.py:329` |
| `flash_freq` | SPI-flash clock readback | fork-only | **NO PR** | yes (`getattr`) | `flashmode.py:56,79` |
| `piano_partials` | piano partial-count tuning | fork-only | **NO PR** | yes (`hasattr`) | `forwarder.py:292`, `amyparams.py:352` |

Related fork bindings that exist in `modtulip.c` but the deck **does not call**
from Python (no coupling): `audio_tap`, `audio_tap_read`, `eq_silent_skip`,
`flash_fence_stats`, `amy_send` (bare). These are firmware-internal or
future-facing and need no shim entry.

### 3c. Fork-only outside the `tulip` module

| Deck symbol | Module | Stock? | PR / track | Guarded? |
|---|---|---|---|---|
| `midi.configure_mpe` | `midi.py` (frozen) | fork-only (0 in stock) | no tulipcc PR — MPE track (amy/midi) | yes (`hasattr(midi, 'configure_mpe')`) |
| `midi.MPE_MEMBER_CHANNELS` | `midi.py` | fork-only | MPE track | via `configure_mpe` path |
| `amy.override_send` | amy lib | fork-only | amy-side PR | yes (`hasattr(amy, 'override_send')`) |

All other `midi.*` (`config`, `add_callback`, `remove_callback`,
`sysex_callback`, `c_fired_midi_event`, `MIDI_CALLBACKS`) and `amy.*`
(`send`, `volume`, `reverb`, `chorus`, `echo`, `eq`, `reset`) match stock.

`pr-tulipcc-midi-callback-reset` (remote) upstreams a `midi_callback` reset
semantic the deck relies on when swapping callbacks; keep it on the watch list
even though the symbol name itself is stock.

### 3d. Asset / build couplings (not Python-API couplings)

- **PCM / vaddr / GM banks** (`gm.py`, `gmbig.py`, `drums_kit.py`,
  `synthkits.py`): the deck addresses instruments by **stock AMY patch/bank
  numbers** (`amy.send(patch=...)`). The coupling is not in code — it is that
  the fork's firmware image bakes a specific set of PCM samples into the flash
  `vaddr` mmap pool at specific bank offsets. On stock firmware the *API calls
  succeed* but the *sounds differ / are missing*. This is a **firmware-data /
  asset** coupling, addressed by shipping the deck's PCM bank as loadable data
  (`pcm_load_file`, which is stock) rather than assuming a fork build — out of
  scope for the code shim, tracked separately as an asset-packaging task.
- **Board/partition assumptions**: deck Python has **no** OTA-slot or partition
  literals (the `slot` hits in `amyparams.py` are oscillator/patch slots, false
  positives). Partition/OTA logic lives entirely in the flash tooling (§3e).

### 3e. Fork-firmware provisioning tooling — EXCLUDE from the portable UI

`flash_ota.py`, `flash_stream.py`, `flash_fw.py`, `flash_pingpong.py`,
`flashmode.py`, `flashlib.py`, `boardfw.py`, `boardxport.py`, `fwprogress.py`,
`update.py`, `presets.py` (firmware upgrade paths). These exist to *build/flash
the fork's firmware* (OTA partitions, ping-pong dual-frequency images, serial
framing). They are the definition of "everything else" — they must **not** be
part of the stock-runnable UI package. Phase 6 physically separates them.

---

## 4. The three-layer boundary + shim API (`deckhw.py`)

Task #86 names three layers. Mapped onto this codebase:

```
  +-------------------------------------------------------------+
  |  UI layer            deckui, home, homeshell, settings,     |
  |  (LVGL / tulip.ui)   rack, instrument, parameditor, mpe,    |
  |                      padeditor, algopicker, screensaver...  |
  |    - queries deckhw.CAPS to show/hide optional features     |
  |    - never touches a fork-only tulip.* symbol directly      |
  +----------------------------|--------------------------------+
                               | (only stock tulip.* + deckhw)
  +----------------------------v--------------------------------+
  |  ENGINE layer        forwarder (routing logic), deckcfg,    |
  |  (pure-Python model) synthkits, drums_kit, amyparams,       |
  |                      patchparams, channels, catalog...      |
  |    - pure logic, host-testable, no LVGL                     |
  |    - fork-only calls routed through deckhw, not inline      |
  +----------------------------|--------------------------------+
                               | (single choke point)
  +----------------------------v--------------------------------+
  |  FIRMWARE-SHIM       deckhw.py  (NEW, ~1 file)              |
  |    - wraps EVERY fork-only firmware surface                 |
  |    - probes once at import, exposes CAPS + safe callables   |
  |    - degrades to a stock fallback for each                  |
  +----------------------------|--------------------------------+
                               |
        stock tulipcc  +  stock amy  +  (optional) fork bindings
```

### 4a. Design rule

`deckhw.py` **mirrors the pattern `modtulip.c` already uses** to degrade
`render_cyc` → `None` off-ESP: probe the binding once, cache a callable-or-None,
and expose a stable Python interface whose behavior is defined on stock. The UI
and engine import `deckhw`; they never write `hasattr(tulip, ...)` again.

### 4b. Capability object

```python
# deckhw.py — probed once at import
class Caps:
    profiling      = False   # tulip.render_cyc present
    level_meter    = False   # tulip.amy_level present
    c_midi_router  = False   # tulip.midi_routes present
    batch_send     = False   # tulip.amy_send_batch + amy.override_send
    display_tuning = False   # tulip.display_partial / display_vsync
    flash_fence    = False   # tulip.flash_fence_auto
    flash_freq     = False   # tulip.flash_freq
    num_devices    = False   # tulip.num_midi_devices
    piano_tuning   = False   # tulip.piano_partials
    mpe            = False   # midi.configure_mpe

CAPS = Caps()   # UI reads this to hide dead features instead of showing them
```

### 4c. Shim function spec (fork call → shim wrapper → stock fallback)

| `deckhw.*` | Wraps | Stock fallback behavior |
|---|---|---|
| `render_cyc()` | `tulip.render_cyc` | `None` — Profiler app hidden via `CAPS.profiling` |
| `amy_level()` | `tulip.amy_level` | `None` — callers use time-based quiet heuristic; meter hidden |
| `flash_freq()` | `tulip.flash_freq` | `None` — flashmode shows "unknown" |
| `flash_fence_auto(on)` | `tulip.flash_fence_auto` | no-op, returns `False` (fence unavailable) |
| `flash_fence(...)` | `tulip.flash_fence` | no-op |
| `display_partial(v)` / `display_vsync(v)` | resp. bindings | no-op; settings toggles read-only/hidden |
| `num_midi_devices()` | `tulip.num_midi_devices` | returns `1` (single-device assumption) |
| `midi_activity()` | `tulip.midi_activity` | returns `0` (screensaver treats as idle) |
| `midi_in_drops()` | `tulip.midi_in_drops` | returns `0` |
| `midi_routes(masks, py_mask, tap)` | `tulip.midi_routes` | returns `False`; forwarder switches to Python tap |
| `has_c_router()` | presence of `midi_routes` | `False` |
| `piano_partials(n)` | `tulip.piano_partials` | no-op |
| `amy_send_batch(msgs)` / `batch()` ctx | `tulip.amy_send_batch` + `amy.override_send` | replays as per-message `amy.send()` |
| `mpe_configure(members, bend, master)` | `midi.configure_mpe` | returns `False`; forwarder uses mono/poly path |

Every wrapper is a ~3-line function that already exists inline somewhere in the
deck — Phase 0 is *moving* that logic, not inventing it.

---

## 5. Hard-dependency → PR map (what stock compatibility costs today)

| Fork dependency | On stock, you lose... | Upstream PR | Cost if kept fork-only |
|---|---|---|---|
| `render_cyc` | the Profiler app entirely | `pr-tulipcc-render-cyc` | low — pure diagnostic, hidden cleanly |
| `midi_routes` + `midi_activity` + `midi_in_drops` | hardware-speed MIDI routing; falls back to slower Python tap that drops under load | `pr-tulipcc-midi-router` | **high** — functional cliff, but works |
| `num_midi_devices` | multi-device fan-out; assumes 1 device | `pr-tulipcc-num-midi-devices` | low |
| `flash_fence_auto` / `flash_fence` | flash-write crash fence (dual-core WDT guard) | `pr-tulipcc-flash-fence` | med — safety feature, deck avoids the trigger anyway |
| `display_partial` / `display_vsync` | render-tuning toggles (tearing/perf) | `pr-tulipcc-display-tuning` | low — cosmetic |
| `amy_send_batch` | batched wire send; falls back to per-msg | `pr-tulipcc-amy-send-batch` | low — perf only |
| (`midi_callback` reset semantics) | clean callback swap | `pr-tulipcc-midi-callback-reset` | low |
| **`amy_level`** | output level meters **and** the flash-safety quiet-gate signal | **NO PR — would need one** | **high** |
| **`flash_freq`** | flash-clock readback in flashmode | **NO PR** | low (tooling-only) |
| **`piano_partials`** | piano partial-count tuning | **NO PR** | low |
| **`midi.configure_mpe` / `MPE_MEMBER_CHANNELS`** | MPE support | **no tulipcc PR — amy/midi MPE track** | med |

**Four surfaces have no upstream PR:** `amy_level`, `flash_freq`,
`piano_partials`, and MPE. Of these, **`amy_level` is the one worth an upstream
PR** — it is the only no-PR dependency with real UX + safety weight. `flash_freq`
and `piano_partials` are cheap to lose (degrade to "unknown"/no-op).
MPE is tracked on its own amy/midi branch, not this decoupling.

---

## 6. Phased migration plan

**Sequencing constraint (collision-sensitivity).** Several deck files are under
active edit in sibling worktrees: `fm-algo-picker`, `fm-deep-edit(-full)`,
`dx7-per-patch-levels`, `levels-and-fill`, `ux-round2/3`. These touch
`instrument.py`, `parameditor.py`, `patchparams.py`, `amyparams.py`,
`synthkits.py`, `rack.py`, `deckui.py`. **Phase 0 creates a NEW file
(`deckhw.py`) and touches nothing else — zero collision.** Phases that edit the
churny files (1–5) are ordered to hit the *least* active subsystems first and
must land only after the FM/levels/kits worktrees merge.

### Phase 0 — introduce `deckhw.py` (mechanical, no behavior change)
- New file only. Implement `CAPS` + every wrapper in §4c by **lifting the
  existing guard logic verbatim** from its current call site into the shim.
- Do **not** yet change call sites. `deckhw` is dead code until Phase 1+.
- Land immediately; safe against all active worktrees.
- Test: unit-test `deckhw` under both mock profiles (§6a). Green = ship.

### Phase 1 — Profiler subsystem (lowest risk, isolated)
- `profiler.py`, `profilerdata.py` → call `deckhw.render_cyc()`; gate the app
  entry on `deckhw.CAPS.profiling`.
- Isolated files, not under active edit. Proves the pattern end-to-end.

### Phase 2 — display + flash tuning
- `settings.py` (`_apply_partial`/`_apply_vsync`), `deckcfg.py` display-apply
  loop → `deckhw.display_partial/vsync`; hide toggles when
  `not CAPS.display_tuning`.
- `flashmode.py` `flash_freq()` → `deckhw.flash_freq()`.
- `deckcfg.py` fence calls → `deckhw.flash_fence_auto`.

### Phase 3 — MIDI router / tap (highest value, central file)
- `forwarder.py` `_has_c_router`, `midi_routes`, `midi_activity`,
  `midi_in_drops`, `num_midi_devices` → `deckhw`. `screensaver.py:89` and
  `amyfleet.py:81`/`deckcfg.py:738` also routed here (fixes the one *unguarded*
  `midi_activity` raw call).
- `forwarder.py` is large and central — schedule after router semantics settle;
  do it as its own PR with the full `test_deck` MIDI suite green.

### Phase 4 — audio level / quiet-gate
- `homeshell.py` level meters + `deckcfg.py` quiet-gate + `screensaver.py`
  → `deckhw.amy_level()`. Decide here whether to open the `amy_level` upstream
  PR (recommended) or accept the time-based fallback permanently.

### Phase 5 — MPE + batch send
- `forwarder.py` `configure_mpe`/`MPE_MEMBER_CHANNELS` → `deckhw.mpe_configure`;
  `amy_send_batch`/`override_send` → `deckhw.amy_send_batch`.

### Phase 6 — physical package split (structural)
- Move firmware-provisioning tooling (§3e: `flash_*`, `boardfw`, `boardxport`,
  `update`, `fwprogress`, `flashlib`) into a separate `deck/fwtools/` (or a
  sibling package) that is **not** shipped with the portable UI.
- Optionally split the tree into `engine/` (pure model), `deckhw.py` (shim),
  `ui/` (LVGL) to make the boundary a directory, not a convention.
- End state: the UI + engine + `deckhw.py` are a drop-in folder that boots on a
  stock Tulip; provisioning tools stay fork-side.

### 6a. Test strategy
- `test_deck.py` already fakes hardware in `_install_hw_mocks()` by
  **assigning exactly the `tulip`/`amy`/`midi` attributes the code needs** — the
  fork-only symbols are simply set or omitted. This is the ideal harness for the
  shim.
- Add a **parametrized fixture with two profiles**:
  - `stock`: build the mock `tulip`/`midi`/`amy` **without** any fork-only
    symbol (no `render_cyc`, `amy_level`, `midi_routes`, `configure_mpe`, ...).
  - `fork`: include them (current behavior).
- Assert that `deckhw.CAPS` flips correctly per profile and every wrapper
  returns its documented fallback under `stock` and the live value under `fork`.
- Each phase's PR must keep both profiles green — this *proves* stock-runnability
  in CI without a device (no device access needed, matching this task's
  constraint).
- Guard against the vacuous-verification trap (see project memory): the `stock`
  profile is the oracle — a wrapper that still `AttributeError`s under `stock`
  fails the test, so a shim that silently forwards can't pass by accident.

---

## 7. Web-compat note

The user flagged that **engine features' C+wire halves are auto-web-compatible**
(WASM build + auto-generated JS API from `_KW_MAP_LIST`), while a **visual web
editor is a separate surface**.

For the deck UI specifically:

- The deck UI is **MicroPython + LVGL** (`tulip.ui`), not DOM/JS. Tulip already
  has a web/WASM build (`tulip/web/`) that runs MicroPython+LVGL in the browser.
  So in principle, once `deckhw` lets the deck tolerate missing fork bindings,
  the deck UI **could load in the stock Tulip web sim** — the same stock-runnable
  property that lets it run on stock hardware also lets it run on stock WASM.
- This is a **bonus, not a goal.** It is not free: the web build would still lack
  every fork binding (profiler, router, level meter), so it would run in the most
  degraded mode. And a **bespoke JS/DOM web editor is explicitly out of scope** —
  nothing in the deck targets it, and building one is a separate project.
- **Recommendation:** treat "deck UI in the Tulip web sim" as an emergent
  side-benefit to validate opportunistically after Phase 6 (it becomes a cheap
  CI smoke: does the deck import + boot under WASM with `CAPS` all-false?). Do
  not add web as a design constraint on the shim.

---

## 8. Methodology (for reproducibility)

- Fork-only vs. stock classification: extracted the deck's `tulip.*` usage, then
  tested each symbol against the **stock** symbol universe built from
  `origin/main` (`shorepine/tulipcc`) — C bindings in `tulip/shared/modtulip.c`
  plus definitions in the frozen `tulip/shared/py/*.py` layer. `lv` is stock
  (`import lvgl as lv` in stock `ui.py`).
- PR coverage: derived from the `pr-tulipcc-*` branch set by diffing each
  branch's added `MP_QSTR_*` bindings against its merge-base; each branch's
  unique binding matches its name (render-cyc, midi-router →
  routes/activity/in_drops, flash-fence → fence/fence_auto, display-tuning →
  partial/vsync, amy-send-batch, num-midi-devices), plus the remote
  `pr-tulipcc-midi-callback-reset`.
- Existing-guard audit: grepped the deck for `hasattr(tulip,...)`,
  `getattr(tulip,...)`, and `try/except` around each fork symbol.
