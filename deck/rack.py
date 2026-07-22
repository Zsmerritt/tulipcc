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
import kbmgr
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
# catalog.py owns the table (E-8); drums keeps a 0 here (kit id lives in
# 'kit', the patch slot is unused but must stay an int for older code).
import catalog as _catalog
_TYPE_FIRST_PATCH = {t: (v if v is not None else 0)
                     for t, v in _catalog.TYPE_FIRST_PATCH.items()}


def _panel_h():
    import homeshell
    return tulip.screen_size()[1] - homeshell.BAR_H


# ---------------- rack list ----------------
def panel(parent, shell=None, footer=None):
    """The instruments rack. As of the S1 rework this is also the HOME ROOT
    (home._build_root wraps it); `footer(body)` lets the root append its
    Devices/System entries under the Add button."""
    # Panel builders share the module-level _s; a prior editor/sound/kit build
    # leaves widget handles behind that now point at deleted LVGL objects. Drop
    # only those stale handles -- list_parent/shell/footer are re-set just below
    # (and _refresh_list needs list_parent), and kitgen is an async guard, so a
    # blanket _s.clear() would be wrong.
    for _k in ('edit_parent', 'kitbtns', 'kitcur', 'vlabel'):
        _s.pop(_k, None)
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
    # ONE call does the whole move: instmove.move_instrument plans (does the
    # target even fit this instrument / this KIND of sound?), commits device +
    # channel, re-enrolls the affected boards, and rebuilds the router. We used
    # to blindly set device + next_free_channel + apply_all here, which (a) never
    # told the user when a board had no free channel (the instrument silently
    # vanished onto ch 1 of a full device) and (b) let GM/drums land on a bare
    # board that can't host them. Now a failure raises a modal instead.
    import instmove
    iid = deckcfg.active_instrument()
    plan = instmove.move_instrument(iid, dev)
    if plan.get('ok'):
        if not plan.get('changed'):
            _rebuild_edit()        # same-device tap: just keep the highlight
            return
        if _s.get('shell') is not None:
            _s['shell'].refresh_chips()
        _refresh_list()
        _rebuild_edit()
        return
    reason = plan.get('reason')
    if reason == 'not_found':
        return                     # stale callback over a removed instrument
    name = sm.device_name(dev)
    if reason == 'full':
        dk.choice("Device full",
                  "%s has no free MIDI channels. Pick another device." % name,
                  [("OK", dk.SURFACE2, None)])
    elif reason == 'unsupported_type':
        instr = deckcfg.get_instrument(iid)
        dk.choice("Can't move to a board",
                  instmove.unsupported_reason_text(instr, dev),
                  [("OK", dk.SURFACE2, None)])


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
    iid = deckcfg.active_instrument()
    deckcfg.set_instrument(iid, 'num_voices', v)
    deckcfg.apply_instrument(iid)   # O-5: only this synth rebuilds


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
    deckcfg.apply_instrument(iid)   # O-5: same slot window, one kit reload
    sh = _s.get('shell')
    if sh is not None:
        sh.refresh_chips()
    try:
        import forwarder
        forwarder.preview(iid, note=36)     # audition: a kick
    except Exception:
        pass
    for entry in _s.get('kitbtns', []):
        b, k = entry[0], entry[1]
        if k is None:
            continue                      # section header, not a kit row
        b.set_style_bg_color(dk.c(dk.ACCENT if k == kit else dk.SURFACE), 0)


