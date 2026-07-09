# instrument.py -- pick the synth voice for the selected instance.
#
# The instance selector at the top chooses which AMY we're editing: the internal
# Tulip synth, or an attached AMYboard. The same controls (patch / voices /
# channel) apply to whichever is selected. Tapping a patch sets it live,
# previews a note, and saves it so boot.py restores it.

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
    return deckcfg.get_instance(deckcfg.active_index())


def _preview():
    inst = _inst()
    ch = inst.get('channel', 1)
    if inst.get('kind') == 'internal':
        import midi
        syn = midi.config.get_synth(ch)
        if syn is not None:
            try:
                syn.note_on(60, 0.8)
                tulip.defer(lambda x: syn.note_off(60), 0, 500)
            except Exception:
                pass
    else:
        c = (ch - 1) & 0x0F
        try:
            tulip.midi_out((0x90 | c, 60, 100))
            tulip.defer(lambda x: tulip.midi_out((0x80 | c, 60, 0)), 0, 500)
        except Exception:
            pass


def _select_patch(patch):
    i = deckcfg.active_index()
    deckcfg.set_instance(i, 'patch', patch)
    deckcfg.apply_instance(i)
    _s['name'].set_text(patches[patch])
    _preview()
    for b, n in _s['rows']:
        b.set_style_bg_color(dk.c(dk.ACCENT if n == patch else dk.SURFACE), 0)


def _voices_cb(e):
    v = e.get_target_obj().get_value()
    i = deckcfg.active_index()
    _s['vlabel'].set_text("%d voices  -  MIDI channel %d"
        % (v, _inst().get('channel', 1)))
    deckcfg.set_instance(i, 'num_voices', v)
    deckcfg.apply_instance(i)


def _channel_cb(ch):
    i = deckcfg.active_index()
    deckcfg.set_instance(i, 'channel', ch)
    _s['vlabel'].set_text("%d voices  -  MIDI channel %d"
        % (_inst().get('num_voices', 10), ch))
    deckcfg.apply_instance(i)


def _build_list(body, lo, hi):
    body.clean()
    _s['rows'] = []
    cur = _inst().get('patch', 0)
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
        b.add_event_cb((lambda pn: (lambda e: _select_patch(pn)))(n), lv.EVENT.CLICKED, None)
        _s['rows'].append((b, n))


def _rebuild_content():
    # tear down and rebuild the card + category buttons + patch list for the
    # currently-active instance.
    if _s.get('content') is not None:
        _s['content'].delete()
    screen = _s['screen']
    inst = _inst()
    cur = inst.get('patch', 0)
    ch = inst.get('channel', 1)

    content = lv.obj(screen.group)
    content.set_pos(0, 150)
    content.set_size(tulip.screen_size()[0], tulip.screen_size()[1] - 150)
    dk._flat(content, bg=dk.BG)
    _s['content'] = content

    card = lv.obj(content)
    card.set_pos(24, 4)
    card.set_size(tulip.screen_size()[0] - 48, 132)
    dk._flat(card, radius=16, bg=dk.SURFACE)
    card.set_style_pad_all(16, 0)
    _s['name'] = dk.label(card, patches[cur], 0, 2, color=dk.WHITE, font=dk.FONT_L)
    _s['vlabel'] = dk.label(card,
        "%d voices  -  MIDI channel %d" % (inst.get('num_voices', 10), ch),
        0, 46, color=dk.MUTED, font=dk.FONT_S)
    dk.label(card, "Voices", 0, 82, color=dk.MUTED, font=dk.FONT_S)
    dk.slider(card, inst.get('num_voices', 10), 1, 16, w=300,
        cb=_voices_cb, color=dk.GREEN).align(lv.ALIGN.LEFT_MID, 84, 32)
    dk.stepper(card, ch, 1, 16, _channel_cb, fmt="Channel %d", w=230).align(
        lv.ALIGN.TOP_RIGHT, -6, 8)

    # category buttons
    _s['catbtns'] = []
    x = 24
    for name, lo, hi, accent in CATS:
        active = lo <= cur < hi
        b = dk.button(content, name, w=150, h=52,
            bg=(accent if active else dk.SURFACE2), font=dk.FONT_M)
        b.set_pos(x, 150)
        _s['catbtns'].append(b)
        b.add_event_cb((lambda l, h, a, bt: (lambda e: _pick_cat(l, h, a, bt)))(lo, hi, accent, b),
                       lv.EVENT.CLICKED, None)
        x += 162

    # patch list
    body = dk.scroll_body(screen, top=0)
    body.set_parent(content)
    body.set_pos(24, 212)
    body.set_size(tulip.screen_size()[0] - 48, tulip.screen_size()[1] - 150 - 224)
    _s['listbody'] = body
    for name, lo, hi, accent in CATS:
        if lo <= cur < hi:
            _build_list(body, lo, hi)
            break


def _pick_cat(lo, hi, accent, btn):
    for b in _s['catbtns']:
        b.set_style_bg_color(dk.c(dk.SURFACE2), 0)
    btn.set_style_bg_color(dk.c(accent), 0)
    _build_list(_s['listbody'], lo, hi)


def _select_instance(i):
    deckcfg.set_active(i)
    for idx, b in enumerate(_s['selbtns']):
        b.set_style_bg_color(dk.c(dk.ACCENT if idx == i else dk.SURFACE2), 0)
    _rebuild_content()


def _build_selector(screen):
    insts = deckcfg.instances()
    active = deckcfg.active_index()
    _s['selbtns'] = []
    x = 300
    for i, inst in enumerate(insts):
        b = dk.button(screen.group, inst.get('name', 'Inst %d' % i), w=150, h=44,
            bg=(dk.ACCENT if i == active else dk.SURFACE2), font=dk.FONT_S)
        b.set_pos(x, 30)
        b.add_event_cb((lambda idx: (lambda e: _select_instance(idx)))(i),
                       lv.EVENT.CLICKED, None)
        _s['selbtns'].append(b)
        x += 158


def run(screen):
    _s.clear()
    _s['screen'] = screen
    _s['content'] = None
    dk.frame(screen, "Instrument", "select an instance, then pick its sound")
    _build_selector(screen)
    _rebuild_content()
    screen.present()
