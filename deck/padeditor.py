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


def _audition(note):
    kit = _live_kit()
    try:
        if kit is not None:
            kit.note_on(note, 1.0)
        else:
            import synthkits
            key = synthkits.kit_notes(_kit_key()).get(note)
            if key:
                synthkits.audition(key, _overrides(note))
    except Exception:
        pass


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


def _select(note):
    _s['note'] = note
    for b, n in _s.get('pads', []):
        b.set_style_bg_color(dk.c(dk.ACCENT if n == note else dk.SURFACE), 0)
    card = _s.get('card')
    if card is None:
        return
    card.clean()
    import synthkits
    key = synthkits.kit_notes(_kit_key() or '').get(note, '')
    dk.label(card, "%s  (note %d)" % (_NOTE_NAMES.get(note, 'Pad'), note),
             color=dk.WHITE, font=dk.FONT_M)
    dk.label(card, synthkits.hit_name(key), color=dk.MUTED, font=dk.FONT_S)
    ov = _overrides(note)
    for pkey, label, vmin, vmax, dflt, scale in _PARAMS:
        r = dk.row(card, h=56, bg=dk.SURFACE2)
        dk.label(r, label, color=dk.TEXT, font=dk.FONT_S)
        cur = ov.get(pkey)
        cur = dflt if cur is None else (cur / scale if scale != 1.0 else cur)
        dk.slider(r, int(round(cur)), vmin, vmax, w=_s['cw'] - 210,
                  cb=(lambda v, pk=pkey, sc=scale:
                      _apply(note, pk, v * sc if sc != 1.0 else v, False)),
                  on_release=(lambda v, pk=pkey, sc=scale:
                              _apply(note, pk, v * sc if sc != 1.0 else v, True)))
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
