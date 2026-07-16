# padeditor.py -- per-hit sound design for SYNTHESIZED drum kits.
#
# Pushed from the rack editor (Home > Instruments > edit > Pads) for a drums
# instrument whose kit is a synth kit ('synth:<key>'). Left: a pad grid, one
# pad per GM note in the kit -- tap = audition through the live router (so
# you hear the instrument's bus FX and current overrides). Right: the
# selected pad's sound-design sliders (Tune/Decay/Level/Snap), applied LIVE
# by rebuilding that one hit's mini-synth (SynthKit.retweak) and persisted
# in the instrument's hits{} overrides, which the router replays on rebuild.

import tulip
import deckui as dk
import deckcfg
import lvgl as lv

_s = {}

# GM drum-note display names for the pads (kit roles map onto these)
_NOTE_NAMES = {
    36: 'Kick', 37: 'Rim', 38: 'Snare', 39: 'Clap', 42: 'CH Hat', 44: 'PD Hat',
    45: 'Tom Lo', 46: 'OP Hat', 47: 'Tom Mid', 49: 'Crash', 50: 'Tom Hi',
    51: 'Ride', 54: 'Tamb', 56: 'Cowbell', 60: 'Perc', 63: 'Conga H',
    64: 'Conga L', 70: 'Shaker', 73: 'Guiro', 75: 'Claves',
}

# slider spec: (key, label, min, max, default, to_value, from_value)
_PARAMS = (
    ('tune', 'Tune (semis)', -12, 12, 0, 1.0),
    ('decay', 'Decay %', 25, 400, 100, 0.01),
    ('level', 'Level %', 0, 200, 100, 0.01),
    ('snap', 'Snap %', 0, 200, 100, 0.01),
)


def _inst():
    return deckcfg.get_instrument(deckcfg.active_instrument()) or {}


def _kit_key():
    kit = _inst().get('kit')
    if isinstance(kit, str) and kit.startswith('synth:'):
        return kit[6:]
    return None


def _overrides(note):
    hits = _inst().get('hits') or {}
    return dict(hits.get(str(note)) or hits.get(note) or {})


def _live_kit():
    """The RUNNING SynthKit for this instrument (None if router isn't up)."""
    try:
        import forwarder
        syn = forwarder._state['synths'].get(deckcfg.active_instrument())
        if hasattr(syn, 'retweak'):
            return syn
    except Exception:
        pass
    return None


def _hit_key_for(note):
    """The pad's EFFECTIVE hit: the user's swap if set, else the kit's."""
    sw = _inst().get('hit_swaps') or {}
    k = sw.get(str(note)) or sw.get(note)
    if k:
        return k
    import synthkits
    return synthkits.kit_notes(_kit_key() or '').get(note)


def _audition(note):
    kit = _live_kit()
    try:
        if kit is not None:
            kit.note_on(note, 1.0)
        else:
            import synthkits
            key = _hit_key_for(note)
            if key:
                synthkits.audition(key, _overrides(note))
    except Exception:
        pass


def _set_swap(note, key):
    """Swap a pad to any corpus hit (None = back to the kit's own)."""
    iid = deckcfg.active_instrument()
    instr = deckcfg.get_instrument(iid) or {}
    sw = dict(instr.get('hit_swaps') or {})
    if key is None:
        sw.pop(str(note), None)
        sw.pop(note, None)
    else:
        sw[str(note)] = key
    deckcfg.set_instrument(iid, 'hit_swaps', sw)
    kit = _live_kit()
    if kit is not None:
        try:
            kit.retweak(note, _overrides(note), hit_key=_hit_key_for(note))
        except Exception:
            pass
    _audition(note)


def _apply(note, key, val, commit):
    ov = _overrides(note)
    ov[key] = val
    kit = _live_kit()
    if kit is not None:
        try:
            kit.retweak(note, ov)
        except Exception:
            pass
    if commit:
        iid = deckcfg.active_instrument()
        instr = deckcfg.get_instrument(iid) or {}
        hits = dict(instr.get('hits') or {})
        hits[str(note)] = ov
        deckcfg.set_instrument(iid, 'hits', hits)
    _audition(note)


def _reset(note):
    iid = deckcfg.active_instrument()
    instr = deckcfg.get_instrument(iid) or {}
    hits = dict(instr.get('hits') or {})
    hits.pop(str(note), None)
    hits.pop(note, None)
    deckcfg.set_instrument(iid, 'hits', hits)
    kit = _live_kit()
    if kit is not None:
        try:
            kit.retweak(note, None)
        except Exception:
            pass
    _select(note)          # rebuild sliders at defaults
    _audition(note)


def _open_swap():
    sh = _s.get('shell')
    if sh is not None:
        sh.push(swap_panel, "Swap hit", key='padswap', slow=True)


