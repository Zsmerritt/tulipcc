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

# Instrument engine types (the mode switch). Drums is a sample/pad engine; the
# rest are the melodic synth engines with curated Sound views.
_TYPE_LIST = [('juno6', 'Juno-6'), ('dx7', 'DX7'), ('piano', 'Piano'),
              ('drums', 'Drums')]
_TYPE_NAMES = dict(_TYPE_LIST)
_TYPE_FIRST_PATCH = {'juno6': 0, 'dx7': 128, 'piano': 256, 'drums': 0}


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
    for instr in deckcfg.instruments():
        _inst_row(body, shell, instr)
    add = dk.button(body, "+ Add instrument", w=lv.pct(100), h=60, bg=dk.GREEN,
                    font=dk.FONT_M)
    add.add_event_cb(lambda e: (_add(shell)
                     if e.get_code() == lv.EVENT.CLICKED else None),
                     lv.EVENT.CLICKED, None)


def _mk_enable(iid):
    def on_change(v):
        deckcfg.set_instrument(iid, 'enabled', v)
        deckcfg.apply_all()          # re-run the router so it starts/stops playing
        sh = _s.get('shell')
        if sh is not None:
            try:
                sh.refresh_chips()
            except Exception:
                pass
    return on_change


def _inst_row(body, shell, instr):
    iid = instr.get('id')
    b = lv.button(body)
    b.set_width(lv.pct(100))
    b.set_height(76)
    dk._flat(b, radius=16, bg=dk.SURFACE)
    dk.label(b, instr.get('name', '?'), 16, 10, color=dk.WHITE, font=dk.FONT_M)
    dk.label(b, sm.instrument_summary(instr), 16, 42, color=dk.MUTED,
             font=dk.FONT_S)
    # Per-instrument ON/OFF. Enabled instruments all play at once (multitimbral) --
    # there is no single "active" instrument; tapping the row just opens its editor.
    sw = dk.switch(b, bool(instr.get('enabled', True)), _mk_enable(iid),
                   color=dk.GREEN)
    sw.align(lv.ALIGN.RIGHT_MID, -16, 0)
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
    instr = deckcfg.get_instrument(iid)
    title = (instr.get('name') or 'Instrument') if instr else 'Instrument'
    if sm.open_panel_action(shell.top_key(), 'inst_edit') == 'rebuild':
        shell.rebuild_top(edit_panel, title, key='inst_edit')
    else:
        shell.push(edit_panel, title, key='inst_edit')


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
    sh = _s.get('shell')
    if sh is None:
        return
    instr = _active()
    if instr and instr.get('type') == 'drums':
        sh.push(kit_panel, "Kit", key='kit')       # drums pick a kit, not a patch
    else:
        import instrument
        sh.push(instrument.panel, "Patch", key='patch')


def _open_type(e):
    if e.get_code() != lv.EVENT.CLICKED:
        return
    if _s.get('shell') is not None:
        _s['shell'].push(type_panel, "Type", key='type')


def _set_type(t):
    iid = deckcfg.active_instrument()
    deckcfg.set_instrument(iid, 'type', t)
    # reset the patch to the type's first patch so patch stays valid for the engine
    deckcfg.set_instrument(iid, 'patch', _TYPE_FIRST_PATCH.get(t, 0))
    if t == 'drums':
        deckcfg.set_instrument(iid, 'kit', 384)   # default TR-808
    deckcfg.apply_all()
    sh = _s.get('shell')
    if sh is not None:
        sh.refresh_chips()
        sh.back()      # pop Type; the deferred refill rebuilds the editor anew


def _set_kit(kit):
    iid = deckcfg.active_instrument()
    deckcfg.set_instrument(iid, 'kit', kit)
    deckcfg.apply_all()
    sh = _s.get('shell')
    if sh is not None:
        sh.refresh_chips()
    try:
        import forwarder
        forwarder.preview(iid, note=36)     # audition: a kick
    except Exception:
        pass
    for b, k in _s.get('kitbtns', []):
        b.set_style_bg_color(dk.c(dk.ACCENT if k == kit else dk.SURFACE), 0)


def kit_panel(parent, shell=None):
    _s['shell'] = shell
    import drums_kit
    w = tulip.screen_size()[0]
    cur = (_active() or {}).get('kit', 384)
    body = dk.scroll_col(parent, w - 48, _panel_h() - 16)
    body.set_pos(24, 8)
    dk.label(body, "Drum kit -- swaps all the sounds at once.", color=dk.MUTED,
             font=dk.FONT_S)
    _s['kitbtns'] = []
    for kit, name in drums_kit.KITS:
        b = dk.button(body, name, w=lv.pct(100), h=64, font=dk.FONT_M,
                      bg=(dk.ACCENT if kit == cur else dk.SURFACE))
        b.add_event_cb((lambda k: (lambda e: _set_kit(k)
                        if e.get_code() == lv.EVENT.CLICKED else None))(kit),
                       lv.EVENT.CLICKED, None)
        _s['kitbtns'].append((b, kit))


