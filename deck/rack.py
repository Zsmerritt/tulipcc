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
# Type = what the instrument IS (an engine); "Kit" stays the label of a drum
# instrument's patch slot (UX-REVIEW-6 L9 / 7).
_TYPE_LIST = [('juno6', 'Juno-6'), ('dx7', 'DX7'), ('piano', 'Piano'),
              ('gm', 'GM Bank'), ('gm2', 'E-mu GM'), ('drums', 'Drums')]
_TYPE_NAMES = dict(_TYPE_LIST)
# For 'gm'/'gm2' the patch slot holds a GM program number (0 = Grand Piano).
_TYPE_FIRST_PATCH = {'juno6': 0, 'dx7': 128, 'piano': 256, 'gm': 0, 'gm2': 0,
                     'drums': 0}


def _panel_h():
    import homeshell
    return tulip.screen_size()[1] - homeshell.BAR_H


# ---------------- rack list ----------------
def panel(parent, shell=None, footer=None):
    """The instruments rack. As of the S1 rework this is also the HOME ROOT
    (home._build_root wraps it); `footer(body)` lets the root append its
    Devices/System entries under the Add button."""
    _s['shell'] = shell
    _s['list_parent'] = parent
    _s['footer'] = footer
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
    f = _s.get('footer')
    if f is not None:
        try:
            f(body)
        except Exception:
            pass


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


# width of the enable-toggle zone at a row's right edge (own tap target)
_TOGGLE_ZONE_W = 148


def _hitzone(parent):
    """A transparent, clickable tap area (no visual footprint)."""
    z = lv.obj(parent)
    z.set_style_bg_opa(lv.OPA.TRANSP, 0)
    z.set_style_border_width(0, 0)
    z.set_style_pad_all(0, 0)
    z.set_style_radius(0, 0)
    z.remove_flag(lv.obj.FLAG.SCROLLABLE)
    return z


def _inst_row(body, shell, instr):
    # The row's two actions have PHYSICALLY DISJOINT tap targets: tapping the
    # whole row used to open the editor, so a finger aimed at the 64x34 enable
    # switch that landed a few px off navigated away instead of toggling. Now
    # the card itself isn't clickable; an "open" zone covers everything LEFT
    # of the toggle, and the toggle owns a full-height padded zone where a
    # near-miss toggles (never navigates).
    iid = instr.get('id')
    row_w = tulip.screen_size()[0] - 48    # body is a full-width scroll_col
    card = lv.obj(body)
    card.set_width(lv.pct(100))
    card.set_height(76)
    dk._flat(card, radius=16, bg=dk.SURFACE)
    dk.edge(card)
    card.remove_flag(lv.obj.FLAG.CLICKABLE)
    # a glyph makes the row scannable at arm's length: note = melodic, bars = kit
    icon = dk.label(card, lv.SYMBOL.BARS if instr.get('type') == 'drums'
                    else lv.SYMBOL.AUDIO, color=dk.TEAL, font=dk.FONT_M)
    icon.align(lv.ALIGN.LEFT_MID, 16, 0)
    dk.label(card, instr.get('name', '?'), 52, 10, color=dk.WHITE, font=dk.FONT_M)
    dk.label(card, sm.instrument_summary(instr), 52, 42, color=dk.MUTED,
             font=dk.FONT_S)

    # open-editor tap area: the row minus the toggle zone
    hit = _hitzone(card)
    hit.set_size(row_w - _TOGGLE_ZONE_W, 76)
    hit.align(lv.ALIGN.LEFT_MID, 0, 0)
    hit.add_event_cb((lambda i: (lambda e: (_open_edit(shell, i)
                      if e.get_code() == lv.EVENT.CLICKED else None)))(iid),
                     lv.EVENT.CLICKED, None)

    # Per-instrument ON/OFF. Enabled instruments all play at once (multitimbral)
    # -- there is no single "active" instrument. The zone gives the switch a
    # full-height tap target; a tap inside it but off the switch still toggles.
    on_change = _mk_enable(iid)
    zone = _hitzone(card)
    zone.set_size(_TOGGLE_ZONE_W, 76)
    zone.align(lv.ALIGN.RIGHT_MID, 0, 0)
    sw = dk.switch(zone, bool(instr.get('enabled', True)), on_change,
                   color=dk.GREEN)
    sw.center()

    def _zone_cb(e):
        if e.get_code() != lv.EVENT.CLICKED:
            return
        # near-miss on the switch: toggle it programmatically (a state change
        # from code doesn't fire VALUE_CHANGED, so invoke on_change ourselves)
        if sw.has_state(lv.STATE.CHECKED):
            sw.remove_state(lv.STATE.CHECKED)
            v = False
        else:
            sw.add_state(lv.STATE.CHECKED)
            v = True
        try:
            on_change(v)
        except Exception:
            pass
    zone.add_event_cb(_zone_cb, lv.EVENT.CLICKED, None)


