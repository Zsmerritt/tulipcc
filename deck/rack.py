# rack.py -- the Instruments rack (Home > Instruments).
#
# An in-shell panel listing every instrument (name + device/channel/patch). Tap
# one to edit it (device / channel / voices, with Patch and MPE as pushed
# sub-panels); "Add instrument" creates one; each editor has Remove + set-active.
# Built on the canonical instrument API (deckcfg.instruments / add_instrument /
# set_instrument / remove_instrument / set_active_instrument / active_instrument).

import tulip
import deckui as dk
import deckcfg
import shellmodel as sm
import lvgl as lv

_s = {}


def _panel_h():
    import homeshell
    return tulip.screen_size()[1] - homeshell.BAR_H


# ---------------- rack list ----------------
def panel(parent, shell=None):
    _s['shell'] = shell
    _s['list_parent'] = parent
    _build_list(parent, shell)


def _refresh_list():
    p = _s.get('list_parent')
    if p is None:
        return
    try:
        p.clean()
    except Exception:
        return
    _build_list(p, _s.get('shell'))


def _build_list(parent, shell):
    w = tulip.screen_size()[0]
    body = dk.scroll_col(parent, w - 48, _panel_h() - 16)
    body.set_pos(24, 8)
    active = deckcfg.active_instrument()
    for instr in deckcfg.instruments():
        _inst_row(body, shell, instr, instr.get('id') == active)
    add = dk.button(body, "+ Add instrument", w=lv.pct(100), h=60, bg=dk.GREEN,
                    font=dk.FONT_M)
    add.add_event_cb(lambda e: (_add(shell)
                     if e.get_code() == lv.EVENT.CLICKED else None),
                     lv.EVENT.CLICKED, None)


def _inst_row(body, shell, instr, is_active):
    b = lv.button(body)
    b.set_width(lv.pct(100))
    b.set_height(76)
    dk._flat(b, radius=16, bg=(dk.SURFACE2 if is_active else dk.SURFACE))
    dk.label(b, instr.get('name', '?'), 16, 10, color=dk.WHITE, font=dk.FONT_M)
    dk.label(b, sm.instrument_summary(instr), 16, 42, color=dk.MUTED,
             font=dk.FONT_S)
    if is_active:
        # A filled badge, not a faint word -- the old green "active" text was
        # nearly invisible (audit).
        badge = lv.obj(b)
        badge.set_size(84, 34)
        dk._flat(badge, radius=17, bg=dk.GREEN)
        badge.remove_flag(lv.obj.FLAG.SCROLLABLE)
        badge.align(lv.ALIGN.RIGHT_MID, -16, 0)
        bl = dk.label(badge, "ACTIVE", color=dk.WHITE, font=dk.FONT_S)
        bl.center()
    iid = instr.get('id')
    b.add_event_cb((lambda i: (lambda e: (_open_edit(shell, i)
                    if e.get_code() == lv.EVENT.CLICKED else None)))(iid),
                   lv.EVENT.CLICKED, None)


def _add(shell):
    instr = deckcfg.add_instrument(device='internal', channel=1)
    deckcfg.apply_all()
    if shell is not None:
        shell.refresh_chips()
    _open_edit(shell, instr['id'])


def _open_edit(shell, iid):
    deckcfg.set_active_instrument(iid)
    if shell is None:
        return
    shell.refresh_chips()
    if sm.open_panel_action(shell.top_key(), 'inst_edit') == 'rebuild':
        shell.rebuild_top(edit_panel, "Edit instrument", key='inst_edit')
    else:
        shell.push(edit_panel, "Edit instrument", key='inst_edit')


# ---------------- edit panel ----------------
def _active():
    return deckcfg.get_instrument(deckcfg.active_instrument())


def edit_panel(parent, shell=None):
    _s['shell'] = shell
    _s['edit_parent'] = parent
    _build_edit(parent, shell)


def _rebuild_edit():
    p = _s.get('edit_parent')
    if p is None:
        return
    try:
        p.clean()
    except Exception:
        return
    _build_edit(p, _s.get('shell'))


