# mpe.py -- enable/configure MPE for the active instrument.
#
# MPE gives every held note its own pitch bend, pressure and slide. This edits
# the active instrument's nested mpe config. Used as a pushed sub-panel from the
# rack editor (Home > Instruments > edit > MPE) and standalone from the launcher.
# Per-note expression opens as a deeper sub-panel (Back pops one level at a time).

import tulip
import deckui as dk
import deckcfg
import lvgl as lv

_w = {}


def _sym(name, fallback):
    return getattr(lv.SYMBOL, name, fallback) if hasattr(lv, 'SYMBOL') else fallback


def _inst():
    return deckcfg.get_instrument(deckcfg.active_instrument()) or {}


def _mpe():
    return _inst().get('mpe', {})


def _members_str(instr):
    m = instr.get('mpe', {})
    n = m.get('members', 15)
    master = instr.get('channel', 1)
    if master == 16:
        lo, hi = 16 - n, 15
    else:
        lo, hi = master + 1, master + n
    return "master ch %d, member channels %d-%d" % (master, lo, hi)


def _apply():
    deckcfg.apply_all()
    instr = _inst()
    on = instr.get('mpe', {}).get('enabled')
    st = _w.get('status')
    if st is not None:
        st.set_text(_members_str(instr) if on else "MPE off")
        st.set_style_text_color(dk.c(dk.GREEN if on else dk.MUTED), 0)


def _set_mpe(sub, value):
    deckcfg.set_instrument_mpe(deckcfg.active_instrument(), sub, value)


def _switch_mpe(sub):
    def on_change(v):
        _set_mpe(sub, v)
        _apply()
    return on_change


def _members_cb(e):
    v = e.get_target_obj().get_value()
    _w['mlabel'].set_text("%d channels" % v)
    _set_mpe('members', v)
    _apply()
    _render_strip()


def _bend_cb(e):
    v = e.get_target_obj().get_value()
    _w['blabel'].set_text("+/- %d semitones" % v)
    _set_mpe('bend', v)
    _apply()


def _channel_cb(ch):
    deckcfg.set_instrument(deckcfg.active_instrument(), 'channel', ch)
    _w['chlabel'].set_text("Zone: %s" % ("upper" if ch == 16 else "lower"))
    _apply()
    _render_strip()


def _render_strip():
    """Draw/redraw the per-device channel-map strip (16 slots): the active
    instrument's zone (master + members) vs other instruments on the device,
    with an overlap warning. Kept LAST in the body flex column so a
    delete + re-add on live edits keeps its position."""
    body = _w.get('strip_parent')
    if body is None:
        return
    old = _w.get('strip')
    if old is not None:
        try:
            old.delete()
        except Exception:
            pass
    import channels
    instr = _inst()
    device = instr.get('device', 'internal')
    mpe_on = deckcfg.mpe_enabled()
    active = deckcfg.active_instrument()
    insts = deckcfg.instruments()
    slots = channels.channel_map(insts, device, mpe_on, active_iid=active)

    card = lv.obj(body)
    card.set_width(lv.pct(100))
    card.set_height(128)
    dk._flat(card, radius=16, bg=dk.SURFACE)
    card.remove_flag(lv.obj.FLAG.SCROLLABLE)
    card.set_style_pad_all(0, 0)
    _w['strip'] = card
    dk.label(card, "Channel map", 16, 10, color=dk.TEXT, font=dk.FONT_M)

    cw, gap, x0, y0 = 52, 4, 16, 42
    for s in slots:
        cell = lv.obj(card)
        cell.set_size(cw, 40)
        cell.set_pos(x0 + (s['ch'] - 1) * (cw + gap), y0)
        col = dk.SURFACE2
        if s['conflict']:
            col = dk.RED              # zone channel also claimed by another instr
        elif s['master'] or (s['mine'] and not s['member']):
            col = dk.ACCENT           # this instrument's master / single channel
        elif s['member']:
            col = dk.TEAL             # this instrument's MPE member channels
        elif s['busy']:
            col = dk.GRAY             # another instrument on the device
        dk._flat(cell, radius=8, bg=col)
        cell.remove_flag(lv.obj.FLAG.SCROLLABLE)
        dk.label(cell, str(s['ch']), color=dk.WHITE, font=dk.FONT_S).center()

    m = instr.get('mpe', {})
    if mpe_on and m.get('enabled'):
        fits, conflicts = channels.zone_fits(insts, device,
                                             instr.get('channel', 1),
                                             m.get('members', 15), active, True)
        if fits:
            msg, colr = ("Zone fits: master ch%d + %d members"
                         % (instr.get('channel', 1), m.get('members', 15)),
                         dk.MUTED)
        else:
            msg, colr = ("%s Zone overlaps ch %s -- shrink it or move those"
                         % (_sym('WARNING', '!'),
                            ",".join(str(c) for c in conflicts)), dk.ORANGE)
        dk.label(card, msg, 16, 92, color=colr, font=dk.FONT_S,
                 w=tulip.screen_size()[0] - 100)


def _open_expression(e):
    if e.get_code() != lv.EVENT.CLICKED:
        return
    sh = _w.get('shell')
    if sh is not None:
        sh.push(expression_panel, "Per-note expression", key='mpe_expr')


