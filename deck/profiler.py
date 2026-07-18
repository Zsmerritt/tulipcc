# profiler.py -- live core-load + memory readout (Home > Settings > System >
# Profiler; debug-mode only, see settings.py). Deliberately imported LAZILY
# (only when the Profiler tile is tapped -- see settings.py's
# _open_debug_panel) so Settings itself stays cheap to build even with
# Debug on (REVIEW-UI build-cost finding).
#
# Cheap while open, same discipline as midimon.py: a handful of labels built
# ONCE, then updated in place on a shared ticker.every() tick -- never a
# panel rebuild (a partial redraw that repainted the WHOLE screen is a real
# regression this deck has shipped before). Teardown rides an LVGL DELETE
# hook on one of those labels, so polling stops the instant the panel is
# popped, not on the next tick.

import tulip
import deckui as dk
import lvgl as lv
import profilerdata as pd

_TICK_MS = 300            # ~3 Hz: live enough to read, cheap enough not to
                          # become the load it's measuring
_WORST_RESET_MS = 2000    # periodically clear render_cyc's worst-counters
                          # so the peak reflects RECENT load, not all-time
_KEY = 'profiler'

_s = {'open': False, 'labels': None, 'last_reset': 0}

# Bar geometry: every row's title/value text keeps its existing x/width (see
# _row below); the bar fills the space to their right. bar_w is resolved
# once per panel() build from the live screen width (build-once discipline
# -- see module docstring), not re-measured per tick.
_BAR_X = 440
_BAR_H = 18
_BAR_COLORS = {
    pd.BAR_GREEN: dk.GREEN,
    pd.BAR_AMBER: dk.ORANGE,     # nearest existing constant to "amber"
    pd.BAR_RED: dk.RED,
    'na': dk.GRAY,               # firmware doesn't expose this reading yet
}


def _now_ms():
    try:
        return tulip.amy_ticks_ms()
    except Exception:
        return 0


def _budget():
    """The per-block cycle budget, from the live CPU clock when
    machine.freq() exists, else profilerdata's compiled-in constant."""
    try:
        import machine
        return pd.block_budget_cycles(machine.freq())
    except Exception:
        return pd.CYCLES_PER_BLOCK


def _memory_snapshot():
    """(free_psram, total_psram, free_internal, largest_internal_block,
    total_internal); any of which is None if that source isn't available on
    this build. Same idiom as homeshell._debug_str (gc.mem_free /
    esp32.idf_heap_info), plus the largest-free-internal-block figure
    mem_probe.py already surfaces -- that's the true headroom metric on
    this board (internal SRAM exhaustion has bricked the deck; a
    fragmented heap can show plenty of total free with no block left big
    enough to allocate). The two `total_*` figures are only for the new
    Memory bars' fill fraction (see profilerdata.mem_pct_free); the free/
    largest text readouts are unchanged."""
    free_psram = total_psram = None
    try:
        import gc
        free_psram = gc.mem_free()
        total_psram = free_psram + gc.mem_alloc()   # exact, no guessed constant
    except Exception:
        pass
    free_int = largest_int = total_int = None
    try:
        import esp32
        # materialize once -- internal_sram_summary and internal_sram_total
        # each walk it, and idf_heap_info's return type isn't guaranteed
        # re-iterable
        regions = list(esp32.idf_heap_info(esp32.HEAP_DATA))
        free_int, largest_int = pd.internal_sram_summary(regions)
        total_int = pd.internal_sram_total(regions)
    except Exception:
        pass
    return free_psram, total_psram, free_int, largest_int, total_int


def _voice_count():
    try:
        import forwarder
        return forwarder.live_voices()
    except Exception:
        return None


def _row(parent, y, title):
    dk.label(parent, title, 24, y, color=dk.MUTED, font=dk.FONT_S, w=280)
    val = dk.label(parent, "--", 300, y, color=dk.TEXT, font=dk.FONT_MONO)
    return val


