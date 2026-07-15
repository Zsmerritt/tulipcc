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

# The instrument's TYPE (chosen in the editor) scopes the picker to one engine's
# patches -- so we build a small list, not all 257. Drums use the pad editor.
_TYPE_RANGE = {'juno6': (0, 128), 'dx7': (128, 256), 'piano': (256, 257)}
_TYPE_NAME = {'juno6': 'Juno-6', 'dx7': 'DX7', 'piano': 'Piano', 'drums': 'Kits'}

_s = {}


def _inst():
    return deckcfg.get_instrument(deckcfg.active_instrument())


def _type():
    return (_inst() or {}).get('type', 'juno6')


def _nums(favs):
    """This instrument type's patch numbers, favorites first (or favorites only
    when the filter is on). `favs` is the favorites SET, loaded once by the
    caller -- calling deckcfg.is_favorite() per patch was a config load per
    row."""
    lo, hi = _TYPE_RANGE.get(_type(), (0, 128))
    nums = [n for n in range(lo, hi) if 0 <= n < len(patches)]
    if _s.get('fav_only'):
        return [n for n in nums if n in favs]
    starred = [n for n in nums if n in favs]
    return starred + [n for n in nums if n not in favs]


def _select_patch(patch):
    iid = deckcfg.active_instrument()
    deckcfg.set_instrument(iid, 'patch', patch)
    deckcfg.apply_all()
    if _s.get('name') is not None:
        _s['name'].set_text("current: " + patches[patch])
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


def _toggle_fav(n, star):
    fav = deckcfg.toggle_favorite(n)
    try:
        star.set_style_bg_color(dk.c(dk.ORANGE if fav else dk.SURFACE2), 0)
    except Exception:
        pass
    if _s.get('fav_only'):
        _build_list()      # an unstarred patch drops out of the favorites filter


def _row(body, n, cur, favs):
    b = lv.button(body)
    b.set_width(lv.pct(100))
    b.set_height(56)
    dk._flat(b, radius=12, bg=(dk.ACCENT if n == cur else dk.SURFACE))
    lb = lv.label(b)
    lb.set_text(patches[n])
    lb.set_style_text_color(dk.c(dk.TEXT), 0)
    lb.set_style_text_font(dk.FONT_M, 0)
    lb.align(lv.ALIGN.LEFT_MID, 12, 0)
    b.add_event_cb((lambda pn: (lambda e: _select_patch(pn)
                    if e.get_code() == lv.EVENT.CLICKED else None))(n),
                   lv.EVENT.CLICKED, None)
    # star toggles favorite (orange when starred). As a child button it captures
    # its own taps, so starring doesn't also select the patch.
    star = dk.button(b, "*", w=48, h=44, font=dk.FONT_L,
                     bg=(dk.ORANGE if n in favs else dk.SURFACE2))
    star.align(lv.ALIGN.RIGHT_MID, -8, 0)
    star.add_event_cb((lambda pn, st: (lambda e: _toggle_fav(pn, st)
                       if e.get_code() == lv.EVENT.CLICKED else None))(n, star),
                      lv.EVENT.CLICKED, None)
    _s['rows'].append((b, n))


def _build_list():
    body = _s.get('listbody')
    if body is None:
        return
    body.clean()
    _s['rows'] = []
    cur = (_inst() or {}).get('patch', 0)
    favs = set(deckcfg.favorites())      # loaded once for the whole list
    q = _s.get('query', '').strip().lower()
    shown = 0
    for n in _nums(favs):
        if q and q not in patches[n].lower():
            continue
        _row(body, n, cur, favs)
        shown += 1
    if shown == 0:
        if _s.get('fav_only') and not q:
            msg = "No favorites in %s yet -- tap the * on a patch." % \
                _TYPE_NAME.get(_type(), '')
        else:
            msg = "No patches match \"%s\"." % _s.get('query', '')
        dk.label(body, msg, color=dk.MUTED, font=dk.FONT_S)


def _toggle_favonly(btn):
    _s['fav_only'] = not _s.get('fav_only')
    try:
        btn.set_style_bg_color(
            dk.c(dk.ORANGE if _s['fav_only'] else dk.SURFACE2), 0)
    except Exception:
        pass
    _build_list()


def _kb_height():
    """Height of the soft keyboard if it's on screen, else 0."""
    try:
        import ui
        kb = getattr(ui, 'lv_soft_kb', None)
        if kb is None:
            return 0
        try:
            kb.update_layout()
            h = kb.get_height()
            if h > 0:
                return h
        except Exception:
            pass
        return tulip.screen_size()[1] // 2   # LVGL keyboard default: 50%
    except Exception:
        return 0


