# logs.py -- live tail viewer for decklog's on-device log (Home > Settings >
# System > Logs; debug-mode only, see settings.py). Imported LAZILY (only
# when the Logs tile is tapped -- settings.py's _open_debug_panel) so
# Settings itself stays cheap to build even with Debug on.
#
# Reads a BOUNDED tail (logtail.py seeks near the end of the file instead of
# loading it whole) on a ticker.every() tick, and repaints ONE reused mono
# label in place -- never rebuilds the panel -- so a screen no one is
# looking at doesn't keep polling flash, and the one that IS visible doesn't
# itself become a source of load. Teardown mirrors midimon.py/profiler.py:
# an LVGL DELETE hook on the label stops the ticker the instant the panel is
# popped.

import tulip
import deckui as dk
import lvgl as lv
import logtail

_TICK_MS = 1000     # log lines change slowly and each tick is a flash read;
                    # no need for the profiler's faster cadence
_MAX_LINES = 40

_s = {'open': False, 'lbl': None, 'show': _MAX_LINES}


def _line_h():
    try:
        return max(12, dk.FONT_MONO.line_height)
    except Exception:
        return 20


def _path():
    try:
        import decklog
        return decklog.logfile()
    except Exception:
        return None


def _render():
    lbl = _s['lbl']
    if lbl is None:
        return
    path = _path()
    if not path:
        lbl.set_text("(no log path available)")
        return
    try:
        import decklog
        decklog.flush()   # surface just-buffered lines too, not only what
                          # has already reached flash/SD
    except Exception:
        pass
    data, truncated = logtail.read_tail_bytes(path)
    lines = logtail.tail_lines(data, _s['show'], truncated)
    lbl.set_text("\n".join(lines) if lines else "(log is empty)")


def _close():
    _s['open'] = False
    _s['lbl'] = None
    _cancel()


def _cancel():
    try:
        import ticker
        ticker.cancel('debuglogs')
    except Exception:
        pass


def _tick(_sid=None):
    if not _s['open']:
        _cancel()
        return
    try:
        _render()
    except Exception:
        _close()


def panel(parent, shell=None):
    import homeshell
    w, H = tulip.screen_size()
    h = H - homeshell.BAR_H

    dk.label(parent, "Log: %s" % (_path() or "?"), 24, 16, color=dk.MUTED,
             font=dk.FONT_S, w=w - 48)

    card_h = h - 68
    card = lv.obj(parent)
    card.set_size(w - 48, card_h)
    card.set_pos(24, 52)
    dk._flat(card, radius=16, bg=dk.SURFACE)
    dk.edge(card)
    card.remove_flag(lv.obj.FLAG.SCROLLABLE)
    lbl = dk.label(card, "loading...", 16, 12, color=dk.GREEN, font=dk.FONT_MONO)
    _s['lbl'] = lbl
    try:
        lbl.add_event_cb(lambda e: _close(), lv.EVENT.DELETE, None)
    except Exception:
        pass
    # size the visible window to what the card actually fits (was a bare
    # fixed 40 -- on a shorter card that either overflows or wastes space;
    # midimon.py's card-fit trick)
    _s['show'] = max(10, min(_MAX_LINES, (card_h - 28) // _line_h()))

    _s['open'] = True
    _render()
    try:
        import ticker
        ticker.every(_TICK_MS, _tick, key='debuglogs')
    except Exception:
        pass
