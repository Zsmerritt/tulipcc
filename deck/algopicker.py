# algopicker.py -- the DX7 algorithm picker modal (feature #102).
#
# A modal over lv.layer_top() (same idiom as deckui.confirm/choice) that draws
# ONE algorithm as a node diagram -- six numbered operator boxes stacked per the
# algorithm, orthogonal connectors showing which op modulates which, the
# feedback loop marked in orange, and carriers seated on an output bar at the
# bottom. Left/right arrows scroll 1..32 (wrapping); Select applies the shown
# algorithm live via on_select(algo).
#
# The routing/layout is pure data from dx7algos.py (derived from AMY's own
# algorithm table); this module only paints it. A redraw is one paint per arrow
# press -- no per-frame work.

import lvgl as lv
import tulip
import deckui as dk
import dx7algos

_state = {'ov': None}

_BOX_W = 62
_BOX_H = 40


def _close():
    ov = _state.get('ov')
    _state['ov'] = None
    if ov is not None:
        try:
            ov.delete()
        except Exception:
            pass


def _rect(parent, x, y, w, h, color, radius=0, border=None):
    o = lv.obj(parent)
    o.set_size(int(w), int(h))
    o.set_pos(int(x), int(y))
    o.set_style_pad_all(0, 0)
    o.set_style_border_width(0, 0)
    o.set_style_radius(radius, 0)
    o.set_style_bg_opa(lv.OPA.COVER, 0)
    o.set_style_bg_color(dk.c(color), 0)
    o.remove_flag(lv.obj.FLAG.SCROLLABLE)
    if border is not None:
        o.set_style_border_width(3, 0)
        o.set_style_border_color(dk.c(border), 0)
    return o