def _add(shell):
    ch = deckcfg.next_free_channel('internal')   # next free channel, not always 1
    instr = deckcfg.add_instrument(device='internal', channel=ch)
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
    # land on the next free channel on the new device (keep the user's name)
    deckcfg.set_instrument(iid, 'channel',
                           deckcfg.next_free_channel(dev, exclude_iid=iid))
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
    # Per-tick during the drag: just the readout. Committing here would rebuild
    # every synth (apply_all -> forwarder.start) dozens of times per drag.
    v = e.get_target_obj().get_value()
    if _s.get('vlabel') is not None:
        _s['vlabel'].set_text("%d voices" % v)


def _voices_done(e):
    # Finger lifted: save + rebuild the router once with the final voice count.
    v = e.get_target_obj().get_value()
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
    dk.label(body, "Drum kit: swaps all the sounds at once.", color=dk.MUTED,
             font=dk.FONT_S)
    _s['kitbtns'] = []
    # sampled kits first, then the synthesized ones (drums_kit.synth_kits) --
    # same list, same tap; the kit id encodes which engine plays it
    for kit, name in list(drums_kit.KITS) + drums_kit.synth_kits():
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
    dk.label(body, "What engine this instrument plays. Changing type resets "
             "the patch and sound edits.", color=dk.MUTED, font=dk.FONT_S)
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


def _open_pads(e):
    if e.get_code() != lv.EVENT.CLICKED:
        return
    if _s.get('shell') is not None:
        import padeditor
        _s['shell'].push(padeditor.panel, "Pads", key='pads')


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
        # back()'s deferred refill rebuilds the revealed rack from its stored
        # builder -- including the home root's footer (an explicit rebuild_top
        # with rack.panel here would rebuild the root WITHOUT it).
        sh.back()


def _remove(e):
    if e.get_code() != lv.EVENT.CLICKED:
        return
    instr = _active()
    name = instr.get('name', 'this instrument') if instr else 'this instrument'
    dk.confirm("Remove instrument?",
               "Delete \"%s\"? This can't be undone." % name,
               _do_remove, yes_text="Remove")