def _vcol(row, title):
    col = lv.obj(row)
    col.set_size(360, 60)
    col.set_style_border_width(0, 0)
    col.set_style_pad_all(0, 0)
    col.set_style_bg_opa(lv.OPA.TRANSP, 0)
    col.remove_flag(lv.obj.FLAG.SCROLLABLE)
    col.set_flex_flow(lv.FLEX_FLOW.COLUMN)
    col.set_style_pad_row(4, 0)
    col.set_flex_align(lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.START,
                       lv.FLEX_ALIGN.START)
    dk.label(col, title, color=dk.TEXT, font=dk.FONT_M)
    return col


def _rebuild():
    if _w.get('content') is not None:
        try:
            _w['content'].delete()
        except Exception:
            pass
        _w['content'] = None
    base = _w['base']
    chh = _w['ch']
    w = tulip.screen_size()[0]

    content = lv.obj(base)
    content.set_pos(0, _w['ctop'])
    content.set_size(w, chh)
    dk._flat(content, bg=dk.BG)
    _w['content'] = content
    instr = _inst()
    m = instr.get('mpe', {})

    # Global gate (C.4): when MPE is off, show nothing but a pointer to Settings.
    if not deckcfg.mpe_enabled():
        dk.label(content, "MPE is turned off. Enable it in Settings to "
                 "configure per-instrument MPE.", 24, 8, color=dk.MUTED,
                 font=dk.FONT_S, w=w - 48)
        return

    supported = deckcfg.mpe_supported()
    top = 6
    if not supported:
        dk.label(content,
                 _sym('WARNING', "!") + "  This firmware has no MPE support -- "
                 "flash your MPE build to activate. Settings are saved and "
                 "applied then.", 24, 6, color=dk.ORANGE, font=dk.FONT_S,
                 w=w - 48)
        top = 40

    body = dk.scroll_col(content, w - 48, chh - top - 44)
    body.set_pos(24, top)

    r = dk.row(body)
    dk.label(r, "Enable MPE", color=dk.WHITE)
    dk.switch(r, bool(m.get('enabled')), _switch_mpe('enabled'))

    r = dk.row(body, h=92)
    col = _vcol(r, "Member channels")
    _w['mlabel'] = dk.label(col, "%d channels" % m.get('members', 15),
                            color=dk.MUTED, font=dk.FONT_S)
    dk.slider(r, m.get('members', 15), 1, 15, w=360, cb=_members_cb,
              color=dk.ACCENT)

    r = dk.row(body, h=92)
    col = _vcol(r, "Pitch bend range")
    _w['blabel'] = dk.label(col, "+/- %d semitones" % m.get('bend', 48),
                            color=dk.MUTED, font=dk.FONT_S)
    dk.slider(r, m.get('bend', 48), 1, 96, w=360, cb=_bend_cb, color=dk.ORANGE)

    r = dk.row(body, h=92)
    col = _vcol(r, "Listen channel")
    _w['chlabel'] = dk.label(col, "Zone: %s" %
                             ("upper" if instr.get('channel', 1) == 16
                              else "lower"), color=dk.MUTED, font=dk.FONT_S)
    dk.stepper(r, instr.get('channel', 1), 1, 16, _channel_cb, fmt="Channel %d",
               w=230)

    # Per-note expression -> a deeper sub-panel (push) in the shell.
    r = dk.row(body)
    dk.label(r, "Per-note expression", color=dk.TEXT)
    nav = dk.button(r, "Edit  " + _sym('RIGHT', ">"), w=150, h=52,
                    bg=dk.SURFACE2, font=dk.FONT_S)
    nav.add_event_cb(_open_expression, lv.EVENT.CLICKED, None)

    # Per-device channel-map strip (kept last so live redraws stay in place).
    _w['strip_parent'] = body
    _w['strip'] = None
    _render_strip()

    _w['status'] = dk.label(content,
                            _members_str(instr) if m.get('enabled') else "MPE off",
                            24, chh - 36,
                            color=(dk.GREEN if m.get('enabled') else dk.MUTED),
                            font=dk.FONT_S)


def expression_panel(parent, shell=None):
    w = tulip.screen_size()[0]
    m = _mpe()
    dk.label(parent, "Per-note expression", 24, 16, color=dk.WHITE, font=dk.FONT_L)
    dk.label(parent, "Route MPE pressure and slide into the AMY voice.",
             24, 56, color=dk.MUTED, font=dk.FONT_S, w=w - 48)
    body = dk.scroll_col(parent, w - 48, tulip.screen_size()[1] - 200)
    body.set_pos(24, 96)

    r = dk.row(body)
    dk.label(r, "Enable expression", color=dk.WHITE)
    dk.switch(r, bool(m.get('expression')), _switch_mpe('expression'))

    r = dk.row(body, h=92)
    col = _vcol(r, "Pressure")
    dk.label(col, "channel pressure -> voice level", color=dk.MUTED,
             font=dk.FONT_S)

    r = dk.row(body, h=92)
    col = _vcol(r, "Slide (CC74)")
    dk.label(col, "timbre slide -> filter cutoff", color=dk.MUTED, font=dk.FONT_S)


def panel(parent, shell=None):
    import homeshell
    _w.clear()
    _w['base'] = parent
    _w['shell'] = shell
    _w['screen'] = shell.screen if shell is not None else None
    _w['content'] = None
    _w['ctop'] = 8
    _w['ch'] = (tulip.screen_size()[1] - homeshell.BAR_H) - 8
    _rebuild()


def run(screen):
    _w.clear()
    _w['base'] = screen.group
    _w['shell'] = None
    _w['screen'] = screen
    _w['content'] = None
    _w['ctop'] = 118
    _w['ch'] = tulip.screen_size()[1] - 118
    dk.frame(screen, "MPE", "configure MPE for the active instrument")
    _rebuild()
    screen.present()