def swap_panel(parent, shell=None):
    """Alternates picker: browse the whole hit corpus by pack, tap to
    audition (with this pad's current overrides), then Use. The pad keeps
    its Tune/Decay/Level/Snap tweaks across the swap."""
    import synthkits
    note = _s.get('note')
    if note is None:
        dk.label(parent, "Pick a pad first.", 24, 24, color=dk.MUTED)
        return
    w = tulip.screen_size()[0]
    import homeshell
    H = tulip.screen_size()[1] - homeshell.BAR_H
    _s['swap_sel'] = None

    # The pushed panel has NO flex layout -- rows added straight to it all
    # land at (0,0) and the opaque body occluded the action row entirely
    # (X-1: the picker could audition but never COMMIT a swap). Explicit
    # positions: action row on top, browser below it.
    top = dk.row(parent, h=64)
    top.set_pos(0, 0)
    dk.label(top, "%s: tap a hit to hear it" % _NOTE_NAMES.get(note, 'Pad'),
             color=dk.TEXT, font=dk.FONT_S)
    ub = dk.button(top, "Use selected", w=200, h=52, bg=dk.GREEN,
                   font=dk.FONT_S)
    db = dk.button(top, "Kit default", w=180, h=52, bg=dk.SURFACE2,
                   font=dk.FONT_S)

    body = dk.row(parent, h=H - 96)
    body.set_pos(0, 72)
    # scroll_col, not bare lv.obj: without a flex column every pack/hit
    # button stacked at (0,0) and only the last one showed
    packs = dk.scroll_col(body, 260, H - 110, gap=8)
    hits = dk.scroll_col(body, w - 320, H - 110, gap=8)
    # empty-state prompt so the right column isn't a bare void (F-7)
    dk.label(hits, "Pick a pack to browse its hits", color=dk.MUTED,
             font=dk.FONT_S)

    def _pick(key, btn):
        _s['swap_sel'] = key
        old = _s.get('swap_btn')
        if old is not None:
            try:
                old.set_style_bg_color(dk.c(dk.SURFACE), 0)
            except Exception:
                pass
        _s['swap_btn'] = btn
        btn.set_style_bg_color(dk.c(dk.ACCENT), 0)
        try:
            synthkits.audition(key, _overrides(note))
        except Exception:
            pass

    def _show_pack(pack, pbtn=None):
        # highlight the tapped pack (F-7) -- same 2-object recolor as hits
        old = _s.get('swap_packbtn')
        if old is not None:
            try:
                old.set_style_bg_color(dk.c(dk.SURFACE), 0)
            except Exception:
                pass
        if pbtn is not None:
            _s['swap_packbtn'] = pbtn
            pbtn.set_style_bg_color(dk.c(dk.ACCENT), 0)
        hits.clean()
        _s['swap_btn'] = None
        keys = synthkits.pack_hits(pack)
        for key in keys[:150]:
            b = dk.button(hits, synthkits.hit_name(key), w=lv.pct(96), h=52,
                          bg=dk.SURFACE, font=dk.FONT_S)
            b.add_event_cb(
                (lambda k: (lambda e: _pick(k, e.get_target_obj())
                            if e.get_code() == lv.EVENT.CLICKED else None))(key),
                lv.EVENT.CLICKED, None)
        if len(keys) > 150:
            dk.label(hits, "(+%d more in this pack)" % (len(keys) - 150),
                     color=dk.MUTED, font=dk.FONT_S)

    all_packs = sorted(synthkits._load().get('packs', {}))
    for pack in all_packs:
        b = dk.button(packs, pack.replace('_', ' '), w=lv.pct(96), h=52,
                      bg=dk.SURFACE, font=dk.FONT_S)
        b.add_event_cb(
            (lambda p, pb: (lambda e: _show_pack(p, pb)
                            if e.get_code() == lv.EVENT.CLICKED else None)
             )(pack, b),
            lv.EVENT.CLICKED, None)

    def _use(e):
        if e.get_code() != lv.EVENT.CLICKED:
            return
        if _s.get('swap_sel'):
            _set_swap(note, _s['swap_sel'])
        sh = _s.get('shell')
        if sh is not None:
            sh.back()

    def _default(e):
        if e.get_code() != lv.EVENT.CLICKED:
            return
        _set_swap(note, None)
        sh = _s.get('shell')
        if sh is not None:
            sh.back()

    ub.add_event_cb(_use, lv.EVENT.CLICKED, None)
    db.add_event_cb(_default, lv.EVENT.CLICKED, None)


