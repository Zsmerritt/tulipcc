# devices.py -- the Devices screen (Home > Devices).
#
# Shows the discovered devices (the internal Tulip AMY + each USB-MIDI board):
# connection, per-device voice load vs capacity, and a Rescan. Devices are
# discovered, not added -- assigning instruments to them happens in the rack.
# Built on deckcfg.device_list() + shellmodel.device_meter().

import tulip
import deckui as dk
import deckcfg
import shellmodel as sm
import lvgl as lv

_s = {}


def _panel_h():
    import homeshell
    return tulip.screen_size()[1] - homeshell.BAR_H


def panel(parent, shell=None):
    _s['shell'] = shell
    _s['parent'] = parent
    _build(parent, shell)


def _refresh():
    p = _s.get('parent')
    if p is None:
        return
    try:
        p.clean()
    except Exception:
        return
    _build(p, _s.get('shell'))


def _rescan(e):
    if e.get_code() != lv.EVENT.CLICKED:
        return
    try:
        import amyfleet
        amyfleet.enroll_from_config()      # re-assign board channels over USB
    except Exception as ex:
        print("devices: enroll failed:", ex)
    deckcfg.apply_all()
    if _s.get('shell') is not None:
        _s['shell'].refresh_chips()
    _refresh()


def _build(parent, shell):
    w = tulip.screen_size()[0]
    body = dk.scroll_col(parent, w - 48, _panel_h() - 16)
    body.set_pos(24, 8)
    for dev in deckcfg.device_list():
        _dev_row(body, shell, dev)
    rs = dk.button(body, "Rescan", w=lv.pct(100), h=56,
                   bg=dk.SURFACE2, font=dk.FONT_M)
    rs.add_event_cb(_rescan, lv.EVENT.CLICKED, None)


def _dev_row(body, shell, dev):
    w = tulip.screen_size()[0]
    m = sm.device_meter(dev)
    r = lv.button(body)                      # tap a device -> its FX bus
    r.set_width(lv.pct(100))
    r.set_height(92)
    dk._flat(r, radius=16, bg=dk.SURFACE)
    dk.label(r, dev['name'], 16, 12, color=dk.WHITE, font=dk.FONT_M)
    status = "connected  -  tap for FX" if m['connected'] else "not connected"
    dk.label(r, status, 16, 44, color=(dk.GREEN if m['connected'] else dk.MUTED),
             font=dk.FONT_S)

    # right side: voices used/capacity + a load bar
    rx = w - 48 - 24 - 190
    dk.label(r, "voices " + m['text'], rx, 16, color=dk.MUTED, font=dk.FONT_S,
             w=190, align=lv.TEXT_ALIGN.RIGHT)
    barbg = lv.obj(r)
    barbg.set_size(190, 12)
    barbg.set_pos(rx, 52)
    dk._flat(barbg, radius=6, bg=dk.SURFACE2)
    fill = lv.obj(barbg)
    fill.set_size(max(2, int(190 * m['fraction'])), 12)
    fill.set_pos(0, 0)
    dk._flat(fill, radius=6, bg=(dk.RED if m['fraction'] >= 0.85 else dk.GREEN))
    dev_id = dev['device']
    r.add_event_cb((lambda d: (lambda e: (open_fx(shell, d)
                    if e.get_code() == lv.EVENT.CLICKED else None)))(dev_id),
                   lv.EVENT.CLICKED, None)


# ---------------- per-device FX bus (reverb / chorus / echo / EQ) ------------
_fx = {}


def open_fx(shell, device):
    if shell is None:
        return
    _fx['device'] = device
    if sm.open_panel_action(shell.top_key(), 'fx') == 'rebuild':
        shell.rebuild_top(fx_panel, "FX", key='fx')
    else:
        shell.push(fx_panel, "FX", key='fx')


def fx_panel(parent, shell=None):
    import parameditor
    import amyparams
    dev = _fx.get('device', 'internal')
    w = tulip.screen_size()[0]
    n = len([i for i in deckcfg.instruments() if i.get('device') == dev])
    dk.label(parent, "FX bus: " + sm.device_name(dev), 24, 12, color=dk.WHITE,
             font=dk.FONT_M)
    dk.label(parent, "shared by %d instrument%s on %s" %
             (n, "" if n == 1 else "s", sm.device_name(dev)), 24, 44,
             color=dk.MUTED, font=dk.FONT_S, w=w - 48)

    def _make(defs):
        return parameditor.FxEditor(dev, on_change=_fx_apply, defs=defs)
    parameditor.build_tabbed(parent, amyparams.fx_tabbed_groups(), _make,
                             x=8, y=72, w=w - 16, h=_panel_h() - 80)


def _fx_apply():
    try:
        import forwarder
        forwarder.reapply_fx()
    except Exception:
        pass