def _set_device(dev):
    iid = deckcfg.active_instrument()
    deckcfg.set_instrument(iid, 'device', dev)
    deckcfg.set_instrument(iid, 'name', sm.device_name(dev))
    deckcfg.apply_all()
    if _s.get('shell') is not None:
        _s['shell'].refresh_chips()
    _refresh_list()
    _rebuild_edit()


def _channel_cb(ch):
    deckcfg.set_instrument(deckcfg.active_instrument(), 'channel', ch)
    deckcfg.apply_all()
    if _s.get('shell') is not None:
        _s['shell'].refresh_chips()
    _refresh_list()


def _voices_cb(e):
    v = e.get_target_obj().get_value()
    if _s.get('vlabel') is not None:
        _s['vlabel'].set_text("%d voices" % v)
    deckcfg.set_instrument(deckcfg.active_instrument(), 'num_voices', v)
    deckcfg.apply_all()


def _open_patch(e):
    if e.get_code() != lv.EVENT.CLICKED:
        return
    if _s.get('shell') is not None:
        import instrument
        _s['shell'].push(instrument.panel, "Patch", key='patch')


def _open_sound(e):
    if e.get_code() != lv.EVENT.CLICKED:
        return
    if _s.get('shell') is not None:
        _s['shell'].push(sound_panel, "Sound", key='sound')


def _open_fx_row(e):
    if e.get_code() != lv.EVENT.CLICKED:
        return
    sh = _s.get('shell')
    if sh is None:
        return
    instr = _active()
    dev = instr.get('device') if instr else 'internal'
    import devices
    devices.open_fx(sh, dev)


def _open_mpe(e):
    if e.get_code() != lv.EVENT.CLICKED:
        return
    if _s.get('shell') is not None:
        import mpe
        _s['shell'].push(mpe.panel, "MPE", key='mpe')


def _do_remove():
    deckcfg.remove_instrument(deckcfg.active_instrument())
    deckcfg.apply_all()
    sh = _s.get('shell')
    if sh is not None:
        sh.refresh_chips()
        sh.back()
        if sh.top_key() == 'rack':
            sh.rebuild_top(panel, "Instruments", key='rack')


def _remove(e):
    if e.get_code() != lv.EVENT.CLICKED:
        return
    instr = _active()
    name = instr.get('name', 'this instrument') if instr else 'this instrument'
    dk.confirm("Remove instrument?",
               "Delete \"%s\"? This can't be undone." % name,
               _do_remove, yes_text="Remove")