def _vline(parent, cx, y0, y1, color, thick=3):
    if y1 < y0:
        y0, y1 = y1, y0
    _rect(parent, cx - thick // 2, y0, thick, max(1, y1 - y0), color)


def _hline(parent, x0, x1, cy, color, thick=3):
    if x1 < x0:
        x0, x1 = x1, x0
    _rect(parent, x0, cy - thick // 2, max(1, x1 - x0), thick, color)


def _draw_diagram(area, algo, aw, ah):
    """Paint algorithm `algo` into `area` (an lv.obj of size aw x ah)."""
    pos, r, max_x, max_depth = dx7algos.layout(algo)
    carriers = set(r['carriers'])
    feedback = set(r['feedback'])

    col_w = aw / (max_x + 1.0)
    bar_h = 6
    bar_y = ah - bar_h - 4
    # rows grow UPWARD from just above the output bar; clamp row pitch to fit.
    avail = bar_y - 8
    row_pitch = _BOX_H + 26
    if (max_depth + 1) * row_pitch > avail and max_depth > 0:
        row_pitch = avail / (max_depth + 1)
    bottom_cy = bar_y - 14 - _BOX_H / 2.0

    def cx_of(op):
        x, _ = pos[op]
        return (x + 0.5) * col_w

    def cy_of(op):
        _, d = pos[op]
        return bottom_cy - d * row_pitch

    # connectors first (under the boxes): orthogonal elbow mod-bottom -> tgt-top
    for mod, tgt in r['edges']:
        mx, tx = cx_of(mod), cx_of(tgt)
        my = cy_of(mod) + _BOX_H / 2.0            # modulator bottom
        ty = cy_of(tgt) - _BOX_H / 2.0            # target top
        midy = (my + ty) / 2.0
        _vline(area, mx, my, midy, dk.PLACEHOLDER)
        if abs(mx - tx) > 1:
            _hline(area, mx, tx, midy, dk.PLACEHOLDER)
        _vline(area, tx, midy, ty, dk.PLACEHOLDER)

    # output bar + carrier drops
    xs = [cx_of(op) for op in carriers]
    if xs:
        _hline(area, min(xs) - _BOX_W / 2, max(xs) + _BOX_W / 2, bar_y, dk.TEAL,
               thick=bar_h)
    for op in carriers:
        _vline(area, cx_of(op), cy_of(op) + _BOX_H / 2.0, bar_y, dk.TEAL)

    # operator boxes on top
    for op in range(1, 7):
        cx, cy = cx_of(op), cy_of(op)
        is_carrier = op in carriers
        bg = dk.ACCENT if is_carrier else dk.SURFACE2
        border = dk.ORANGE if op in feedback else None
        box = _rect(area, cx - _BOX_W / 2, cy - _BOX_H / 2, _BOX_W, _BOX_H,
                    bg, radius=8, border=border)
        lbl = dk.label(box, str(op), color=dk.WHITE, font=dk.FONT_M)
        lbl.center()
        if op in feedback:
            # a small orange self-loop hooking off the box's top-right corner
            fx = cx + _BOX_W / 2
            fy = cy - _BOX_H / 2
            _hline(area, fx, fx + 14, fy + 6, dk.ORANGE)
            _vline(area, fx + 14, fy + 6, fy + 20, dk.ORANGE)
            _hline(area, fx, fx + 14, fy + 20, dk.ORANGE)


def _render(card, algo):
    """(Re)draw the whole modal body for `algo`, replacing any prior diagram."""
    algo = dx7algos.clamp(algo)
    _state['algo'] = algo
    old = _state.get('body')
    if old is not None:
        try:
            old.delete()
        except Exception:
            pass
    cw = _state['cw']
    # height stops short of the action bar (buttons live on the card at y=ch-76,
    # ch=540) so this transparent body never overlaps / swallows their taps.
    body = lv.obj(card)
    body.set_size(cw, 400)
    body.set_pos(0, 48)
    body.set_style_pad_all(0, 0)
    body.set_style_border_width(0, 0)
    body.set_style_bg_opa(lv.OPA.TRANSP, 0)
    body.remove_flag(lv.obj.FLAG.SCROLLABLE)
    _state['body'] = body

    dk.label(body, "Algorithm %d" % algo, 32, 0, color=dk.WHITE, font=dk.FONT_L)
    s = dx7algos.summary(algo)
    carr = ", ".join(str(o) for o in s['carriers'])
    fb = (", ".join(str(o) for o in s['feedback'])) or "none"
    dk.label(body, "Carriers: %s      Feedback: %s" % (carr, fb),
             32, 40, color=dk.MUTED, font=dk.FONT_S)

    area_x, area_y, area_w, area_h = 32, 74, cw - 64, 320
    area = lv.obj(body)
    area.set_size(area_w, area_h)
    area.set_pos(area_x, area_y)
    dk._flat(area, radius=14, bg=dk.BG)
    area.remove_flag(lv.obj.FLAG.SCROLLABLE)
    area.set_style_pad_all(0, 0)
    _draw_diagram(area, algo, area_w, area_h)


def _step(delta):
    algo = _state.get('algo', 1)
    # wrap 1..32
    algo = ((algo - 1 + delta) % dx7algos.NUM_ALGOS) + 1
    _render(_state['card'], algo)


def open_modal(algo, on_select):
    """Open the picker showing `algo` (1..32). Select runs on_select(algo)."""
    _close()
    w, h = tulip.screen_size()
    ov = lv.obj(lv.layer_top())
    ov.set_size(w, h)
    ov.set_pos(0, 0)
    ov.set_style_border_width(0, 0)
    ov.set_style_pad_all(0, 0)
    ov.set_style_bg_color(dk.c(dk.BG), 0)
    ov.set_style_bg_opa(200, 0)
    ov.remove_flag(lv.obj.FLAG.SCROLLABLE)
    _state['ov'] = ov

    cw, ch = 760, 540
    card = lv.obj(ov)
    card.set_size(cw, ch)
    card.center()
    dk._flat(card, radius=20, bg=dk.SURFACE)
    card.remove_flag(lv.obj.FLAG.SCROLLABLE)
    card.set_style_pad_all(0, 0)
    _state['card'] = card
    _state['cw'] = cw
    _state['body'] = None

    # bottom action bar: [<]  Cancel   Select  [>]
    prev = dk.button(card, "<", w=64, h=60, bg=dk.SURFACE2, font=dk.FONT_L)
    prev.set_pos(32, ch - 76)
    prev.add_event_cb(lambda e: (_step(-1)
                      if e.get_code() == lv.EVENT.CLICKED else None),
                      lv.EVENT.CLICKED, None)
    nxt = dk.button(card, ">", w=64, h=60, bg=dk.SURFACE2, font=dk.FONT_L)
    nxt.set_pos(cw - 64 - 32, ch - 76)
    nxt.add_event_cb(lambda e: (_step(1)
                     if e.get_code() == lv.EVENT.CLICKED else None),
                     lv.EVENT.CLICKED, None)

    cancel = dk.button(card, "Cancel", w=210, h=60, bg=dk.SURFACE2,
                       font=dk.FONT_M)
    cancel.set_pos(cw // 2 - 210 - 12, ch - 76)
    cancel.add_event_cb(lambda e: (_close()
                        if e.get_code() == lv.EVENT.CLICKED else None),
                        lv.EVENT.CLICKED, None)

    sel = dk.button(card, "Select", w=210, h=60, bg=dk.ACCENT, font=dk.FONT_M)
    sel.set_pos(cw // 2 + 12, ch - 76)

    def _do_select(e):
        if e.get_code() != lv.EVENT.CLICKED:
            return
        chosen = _state.get('algo', 1)
        _close()
        try:
            on_select(chosen)
        except Exception:
            pass
    sel.add_event_cb(_do_select, lv.EVENT.CLICKED, None)

    _render(card, algo)
    return ov
