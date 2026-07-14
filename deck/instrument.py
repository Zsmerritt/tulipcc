# instrument.py -- the patch/preset picker for the active instrument.
#
# Used as a pushed sub-panel from the rack editor (Home > Instruments > edit >
# Patch), and standalone from the REPL launcher. Tapping a patch sets it live on
# the active instrument, saves it, and auditions it through the router
# (forwarder.preview). Device/channel/voices/MPE live in the rack editor.

import tulip
import deckui as dk
import deckcfg
from patches import patches
import lvgl as lv

CATS = [("Juno-6", 0, 128, dk.ACCENT),
        ("DX7", 128, 256, dk.PURPLE),
        ("Piano", 256, 257, dk.GREEN)]

_s = {}


def _inst():
    return deckcfg.get_instrument(deckcfg.active_instrument())


def _select_patch(patch):
    iid = deckcfg.active_instrument()
    deckcfg.set_instrument(iid, 'patch', patch)
    deckcfg.apply_all()
    if _s.get('name') is not None:
        _s['name'].set_text(patches[patch])
    try:
        import forwarder
        forwarder.preview(iid)
    except Exception:
        pass
    if _s.get('shell') is not None:
        try:
            _s['shell'].refresh_chips()
        except Exception:
            pass
    for b, n in _s.get('rows', []):
        b.set_style_bg_color(dk.c(dk.ACCENT if n == patch else dk.SURFACE), 0)


def _build_list(body, lo, hi):
    body.clean()
    _s['rows'] = []
    cur = (_inst() or {}).get('patch', 0)
    for n in range(lo, hi):
        b = lv.button(body)
        b.set_width(lv.pct(100))
        b.set_height(56)
        dk._flat(b, radius=12, bg=(dk.ACCENT if n == cur else dk.SURFACE))
        lb = lv.label(b)
        lb.set_text(patches[n])
        lb.set_style_text_color(dk.c(dk.TEXT), 0)
        lb.set_style_text_font(dk.FONT_M, 0)
        lb.align(lv.ALIGN.LEFT_MID, 6, 0)
        b.add_event_cb((lambda pn: (lambda e: _select_patch(pn)))(n),
                       lv.EVENT.CLICKED, None)
        _s['rows'].append((b, n))


def _pick_cat(lo, hi, accent, btn):
    for b in _s['catbtns']:
        b.set_style_bg_color(dk.c(dk.SURFACE2), 0)
    btn.set_style_bg_color(dk.c(accent), 0)
    _build_list(_s['listbody'], lo, hi)


def _rebuild_content():
    if _s.get('content') is not None:
        try:
            _s['content'].delete()
        except Exception:
            pass
        _s['content'] = None
    base = _s['base']
    ctop = _s['ctop']
    chh = _s['ch']
    w = tulip.screen_size()[0]
    inst = _inst() or {}
    cur = inst.get('patch', 0)

    content = lv.obj(base)
    content.set_pos(0, ctop)
    content.set_size(w, chh)
    dk._flat(content, bg=dk.BG)
    _s['content'] = content

    _s['name'] = dk.label(content, patches[cur], 24, 6, color=dk.WHITE,
                          font=dk.FONT_L)

    # category buttons
    _s['catbtns'] = []
    x = 24
    for name, lo, hi, accent in CATS:
        active = lo <= cur < hi
        b = dk.button(content, name, w=150, h=52,
                      bg=(accent if active else dk.SURFACE2), font=dk.FONT_M)
        b.set_pos(x, 48)
        _s['catbtns'].append(b)
        b.add_event_cb((lambda l, h, a, bt: (lambda e: _pick_cat(l, h, a, bt)))(
            lo, hi, accent, b), lv.EVENT.CLICKED, None)
        x += 162

    # patch list
    body = dk.scroll_col(content, w - 48, chh - 116)
    body.set_pos(24, 110)
    _s['listbody'] = body
    for name, lo, hi, accent in CATS:
        if lo <= cur < hi:
            _build_list(body, lo, hi)
            break


def panel(parent, shell=None):
    import homeshell
    _s.clear()
    _s['shell'] = shell
    _s['screen'] = None
    _s['base'] = parent
    _s['content'] = None
    _s['ctop'] = 8
    _s['ch'] = (tulip.screen_size()[1] - homeshell.BAR_H) - 8
    _rebuild_content()


def run(screen):
    # Standalone (REPL launcher): pick the active instrument's patch.
    _s.clear()
    _s['shell'] = None
    _s['screen'] = screen
    _s['base'] = screen.group
    _s['content'] = None
    _s['ctop'] = 118
    _s['ch'] = tulip.screen_size()[1] - 118
    dk.frame(screen, "Patch", "pick a sound for the active instrument")
    _rebuild_content()
    screen.present()