def kit_panel(parent, shell=None):
    _s['shell'] = shell
    import drums_kit
    w = tulip.screen_size()[0]
    cur = (_active() or {}).get('kit', 384)
    # search on top (X-3: 76 kits was ~11 screens of scrolling with no way
    # to jump); filtering HIDES non-matching rows -- no rebuild per keystroke
    sr = dk.row(parent, h=64)
    sr.set_pos(24, 4)
    sr.set_width(w - 48)
    tf = dk.text_field(sr, placeholder="Search kits", w=w - 120, h=48)

    body = dk.scroll_col(parent, w - 48, _panel_h() - 84)
    body.set_pos(24, 74)
    # E-1: kitgen was only bumped on the NEXT open, so a teardown mid-chain
    # left deferred chunks building rows into a FREED body (LVGL hard-crashes
    # on that; try/except can't catch it). Bump the generation on DELETE.
    try:
        body.add_event_cb(lambda e: _s.update(kitgen=_s.get('kitgen', 0) + 1),
                          lv.EVENT.DELETE, None)
    except Exception:
        pass
    _s['kitbtns'] = []
    # sampled kits first, then a SECTION HEADER, then the synthesized ones
    # (the header carries the meaning; the per-row '(synth)' suffix is gone)
    rows = [(k, n, False) for k, n in drums_kit.KITS]
    rows.append((None, "Synthesized", None))
    for k, n in drums_kit.synth_kits():
        n = n[:-8] if n.endswith(' (synth)') else n
        rows.append((k, n, True))
    # Chunked fill (O-8): ~80 rows x (button+label+styles) in one LVGL tick
    # was a 40-80 ms event-callback stall -- the interrupt-WDT shape this
    # codebase has been bitten by before. First screenful lands inline, the
    # rest arrives in deferred chunks; the whole list exists within ~3 ticks.
    _s['kitgen'] = _s.get('kitgen', 0) + 1
    gen = _s['kitgen']

    def _build_row(kit, name):
        if kit is None:
            h = dk.label(body, name, color=dk.MUTED, font=dk.FONT_S)
            _s['kitbtns'].append((h, None, ''))
            return
        b = dk.button(body, name, w=lv.pct(100), h=64, font=dk.FONT_M,
                      bg=(dk.ACCENT if kit == cur else dk.SURFACE))
        b.add_event_cb((lambda k: (lambda e: _set_kit(k)
                        if e.get_code() == lv.EVENT.CLICKED else None))(kit),
                       lv.EVENT.CLICKED, None)
        _s['kitbtns'].append((b, kit, name.lower()))
        if kit == cur:
            _s['kitcur'] = b

    def _fill(start):
        if gen != _s.get('kitgen'):
            return                        # panel rebuilt/closed mid-chain
        end = min(len(rows), start + (16 if start == 0 else 25))
        try:
            for kit, name, _synth in rows[start:end]:
                _build_row(kit, name)
        except Exception:
            return                        # widgets deleted mid-chain
        if end < len(rows):
            try:
                tulip.defer(_fill, end, 10)
            except Exception:
                _fill(end)                # no defer slot: finish inline
            return
        # all built: jump (no animation -- an animated scroll repaints
        # every frame) so the CURRENT kit is visible on open (F-2)
        curbtn = _s.pop('kitcur', None)
        if curbtn is not None:
            try:
                curbtn.scroll_to_view(lv.ANIM.OFF)
            except Exception:
                pass
    _fill(0)

    def _filter(e):
        try:
            q = tf.ta.get_text().strip().lower()
        except Exception:
            return
        for widget, kit, lname in _s.get('kitbtns', []):
            try:
                if kit is None:
                    show = not q          # headers only when unfiltered
                else:
                    show = (not q) or (q in lname)
                if show:
                    widget.remove_flag(lv.obj.FLAG.HIDDEN)
                else:
                    widget.add_flag(lv.obj.FLAG.HIDDEN)
            except Exception:
                pass
    try:
        tf.ta.add_event_cb(_filter, lv.EVENT.VALUE_CHANGED, None)
    except Exception:
        pass


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