def _bar_row(parent, y, title, bar_w):
    """One title + value-text + horizontal-bar row, built ONCE (build-once
    discipline -- see module docstring): every tick after this only calls
    _bar_set() on the handles returned here, never rebuilds them.

    Returns {'title', 'value', 'bar', 'track_w'}. This is the reusable
    primitive for the panel's bar readouts -- a SECOND set of these, same
    function, is how a connected AMYboard's core-load bars will plug in
    once that board has a data source (no new primitive needed then, just
    more _bar_row()/_bar_set() calls fed from a different reader)."""
    title_lbl = dk.label(parent, title, 24, y, color=dk.MUTED, font=dk.FONT_S,
                         w=280)
    val = dk.label(parent, "--", 300, y, color=dk.TEXT, font=dk.FONT_MONO,
                   w=_BAR_X - 300 - 8)
    try:
        val.set_long_mode(lv.label.LONG.DOT)   # same idiom as rack.py/padeditor.py
    except Exception:
        pass

    track = lv.obj(parent)
    track.set_size(bar_w, _BAR_H)
    track.set_pos(_BAR_X, y + 3)
    dk._flat(track, radius=_BAR_H // 2, bg=dk.BG)
    track.remove_flag(lv.obj.FLAG.SCROLLABLE)

    bar = lv.obj(track)
    bar.set_size(1, _BAR_H)
    bar.align(lv.ALIGN.LEFT_MID, 0, 0)
    dk._flat(bar, radius=_BAR_H // 2, bg=dk.GREEN)
    bar.remove_flag(lv.obj.FLAG.SCROLLABLE)

    return {'title': title_lbl, 'value': val, 'bar': bar, 'track_w': bar_w}


def _bar_set(row, pct, text, color_key=pd.BAR_GREEN):
    """Update one _bar_row()'s fill width + color + text label from a
    percent -- the shared update path for every bar on this panel (core
    load now, an AMYboard's core load later). `pct` drives the fill and
    should already be bar-safe (0..100 -- see profilerdata.bar_fill_pct for
    the over-budget-clamp case, profilerdata.mem_pct_free for memory);
    `text` is shown as-is and can carry the real, unclamped number."""
    frac = 0.0 if pct <= 0 else (1.0 if pct >= 100.0 else pct / 100.0)
    try:
        row['bar'].set_width(max(1, int(row['track_w'] * frac)))
        row['bar'].set_style_bg_color(dk.c(_BAR_COLORS.get(color_key, dk.GREEN)), 0)
        row['value'].set_text(text)
    except Exception:
        pass


def panel(parent, shell=None):
    y = 16
    dk.label(parent, "Core load (percent of per-block budget)", 24, y,
             color=dk.WHITE, font=dk.FONT_M)
    y += 38
    w, _h = tulip.screen_size()
    bar_w = max(60, w - _BAR_X - 24)
    labels = {}
    labels['c0'] = _bar_row(parent, y, "Core 0  last / recent peak", bar_w); y += 32
    labels['c1'] = _bar_row(parent, y, "Core 1  last / recent peak", bar_w); y += 44

    dk.label(parent, "Memory", 24, y, color=dk.WHITE, font=dk.FONT_M)
    y += 38
    labels['psram'] = _bar_row(parent, y, "Free PSRAM (MicroPython heap)", bar_w); y += 32
    labels['sram'] = _bar_row(parent, y, "Free internal SRAM (total)", bar_w); y += 32
    labels['sram_largest'] = _row(parent, y, "Largest free internal block"); y += 32
    labels['voices'] = _row(parent, y, "Active voices (internal)"); y += 32

    _s['labels'] = labels
    _s['open'] = True
    _s['last_reset'] = _now_ms()
    # teardown rides the DELETE event of the first label built -- the
    # cheapest reliable "this panel is gone" signal (midimon.py's pattern),
    # no separate close button or lifecycle plumbing needed
    try:
        labels['c0']['value'].add_event_cb(lambda e: _close(), lv.EVENT.DELETE, None)
    except Exception:
        pass

    _refresh()   # paint immediately -- don't make the first frame wait a tick
    try:
        import ticker
        ticker.every(_TICK_MS, _tick, key=_KEY)
    except Exception:
        pass


def _close():
    _s['open'] = False
    _s['labels'] = None
    _cancel()


def _cancel():
    try:
        import ticker
        ticker.cancel(_KEY)
    except Exception:
        pass


def _tick(_sid=None):
    if not _s['open']:
        _cancel()
        return
    try:
        _refresh()
    except Exception:
        # the panel (and its labels) was deleted out from under us
        _close()


def _fmt_bytes_k(n):
    if n is None:
        return "n/a"
    return "%d K" % (n // 1024)


def _refresh():
    labels = _s['labels']
    if not labels:
        return
    now = _now_ms()
    do_reset = (now - _s['last_reset']) >= _WORST_RESET_MS

    rc = pd.read_render_cyc(tulip)
    if rc is None:
        na = "n/a (needs newer firmware)"
        _bar_set(labels['c0'], 0, na, color_key='na')
        _bar_set(labels['c1'], 0, na, color_key='na')
    else:
        budget = _budget()
        pcts = pd.core_load_lines(rc, budget=budget)
        _, _, c0l, c1l = rc      # (core0_worst, core1_worst, core0_last, core1_last)
        c0_pct = pd.pct_of_budget(c0l, budget)
        c1_pct = pd.pct_of_budget(c1l, budget)
        # the BAR (fill + color) tracks the CURRENT reading (last); recent
        # peak (worst) stays text-only in the same value string, per the
        # panel's existing "last / recent peak" format
        _bar_set(labels['c0'], pd.bar_fill_pct(c0_pct),
                 "%s / %s" % (pcts['core0_last'], pcts['core0_worst']),
                 color_key=pd.load_bar_color(c0_pct))
        _bar_set(labels['c1'], pd.bar_fill_pct(c1_pct),
                 "%s / %s" % (pcts['core1_last'], pcts['core1_worst']),
                 color_key=pd.load_bar_color(c1_pct))
        if do_reset:
            pd.reset_worst(tulip)
    if do_reset:
        _s['last_reset'] = now

    free_psram, total_psram, free_int, largest_int, total_int = _memory_snapshot()
    _bar_set(labels['psram'], pd.mem_pct_free(free_psram, total_psram),
             _fmt_bytes_k(free_psram))
    _bar_set(labels['sram'], pd.mem_pct_free(free_int, total_int),
             _fmt_bytes_k(free_int))
    labels['sram_largest'].set_text(_fmt_bytes_k(largest_int))

    voices = _voice_count()
    labels['voices'].set_text(str(voices) if voices is not None else "n/a")
