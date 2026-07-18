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
_SHOW = 16      # default lines rendered (panel() sizes this to the card:
                # a fixed 16 filled only half the screen)
_TICK_MS = 150


def _line_h():
    # ask the FONT (unscii_16 is 17px/line, not 16 -- assuming 16 overflowed
    # the card); generous fallback so a miss shows fewer lines, never too many
    try:
        return max(12, dk.FONT_MONO.line_height)
    except Exception:
        return 20

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
    # formatting, no prints. Fully guarded: this runs inside
    # midi.c_fired_midi_event's drain loop, and any exception escaping there
    # latches the C coalescing flag (tulip_midi_py_pending) and permanently
    # wedges the whole Python MIDI drain (blind monitor until reboot).
    try:
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
    except Exception:
        pass


def _fmt(dt, m):
    # dt = ms since the PREVIOUS message -- the number MIDI debugging actually
    # needs (UX-REVIEW-7 NEW-7); raw boot-ticks told you nothing.
    t = "+%dms" % dt if dt < 10000 else "+%ds" % (dt // 1000)
    st = m[0] & 0xF0
    ch = (m[0] & 0x0F) + 1
    if m[0] >= 0xF0:
        n = {0xF8: 'clock', 0xFA: 'start', 0xFB: 'continue', 0xFC: 'stop',
             0xF0: 'sysex', 0xFE: 'sense', 0xFF: 'reset'}.get(m[0], 'sys %02X' % m[0])
        return "%8s       %s" % (t, n)
    if st == 0x90 and len(m) > 2 and m[2] > 0:
        return "%8s ch%-2d  note-on   %-4s vel %d" % (t, ch, _note_name(m[1]), m[2])
    if st == 0x80 or (st == 0x90 and len(m) > 2):
        return "%8s ch%-2d  note-off  %-4s" % (t, ch, _note_name(m[1]))
    if st == 0xB0 and len(m) > 2:
        return "%8s ch%-2d  cc %-3d    val %d" % (t, ch, m[1], m[2])
    if st == 0xC0:
        return "%8s ch%-2d  program   %d" % (t, ch, m[1])
    if st == 0xD0:
        return "%8s ch%-2d  pressure  %d" % (t, ch, m[1])
    if st == 0xE0 and len(m) > 2:
        bend = ((m[2] << 7) | m[1]) - 8192
        return "%8s ch%-2d  bend      %+d" % (t, ch, bend)
    if st == 0xA0 and len(m) > 2:
        return "%8s ch%-2d  polytouch %-4s val %d" % (t, ch, _note_name(m[1]), m[2])
    return "%8s ch%-2d  %02X %s" % (t, ch, m[0],
                                    " ".join("%02X" % x for x in m[1:]))


def _render():
    lbl = _s['lbl']
    if lbl is None:
        return
    buf = _s['buf']
    show = _s.get('show', _SHOW)
    window = buf[-(show + 1):]           # one extra for the first delta
    lines = []
    prev = window[0][0] if window else 0
    for i, (t, m) in enumerate(window):
        dt = max(0, t - prev)
        prev = t
        if len(window) > show and i == 0:
            continue                     # the extra row only seeds the delta
        lines.append(_fmt(dt, m))
    if not lines:
        lines = ["waiting for MIDI..."]
    # Restore the normal color: a prior tap-failure banner may have turned the
    # label red, and the self-heal just recovered.
    try:
        lbl.set_style_text_color(dk.c(dk.GREEN), 0)
    except Exception:
        pass
    lbl.set_text("\n".join(lines))
    if _s['cntlbl'] is not None:
        stalls = _s.get('stalls', 0)
        if stalls:
            # A recovered drain stall is worth showing: it means MIDI briefly
            # wedged and the watchdog force-drained it (see decklog).
            _s['cntlbl'].set_text("%d msgs  (%d MIDI stall%s recovered)"
                                  % (_s['count'], stalls,
                                     '' if stalls == 1 else 's'))
        else:
            _s['cntlbl'].set_text("%d msgs" % _s['count'])
    _s['dirty'] = False


def _close():
    _s['open'] = False
    _s['lbl'] = None
    _s['cntlbl'] = None
    _cancel()
    try:
        import midi
        midi.remove_callback(_cb)
    except Exception:
        pass
    try:
        import forwarder
        forwarder.set_midi_tap(False)   # C-owned channels go quiet again
    except Exception:
        pass


def _show_error(text):
    # Paint a loud red banner in the log instead of a silently empty pane. A
    # blind monitor once had a user told "you must not have had it open" -- the
    # panel must always SAY what is wrong (pending task #85).
    lbl = _s['lbl']
    if lbl is None:
        return
    try:
        lbl.set_text(text)
        try:
            lbl.set_style_text_color(dk.c(dk.RED), 0)
        except Exception:
            pass
    except Exception:
        pass


def _show_tap_error():
    _show_error(
        "MIDI TAP FAILED TO ENGAGE\n"
        "\n"
        "The C MIDI router is active but the monitor could not\n"
        "register its tap, so C-owned channels are NOT reaching\n"
        "this panel. Incoming MIDI is being played but hidden.\n"
        "\n"
        "Try: rebuild instruments, or reboot the deck. If it keeps\n"
        "happening, report it -- this is a bug, not 'no MIDI'.")


def _tick(sid=None):
    if not _s['open']:
        _cancel()
        return
    # Liveness probe FIRST, in isolation: a deleted label is the ONLY thing
    # that tears the panel down from here. A render/format error must NOT --
    # the old catch-all _close() disengaged the tap on any transient hiccup,
    # leaving a still-open panel permanently blind (the intermittent failure).
    try:
        _s['lbl'].get_text()
    except Exception:
        _close()
        return
    # SELF-HEAL the tap: an instrument rebuild, a transient error, or an app
    # reload can drop it. Re-assert only when it actually dropped (idempotent;
    # no per-tick route-table churn), so the monitor recovers on the next tick
    # instead of going silently blind. Surface a hard failure loudly (#85).
    reg_ok = True
    try:
        import forwarder
        reg_ok = forwarder.register_ok()
        if not forwarder.tap_engaged():
            _s['tap_ok'] = bool(forwarder.set_midi_tap(True))
        else:
            _s['tap_ok'] = True
        _s['stalls'] = forwarder.midi_stalls()
    except Exception:
        pass
    # Render (best-effort: errors here are swallowed, never a teardown). Show
    # the most severe fault first: routing disabled > tap not engaged > log.
    try:
        if not reg_ok:
            _show_error(
                "MIDI ROUTING DISABLED\n"
                "\n"
                "The forwarder could not register its MIDI callback, so NO\n"
                "MIDI reaches the deck at all (no notes, no monitor).\n"
                "\n"
                "Reboot the deck; if it persists, report it -- see the log.")
        elif not _s.get('tap_ok', True):
            _show_tap_error()
        elif _s['dirty']:
            _render()
    except Exception:
        pass


def _cancel():
    try:
        import ticker
        ticker.cancel('midimon')
    except Exception:
        pass


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

    # the log: one card, one mono label, sized so the line count FILLS the
    # card (a fixed 16 lines used only ~half the screen)
    card_h = h - 68
    card = lv.obj(parent)
    card.set_size(w - 48, card_h)
    card.set_pos(24, 60)
    dk._flat(card, radius=16, bg=dk.SURFACE)
    dk.edge(card)
    card.remove_flag(lv.obj.FLAG.SCROLLABLE)
    lbl = dk.label(card, "waiting for MIDI...", 16, 12, color=dk.GREEN,
                   font=dk.FONT_MONO)
    _s['lbl'] = lbl
    # immediate teardown on panel deletion (E-7): unregisters the
    # per-message MIDI callback the moment the label dies instead of
    # waiting for the next tick's probe
    try:
        lbl.add_event_cb(lambda e: _close(), lv.EVENT.DELETE, None)
    except Exception:
        pass
    _s['show'] = max(10, (card_h - 28) // _line_h())

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
        import forwarder
        # the monitor wants EVERY message; with the C router active,
        # C-owned channels otherwise never reach Python (O-2). Capture whether
        # the tap actually engaged -- if not, the tick shows a loud banner
        # instead of a silently empty log (#85).
        _s['tap_ok'] = bool(forwarder.set_midi_tap(True))
    except Exception:
        _s['tap_ok'] = False
    try:
        import ticker
        ticker.every(_TICK_MS, _tick, key='midimon')   # shared tick (O-7)
    except Exception:
        pass
