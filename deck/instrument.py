# instrument.py -- pick the single synth voice for MIDI channel 1.
#
# Tulip boots into a live MIDI synth; this is a touch picker for which patch
# it plays. Juno-6 (0-127), DX7 (128-255) and Piano (256). Tapping a patch
# sets it live, previews a note, and saves it so boot.py restores it.

import tulip
import deckui as dk
import deckcfg
from patches import patches
import lvgl as lv

CATS = [("Juno-6", 0, 128, dk.ACCENT),
        ("DX7", 128, 256, dk.PURPLE),
        ("Piano", 256, 257, dk.GREEN)]

_state = {}


def _master():
    return deckcfg.get('midi_channel', 1)


def _preview():
    import midi
    s = midi.config.get_synth(_master())
    if s is None:
        return
    try:
        s.note_on(60, 0.8)
        tulip.defer(lambda x: s.note_off(60), 0, 500)
    except Exception:
        pass


def _select(patch):
    deckcfg.set('patch', patch)
    deckcfg.apply_instrument()
    _state['name'].set_text(patches[patch])
    _preview()
    # restyle rows
    for b, n in _state['rows']:
        sel = (n == patch)
        b.set_style_bg_color(dk.c(dk.ACCENT if sel else dk.SURFACE), 0)


def _voices_cb(e):
    v = e.get_target_obj().get_value()
    _state['vlabel'].set_text("%d voices" % v)
    deckcfg.set('num_voices', v)
    deckcfg.apply_instrument()


def _channel_cb(ch):
    _state['vlabel'].set_text("%d voices  -  MIDI channel %d"
        % (deckcfg.get('num_voices', 10), ch))
    deckcfg.set('midi_channel', ch)
    deckcfg.apply_instrument()


def _build_list(body, lo, hi, accent):
    for w in list(_state['rows']):
        pass
    body.clean()
    _state['rows'] = []
    cur = deckcfg.get('patch', 0)
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
        b.add_event_cb((lambda pn: (lambda e: _select(pn)))(n), lv.EVENT.CLICKED, None)
        _state['rows'].append((b, n))


def run(screen):
    dk.frame(screen, "Instrument", "one synth on MIDI channel 1")

    cur = deckcfg.get('patch', 0)
    ch = deckcfg.get('midi_channel', 1)
    # Current selection card
    card = lv.obj(screen.group)
    card.set_pos(24, 110)
    card.set_size(tulip.screen_size()[0] - 48, 140)
    dk._flat(card, radius=16, bg=dk.SURFACE)
    card.set_style_pad_all(16, 0)
    _state['name'] = dk.label(card, patches[cur], 0, 2, color=dk.WHITE, font=dk.FONT_L)
    _state['vlabel'] = dk.label(card,
        "%d voices  -  MIDI channel %d" % (deckcfg.get('num_voices', 10), ch),
        0, 46, color=dk.MUTED, font=dk.FONT_S)
    dk.label(card, "Voices", 0, 84, color=dk.MUTED, font=dk.FONT_S)
    dk.slider(card, deckcfg.get('num_voices', 10), 1, 16, w=300,
        cb=_voices_cb, color=dk.GREEN).align(lv.ALIGN.LEFT_MID, 84, 34)
    dk.stepper(card, ch, 1, 16, _channel_cb, fmt="Channel %d", w=230).align(
        lv.ALIGN.TOP_RIGHT, -6, 8)

    # Category buttons
    body = dk.scroll_body(screen, top=340)
    _state['rows'] = []

    def cat_cb(lo, hi, accent, btn):
        def cb(e):
            for b in _state['catbtns']:
                b.set_style_bg_color(dk.c(dk.SURFACE2), 0)
            btn.set_style_bg_color(dk.c(accent), 0)
            _build_list(body, lo, hi, accent)
        return cb

    _state['catbtns'] = []
    x = 24
    for name, lo, hi, accent in CATS:
        active = lo <= cur < hi
        b = dk.button(screen.group, name, w=150, h=52,
            bg=(accent if active else dk.SURFACE2), font=dk.FONT_M)
        b.set_pos(x, 278)
        _state['catbtns'].append(b)
        b.add_event_cb(cat_cb(lo, hi, accent, b), lv.EVENT.CLICKED, None)
        x += 162

    # initial list = category containing current patch
    for name, lo, hi, accent in CATS:
        if lo <= cur < hi:
            _build_list(body, lo, hi, accent)
            break

    screen.present()
