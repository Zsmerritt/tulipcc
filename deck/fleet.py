# fleet.py -- manage the AMY fleet: Tulip + attached AMYboards.
#
# Choose the performance mode, add/remove boards, set each one's MIDI channel,
# and (in Stack mode) dial in unison detune. Changes are saved and the router
# (forwarder.py) is restarted so they take effect live.

import tulip
import deckui as dk
import deckcfg
import lvgl as lv

_s = {}


def _restart_router():
    try:
        import forwarder
        forwarder.start()
    except Exception as e:
        print("fleet: router restart failed:", e)


def _set_mode(mode):
    deckcfg.set_mode(mode)
    _restart_router()
    _rebuild()


def _add_board():
    deckcfg.ensure_count(deckcfg.num_instances() + 1)
    _restart_router()
    _rebuild()


def _remove_board():
    n = deckcfg.num_instances()
    if n > 1:
        deckcfg.ensure_count(n - 1)
        _restart_router()
        _rebuild()


def _inst_channel_cb(i):
    def cb(ch):
        deckcfg.set_instance(i, 'channel', ch)
        deckcfg.apply_instance(i)
        _restart_router()
    return cb


def _detune_toggle(btn):
    def cb(e):
        v = not deckcfg.load()['detune'].get('enabled')
        deckcfg.set_detune('enabled', v)
        btn.set_style_bg_color(dk.c(dk.GREEN if v else dk.SURFACE2), 0)
        btn.get_child(0).set_text("On" if v else "Off")
        _restart_router()
    return cb


def _spread_cb(e):
    v = e.get_target_obj().get_value()
    _s['spreadlbl'].set_text("%d cents" % v)
    deckcfg.set_detune('spread_cents', v)
    _restart_router()


def _unison_cb(v):
    deckcfg.set_detune('unison_voices', v)
    _restart_router()


def _rescan(e):
    # Size the fleet to the USB-MIDI devices the host has claimed (needs firmware
    # with tulip.num_midi_devices; older firmware reports 1).
    try:
        n = tulip.num_midi_devices()
    except (AttributeError, Exception):
        n = 0
    if n > 0:
        deckcfg.ensure_count(1 + n)   # Tulip + one instance per detected device
        _restart_router()
        _rebuild()
        dk.toast(_s['screen'], "Found %d MIDI device(s)" % n, dk.GREEN)
    else:
        dk.toast(_s['screen'], "No USB-MIDI devices found -- add boards manually", dk.ORANGE)