def _build_edit(parent, shell):
    from patches import patches
    w = tulip.screen_size()[0]
    instr = _active()
    if instr is None:
        dk.label(parent, "No instrument selected.", 24, 24, color=dk.MUTED,
                 font=dk.FONT_M)
        return
    body = dk.scroll_col(parent, w - 48, _panel_h() - 16)
    body.set_pos(24, 8)

    # Device chooser (internal + each board)
    r = dk.row(body, h=76)
    dk.label(r, "Device", color=dk.WHITE)
    g = dk.hgroup(r, w=w - 260, h=52)
    cur_dev = instr.get('device')
    for d in deckcfg.device_list():
        dev = d['device']
        bb = dk.button(g, sm.name_short(d['name']), w=100, h=52,
                       bg=(dk.ACCENT if dev == cur_dev else dk.SURFACE2),
                       font=dk.FONT_S)
        bb.add_event_cb((lambda dv: (lambda e: (_set_device(dv)
                        if e.get_code() == lv.EVENT.CLICKED else None)))(dev),
                        lv.EVENT.CLICKED, None)

    # MIDI channel
    r = dk.row(body, h=76)
    dk.label(r, "MIDI channel", color=dk.TEXT)
    dk.stepper(r, instr.get('channel', 1), 1, 16, _channel_cb, fmt="Channel %d",
               w=230)

    # Voices
    r = dk.row(body, h=76)
    _s['vlabel'] = dk.label(r, "%d voices" % instr.get('num_voices', 10),
                            color=dk.TEXT)
    dk.slider(r, instr.get('num_voices', 10), 1, 32, w=340, cb=_voices_cb,
              color=dk.GREEN)

    # Patch -> picker sub-panel
    r = dk.row(body)
    dk.label(r, "Patch  " + patches[instr.get('patch', 0)], color=dk.TEXT)
    nav = dk.button(r, sm.patch_category(instr.get('patch', 0)) + "  >", w=180,
                    h=52, bg=dk.SURFACE2, font=dk.FONT_S)
    nav.add_event_cb(_open_patch, lv.EVENT.CLICKED, None)

    # Sound (per-instrument params) -> ParamEditor sub-panel
    r = dk.row(body)
    dk.label(r, "Sound", color=dk.TEXT)
    nav = dk.button(r, "Edit  >", w=150, h=52, bg=dk.SURFACE2, font=dk.FONT_S)
    nav.add_event_cb(_open_sound, lv.EVENT.CLICKED, None)

    # FX -> the OWNING DEVICE's FX bus (shared by all instruments on it)
    r = dk.row(body)
    dk.label(r, "FX (device)", color=dk.TEXT)
    nav = dk.button(r, "Edit  >", w=150, h=52, bg=dk.SURFACE2, font=dk.FONT_S)
    nav.add_event_cb(_open_fx_row, lv.EVENT.CLICKED, None)

    # MPE -> sub-panel, only when the global MPE gate is on (C.4). When off,
    # no MPE button shows and the MPE panel is unreachable.
    if deckcfg.mpe_enabled():
        r = dk.row(body)
        dk.label(r, "MPE", color=dk.TEXT)
        on = instr.get('mpe', {}).get('enabled')
        nav = dk.button(r, ("On" if on else "Off") + "  >", w=150, h=52,
                        bg=(dk.GREEN if on else dk.SURFACE2), font=dk.FONT_S)
        nav.add_event_cb(_open_mpe, lv.EVENT.CLICKED, None)

    # Remove
    rm = dk.button(body, "Remove instrument", w=lv.pct(100), h=56, bg=dk.RED,
                   font=dk.FONT_M)
    rm.add_event_cb(_remove, lv.EVENT.CLICKED, None)


# ---------------- Sound sub-panel (per-instrument params) ----------------
_snd = {}


def sound_panel(parent, shell=None):
    _snd['shell'] = shell
    _snd['iid'] = deckcfg.active_instrument()
    _snd['adv'] = False
    _snd['parent'] = parent
    _render_sound()


def _render_sound():
    parent = _snd.get('parent')
    if parent is None:
        return
    try:
        parent.clean()
    except Exception:
        return
    import parameditor
    import amyparams
    import curated
    w = tulip.screen_size()[0]
    iid = _snd['iid']
    instr = deckcfg.get_instrument(iid) or {}
    engine = amyparams.engine_of(instr.get('patch'))
    labels = curated.labels(engine)
    vname = curated.view_name(engine)
    # Curated-view badge (engine-native labels) top-left, so it's clear which
    # familiar layout you're in (e.g. Juno-6 -> DCO/VCF/VCA).
    if vname:
        dk.label(parent, vname + " view", 12, 18, color=dk.MUTED, font=dk.FONT_S)
    # Basic/Advanced view toggle: a compact chip pinned top-right (ACCENT when on),
    # clear of the left tab rail and the param list.
    tog = dk.button(parent, "Advanced", w=150, h=40, font=dk.FONT_S,
                    bg=(dk.ACCENT if _snd['adv'] else dk.SURFACE2))
    tog.set_pos(w - 150 - 16, 8)
    tog.add_event_cb(_toggle_adv, lv.EVENT.CLICKED, None)

    # left-tabbed: one tab per engine-native group (tier-filtered), each a short
    # list. curated.tabbed falls back to the generic grouping for unknown engines.
    def _make(defs):
        return curated.CuratedEditor(iid, defs=defs, labels=labels,
                                     on_change=_snd_apply, show_advanced=True)
    parameditor.build_tabbed(parent, curated.tabbed(engine, _snd['adv']), _make,
                             x=8, y=56, w=w - 16, h=_panel_h() - 64)


def _toggle_adv(e):
    if e.get_code() != lv.EVENT.CLICKED:
        return
    _snd['adv'] = not _snd['adv']
    _render_sound()


def _snd_apply():
    try:
        import forwarder
        forwarder.reapply_params(_snd['iid'])
    except Exception:
        pass
