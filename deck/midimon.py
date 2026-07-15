# midimon.py -- live MIDI monitor (shell panel, Home > System > MIDI monitor).
#
# Watches every incoming MIDI message and shows the last screenful, decoded
# (note names, CC numbers, 14-bit bend), newest at the bottom. Built cheap for
# the ESP32-S3:
#   * the midi callback is registered ONLY while the panel is open (zero cost
#     on the hot path otherwise), stores raw bytes into a capped ring, and does
#     no string work;
#   * rendering is ONE mono-font label rebuilt on a 150 ms tick, and only when
#     new messages arrived (dirty flag) -- not per message;
#   * clock (0xF8) and active-sense (0xFE) are filtered by default, or a DAW's
#     24-ppq clock would flood the ring.
# The render tick doubles as the lifetime probe: when the panel is popped its
# label dies, the tick notices and unregisters the callback.

import tulip
import deckui as dk
import lvgl as lv

_MAX = 120      # ring capacity (messages)
_SHOW = 16      # lines rendered
_TICK_MS = 150

_s = {'buf': [], 'count': 0, 'paused': False, 'open': False, 'dirty': False,
      'lbl': None, 'cntlbl': None, 'show_clock': False}

_NOTES = ('C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B')


def _note_name(n):
    return "%s%d" % (_NOTES[n % 12], (n // 12) - 1)


def _ticks():
    try:
        return tulip.amy_ticks_ms()
    except Exception:
        return 0


def _cb(m):
    # HOT PATH (runs per MIDI message while the panel is open): store, no
    # formatting, no prints.
    if not _s['open'] or _s['paused'] or not m:
        return
    b0 = m[0]
    if not _s['show_clock'] and (b0 == 0xF8 or b0 == 0xFE):
        return
    buf = _s['buf']
    buf.append((_ticks(), bytes(m)))
    if len(buf) > _MAX:
        del buf[:len(buf) - _MAX]
    _s['count'] += 1
    _s['dirty'] = True


def _fmt(t, m):
    st = m[0] & 0xF0
    ch = (m[0] & 0x0F) + 1
    if m[0] >= 0xF0:
        n = {0xF8: 'clock', 0xFA: 'start', 0xFB: 'continue', 0xFC: 'stop',
             0xF0: 'sysex', 0xFE: 'sense', 0xFF: 'reset'}.get(m[0], 'sys %02X' % m[0])
        return "%7d       %s" % (t, n)
    if st == 0x90 and len(m) > 2 and m[2] > 0:
        return "%7d ch%-2d  note-on   %-4s vel %d" % (t, ch, _note_name(m[1]), m[2])
    if st == 0x80 or (st == 0x90 and len(m) > 2):
        return "%7d ch%-2d  note-off  %-4s" % (t, ch, _note_name(m[1]))
    if st == 0xB0 and len(m) > 2:
        return "%7d ch%-2d  cc %-3d    val %d" % (t, ch, m[1], m[2])
    if st == 0xC0:
        return "%7d ch%-2d  program   %d" % (t, ch, m[1])
    if st == 0xD0:
        return "%7d ch%-2d  pressure  %d" % (t, ch, m[1])
    if st == 0xE0 and len(m) > 2:
        bend = ((m[2] << 7) | m[1]) - 8192
        return "%7d ch%-2d  bend      %+d" % (t, ch, bend)
    if st == 0xA0 and len(m) > 2:
        return "%7d ch%-2d  polytouch %-4s val %d" % (t, ch, _note_name(m[1]), m[2])
    return "%7d ch%-2d  %02X %s" % (t, ch, m[0],
                                    " ".join("%02X" % x for x in m[1:]))


def _render():
    lbl = _s['lbl']
    if lbl is None:
        return
    buf = _s['buf']
    lines = [_fmt(t, m) for (t, m) in buf[-_SHOW:]]
    if not lines:
        lines = ["waiting for MIDI..."]
    lbl.set_text("\n".join(lines))
    if _s['cntlbl'] is not None:
        _s['cntlbl'].set_text("%d msgs" % _s['count'])
    _s['dirty'] = False


def _close():
    _s['open'] = False
    _s['lbl'] = None
    _s['cntlbl'] = None
    try:
        import midi
        midi.remove_callback(_cb)
    except Exception:
        pass


def _tick(sid):
    # sid guards against a stale tick chain surviving a panel re-open
    if not _s['open'] or sid != _s.get('sid'):
        return
    try:
        if _s['dirty']:
            _render()
    except Exception:
        # the panel (and our label) was deleted: stop + unregister
        _close()
        return
    try:
        tulip.defer(_tick, sid, _TICK_MS)
    except Exception:
        _s['open'] = False


def _toggle_pause(e):
    if e.get_code() != lv.EVENT.CLICKED:
        return
    _s['paused'] = not _s['paused']
    b = _s.get('pausebtn')
    if b is not None:
        try:
            b.get_child(0).set_text("Resume" if _s['paused'] else "Pause")
            b.set_style_bg_color(dk.c(dk.ORANGE if _s['paused'] else dk.SURFACE2), 0)
        except Exception:
            pass


def _clear(e):
    if e.get_code() != lv.EVENT.CLICKED:
        return
    _s['buf'] = []
    _s['count'] = 0
    _s['dirty'] = True


def panel(parent, shell=None):
    import homeshell
    w, H = tulip.screen_size()
    h = H - homeshell.BAR_H

    # header: count + Pause/Clear
    _s['cntlbl'] = dk.label(parent, "0 msgs", 24, 20, color=dk.MUTED,
                            font=dk.FONT_S)
    pb = dk.button(parent, "Pause", w=130, h=44, bg=dk.SURFACE2, font=dk.FONT_S)
    pb.set_pos(w - 24 - 130 - 130 - 8, 8)
    pb.add_event_cb(_toggle_pause, lv.EVENT.CLICKED, None)
    _s['pausebtn'] = pb
    cb_ = dk.button(parent, "Clear", w=130, h=44, bg=dk.SURFACE2, font=dk.FONT_S)
    cb_.set_pos(w - 24 - 130, 8)
    cb_.add_event_cb(_clear, lv.EVENT.CLICKED, None)

    # the log: one card, one mono label
    card = lv.obj(parent)
    card.set_size(w - 48, h - 68)
    card.set_pos(24, 60)
    dk._flat(card, radius=16, bg=dk.SURFACE)
    dk.edge(card)
    card.remove_flag(lv.obj.FLAG.SCROLLABLE)
    lbl = dk.label(card, "waiting for MIDI...", 16, 12, color=dk.GREEN,
                   font=dk.FONT_MONO)
    _s['lbl'] = lbl

    _s['buf'] = []
    _s['count'] = 0
    _s['paused'] = False
    _s['dirty'] = True
    _s['open'] = True
    _s['sid'] = _s.get('sid', 0) + 1
    try:
        import midi
        midi.add_callback(_cb)     # MIDI_CALLBACKS is a set: re-add is a no-op
    except Exception:
        pass
    try:
        tulip.defer(_tick, _s['sid'], _TICK_MS)
    except Exception:
        pass
