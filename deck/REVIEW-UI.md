# SLICE E — UI shell + panel/LVGL lifecycle — fresh deep-scan review

## Exec summary

The panel/LVGL lifecycle is, on the whole, carefully built: teardown paths
(`push`/`back`/`reset_to_root`) uniformly close the soft keyboard and the
confirm modal, deferred refills are cancelled by a generation counter, the big
lists (kit picker, patch picker) are chunked/windowed, and per-tick work runs
through one shared `ticker` instead of per-tick `tulip.defer` re-arms. The
confirm overlay genuinely blocks background input (base `lv.obj` is
clickable-by-default in this build — proven by `rack._inst_row` explicitly
removing `CLICKABLE` at rack.py:120). The one material correctness defect is a
use-after-free: the patch picker's search debounce can fire `_build_list()`
against a freed panel after Back, and that path's `body.clean()` is unguarded —
the exact deleted-widget hard-crash family the rest of this code fights. The
rest are single-tick build-cost items (Files list and the star rasterizer both
run unbounded/heavy work on a build tick) and a handful of low-severity
stale-handle / wasted-rebuild issues. Host tests: `python -m pytest
deck/test_deck.py -q` → 93 passed (the UAF is device-timing-specific and not
exercised on host, where `tulip.defer` falls back to inline).

Severity counts: CRIT 0 · HIGH 1 · MED 3 · LOW 4.

---

## Findings (most severe first)

### 1. HIGH — Patch-picker search debounce use-after-frees the freed panel after Back
`deck/instrument.py:287` (guard) → `deck/instrument.py:162-183` (`_build_list`, `body.clean()` at :165)

`_search_changed` (typing) schedules `tulip.defer(_do, 0, 250)` (instrument.py:289).
`_do` only proceeds if `_s.get('search_gen') == gen and _s.get('listbody') is
not None` (:287). Neither guard survives panel teardown: `panel()` clears `_s`
only on the *next* open, so after leaving the patch picker `_s['listbody']`
still holds the **deleted** scroll-body handle (never `None`), and `search_gen`
is unchanged. `_build_list()` then calls `body.clean()` at instrument.py:165
with **no try/except**, followed by `dk.label(body, …)` / `_append_rows(body,…)`
which create children on a freed parent.

Failure scenario: open Home ▸ Instruments ▸ edit ▸ Patch, type one character
(fires VALUE_CHANGED → 250 ms defer), then tap Back within 250 ms. `homeshell.back()`
deletes the patch panel via its own `_do` at +10 ms (homeshell.py:444-449); the
search `_do` fires at +250 ms and touches the freed `listbody`. Per this
codebase's own lifecycle notes (deckui `close_keyboard`/F-12), poking a deleted
LVGL object is a hard device crash, not a catchable error.

Fix: give `instrument._s` an alive/generation token set in `panel()`/`run()`
and checked in `_do` (mirror `homeshell._refill_gen`), and wrap the
`body.clean()` + build in try/except with an early `is None`/handle-validity
bail — the same shape `rack.kit_panel._fill` already uses (rack.py:344-358).

---

### 2. MED — Files list builds every directory entry in one tick, with a per-file stat fallback
`deck/files.py:156-212` (`_refresh`; build loop at :178, `os.stat` fallback at :200)

`_refresh()` runs inside the panel build (`_build` → `_refresh`, files.py:313)
and creates one button + 2-3 labels for **every** entry in `dirs + files` in a
single LVGL callback, with **no windowing or chunking** — unlike the patch
picker (`_WINDOW = 40`, instrument.py:132) and kit picker (chunked defer,
rack.py:344) which were explicitly split precisely to avoid the interrupt-WDT
stall. A full `/user` (dozens of files) is dozens×~4 objects in one tick. Worse,
when `ilistdir` doesn't yield size (`len(ent) > 3` false, files.py:172), each
file takes an extra `os.stat(full)[6]` at files.py:200 — N littlefs metadata
walks *inside* the build loop, re-introducing the F-17 cost the ilistdir
single-pass was meant to remove.

Cost: at the codebase's stated ~40-80 ms / ~80-row WDT threshold, a large
`/user` opening Files is in range. Fix: window the list (reuse the
instrument.py `_append_rows` + "Show N more" pattern) or fill in deferred chunks
(reuse `rack.kit_panel._fill`); drop the per-file `os.stat` fallback or batch it.

---

### 3. MED — Star icon is rasterized in pure Python on the panel-build tick
`deck/deckui.py:201-242` (`star_src`), first triggered from `deck/instrument.py:123,327`

`star_src` does a point-in-polygon test with 4 sub-pixel samples over `size²`
pixels against 10 star edges — ~`size²·4·10` Python float iterations (~31k for
size 28, ~19k for size 22). It's cached in `_STAR`, but the **first** patch-picker
open rasterizes both sizes (rows use default 28 via `dk.star` at instrument.py:123;
the Favorites button uses size 22 at instrument.py:327) lazily *inside the build
tick*, i.e. ~50k interpreted iterations layered on top of the 40-row window build
in the same open. On a 240 MHz MP interpreter that is tens of ms of one-tick
stall on first open, adjacent to the AMY 5.8 ms render deadline.

