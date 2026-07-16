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
    # clamp the DISPLAYED span like the zone math already does -- this used
    # to happily print "member channels 3-17" (ch 17 doesn't exist) and
    # claim more members than the zone can hold (fresh-eyes F-10)
    m = instr.get('mpe', {})
    n = m.get('members', 15)
    master = instr.get('channel', 1)
    if master == 16:
        lo, hi = max(1, 16 - n), 15
    else:
        lo, hi = master + 1, min(16, master + n)
    eff = hi - lo + 1
    s = "master ch %d, member channels %d-%d" % (master, lo, hi)
    if eff < n:
        s += " (%d of %d fit)" % (eff, n)
    return s


def _apply():
    deckcfg.apply_all()
    instr = _inst()
    on = instr.get('mpe', {}).get('enabled')
    st = _w.get('status')
    if st is not None:
        st.set_text(_members_str(instr) if on else "MPE off")
        st.set_style_text_color(dk.c(dk.GREEN if on else dk.MUTED), 0)


def _disable_tree(obj, disabled):
    """Add/remove DISABLED down a widget tree (rows hold their controls)."""
    try:
        if disabled:
            obj.add_state(lv.STATE.DISABLED)
        else:
            obj.remove_state(lv.STATE.DISABLED)
        for i in range(obj.get_child_count()):
            _disable_tree(obj.get_child(i), disabled)
    except Exception:
        pass


def _apply_dep_state(on):
    """Dim + disable the rows that only mean something while MPE is enabled --
    they rendered fully live with the gate off (UX-REVIEW-6 M2)."""
    for r in _w.get('dep_rows', ()):
        try:
            r.set_style_opa(255 if on else 102, 0)
        except Exception:
            pass
        _disable_tree(r, not on)


def _switch_enabled(v):
    _set_mpe('enabled', v)
    _apply()
    _apply_dep_state(bool(v))
    _render_strip()


def _set_mpe(sub, value):
    deckcfg.set_instrument_mpe(deckcfg.active_instrument(), sub, value)


def _switch_mpe(sub):
    def on_change(v):
        _set_mpe(sub, v)
        _apply()
    return on_change


def _members_cb(e):
    # Per-tick during the drag: readout only. The commit (config write + router
    # restart, which releases and rebuilds every synth) waits for release.
    v = e.get_target_obj().get_value()
    _w['mlabel'].set_text("%d channels" % v)


def _members_done(e):
    v = e.get_target_obj().get_value()
    _set_mpe('members', v)
    _apply()
    _render_strip()


def _bend_cb(e):
    v = e.get_target_obj().get_value()
    _w['blabel'].set_text("+/- %d semitones" % v)


