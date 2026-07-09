# mpe.py -- enable/disable and configure MPE for the channel-1 synth.
#
# Uses this Tulip's local AMY MPE support (midi.configure_mpe): an MPE zone
# gives every held note its own pitch bend, pressure and slide (CC74), routed
# by AMY's C layer to the zone master synth. Great with MPE controllers
# (LinnStrument, Seaboard, etc). All settings persist via deckcfg.

import tulip
import deckui as dk
import deckcfg
import lvgl as lv

_w = {}


def _members_str(cfg):
    n = cfg.get('mpe_members', 15)
    master = cfg.get('midi_channel', 1)
    if master == 16:
        lo, hi = 16 - n, 15          # upper zone: members descend from 15
    else:
        lo, hi = master + 1, master + n   # lower zone: ascend from master+1
    return "master ch %d, member channels %d-%d" % (master, lo, hi)


def _apply():
    cfg = deckcfg.load()
    deckcfg.apply_instrument(cfg)
    on = cfg.get('mpe')
    _w['status'].set_text(_members_str(cfg) if on else "MPE off")
    _w['status'].set_style_text_color(dk.c(dk.GREEN if on else dk.MUTED), 0)


def _toggle(key, btn, extra=None):
    def cb(e):
        v = not deckcfg.get(key)
        deckcfg.set(key, v)
        _paint_toggle(btn, v)
        if extra:
            extra(v)
        _apply()
    return cb


def _paint_toggle(btn, v):
    btn.set_style_bg_color(dk.c(dk.GREEN if v else dk.SURFACE2), 0)
    btn.get_child(0).set_text("On" if v else "Off")


def _members_cb(e):
    v = e.get_target_obj().get_value()
    _w['mlabel'].set_text("%d channels" % v)
    deckcfg.set('mpe_members', v)
    _apply()


def _bend_cb(e):
    v = e.get_target_obj().get_value()
    _w['blabel'].set_text("+/- %d semitones" % v)
    deckcfg.set('mpe_bend', v)
    _apply()


def _channel_cb(ch):
    deckcfg.set('midi_channel', ch)
    _w['chlabel'].set_text("Zone: %s" % ("upper" if ch == 16 else "lower"))
    _apply()


def run(screen):
    dk.frame(screen, "MPE", "MIDI Polyphonic Expression")
    cfg = deckcfg.load()

    supported = deckcfg.mpe_supported()
    if not supported:
        dk.label(screen.group,
            lv.SYMBOL.WARNING + "  This firmware has no MPE support -- flash your "
            "MPE build to activate. Settings below are saved and applied then.",
            24, 84, color=dk.ORANGE, font=dk.FONT_S, w=tulip.screen_size()[0] - 48)

    body = dk.scroll_body(screen, top=132 if not supported else 118)

    # Enable
    r = dk.row(body)
    dk.label(r, "Enable MPE", color=dk.WHITE)
    en = dk.button(r, "On" if cfg.get('mpe') else "Off", w=120, h=52,
        bg=(dk.GREEN if cfg.get('mpe') else dk.SURFACE2))
    en.add_event_cb(_toggle('mpe', en), lv.EVENT.CLICKED, None)

    # Member channels
    r = dk.row(body, h=92)
    col = _vlabelcol(r, "Member channels")
    _w['mlabel'] = dk.label(col, "%d channels" % cfg.get('mpe_members', 15),
        color=dk.MUTED, font=dk.FONT_S)
    dk.slider(r, cfg.get('mpe_members', 15), 1, 15, w=360, cb=_members_cb, color=dk.ACCENT)

    # Bend range
    r = dk.row(body, h=92)
    col = _vlabelcol(r, "Pitch bend range")
    _w['blabel'] = dk.label(col, "+/- %d semitones" % cfg.get('mpe_bend', 48),
        color=dk.MUTED, font=dk.FONT_S)
    dk.slider(r, cfg.get('mpe_bend', 48), 1, 96, w=360, cb=_bend_cb, color=dk.ORANGE)

    # Listen channel (the MPE zone master; ch16 = upper zone, else lower)
    r = dk.row(body, h=92)
    col = _vlabelcol(r, "Listen channel")
    _w['chlabel'] = dk.label(col, "Zone: %s" %
        ("upper" if cfg.get('midi_channel', 1) == 16 else "lower"),
        color=dk.MUTED, font=dk.FONT_S)
    dk.stepper(r, cfg.get('midi_channel', 1), 1, 16, _channel_cb, fmt="Channel %d", w=230)

    # Expression
    r = dk.row(body, h=92)
    col = _vlabelcol(r, "Per-note expression")
    dk.label(col, "pressure -> level, slide -> filter", color=dk.MUTED, font=dk.FONT_S)
    ex = dk.button(r, "On" if cfg.get('mpe_expression') else "Off", w=120, h=52,
        bg=(dk.GREEN if cfg.get('mpe_expression') else dk.SURFACE2))
    ex.add_event_cb(_toggle('mpe_expression', ex), lv.EVENT.CLICKED, None)

    # Status
    _w['status'] = dk.label(screen.group,
        _members_str(cfg) if cfg.get('mpe') else "MPE off",
        24, tulip.screen_size()[1] - 40,
        color=(dk.GREEN if cfg.get('mpe') else dk.MUTED), font=dk.FONT_S)

    screen.present()


def _vlabelcol(row, title):
    # A transparent left-aligned vertical block holding a title + sub-label,
    # so a row can show two lines of text on the left of its control.
    col = lv.obj(row)
    col.set_size(360, 60)
    col.set_style_border_width(0, 0)
    col.set_style_pad_all(0, 0)
    col.set_style_bg_opa(lv.OPA.TRANSP, 0)
    col.remove_flag(lv.obj.FLAG.SCROLLABLE)
    col.set_flex_flow(lv.FLEX_FLOW.COLUMN)
    col.set_style_pad_row(4, 0)
    col.set_flex_align(lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.START, lv.FLEX_ALIGN.START)
    dk.label(col, title, color=dk.TEXT, font=dk.FONT_M)
    return col