Fix: pre-rasterize the two used sizes once at import time (module load, off the
interaction path), or precompute the star mask with integer scanline fill rather
than per-subpixel PIP.

---

### 4. MED — `mpe._rebuild` builds the whole panel (incl. 16-cell channel strip) synchronously
`deck/mpe.py:243-374` (`_rebuild`), strip at `deck/mpe.py:146-217` (`_render_strip`)

Unlike the tabbed editors (parameditor.build_tabbed fills tab 1 sync, the rest
deferred — parameditor.py:347-363) and the chunked list panels, the pushed MPE
panel builds two slider cards, two info rows, and the full 16-cell channel-map
strip (16 `lv.obj` cells + labels, mpe.py:184-199) in one builder call on the
push event tick — ~50 objects plus a `_disable_tree` recursion (mpe.py:68-78).
Not fatal at that count, but it's the un-chunked outlier on a nav path and
compounds with brisk Back/forward. Fix: defer the strip build one tick after the
controls (it's already isolated as `_render_strip`), or accept and document if
measured under the WDT budget.

---

### 5. LOW — `homeshell.rebuild_top` skips the keyboard/confirm teardown that every other nav path performs
`deck/homeshell.py:372-387`

`push` (homeshell.py:333-334), `back` (:410-411) and `reset_to_root` (:455-456)
all call `dk.close_keyboard()` + `dk.close_confirm()` before mutating panels;
`rebuild_top` calls **neither** before `h.clean()` (:382) tears the panel's
children (incl. any text field) out from under the global soft keyboard's raw
target pointer — the documented textarea UAF. Currently near-unreachable (the
same-key rebuild paths in `home.py`/`rack.py` aren't reachable with a live
keyboard on that panel), so LOW, but it's a defense-in-depth gap in an otherwise
uniform invariant. Fix: add both `close_*` calls at the top of `rebuild_top`.

---

### 6. LOW — Editing device/channel from the edit panel rebuilds the hidden root list every time
`deck/rack.py:48-56` (`_refresh_list`), called from `_set_device` (:219) and `_channel_cb` (:228)

`_s['list_parent']` still points at the Home root panel while you're on the
(pushed) edit panel. `_set_device`/`_channel_cb` call `_refresh_list()`, which
`clean()`s and rebuilds the entire instrument list + footer on a **hidden**
panel — wasted work, since `homeshell.back()`'s deferred refill rebuilds the
revealed root from its stored builder anyway (homeshell.py:435-441). Fix: skip
`_refresh_list()` when the rack list isn't the top panel (the edit flow already
rebuilds on return).

---

### 7. LOW — Kit picker retains ~80 deleted button handles after you leave it
`deck/rack.py:315,340` (`_s['kitbtns']`)

Leaving `kit_panel` deletes its widgets, but `_s['kitbtns']` (up to ~80
`(button, kit, name)` tuples) is only reset on the *next* `kit_panel` open
(rack.py:315). Until then the module dict roots dangling handles — harmless
(nothing re-touches them; `_set_kit`'s iteration at rack.py:294 is only reachable
from the now-deleted buttons) but it's needless GC-root retention on a
memory-tight target. Fix: clear `kitbtns`/`kitcur` on teardown, or key them to a
generation as `kitgen` already does for the fill chain.

---

### 8. LOW — Shared ticker keeps a 10 Hz timer alive with zero subscribers; HomeShell never tears down
`deck/ticker.py:49-56` (`_ensure`) and `deck/homeshell.py:128-152` (no `_alive=False` path)

`ticker` creates its `lv.timer` once and never stops it when `_state['subs']`
empties (ticker.py:49-56) — a permanent 100 ms empty-loop wake. Separately,
`HomeShell._alive` is only ever cleared inside `_start_clock`'s exception branch
(homeshell.py:620); there is no destroy hook, and `_schedule_clock` wraps
`screen.activate_callback` (homeshell.py:500-513), so if a Home screen object is
ever reused across relaunches the activate wrappers chain. Both are negligible
today (single long-lived Home, subs never truly empty) — noting for
completeness. Fix: stop the ticker timer when subs hit zero; give HomeShell an
explicit `destroy()` that sets `_alive=False` and cancels its ticker keys.

---

## Ranked optimization list (perf-relevant UI build cost)

1. **Window/chunk the Files list** (Finding 2) — biggest single-tick win; reuse
   the existing `_append_rows`/`_fill` patterns already in this slice.
2. **Pre-rasterize the star at import** (Finding 3) — moves ~50k interpreted
   iterations off the first patch-picker build tick.
3. **Defer the MPE channel strip one tick** (Finding 4) — shortens the longest
   un-chunked push builder.
4. **Skip hidden-root rebuilds** (Finding 6) — removes a full list rebuild per
   device/channel edit.
5. **Stale-handle cleanup** (Findings 7, and the Finding 1 alive-token) — lowers
   GC-root pressure on teardown, which on this target is audible-jitter-relevant.
