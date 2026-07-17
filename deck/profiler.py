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
    """(free_psram, free_internal, largest_internal_block), any of which is
    None if that source isn't available on this build. Same idiom as
    homeshell._debug_str (gc.mem_free / esp32.idf_heap_info), plus the
    largest-free-internal-block figure mem_probe.py already surfaces --
    that's the true headroom metric on this board (internal SRAM
    exhaustion has bricked the deck; a fragmented heap can show plenty of
    total free with no block left big enough to allocate)."""
    free_psram = None
    try:
        import gc
        free_psram = gc.mem_free()
    except Exception:
        pass
    free_int = largest_int = None
    try:
        import esp32
        regions = esp32.idf_heap_info(esp32.HEAP_DATA)
        free_int, largest_int = pd.internal_sram_summary(regions)
    except Exception:
        pass
    return free_psram, free_int, largest_int


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


def panel(parent, shell=None):
    y = 16
    dk.label(parent, "Core load (percent of per-block budget)", 24, y,
             color=dk.WHITE, font=dk.FONT_M)
    y += 38
    labels = {}
    labels['c0'] = _row(parent, y, "Core 0  last / recent peak"); y += 32
    labels['c1'] = _row(parent, y, "Core 1  last / recent peak"); y += 44

    dk.label(parent, "Memory", 24, y, color=dk.WHITE, font=dk.FONT_M)
    y += 38
    labels['psram'] = _row(parent, y, "Free PSRAM (MicroPython heap)"); y += 32
    labels['sram'] = _row(parent, y, "Free internal SRAM (total)"); y += 32
    labels['sram_largest'] = _row(parent, y, "Largest free internal block"); y += 32
    labels['voices'] = _row(parent, y, "Active voices (internal)"); y += 32

    _s['labels'] = labels
    _s['open'] = True
    _s['last_reset'] = _now_ms()
    # teardown rides the DELETE event of the first label built -- the
    # cheapest reliable "this panel is gone" signal (midimon.py's pattern),
    # no separate close button or lifecycle plumbing needed
    try:
        labels['c0'].add_event_cb(lambda e: _close(), lv.EVENT.DELETE, None)
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
        labels['c0'].set_text(na)
        labels['c1'].set_text(na)
    else:
        pcts = pd.core_load_lines(rc, budget=_budget())
        labels['c0'].set_text("%s / %s" % (pcts['core0_last'], pcts['core0_worst']))
        labels['c1'].set_text("%s / %s" % (pcts['core1_last'], pcts['core1_worst']))
        if do_reset:
            pd.reset_worst(tulip)
    if do_reset:
        _s['last_reset'] = now

    free_psram, free_int, largest_int = _memory_snapshot()
    labels['psram'].set_text(_fmt_bytes_k(free_psram))
    labels['sram'].set_text(_fmt_bytes_k(free_int))
    labels['sram_largest'].set_text(_fmt_bytes_k(largest_int))

    voices = _voice_count()
    labels['voices'].set_text(str(voices) if voices is not None else "n/a")