def _rebuild():
    if _s.get('content') is not None:
        _s['content'].delete()
    screen = _s['screen']
    cfg = deckcfg.load()
    content = lv.obj(screen.group)
    content.set_pos(0, 112)
    content.set_size(tulip.screen_size()[0], tulip.screen_size()[1] - 112)
    dk._flat(content, bg=dk.BG)
    _s['content'] = content
    body = dk.scroll_body(screen, top=0)
    body.set_parent(content)
    body.set_pos(24, 6)
    body.set_size(tulip.screen_size()[0] - 48, tulip.screen_size()[1] - 124)

    # Mode
    r = dk.row(body)
    dk.label(r, "Mode", color=dk.WHITE)
    g = dk.hgroup(r, w=320, h=52)
    multi = cfg['mode'] != 'stack'
    dk.button(g, "Multi", w=150, h=52, bg=(dk.ACCENT if multi else dk.SURFACE2),
        cb=lambda e: _set_mode('multi'))
    dk.button(g, "Stack", w=150, h=52, bg=(dk.ACCENT if not multi else dk.SURFACE2),
        cb=lambda e: _set_mode('stack'))
    dk.label(body,
        "Multi: each instance is its own instrument on its own channel.   "
        "Stack: one sound fanned across all instances (round-robin, or unison "
        "when detune is on).", color=dk.MUTED, font=dk.FONT_S,
        w=tulip.screen_size()[0] - 96)

    # Instances. In Stack mode the player always uses the Tulip's channel and we
    # fan out internally, so per-instance channels are hidden there.
    stack = cfg['mode'] == 'stack'
    for i, inst in enumerate(cfg['instances']):
        r = dk.row(body, h=76)
        left = lv.obj(r)
        left.set_size(500, 56)
        dk._flat(left, bg=dk.SURFACE)
        left.set_style_bg_opa(lv.OPA.TRANSP, 0)
        dk.label(left, inst.get('name', 'Inst %d' % i), 0, 4, color=dk.TEXT, font=dk.FONT_M)
        dk.label(left, inst.get('kind', ''), 0, 32, color=dk.MUTED, font=dk.FONT_S)
        if stack:
            dk.label(r, "voice in the stack" if i else "input + voice",
                color=dk.MUTED, font=dk.FONT_S)
        else:
            dk.stepper(r, inst.get('channel', 1), 1, 16, _inst_channel_cb(i),
                fmt="Channel %d", w=230)

    g2 = dk.hgroup(dk.row(body), w=440, h=48)
    dk.button(g2, lv.SYMBOL.PLUS + " Add board", w=180, h=48, bg=dk.GREEN,
        font=dk.FONT_S, cb=lambda e: _add_board())
    dk.button(g2, lv.SYMBOL.MINUS + " Remove", w=150, h=48, bg=dk.SURFACE2,
        font=dk.FONT_S, cb=lambda e: _remove_board())
    dk.button(g2, lv.SYMBOL.REFRESH, w=80, h=48, bg=dk.SURFACE2, cb=_rescan)

    # Detune (stack mode only)
    if cfg['mode'] == 'stack':
        det = cfg['detune']
        r = dk.row(body)
        dk.label(r, "Unison detune", color=dk.WHITE)
        db = dk.button(r, "On" if det.get('enabled') else "Off", w=120, h=52,
            bg=(dk.GREEN if det.get('enabled') else dk.SURFACE2))
        db.add_event_cb(_detune_toggle(db), lv.EVENT.CLICKED, None)

        r = dk.row(body, h=92)
        col = lv.obj(r)
        col.set_size(360, 60)
        col.set_style_border_width(0, 0)
        col.set_style_bg_opa(lv.OPA.TRANSP, 0)
        col.remove_flag(lv.obj.FLAG.SCROLLABLE)
        col.set_flex_flow(lv.FLEX_FLOW.COLUMN)
        col.set_flex_align(lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.START, lv.FLEX_ALIGN.START)
        dk.label(col, "Detune spread", color=dk.TEXT, font=dk.FONT_M)
        _s['spreadlbl'] = dk.label(col, "%d cents" % det.get('spread_cents', 8),
            color=dk.MUTED, font=dk.FONT_S)
        dk.slider(r, det.get('spread_cents', 8), 0, 50, w=360, cb=_spread_cb, color=dk.TEAL)

        r = dk.row(body, h=92)
        col2 = lv.obj(r)
        col2.set_size(360, 60)
        col2.set_style_border_width(0, 0)
        col2.set_style_bg_opa(lv.OPA.TRANSP, 0)
        col2.remove_flag(lv.obj.FLAG.SCROLLABLE)
        col2.set_flex_flow(lv.FLEX_FLOW.COLUMN)
        col2.set_flex_align(lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.START, lv.FLEX_ALIGN.START)
        dk.label(col2, "Unison voices", color=dk.TEXT, font=dk.FONT_M)
        dk.label(col2, "detuned voices per note (on the Tulip AMY)",
            color=dk.MUTED, font=dk.FONT_S)
        dk.stepper(r, det.get('unison_voices', 3), 1, 16, _unison_cb,
            fmt="%d voices", w=230)

    for idx, b in enumerate(_s.get('selbtns', [])):
        pass


def run(screen):
    _s.clear()
    _s['screen'] = screen
    _s['content'] = None
    dk.frame(screen, "Fleet", "Tulip + AMYboards")
    _rebuild()
    screen.present()