def _bend_done(e):
    v = e.get_target_obj().get_value()
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
    # color legend (right of the title) so the cell colors are self-explanatory
    lx = 210
    for txt, cc in (("master", dk.ACCENT), ("members", dk.TEAL),
                    ("other", dk.GRAY), ("conflict", dk.RED)):
        dk.label(card, txt, lx, 14, color=cc, font=dk.FONT_S)
        lx += len(txt) * 9 + 26

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
            msg, colr = ("%s Zone overlaps ch %s: shrink it or move those"
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
                 _sym('WARNING', "!") + "  This firmware has no MPE support. "
                 "Flash your MPE build to activate. Settings are saved and "
                 "applied then.", 24, 6, color=dk.ORANGE, font=dk.FONT_S,
                 w=w - 48)
        top = 40

    body = dk.scroll_col(content, w - 48, chh - top - 44)
    body.set_pos(24, top)

    on = bool(m.get('enabled'))
    r = dk.row(body)
    dk.label(r, "Enable MPE", color=dk.WHITE)
    # status lives NEXT TO the switch it reflects (was a tiny corner label)
    _w['status'] = dk.label(r, _members_str(instr) if on else "MPE off",
                            color=(dk.GREEN if on else dk.MUTED), font=dk.FONT_S)
    dk.switch(r, on, _switch_enabled)

    # S2: two-column pairs on the landscape panel, so the whole MPE config
    # fits without scrolling. Sliders get a card each (title stacked above the
    # track so the track keeps its width).
    cw = (w - 48 - 16) // 2
    dep = []

    def _pair():
        p = lv.obj(body)
        p.set_width(lv.pct(100))
        p.set_height(lv.SIZE_CONTENT)
        p.set_style_border_width(0, 0)
        p.set_style_pad_all(0, 0)
        p.set_style_bg_opa(lv.OPA.TRANSP, 0)
        p.remove_flag(lv.obj.FLAG.SCROLLABLE)
        p.set_flex_flow(lv.FLEX_FLOW.ROW)
        p.set_style_pad_column(16, 0)
        return p

    def _slider_card(pair, title, sub, value, vmin, vmax, cb, done, color):
        card = lv.obj(pair)
        card.set_size(cw, 110)
        dk._flat(card, radius=16, bg=dk.SURFACE)
        card.remove_flag(lv.obj.FLAG.SCROLLABLE)
        card.set_style_pad_all(0, 0)
        dk.label(card, title, color=dk.TEXT, font=dk.FONT_M).align(
            lv.ALIGN.TOP_LEFT, 20, 12)
        lab = dk.label(card, sub, color=dk.MUTED, font=dk.FONT_S)
        lab.align(lv.ALIGN.TOP_RIGHT, -20, 16)
        s = dk.slider(card, value, vmin, vmax, w=cw - 48, cb=cb, color=color,
                      h=24, on_release=done)
        s.align(lv.ALIGN.BOTTOM_MID, 0, -18)
        return card, lab

    p = _pair()
    card, _w['mlabel'] = _slider_card(p, "Member channels",
                                      "%d channels" % m.get('members', 15),
                                      m.get('members', 15), 1, 15,
                                      _members_cb, _members_done, dk.ACCENT)
    dep.append(card)
    card, _w['blabel'] = _slider_card(p, "Pitch bend range",
                                      "+/- %d semitones" % m.get('bend', 48),
                                      m.get('bend', 48), 1, 96,
                                      _bend_cb, _bend_done, dk.ORANGE)
    dep.append(card)

    p = _pair()
    r = lv.obj(p)
    r.set_size(cw, 92)
    dk._flat(r, radius=16, bg=dk.SURFACE)
    r.remove_flag(lv.obj.FLAG.SCROLLABLE)
    r.set_style_pad_hor(20, 0)
    r.set_flex_flow(lv.FLEX_FLOW.ROW)
    r.set_flex_align(lv.FLEX_ALIGN.SPACE_BETWEEN, lv.FLEX_ALIGN.CENTER,
                     lv.FLEX_ALIGN.CENTER)
    dep.append(r)
    col = _vcol(r, "Listen channel")
    col.set_size(180, 60)
    _w['chlabel'] = dk.label(col, "Zone: %s" %
                             ("upper" if instr.get('channel', 1) == 16
                              else "lower"), color=dk.MUTED, font=dk.FONT_S)
    dk.stepper(r, instr.get('channel', 1), 1, 16, _channel_cb, fmt="Ch %d",
               w=210)

    # Per-note expression -> a deeper sub-panel (push) in the shell.
    r = lv.obj(p)
    r.set_size(cw, 92)
    dk._flat(r, radius=16, bg=dk.SURFACE)
    r.remove_flag(lv.obj.FLAG.SCROLLABLE)
    r.set_style_pad_hor(20, 0)
    r.set_flex_flow(lv.FLEX_FLOW.ROW)
    r.set_flex_align(lv.FLEX_ALIGN.SPACE_BETWEEN, lv.FLEX_ALIGN.CENTER,
                     lv.FLEX_ALIGN.CENTER)
    dep.append(r)
    dk.label(r, "Per-note expression", color=dk.TEXT)
    nav = dk.button(r, "Edit  " + _sym('RIGHT', ">"), w=150, h=52,
                    bg=dk.SURFACE2, font=dk.FONT_S)
    nav.add_event_cb(_open_expression, lv.EVENT.CLICKED, None)

    _w['dep_rows'] = dep
    _apply_dep_state(on)

    # Per-device channel-map strip (kept last so live redraws stay in place).
    _w['strip_parent'] = body
    _w['strip'] = None
    _render_strip()


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

    # What expression routes to -- static descriptions, NOT interactive rows, so
    # they're plain text (no card chrome) to avoid looking tappable.
    dk.label(body, "What it does", color=dk.MUTED, font=dk.FONT_S)
    for title, desc in (("Pressure", "channel pressure controls voice level"),
                        ("Slide (CC74)", "timbre slide controls filter cutoff")):
        dk.label(body, title, color=dk.TEXT, font=dk.FONT_M)
        dk.label(body, desc, color=dk.MUTED, font=dk.FONT_S)


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