def _layout_list():
    """Size the results list to the space above the soft keyboard, so live
    search shows several rows instead of ONE while typing (UX-REVIEW-6 M4);
    full height again once the keyboard closes."""
    body = _s.get('listbody')
    if body is None:
        return
    full = _s['ch'] - 176
    h = full
    kb = _kb_height()
    if kb:
        h = max(120, tulip.screen_size()[1] - kb - _s.get('list_top_abs', 236) - 8)
        if h > full:
            h = full
    try:
        body.set_height(h)
    except Exception:
        pass


def _kb_layout_cb(e):
    # after focus/defocus the keyboard may be opening/closing this tick; relayout
    # on the next one so its size is real
    try:
        tulip.defer(lambda x: _layout_list(), 0, 60)
    except Exception:
        _layout_list()


def _search_changed(e):
    # Debounced: rebuilding ~130 rows (several hundred LVGL objects) per
    # keystroke makes typing crawl. Rebuild once, shortly after typing pauses;
    # the generation counter cancels rebuilds superseded by newer keystrokes.
    ta = _s.get('searchta')
    try:
        _s['query'] = ta.get_text() if ta is not None else ''
    except Exception:
        _s['query'] = ''
    _s['search_gen'] = _s.get('search_gen', 0) + 1
    gen = _s['search_gen']

    def _do(x):
        if _s.get('search_gen') == gen and _s.get('listbody') is not None:
            _build_list()
    try:
        tulip.defer(_do, 0, 250)
    except Exception:
        _build_list()      # no defer (host): rebuild inline


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
    _s.setdefault('query', '')
    _s.setdefault('fav_only', False)

    content = lv.obj(base)
    content.set_pos(0, ctop)
    content.set_size(w, chh)
    dk._flat(content, bg=dk.BG)
    _s['content'] = content

    # STABLE title = the collection ("Juno-6 patches"); the current selection
    # lives in the subtitle + highlighted row. The old selection-as-title
    # duplicated the row and went stale under a search filter (UX-REVIEW-6 L2).
    dk.label(content, _TYPE_NAME.get(_type(), 'Patches') + " patches", 24, 6,
             color=dk.WHITE, font=dk.FONT_L)
    _s['name'] = dk.label(content, "current: " + patches[cur], 24, 52,
                          color=dk.MUTED, font=dk.FONT_S)
    # favorites filter (right)
    favbtn = dk.button(content, "* Favorites", w=180, h=44, font=dk.FONT_S,
                       bg=(dk.ORANGE if _s['fav_only'] else dk.SURFACE2))
    favbtn.set_pos(w - 24 - 180, 44)
    favbtn.add_event_cb((lambda bt: (lambda e: _toggle_favonly(bt)
                        if e.get_code() == lv.EVENT.CLICKED else None))(favbtn),
                        lv.EVENT.CLICKED, None)

    # search field + on-screen keyboard button (filters the list live by name)
    sw = w - 48 - 84
    t = dk.text_field(content, text=_s.get('query', ''),
                      placeholder="search patches", w=sw, h=60, font=dk.FONT_M)
    t.group.set_pos(24, 100)
    _s['searchta'] = t.ta
    try:
        t.ta.add_event_cb(_search_changed, lv.EVENT.VALUE_CHANGED, None)
        # keyboard covers the bottom half: shrink the results list while it's
        # up so live search shows more than ONE row (UX-REVIEW-6 M4)
        t.ta.add_event_cb(_kb_layout_cb, lv.EVENT.FOCUSED, None)
        t.ta.add_event_cb(_kb_layout_cb, lv.EVENT.DEFOCUSED, None)
    except Exception:
        pass
    dk.button(content, tulip.lv.SYMBOL.KEYBOARD, w=72, h=60, bg=dk.SURFACE2,
        cb=lambda e: (tulip.keyboard(), _layout_list())).set_pos(w - 24 - 72, 100)

    # patch list
    body = dk.scroll_col(content, w - 48, chh - 176)
    body.set_pos(24, 172)
    _s['listbody'] = body
    _build_list()


def panel(parent, shell=None):
    import homeshell
    _s.clear()
    _s['shell'] = shell
    _s['screen'] = None
    _s['base'] = parent
    _s['content'] = None
    _s['ctop'] = 8
    _s['ch'] = (tulip.screen_size()[1] - homeshell.BAR_H) - 8
    # absolute screen y of the results list (for keyboard-aware sizing)
    _s['list_top_abs'] = homeshell.BAR_H + _s['ctop'] + 172
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
    _s['list_top_abs'] = _s['ctop'] + 172
    dk.frame(screen, "Patch", "pick a sound for the active instrument")
    _rebuild_content()
    screen.present()
