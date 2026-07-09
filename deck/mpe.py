# mpe.py -- enable/configure MPE for the selected instance.
#
# MPE gives every held note its own pitch bend, pressure and slide, routed by
# AMY's C layer to the zone master synth. The instance selector chooses which
# AMY (Tulip or an AMYboard) we're configuring. Needs firmware with MPE support.

import tulip
import deckui as dk
import deckcfg
import lvgl as lv

_w = {}


def _inst():
    return deckcfg.get_instance(deckcfg.active_index())


def _members_str(inst):
    n = inst.get('mpe_members', 15)
    master = inst.get('channel', 1)
    if master == 16:
        lo, hi = 16 - n, 15
    else:
        lo, hi = master + 1, master + n
    return "master ch %d, member channels %d-%d" % (master, lo, hi)


def _apply():
    i = deckcfg.active_index()
    deckcfg.apply_instance(i)
    inst = _inst()
    on = inst.get('mpe')
    _w['status'].set_text(_members_str(inst) if on else "MPE off")
    _w['status'].set_style_text_color(dk.c(dk.GREEN if on else dk.MUTED), 0)


def _set(key, value):
    deckcfg.set_instance(deckcfg.active_index(), key, value)


def _toggle(key, btn):
    def cb(e):
        v = not _inst().get(key)
        _set(key, v)
        _paint_toggle(btn, v)
        _apply()
    return cb


def _paint_toggle(btn, v):
    btn.set_style_bg_color(dk.c(dk.GREEN if v else dk.SURFACE2), 0)
    btn.get_child(0).set_text("On" if v else "Off")


def _members_cb(e):
    v = e.get_target_obj().get_value()
    _w['mlabel'].set_text("%d channels" % v)
    _set('mpe_members', v)
    _apply()


def _bend_cb(e):
    v = e.get_target_obj().get_value()
    _w['blabel'].set_text("+/- %d semitones" % v)
    _set('mpe_bend', v)
    _apply()


def _channel_cb(ch):
    _set('channel', ch)
    _w['chlabel'].set_text("Zone: %s" % ("upper" if ch == 16 else "lower"))
    _apply()


def _select_instance(i):
    deckcfg.set_active(i)
    _s = _w.get('screen')
    if _s is not None:
        _rebuild(_s)


def _build_selector(screen):
    insts = deckcfg.instances()
    active = deckcfg.active_index()
    _w['selbtns'] = []
    x = 300
    for i, inst in enumerate(insts):
        b = dk.button(screen.group, inst.get('name', 'Inst %d' % i), w=150, h=44,
            bg=(dk.ACCENT if i == active else dk.SURFACE2), font=dk.FONT_S)
        b.set_pos(x, 30)
        b.add_event_cb((lambda idx: (lambda e: _select_instance(idx)))(i),
                       lv.EVENT.CLICKED, None)
        _w['selbtns'].append(b)
        x += 158


def _rebuild(screen):
    if _w.get('content') is not None:
        _w['content'].delete()
    content = lv.obj(screen.group)
    content.set_pos(0, 140)
    content.set_size(tulip.screen_size()[0], tulip.screen_size()[1] - 140)
    dk._flat(content, bg=dk.BG)
    _w['content'] = content
    inst = _inst()

    supported = deckcfg.mpe_supported()
    top = 6
    if not supported:
        dk.label(content,
            lv.SYMBOL.WARNING + "  This firmware has no MPE support -- flash your "
            "MPE build to activate. Settings are saved and applied then.",
            24, 6, color=dk.ORANGE, font=dk.FONT_S, w=tulip.screen_size()[0] - 48)
        top = 40

    body = dk.scroll_body(screen, top=0)
    body.set_parent(content)
    body.set_pos(24, top)
    body.set_size(tulip.screen_size()[0] - 48, tulip.screen_size()[1] - 140 - top - 44)

    r = dk.row(body)
    dk.label(r, "Enable MPE", color=dk.WHITE)
    en = dk.button(r, "On" if inst.get('mpe') else "Off", w=120, h=52,
        bg=(dk.GREEN if inst.get('mpe') else dk.SURFACE2))
    en.add_event_cb(_toggle('mpe', en), lv.EVENT.CLICKED, None)

    r = dk.row(body, h=92)
    col = _vlabelcol(r, "Member channels")
    _w['mlabel'] = dk.label(col, "%d channels" % inst.get('mpe_members', 15),
        color=dk.MUTED, font=dk.FONT_S)
    dk.slider(r, inst.get('mpe_members', 15), 1, 15, w=360, cb=_members_cb, color=dk.ACCENT)

    r = dk.row(body, h=92)
    col = _vlabelcol(r, "Pitch bend range")
    _w['blabel'] = dk.label(col, "+/- %d semitones" % inst.get('mpe_bend', 48),
        color=dk.MUTED, font=dk.FONT_S)
    dk.slider(r, inst.get('mpe_bend', 48), 1, 96, w=360, cb=_bend_cb, color=dk.ORANGE)

    r = dk.row(body, h=92)
    col = _vlabelcol(r, "Listen channel")
    _w['chlabel'] = dk.label(col, "Zone: %s" %
        ("upper" if inst.get('channel', 1) == 16 else "lower"),
        color=dk.MUTED, font=dk.FONT_S)
    dk.stepper(r, inst.get('channel', 1), 1, 16, _channel_cb, fmt="Channel %d", w=230)

    r = dk.row(body, h=92)
    col = _vlabelcol(r, "Per-note expression")
    dk.label(col, "pressure -> level, slide -> filter", color=dk.MUTED, font=dk.FONT_S)
    ex = dk.button(r, "On" if inst.get('mpe_expression') else "Off", w=120, h=52,
        bg=(dk.GREEN if inst.get('mpe_expression') else dk.SURFACE2))
    ex.add_event_cb(_toggle('mpe_expression', ex), lv.EVENT.CLICKED, None)

    _w['status'] = dk.label(content,
        _members_str(inst) if inst.get('mpe') else "MPE off",
        24, tulip.screen_size()[1] - 140 - 36,
        color=(dk.GREEN if inst.get('mpe') else dk.MUTED), font=dk.FONT_S)

    for idx, b in enumerate(_w.get('selbtns', [])):
        b.set_style_bg_color(dk.c(dk.ACCENT if idx == deckcfg.active_index() else dk.SURFACE2), 0)


def _vlabelcol(row, title):
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


def run(screen):
    _w.clear()
    _w['screen'] = screen
    _w['content'] = None
    dk.frame(screen, "MPE", "MIDI Polyphonic Expression")
    _build_selector(screen)
    _rebuild(screen)
    screen.present()