def _do_reset_patch():
    """Wipe the active instrument's sound-design overrides back to the patch.
    Run only after the confirm (see _build_edit._reset_patch)."""
    rid = deckcfg.active_instrument()
    deckcfg.set_instrument(rid, 'params', {}, flush=False)
    deckcfg.set_instrument(rid, 'reverb_send', 0.0, flush=False)
    deckcfg.set_instrument(rid, 'hits', {}, flush=False)
    # per-pad SWAPS are sound-design overrides too (review F-13)
    deckcfg.set_instrument(rid, 'hit_swaps', {})
    deckcfg.apply_instrument(rid)   # O-5
    _preset_toast("Patch reset")
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
              cb=lambda e: kbmgr.toggle(nt.ta, echo=True)).align(
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

    # Voices -- a sampled drum kit is one voice with a fixed osc per drum
    # sound baked into the patch (drums_kit.SAMPLED_KIT_IDS forces 1
    # regardless of this setting), so "voices" isn't polyphony for a drums
    # instrument. Show a static readout instead of a slider that would just
    # be silently ignored.
    r = dk.row(left, h=76)
    if instr.get('type') == 'drums':
        dk.label(r, "Voices", color=dk.TEXT)
        dk.label(r, "1 (fixed by kit)", color=dk.MUTED)
    else:
        _s['vlabel'] = dk.label(r, "%d voices" % instr.get('num_voices', 10),
                                color=dk.TEXT)
        dk.slider(r, instr.get('num_voices', 10), 1, 32, w=250, cb=_voices_cb,
                  color=dk.GREEN, on_release=_voices_done)

    # Reset patch: clear this instrument's sound-design overrides (params,
    # reverb send, per-pad tweaks) back to what the patch itself defines.
    # Gated behind a confirm (UX10-6): it wipes potentially hours of sound
    # design instantly, the same severity as Remove instrument / Factory reset,
    # which both confirm. A toast acknowledges the (intentional) reset.
    def _reset_patch(e):
        if e.get_code() != lv.EVENT.CLICKED:
            return
        dk.confirm("Reset patch?",
                   "Clears this instrument's sound edits (params, reverb send, "
                   "per-pad tweaks) back to the patch. This can't be undone.",
                   _do_reset_patch, yes_text="Reset")
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
                    + " >", w=180, h=52, bg=dk.SURFACE2, font=dk.FONT_S)
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
    nav = dk.button(r, "Browse >", w=180, h=52, bg=dk.SURFACE2, font=dk.FONT_S)
    nav.add_event_cb(_open_patch, lv.EVENT.CLICKED, None)

    # Sound (per-instrument params) -> ParamEditor sub-panel. Sampled drums
    # have no osc/filter design; SYNTH kits get the per-pad editor instead.
    if not is_drum:
        r = dk.row(right)
        dk.label(r, "Sound", color=dk.TEXT)
        nav = dk.button(r, "Edit >", w=150, h=52, bg=dk.SURFACE2, font=dk.FONT_S)
        nav.add_event_cb(_open_sound, lv.EVENT.CLICKED, None)
    elif isinstance(instr.get('kit'), str) and instr['kit'].startswith('synth:'):
        r = dk.row(right)
        dk.label(r, "Pads", color=dk.TEXT)
        nav = dk.button(r, "Edit >", w=150, h=52, bg=dk.SURFACE2, font=dk.FONT_S)
        nav.add_event_cb(_open_pads, lv.EVENT.CLICKED, None)

    # FX -> the OWNING DEVICE's FX bus (shared by all instruments on it)
    r = dk.row(right)
    dk.label(r, "FX (device)", color=dk.TEXT)
    nav = dk.button(r, "Edit >", w=150, h=52, bg=dk.SURFACE2, font=dk.FONT_S)
    nav.add_event_cb(_open_fx_row, lv.EVENT.CLICKED, None)

    # Presets -> save/recall this instrument's SOUND-DESIGN OVERLAY (type,
    # patch, params, reverb send, and for drums the kit + per-pad edits). NOT
    # the device FX bus (shared by other instruments) -- see presets.py.
    r = dk.row(right)
    dk.label(r, "Presets", color=dk.TEXT)
    nav = dk.button(r, "Open >", w=150, h=52, bg=dk.SURFACE2, font=dk.FONT_S)
    nav.add_event_cb(_open_presets, lv.EVENT.CLICKED, None)

    # (Reverb send moved into the Sound editor's FX group: it's a
    # per-instrument sound parameter, edited where the rest of the
    # instrument's sound lives. amyparams 'reverb_send' + forwarder
    # _apply_params carry the semantics: dry default, auto-room on raise.)

    # MPE -> sub-panel, only when the global MPE gate is on (C.4). When off,
    # no MPE button shows and the MPE panel is unreachable.
    if deckcfg.mpe_enabled():
        r = dk.row(right)
        dk.label(r, "MPE", color=dk.TEXT)
        on = instr.get('mpe', {}).get('enabled')
        nav = dk.button(r, ("On" if on else "Off") + " >", w=150, h=52,
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
    _snd['tab'] = 0        # active param-group tab, carried across rebuilds
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

    # DX7 FM edits are made HERE, on the per-operator pages, so the reset for
    # them lives here too -- a "Reset FM" that clears just the fm_* overrides
    # (leaving level/pan/reverb/tone) and reloads the baked patch. Same confirm
    # pattern as the rack "Reset patch" button (it wipes hand-tuned operators).
    if engine == 'dx7':
        rf = dk.button(hdr, "Reset FM", w=140, h=34, font=dk.FONT_S,
                       bg=dk.SURFACE2)
        rf.set_pos(200, 5)
        rf.add_event_cb(lambda e: (_reset_fm()
                        if e.get_code() == lv.EVENT.CLICKED else None),
                        lv.EVENT.CLICKED, None)

    # left-tabbed: one tab per engine-native group (tier-filtered), each a short
    # list. curated.tabbed falls back to the generic grouping for unknown engines.
    # DX7 also gets the algorithm picker (on_action) + per-OP carrier/modulator
    # role labels (header_note); both are no-ops for the other engines.
    act = _open_algo_picker if engine == 'dx7' else None
    note = _dx7_op_role_note if engine == 'dx7' else None

    def _make(defs):
        return curated.CuratedEditor(iid, defs=defs, labels=labels,
                                     on_change=_snd_apply, show_advanced=True,
                                     on_action=act, header_note=note)
    tabs = curated.tabbed(engine, _snd['adv'])
    tv = parameditor.build_tabbed(parent, tabs, _make,
                                  x=8, y=58, w=w - 16, h=_panel_h() - 66)
    # Carry the active tab across a Basic/Advanced rebuild (UX10-11): the switch
    # rebuilds the whole view, which otherwise dropped the user back on the
    # first tab (VCF -> Advanced landed on DCO). Restore the remembered tab and
    # keep tracking it as the user switches tabs.
    _restore_tab(tv, len(tabs))


def _restore_tab(tv, n):
    if tv is None:
        return
    want = _snd.get('tab', 0)
    if want >= n:
        want = 0
    if want:
        try:
            tv.set_active(want, lv.ANIM.OFF)
        except Exception:
            try:
                tv.set_tab_active(want, lv.ANIM.OFF)
            except Exception:
                pass
    try:
        tv.add_event_cb(lambda e: _on_snd_tab(tv), lv.EVENT.VALUE_CHANGED, None)
    except Exception:
        pass


def _on_snd_tab(tv):
    try:
        _snd['tab'] = tv.get_tab_active()
    except Exception:
        try:
            _snd['tab'] = tv.get_active()
        except Exception:
            pass


def _set_adv(value):
    if _snd.get('adv') == value:
        return
    _snd['adv'] = value
    _render_sound()


def _current_algo(iid):
    """The instrument's stored FM algorithm, or None when unset (the deck can't
    read a DX7 patch's baked algorithm, so role labels stay unknown until the
    user picks one)."""
    try:
        v = ((deckcfg.get_instrument(iid) or {}).get('params') or {}).get(
            'fm_algorithm')
        return int(v) if v is not None else None
    except Exception:
        return None


def _open_algo_picker(d):
    """Open the algorithm node-diagram modal from the Voice page's algorithm
    control. Selecting sets the voice algorithm live (fm_algorithm -> the 'o'
    param on osc 0) and re-renders so the diagram button + OP role labels
    update."""
    import algopicker
    iid = _snd.get('iid')
    cur = _current_algo(iid) or 1

    def _select(algo):
        deckcfg.set_instrument_param(iid, 'fm_algorithm', int(algo))
        _snd_apply()                 # live: reapply_params sends osc0 algorithm
        _render_sound()              # refresh the button label + OP roles
    algopicker.open_modal(cur, _select)


def _dx7_op_role_note(defs):
    """(text, color) role line for an OP page, or None for non-OP pages. Shows
    CARRIER / MODULATOR (+ feedback) for the operator this page edits under the
    CURRENTLY SET algorithm; muted prompt when no algorithm has been chosen."""
    if not defs:
        return None
    name = defs[0].get('name', '')
    if not name.startswith('fm_op'):
        return None
    try:
        op = int(name[5])
    except (ValueError, IndexError):
        return None
    algo = _current_algo(_snd.get('iid'))
    if algo is None:
        return ("Role: set algorithm to see", dk.MUTED)
    import dx7algos
    r = dx7algos.role(algo, op)
    fb = " + feedback" if dx7algos.has_feedback(algo, op) else ""
    if r == 'carrier':
        return ("CARRIER (algo %d)%s" % (algo, fb), dk.GREEN)
    return ("MODULATOR (algo %d)%s" % (algo, fb), dk.TEAL)


def _reset_fm():
    """Confirm, then clear this instrument's FM operator overrides (the fm_*
    params) back to the baked DX7 patch. Non-FM sound edits (level, pan,
    reverb, tone) are kept. apply_instrument -> rebuild_one reloads the patch
    string, restoring every baked operator ratio/level/envelope, then reapplies
    the params that remain."""
    def _do():
        iid = _snd.get('iid')
        instr = deckcfg.get_instrument(iid) or {}
        kept = {k: v for k, v in (instr.get('params') or {}).items()
                if not k.startswith('fm_')}
        deckcfg.set_instrument(iid, 'params', kept)
        deckcfg.apply_instrument(iid)
        _render_sound()
    dk.confirm("Reset FM?",
               "Clears this instrument's FM operator edits (ratios, levels, "
               "envelopes, algorithm, feedback) back to the patch. This can't "
               "be undone.",
               _do, yes_text="Reset")


def _snd_apply():
    try:
        import forwarder
        forwarder.reapply_params(_snd['iid'])
    except Exception:
        pass


# ---------------- Presets sub-panel (save/recall the sound overlay) ----------
# A "preset" is this instrument's sound-design overlay (type/patch/params/
# reverb_send, and for drums kit + per-pad edits) -- NOT the device FX bus.
# Storage + capture/recall logic lives in presets.py (host-tested); this is
# only the LVGL front end.
_pre = {}


def _open_presets(e):
    if e.get_code() != lv.EVENT.CLICKED:
        return
    if _s.get('shell') is not None:
        _s['shell'].push(presets_panel, "Presets", key='presets')


def _preset_toast(msg, color=None):
    sh = _s.get('shell')
    scr = getattr(sh, 'screen', None) if sh is not None else None
    if scr is not None:
        try:
            dk.toast(scr, msg, color if color is not None else dk.GREEN)
        except Exception:
            pass


def _do_save_preset(name):
    import presets
    instr = _active() or {}
    try:
        presets.save(name, instr)
        _preset_toast('Saved "%s"' % name)
    except Exception as ex:
        _preset_toast("Save failed: %r" % ex, dk.RED)
    _refresh_presets()


def _save_preset(name):
    """Save the active instrument's overlay as `name`. On a slug collision,
    offer Overwrite vs Save as a new (auto-suffixed) name vs Cancel."""
    import presets
    name = (name or '').strip()
    if not name:
        _preset_toast("Name the preset first", dk.ORANGE)
        return
    if presets.exists(name):
        alt = presets.unique_name(name)
        dk.choice('Preset exists',
                  'A preset named "%s" already exists.' % name,
                  [("Overwrite", dk.RED, lambda: _do_save_preset(name)),
                   ('Save as "%s"' % alt, dk.GREEN,
                    lambda: _do_save_preset(alt)),
                   ("Cancel", dk.SURFACE2, None)])
    else:
        _do_save_preset(name)


def _refresh_presets():
    parent = _pre.get('parent')
    if parent is None:
        return
    try:
        parent.clean()
    except Exception:
        return
    _build_presets(parent)


def presets_panel(parent, shell=None):
    _s['shell'] = shell
    _pre['parent'] = parent
    _build_presets(parent)


def _build_presets(parent):
    import presets
    w = tulip.screen_size()[0]
    instr = _active() or {}
    body = dk.scroll_col(parent, w - 48, _panel_h() - 16)
    body.set_pos(24, 8)

    dk.label(body, "Presets save this instrument's SOUND edits (type, patch, "
             "sound params, and for drums the kit + pad edits). Device FX are "
             "not included -- they are shared by every instrument on the "
             "device.", color=dk.MUTED, font=dk.FONT_S, w=w - 84)

    # --- Save current ---
    card = lv.obj(body)
    card.set_width(lv.pct(100))
    card.set_height(76)
    dk._flat(card, radius=16, bg=dk.SURFACE)
    card.remove_flag(lv.obj.FLAG.SCROLLABLE)
    card.set_style_pad_all(0, 0)
    dk.label(card, "Save as", color=dk.TEXT, font=dk.FONT_M).align(
        lv.ALIGN.LEFT_MID, 20, 0)
    nt = dk.text_field(card, text=instr.get('name', ''),
                       placeholder="preset name", w=250, h=44)
    nt.group.align(lv.ALIGN.RIGHT_MID, -220, 0)
    dk.button(card, tulip.lv.SYMBOL.KEYBOARD, w=52, h=44, bg=dk.SURFACE2,
              cb=lambda e: kbmgr.toggle(nt.ta, echo=True)).align(
                  lv.ALIGN.RIGHT_MID, -158, 0)
    sb = dk.button(card, "Save", w=130, h=48, bg=dk.GREEN, font=dk.FONT_S)
    sb.align(lv.ALIGN.RIGHT_MID, -14, 0)
    sb.add_event_cb(lambda e: (_save_preset(nt.ta.get_text())
                    if e.get_code() == lv.EVENT.CLICKED else None),
                    lv.EVENT.CLICKED, None)

    # --- Saved presets ---
    dk.label(body, "Saved presets", color=dk.MUTED, font=dk.FONT_S)
    items = presets.list_presets()
    if not items:
        dk.label(body, "No presets yet. Design a sound, then save it above.",
                 color=dk.MUTED, font=dk.FONT_S, w=w - 84)
        return
    for rec in items:
        rowc = lv.obj(body)
        rowc.set_width(lv.pct(100))
        rowc.set_height(64)
        dk._flat(rowc, radius=12, bg=dk.SURFACE)
        rowc.remove_flag(lv.obj.FLAG.SCROLLABLE)
        rowc.set_style_pad_all(0, 0)
        nm = dk.label(rowc, rec.get('name', '?'), color=dk.WHITE,
                      font=dk.FONT_M, w=w - 48 - 180)
        nm.align(lv.ALIGN.LEFT_MID, 16, 0)
        try:
            nm.set_long_mode(lv.label.LONG.DOT)
        except Exception:
            pass
        ob = dk.button(rowc, "Open >", w=140, h=48, bg=dk.SURFACE2,
                       font=dk.FONT_S)
        ob.align(lv.ALIGN.RIGHT_MID, -14, 0)
        ob.add_event_cb((lambda sl: (lambda e: _open_preset_detail(sl)
                        if e.get_code() == lv.EVENT.CLICKED else None))(
                            rec['slug']), lv.EVENT.CLICKED, None)


def _open_preset_detail(slug):
    _pre['slug'] = slug
    if _s.get('shell') is not None:
        _s['shell'].push(preset_detail_panel, "Preset", key='preset_detail')


def _do_recall():
    import presets
    rec = presets.load(_pre.get('slug'))
    if rec is None:
        _preset_toast("Preset not found", dk.RED)
        return
    iid = deckcfg.active_instrument()
    try:
        presets.recall(iid, rec)
    except Exception as ex:
        _preset_toast("Recall failed: %r" % ex, dk.RED)
        return
    sh = _s.get('shell')
    if sh is not None:
        try:
            sh.refresh_chips()
        except Exception:
            pass
    _preset_toast('Recalled "%s"' % rec.get('name', ''))
    # pop the detail panel; the revealed presets list (and the editor beneath
    # it) rebuild from their stored builders when next shown -- so the editor
    # reflects the recalled overlay (same mechanism _reset_patch relies on).
    if sh is not None:
        sh.back()


def _do_delete_preset():
    import presets
    presets.delete(_pre.get('slug'))
    sh = _s.get('shell')
    if sh is not None:
        sh.back()          # back to the (rebuilt) presets list


def _do_rename_preset(newname):
    import presets
    newname = (newname or '').strip()
    if not newname:
        _preset_toast("Enter a new name", dk.ORANGE)
        return
    rec = presets.rename(_pre.get('slug'), newname)
    if rec is not None:
        _pre['slug'] = rec['slug']
        _preset_toast('Renamed to "%s"' % rec['name'])
    _refresh_preset_detail()


def _refresh_preset_detail():
    parent = _pre.get('detail_parent')
    if parent is None:
        return
    try:
        parent.clean()
    except Exception:
        return
    _build_preset_detail(parent)


def preset_detail_panel(parent, shell=None):
    _s['shell'] = shell
    _pre['detail_parent'] = parent
    _build_preset_detail(parent)


def _build_preset_detail(parent):
    import presets
    w = tulip.screen_size()[0]
    rec = presets.load(_pre.get('slug'))
    body = dk.scroll_col(parent, w - 48, _panel_h() - 16)
    body.set_pos(24, 8)
    if rec is None:
        dk.label(body, "This preset is no longer available.", color=dk.MUTED,
                 font=dk.FONT_M)
        return
    dk.label(body, rec.get('name', '?'), color=dk.WHITE, font=dk.FONT_L)
    # a short summary of what recalling will apply. catalog.sound_label gives
    # the real sound NAME ("A11 Brass Set 1", "TR-808 kit") -- the old branch
    # printed a raw "patch 0" number instead (UX11-5); same helper the Home
    # footer/chip already use (E-8).
    import catalog
    summ = "%s - %s" % (_TYPE_NAMES.get(rec.get('type', ''),
                                        rec.get('type', '?')),
                        catalog.sound_label(rec))
    dk.label(body, summ, color=dk.MUTED, font=dk.FONT_S)
    dk.label(body, "Recall applies this sound to the current instrument "
             "(and may change its type). Device FX are not affected.",
             color=dk.MUTED, font=dk.FONT_S, w=w - 84)

    rb = dk.button(body, "Recall onto this instrument", w=lv.pct(100), h=60,
                   bg=dk.GREEN, font=dk.FONT_M)
    rb.add_event_cb(lambda e: (_do_recall()
                    if e.get_code() == lv.EVENT.CLICKED else None),
                    lv.EVENT.CLICKED, None)

    # --- Rename ---
    rcard = lv.obj(body)
    rcard.set_width(lv.pct(100))
    rcard.set_height(76)
    dk._flat(rcard, radius=16, bg=dk.SURFACE)
    rcard.remove_flag(lv.obj.FLAG.SCROLLABLE)
    rcard.set_style_pad_all(0, 0)
    dk.label(rcard, "Rename", color=dk.TEXT, font=dk.FONT_M).align(
        lv.ALIGN.LEFT_MID, 20, 0)
    rt = dk.text_field(rcard, text=rec.get('name', ''),
                       placeholder="new name", w=250, h=44)
    rt.group.align(lv.ALIGN.RIGHT_MID, -220, 0)
    dk.button(rcard, tulip.lv.SYMBOL.KEYBOARD, w=52, h=44, bg=dk.SURFACE2,
              cb=lambda e: kbmgr.toggle(rt.ta, echo=True)).align(
                  lv.ALIGN.RIGHT_MID, -158, 0)
    nb = dk.button(rcard, "Rename", w=130, h=48, bg=dk.ACCENT, font=dk.FONT_S)
    nb.align(lv.ALIGN.RIGHT_MID, -14, 0)
    nb.add_event_cb(lambda e: (_do_rename_preset(rt.ta.get_text())
                    if e.get_code() == lv.EVENT.CLICKED else None),
                    lv.EVENT.CLICKED, None)

    # --- Delete ---
    def _confirm_delete(e):
        if e.get_code() != lv.EVENT.CLICKED:
            return
        dk.confirm("Delete preset?",
                   'Delete "%s"? This cannot be undone.' % rec.get('name', ''),
                   _do_delete_preset, yes_text="Delete")
    db = dk.button(body, "Delete preset", w=lv.pct(100), h=56, bg=dk.RED,
                   font=dk.FONT_M)
    db.add_event_cb(_confirm_delete, lv.EVENT.CLICKED, None)