def _vcol2(parent, cw):
    """A transparent fixed-width flex column -- one column of the two-column
    editor layout (S2). dk.row(pct 100) children size to the column."""
    col = lv.obj(parent)
    col.set_width(cw)
    col.set_height(lv.SIZE_CONTENT)
    col.set_style_border_width(0, 0)
    col.set_style_pad_all(0, 0)
    col.set_style_bg_opa(lv.OPA.TRANSP, 0)
    col.remove_flag(lv.obj.FLAG.SCROLLABLE)
    col.set_flex_flow(lv.FLEX_FLOW.COLUMN)
    col.set_style_pad_row(12, 0)
    return col


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

    # S2: two columns on the landscape panel -- left = identity/routing,
    # right = sound -- so the editor fits without scrolling instead of
    # stretching phone-portrait rows across 1024 px.
    cols = lv.obj(body)
    cols.set_width(lv.pct(100))
    cols.set_height(lv.SIZE_CONTENT)
    cols.set_style_border_width(0, 0)
    cols.set_style_pad_all(0, 0)
    cols.set_style_bg_opa(lv.OPA.TRANSP, 0)
    cols.remove_flag(lv.obj.FLAG.SCROLLABLE)
    cols.set_flex_flow(lv.FLEX_FLOW.ROW)
    cols.set_style_pad_column(16, 0)
    # body is a scroll_col with an 18px right scrollbar gutter -- subtract it
    # or the right column overflows and its card borders clip at the screen
    # edge (UX-REVIEW-8 R-4).
    cw = (w - 48 - 18 - 16) // 2
    left = _vcol2(cols, cw)
    right = _vcol2(cols, cw)

    # --- LEFT column: identity / routing ---
    # Name (rename via the on-screen keyboard) -- so several instruments on one
    # device don't all read the same, and you can tell which you're editing.
    ncard = lv.obj(left)
    ncard.set_width(lv.pct(100))
    ncard.set_height(76)
    dk._flat(ncard, radius=16, bg=dk.SURFACE)
    ncard.remove_flag(lv.obj.FLAG.SCROLLABLE)
    ncard.set_style_pad_all(0, 0)
    dk.label(ncard, "Name", color=dk.TEXT, font=dk.FONT_M).align(
        lv.ALIGN.LEFT_MID, 20, 0)
    nt = dk.text_field(ncard, text=instr.get('name', ''),
                       placeholder="instrument name", w=250, h=44)
    nt.group.align(lv.ALIGN.RIGHT_MID, -76, 0)
    dk.button(ncard, tulip.lv.SYMBOL.KEYBOARD, w=52, h=44, bg=dk.SURFACE2,
              cb=lambda e: dk.toggle_keyboard_for(nt.ta)).align(
                  lv.ALIGN.RIGHT_MID, -14, 0)

    # Per keystroke: RAM cache only (a full config flash write per keypress was
    # UX-REVIEW-6 M1 -- the exact pattern the perf round outlawed for sliders).
    # Commit + refresh the top-bar chips once, when the field loses focus.
    def _name_cb(e):
        try:
            deckcfg.set_instrument(iid, 'name', nt.ta.get_text(), flush=False)
        except Exception:
            pass

    def _name_done(e):
        try:
            deckcfg.flush()
            if _s.get('shell') is not None:
                _s['shell'].refresh_chips()
        except Exception:
            pass
    try:
        nt.ta.add_event_cb(_name_cb, lv.EVENT.VALUE_CHANGED, None)
        nt.ta.add_event_cb(_name_done, lv.EVENT.DEFOCUSED, None)
        nt.ta.add_event_cb(_name_done, lv.EVENT.READY, None)
    except Exception:
        pass

    # Device chooser (internal + each board)
    r = dk.row(left, h=76)
    dk.label(r, "Device", color=dk.WHITE)
    g = dk.hgroup(r, w=cw - 150, h=52)
    cur_dev = instr.get('device')
    for d in deckcfg.device_list():
        dev = d['device']
        bb = dk.button(g, sm.name_short(d['name']), w=96, h=52,
                       bg=(dk.ACCENT if dev == cur_dev else dk.SURFACE2),
                       font=dk.FONT_S)
        bb.add_event_cb((lambda dv: (lambda e: (_set_device(dv)
                        if e.get_code() == lv.EVENT.CLICKED else None)))(dev),
                        lv.EVENT.CLICKED, None)

    # MIDI channel
    r = dk.row(left, h=76)
    dk.label(r, "MIDI channel", color=dk.TEXT)
    dk.stepper(r, instr.get('channel', 1), 1, 16, _channel_cb, fmt="Channel %d",
               w=230)

    # Voices
    r = dk.row(left, h=76)
    _s['vlabel'] = dk.label(r, "%d voices" % instr.get('num_voices', 10),
                            color=dk.TEXT)
    dk.slider(r, instr.get('num_voices', 10), 1, 32, w=250, cb=_voices_cb,
              color=dk.GREEN, on_release=_voices_done)

    # Reset patch: clear this instrument's sound-design overrides (params,
    # reverb send, per-pad tweaks) back to what the patch itself defines.
    def _reset_patch(e):
        if e.get_code() != lv.EVENT.CLICKED:
            return
        rid = deckcfg.active_instrument()
        deckcfg.set_instrument(rid, 'params', {}, flush=False)
        deckcfg.set_instrument(rid, 'reverb_send', 1.0, flush=False)
        deckcfg.set_instrument(rid, 'hits', {})
        deckcfg.apply_all()
        sh2 = _s.get('shell')
        if sh2 is not None:
            try:
                sh2.refresh_chips()
            except Exception:
                pass

            def _redraw(_x):   # rebuild the editor with the patch values
                try:           # (same clean+fill back() uses, off-tick)
                    h = sh2.stack.top_handle()
                    b = sh2.stack.top_builder()
                    if h is not None and b is not None:
                        h.clean()
                        sh2._fill(h, b)
                except Exception:
                    pass
            try:
                tulip.defer(_redraw, 0, 30)
            except Exception:
                pass
    r = dk.row(left, h=76)
    dk.label(r, "Patch values", color=dk.TEXT)
    rb = dk.button(r, "Reset patch", w=190, h=52, bg=dk.SURFACE2,
                   font=dk.FONT_S)
    rb.add_event_cb(_reset_patch, lv.EVENT.CLICKED, None)

    # --- RIGHT column: sound ---
    # Type (engine) -> mode switch. Scopes the patch picker + drives the Sound
    # editor (a synth gets Sound tabs; a drum gets the pad list).
    r = dk.row(right)
    dk.label(r, "Type", color=dk.TEXT)
    nav = dk.button(r, _TYPE_NAMES.get(instr.get('type', 'juno6'), 'Juno-6')
                    + "  >", w=180, h=52, bg=dk.SURFACE2, font=dk.FONT_S)
    nav.add_event_cb(_open_type, lv.EVENT.CLICKED, None)

    # Patch (or Kit for drums) -> picker sub-panel
    is_drum = instr.get('type') == 'drums'
    r = dk.row(right)
    if is_drum:
        import drums_kit
        pl = dk.label(r, "Kit  " + drums_kit.kit_name(instr.get('kit', 384)),
                      color=dk.TEXT, w=cw - 240)
    else:
        if instr.get('type') in ('gm', 'gm2'):
            import gm as _gmnames
            pname = _gmnames.name(instr.get('patch', 0))
        else:
            pname = patches[instr.get('patch', 0)]
        pl = dk.label(r, "Patch  " + pname, color=dk.TEXT, w=cw - 240)
    try:
        pl.set_long_mode(lv.label.LONG.DOT)   # long patch names ellipsize
    except Exception:
        pass
    nav = dk.button(r, "Browse  >", w=180, h=52, bg=dk.SURFACE2, font=dk.FONT_S)
    nav.add_event_cb(_open_patch, lv.EVENT.CLICKED, None)

    # Sound (per-instrument params) -> ParamEditor sub-panel. Sampled drums
    # have no osc/filter design; SYNTH kits get the per-pad editor instead.
    if not is_drum:
        r = dk.row(right)
        dk.label(r, "Sound", color=dk.TEXT)
        nav = dk.button(r, "Edit  >", w=150, h=52, bg=dk.SURFACE2, font=dk.FONT_S)
        nav.add_event_cb(_open_sound, lv.EVENT.CLICKED, None)
    elif isinstance(instr.get('kit'), str) and instr['kit'].startswith('synth:'):
        r = dk.row(right)
        dk.label(r, "Pads", color=dk.TEXT)
        nav = dk.button(r, "Edit  >", w=150, h=52, bg=dk.SURFACE2, font=dk.FONT_S)
        nav.add_event_cb(_open_pads, lv.EVENT.CLICKED, None)

    # FX -> the OWNING DEVICE's FX bus (shared by all instruments on it)
    r = dk.row(right)
    dk.label(r, "FX (device)", color=dk.TEXT)
    nav = dk.button(r, "Edit  >", w=150, h=52, bg=dk.SURFACE2, font=dk.FONT_S)
    nav.add_event_cb(_open_fx_row, lv.EVENT.CLICKED, None)

    # Reverb send: how much of THIS instrument feeds the shared room
    # (AMY aux-send; 1.0 = classic everything-in-the-room, 0 = dry).
    if instr.get('device') == 'internal':
        def _set_send(v, commit):
            val = v / 100.0
            if commit:
                deckcfg.set_instrument(iid, 'reverb_send', val)
            try:
                import forwarder
                for t in (forwarder._state.get('fx_targets') or ()):
                    if t.get('iid') == iid:
                        import amy
                        amy.send(bus=t['bus'], reverb_send=val)
                        t['send'] = val
                        break
            except Exception:
                pass
        r = dk.row(right)
        dk.label(r, "Reverb send", color=dk.TEXT)
        # The send only matters while the device ROOM is on -- built-in
        # patches bake NO reverb (verified: zero 'h' params in patches.h),
        # so with the room off this slider changes nothing audible. Say so
        # instead of letting it feel broken.
        try:
            import amyparams as _ap
            _rvfx = deckcfg.device_fx('internal') or {}
            _lvl = _ap.fx_value(_rvfx, {}, 'reverb', 'level')
        except Exception:
            _lvl = 0
        if not _lvl:
            dk.label(r, "(room off -- set Reverb in FX)", color=dk.MUTED,
                     font=dk.FONT_S)
        # Show the LIVE send (what the router actually told AMY), falling
        # back to the stored value -- the slider used to show the default
        # 100% while the bus sat elsewhere.
        cur_send = None
        try:
            import forwarder as _fwd
            for t in (_fwd._state.get('fx_targets') or ()):
                if t.get('iid') == iid:
                    cur_send = t.get('send')
                    break
        except Exception:
            pass
        if cur_send is None:
            cur_send = instr.get('reverb_send', 1.0)
        # (dk.slider callbacks receive the LVGL EVENT, not the value --
        # treating it as a number made every tick throw and the slider dead)
        dk.slider(r, int(cur_send * 100), 0, 100,
                  w=cw - 260,
                  cb=lambda e: _set_send(
                      e.get_target_obj().get_value(), False),
                  on_release=lambda e: _set_send(
                      e.get_target_obj().get_value(), True))

    # MPE -> sub-panel, only when the global MPE gate is on (C.4). When off,
    # no MPE button shows and the MPE panel is unreachable.
    if deckcfg.mpe_enabled():
        r = dk.row(right)
        dk.label(r, "MPE", color=dk.TEXT)
        on = instr.get('mpe', {}).get('enabled')
        nav = dk.button(r, ("On" if on else "Off") + "  >", w=150, h=52,
                        bg=(dk.GREEN if on else dk.SURFACE2), font=dk.FONT_S)
        nav.add_event_cb(_open_mpe, lv.EVENT.CLICKED, None)

    # Remove -- full width, below both columns
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