def type_panel(parent, shell=None):
    _s['shell'] = shell
    w = tulip.screen_size()[0]
    cur = (_active() or {}).get('type', 'juno6')
    body = dk.scroll_col(parent, w - 48, _panel_h() - 16)
    body.set_pos(24, 8)
    dk.label(body, "What engine this instrument plays.", color=dk.MUTED,
             font=dk.FONT_S)
    for t, name in _TYPE_LIST:
        b = dk.button(body, name, w=lv.pct(100), h=64, font=dk.FONT_M,
                      bg=(dk.ACCENT if t == cur else dk.SURFACE))
        b.add_event_cb((lambda tt: (lambda e: _set_type(tt)
                        if e.get_code() == lv.EVENT.CLICKED else None))(t),
                       lv.EVENT.CLICKED, None)


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
    iid = instr.get('id')

    # Name (rename via the on-screen keyboard) -- so several instruments on one
    # device don't all read the same, and you can tell which you're editing.
    ncard = lv.obj(body)
    ncard.set_width(lv.pct(100))
    ncard.set_height(76)
    dk._flat(ncard, radius=16, bg=dk.SURFACE)
    ncard.remove_flag(lv.obj.FLAG.SCROLLABLE)
    ncard.set_style_pad_all(0, 0)
    dk.label(ncard, "Name", color=dk.TEXT, font=dk.FONT_M).align(
        lv.ALIGN.LEFT_MID, 20, 0)
    nt = tulip.UIText(text=instr.get('name', ''), placeholder="instrument name",
                      w=460, h=44, bg_color=dk.SURFACE2, fg_color=dk.TEXT,
                      font=dk.FONT_S)
    nt.group.set_parent(ncard)
    nt.group.set_size(460, 44)
    nt.group.set_style_bg_opa(lv.OPA.TRANSP, 0)
    nt.group.align(lv.ALIGN.RIGHT_MID, -84, 0)
    dk.button(ncard, tulip.lv.SYMBOL.KEYBOARD, w=56, h=44, bg=dk.SURFACE2,
              cb=lambda e: tulip.keyboard()).align(lv.ALIGN.RIGHT_MID, -16, 0)

    def _name_cb(e):
        try:
            deckcfg.set_instrument(iid, 'name', nt.ta.get_text())
            if _s.get('shell') is not None:
                _s['shell'].refresh_chips()
        except Exception:
            pass
    try:
        nt.ta.add_event_cb(_name_cb, lv.EVENT.VALUE_CHANGED, None)
    except Exception:
        pass

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

    # Type (engine) -> mode switch. Scopes the patch picker + drives the Sound
    # editor (a synth gets Sound tabs; a drum gets the pad list).
    r = dk.row(body)
    dk.label(r, "Type", color=dk.TEXT)
    nav = dk.button(r, _TYPE_NAMES.get(instr.get('type', 'juno6'), 'Juno-6')
                    + "  >", w=180, h=52, bg=dk.SURFACE2, font=dk.FONT_S)
    nav.add_event_cb(_open_type, lv.EVENT.CLICKED, None)

    # Patch (or Kit for drums) -> picker sub-panel
    is_drum = instr.get('type') == 'drums'
    r = dk.row(body)
    if is_drum:
        import drums_kit
        dk.label(r, "Kit  " + drums_kit.kit_name(instr.get('kit', 384)),
                 color=dk.TEXT)
    else:
        dk.label(r, "Patch  " + patches[instr.get('patch', 0)], color=dk.TEXT)
    nav = dk.button(r, "Browse  >", w=180, h=52, bg=dk.SURFACE2, font=dk.FONT_S)
    nav.add_event_cb(_open_patch, lv.EVENT.CLICKED, None)

    # Sound (per-instrument params) -> ParamEditor sub-panel. Drums have no
    # osc/filter design (per-pad tune/decay is a planned follow-up), so no Sound.
    if not is_drum:
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
    engine = instr.get('type') or amyparams.engine_of(instr.get('patch'))
    labels = curated.labels(engine)
    vname = curated.view_name(engine)
    # Anchored header bar: the curated-view badge (left) + a Basic|Advanced
    # SEGMENTED toggle (right). Replaces the button that floated loose in the
    # top-right corner; now it reads as a proper toolbar and both states show.
    hdr = lv.obj(parent)
    hdr.set_size(w - 16, 44)
    hdr.set_pos(8, 6)
    dk._flat(hdr, radius=12, bg=dk.SURFACE)
    hdr.remove_flag(lv.obj.FLAG.SCROLLABLE)
    hdr.set_style_pad_all(0, 0)
    dk.label(hdr, (vname + " view") if vname else "Sound design", 16, 13,
             color=dk.MUTED, font=dk.FONT_S)
    seg_w = 116
    adv_x = (w - 16) - 16 - seg_w
    for lbl, val, bx in (("Basic", False, adv_x - seg_w - 4),
                         ("Advanced", True, adv_x)):
        b = dk.button(hdr, lbl, w=seg_w, h=34, font=dk.FONT_S,
                      bg=(dk.ACCENT if _snd['adv'] == val else dk.SURFACE2))
        b.set_pos(bx, 5)
        b.add_event_cb((lambda v: (lambda e: _set_adv(v)
                        if e.get_code() == lv.EVENT.CLICKED else None))(val),
                       lv.EVENT.CLICKED, None)

    # left-tabbed: one tab per engine-native group (tier-filtered), each a short
    # list. curated.tabbed falls back to the generic grouping for unknown engines.
    def _make(defs):
        return curated.CuratedEditor(iid, defs=defs, labels=labels,
                                     on_change=_snd_apply, show_advanced=True)
    parameditor.build_tabbed(parent, curated.tabbed(engine, _snd['adv']), _make,
                             x=8, y=58, w=w - 16, h=_panel_h() - 66)


def _set_adv(value):
    if _snd.get('adv') == value:
        return
    _snd['adv'] = value
    _render_sound()


def _snd_apply():
    try:
        import forwarder
        forwarder.reapply_params(_snd['iid'])
    except Exception:
        pass