def _select(note):
    _s['note'] = note
    for b, n in _s.get('pads', []):
        b.set_style_bg_color(dk.c(dk.ACCENT if n == note else dk.SURFACE), 0)
    card = _s.get('card')
    if card is None:
        return
    card.clean()
    import synthkits
    key = _hit_key_for(note) or ''
    swapped = bool((_inst().get('hit_swaps') or {}).get(str(note)))
    dk.label(card, "%s  (note %d)" % (_NOTE_NAMES.get(note, 'Pad'), note),
             color=dk.WHITE, font=dk.FONT_M)
    r = dk.row(card, h=56, bg=dk.SURFACE2)
    nl = dk.label(r, synthkits.hit_name(key) + (' *' if swapped else ''),
                  color=dk.MUTED, font=dk.FONT_S, w=_s['cw'] - 220)
    try:
        nl.set_long_mode(lv.label.LONG.DOT)
    except Exception:
        pass
    sb = dk.button(r, "Swap >", w=130, h=44, bg=dk.ACCENT, font=dk.FONT_S)
    sb.add_event_cb(lambda e: (_open_swap()
                               if e.get_code() == lv.EVENT.CLICKED else None),
                    lv.EVENT.CLICKED, None)
    ov = _overrides(note)
    for pkey, label, vmin, vmax, dflt, scale in _PARAMS:
        r = dk.row(card, h=56, bg=dk.SURFACE2)
        dk.label(r, label, color=dk.TEXT, font=dk.FONT_S)
        cur = ov.get(pkey)
        cur = dflt if cur is None else (cur / scale if scale != 1.0 else cur)
        # live numeric readout (X-2): every other slider in the app shows
        # its value; these four were tune-by-ear-only
        unit = '' if pkey == 'tune' else '%'
        vl = dk.label(r, "%d%s" % (int(round(cur)), unit), color=dk.TEAL,
                      font=dk.FONT_S)

        # dk.slider callbacks receive the LVGL EVENT, not the value --
        # treating it as a number made every tick throw and the sliders dead
        def _mk(pk, sc, lbl, un):
            def _cb(e, commit):
                v = e.get_target_obj().get_value()
                try:
                    lbl.set_text("%d%s" % (v, un))
                except Exception:
                    pass
                _apply(note, pk, v * sc if sc != 1.0 else v, commit)
            return _cb
        cbf = _mk(pkey, scale, vl, unit)
        dk.slider(r, int(round(cur)), vmin, vmax, w=_s['cw'] - 260,
                  cb=(lambda e, f=cbf: f(e, False)),
                  on_release=(lambda e, f=cbf: f(e, True)))
    b = dk.button(card, "Reset pad", w=170, h=48, bg=dk.SURFACE2, font=dk.FONT_S,
                  cb=lambda e: _reset(_s['note']))


def panel(parent, shell=None):
    _s['shell'] = shell
    import synthkits
    kit_key = _kit_key()
    w = tulip.screen_size()[0]
    import homeshell
    H = tulip.screen_size()[1] - homeshell.BAR_H
    if kit_key is None:
        dk.label(parent, "Per-pad editing needs a synth kit -- pick one under "
                 "Kit (names ending in '(synth)').", 24, 24, color=dk.MUTED,
                 font=dk.FONT_M, w=w - 48)
        return
    notes = sorted(synthkits.kit_notes(kit_key))
    # left: pad grid | right: selected-pad card
    gw = (w - 48) // 2
    _s['cw'] = w - 48 - gw - 16
    grid = lv.obj(parent)
    grid.set_pos(24, 8)
    grid.set_size(gw, H - 16)
    dk._flat(grid, bg=dk.BG, scroll=True)
    grid.set_flex_flow(lv.FLEX_FLOW.ROW_WRAP)
    grid.set_style_pad_row(10, 0)
    grid.set_style_pad_column(10, 0)
    _s['pads'] = []
    pw = (gw - 3 * 10 - 24) // 3
    for n in notes:
        b = dk.button(grid, "%s\n%d" % (_NOTE_NAMES.get(n, 'Pad'), n),
                      w=pw, h=84, bg=dk.SURFACE, font=dk.FONT_S)
        b.add_event_cb((lambda nn: (lambda e: (_select(nn), _audition(nn))
                        if e.get_code() == lv.EVENT.CLICKED else None))(n),
                       lv.EVENT.CLICKED, None)
        _s['pads'].append((b, n))
    card = lv.obj(parent)
    card.set_pos(24 + gw + 16, 8)
    card.set_size(_s['cw'], H - 16)
    dk._flat(card, radius=16, bg=dk.SURFACE, scroll=True)
    card.set_flex_flow(lv.FLEX_FLOW.COLUMN)
    card.set_style_pad_all(14, 0)
    card.set_style_pad_row(10, 0)
    _s['card'] = card
    if notes:
        _select(notes[0])
