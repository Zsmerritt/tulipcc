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


def _hit_display(key):
    """Human name for a pad's hit -- the WAV basename for a sample swap, else
    the corpus hit's display name."""
    import samplepresets
    if samplepresets.is_sample_swap(key):
        return (samplepresets.swap_path(key) or '').rsplit('/', 1)[-1]
    import synthkits
    return synthkits.hit_name(key or '')


def _audition(note):
    kit = _live_kit()
    try:
        if kit is not None:
            kit.note_on(note, 1.0)
        else:
            import samplepresets
            key = _hit_key_for(note)
            # a resident sample can only be auditioned through the live router
            # (it plays from the pad's loaded PCM preset) -- skip off-router
            if key and not samplepresets.is_sample_swap(key):
                import synthkits
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
    else:
        # drag ticks fire per VALUE_CHANGED -- a machine-gun of hits +
        # store_patch traffic (E-12). Keep retweak per tick (the NEXT
        # natural hit stays live) but rate-limit the triggered note ~5 Hz.
        from time import ticks_ms, ticks_diff
        now = ticks_ms()
        if ticks_diff(now, _s.get('last_audition', -10000)) >= 200:
            _s['last_audition'] = now
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


def _set_swap_use_enabled(on):
    """Enable/dim the swap picker's "Use selected" button (Files action-bar
    pattern): it opens disabled so it never looks tappable before a hit is
    chosen (UX11-3), then enables on the first selection."""
    b = _s.get('swap_use_btn')
    if b is None:
        return
    try:
        if on:
            b.set_style_bg_color(dk.c(dk.GREEN), 0)
            b.get_child(0).set_style_text_color(dk.c(dk.WHITE), 0)
            b.remove_state(lv.STATE.DISABLED)
        else:
            b.set_style_bg_color(dk.c(dk.SURFACE2), 0)
            b.get_child(0).set_style_text_color(dk.c(dk.MUTED), 0)
            b.add_state(lv.STATE.DISABLED)
    except Exception:
        pass


def _pad_toast(msg, color=None):
    sh = _s.get('shell')
    scr = getattr(sh, 'screen', None) if sh is not None else None
    if scr is not None:
        try:
            dk.toast(scr, msg, color if color is not None else dk.ORANGE)
        except Exception:
            pass


def _set_sample_swap(note, path):
    """Commit a WAV as this pad's hit: allocate (or reuse) a user PCM preset,
    then store a TYPED sample swap. drums_kit loads the WAV + arms a one-shot."""
    import samplepresets
    iid = deckcfg.active_instrument()
    instr = deckcfg.get_instrument(iid) or {}
    existing = (instr.get('hit_swaps') or {}).get(str(note)) \
        or (instr.get('hit_swaps') or {}).get(note)
    try:
        preset = samplepresets.preset_for(deckcfg.instruments(), existing)
    except ValueError as ex:
        _pad_toast(str(ex), dk.RED)
        return
    _set_swap(note, samplepresets.make_sample_swap(path, preset))


def swap_panel(parent, shell=None):
    """Alternates picker with two SOURCES: the built-in hit CORPUS (browse by
    pack, tap to audition) and user SAMPLES (browse the SD card for 16-bit mono
    WAVs). Tap to select, then Use. The pad keeps its tweaks across a corpus
    swap; sample pads play the WAV as a one-shot (see samplepresets)."""
    import synthkits
    import samplepresets
    note = _s.get('note')
    if note is None:
        dk.label(parent, "Pick a pad first.", 24, 24, color=dk.MUTED)
        return
    w = tulip.screen_size()[0]
    import homeshell
    H = tulip.screen_size()[1] - homeshell.BAR_H
    _s['swap_sel'] = None            # pending corpus key
    _s['swap_sample'] = None         # pending validated WAV path
    _s.setdefault('swap_src', 'packs')
    _s.setdefault('sd_dir', samplepresets.SD_ROOT)

    top = dk.row(parent, h=64)
    top.set_pos(0, 0)
    # source toggle: Packs | Samples
    src_seg = dk.hgroup(top, w=260, h=48)
    ub = dk.button(top, "Use selected", w=190, h=52, bg=dk.GREEN, font=dk.FONT_S)
    db = dk.button(top, "Kit default", w=170, h=52, bg=dk.SURFACE2,
                   font=dk.FONT_S)
    _s['swap_use_btn'] = ub
    _set_swap_use_enabled(False)    # nothing selected yet (UX11-3)

    body = dk.row(parent, h=H - 96)
    body.set_pos(0, 72)
    # scroll_col, not bare lv.obj: without a flex column every pack/hit/sample
    # button stacked at (0,0) and only the last one showed.
    # Use scroll_col's DEFAULT row gap (12) -- the gap=8 override was the lone
    # deviation in the app, and at 8px the rows' rounded corners carved the dark
    # column bg into a right-pointing "funnel" wedge on RGB332 (UX12-1). The
    # patch and kit pickers use the default 12 with the same radius/colors and
    # show no funnel (verified: ux-review-12 shots 04 vs 05); match them.
    # left/right (not packs/hits): both columns are rebuilt per SOURCE (packs
    # vs SD samples), so the names are source-neutral. The empty-state prompt
    # now lives in _build_packs/_build_samples (rebuilt on every source switch).
    left = dk.scroll_col(body, 300, H - 110)
    right = dk.scroll_col(body, w - 360, H - 110)

    def _clear_sel():
        _s['swap_sel'] = None
        _s['swap_sample'] = None
        _s['swap_btn'] = None
        _set_swap_use_enabled(False)    # nothing selected -> dim Use (UX11-3)

    # ---- corpus (packs) browser ----
    def _pick(key, btn):
        _clear_sel()
        _s['swap_sel'] = key
        _highlight(btn)
        try:
            synthkits.audition(key, _overrides(note))
        except Exception:
            pass

    def _highlight(btn):
        old = _s.get('swap_btn')
        if old is not None:
            try:
                old.set_style_bg_color(dk.c(dk.SURFACE), 0)
            except Exception:
                pass
        _s['swap_btn'] = btn
        _set_swap_use_enabled(True)     # a hit/sample is now chosen (UX11-3)
        try:
            btn.set_style_bg_color(dk.c(dk.ACCENT), 0)
        except Exception:
            pass

    def _show_pack(pack, pbtn=None):
        old = _s.get('swap_packbtn')
        if old is not None:
            try:
                old.set_style_bg_color(dk.c(dk.SURFACE), 0)
            except Exception:
                pass
        if pbtn is not None:
            _s['swap_packbtn'] = pbtn
            pbtn.set_style_bg_color(dk.c(dk.ACCENT), 0)
        right.clean()
        _s['swap_btn'] = None
        # mark the pad's CURRENT effective hit with the same accent the patch
        # and kit pickers use for the current row (UX11-4)
        cur_hit = _hit_key_for(note)
        keys = synthkits.pack_hits(pack)
        seen = {}
        for key in keys[:150]:
            nm = synthkits.hit_name(key)
            seen[nm] = seen.get(nm, 0) + 1
            if seen[nm] > 1:
                nm = "%s (%d)" % (nm, seen[nm])
            is_cur = (key == cur_hit)
            b = dk.button(right, nm, w=lv.pct(96), h=52,
                          bg=(dk.ACCENT if is_cur else dk.SURFACE),
                          font=dk.FONT_S)
            b.add_event_cb(
                (lambda k: (lambda e: _pick(k, e.get_target_obj())
                            if e.get_code() == lv.EVENT.CLICKED else None))(key),
                lv.EVENT.CLICKED, None)
            if is_cur:
                # track it so the first real pick de-highlights it, like the
                # pickers do; Use stays disabled until an actual tap (UX11-3)
                _s['swap_btn'] = b
        if len(keys) > 150:
            dk.label(right, "(+%d more in this pack)" % (len(keys) - 150),
                     color=dk.MUTED, font=dk.FONT_S)

    def _build_packs():
        left.clean()
        right.clean()
        dk.label(right, "Pick a pack to browse its hits", color=dk.MUTED,
                 font=dk.FONT_S)
        for pack in sorted(synthkits._load().get('packs', {})):
            b = dk.button(left, pack.replace('_', ' '), w=lv.pct(96), h=52,
                          bg=dk.SURFACE, font=dk.FONT_S)
            b.add_event_cb(
                (lambda p, pb: (lambda e: _show_pack(p, pb)
                                if e.get_code() == lv.EVENT.CLICKED else None)
                 )(pack, b), lv.EVENT.CLICKED, None)

    # ---- samples (SD) browser ----
    def _pick_wav(path, btn):
        ok, info = samplepresets.validate(path)
        if not ok:
            _pad_toast("Not usable: %s" % info, dk.RED)
            return
        _clear_sel()
        _s['swap_sample'] = path
        _highlight(btn)
        _pad_toast("Selected %s" % path.rsplit('/', 1)[-1], dk.GREEN)

    def _show_dir(directory):
        _s['sd_dir'] = directory
        _build_samples()

    def _build_samples():
        left.clean()
        right.clean()
        directory = _s.get('sd_dir', samplepresets.SD_ROOT)
        dk.label(left, directory, color=dk.MUTED, font=dk.FONT_S,
                 w=290)
        root = samplepresets.SD_ROOT
        if directory.rstrip('/') != root.rstrip('/'):
            up = dk.button(left, lv.SYMBOL.UP + " Up", w=lv.pct(96), h=48,
                           bg=dk.SURFACE2, font=dk.FONT_S)
            parent_dir = directory.rstrip('/').rsplit('/', 1)[0] or root
            up.add_event_cb((lambda d: (lambda e: _show_dir(d)
                            if e.get_code() == lv.EVENT.CLICKED else None))(
                                parent_dir), lv.EVENT.CLICKED, None)
        dirs, wavs = samplepresets.list_wavs(directory)
        for d in dirs:
            full = directory.rstrip('/') + '/' + d
            b = dk.button(left, lv.SYMBOL.DIRECTORY + "  " + d, w=lv.pct(96),
                          h=48, bg=dk.SURFACE, font=dk.FONT_S)
            b.add_event_cb((lambda p: (lambda e: _show_dir(p)
                            if e.get_code() == lv.EVENT.CLICKED else None))(
                                full), lv.EVENT.CLICKED, None)
        if not dirs and not wavs:
            dk.label(right, "No WAV files here. 16-bit mono WAVs on the SD "
                     "card show up in this list.", color=dk.MUTED,
                     font=dk.FONT_S, w=w - 400)
            return
        for name in wavs:
            full = directory.rstrip('/') + '/' + name
            b = dk.button(right, name, w=lv.pct(96), h=52, bg=dk.SURFACE,
                          font=dk.FONT_S)
            b.add_event_cb((lambda p: (lambda e: _pick_wav(
                p, e.get_target_obj())
                if e.get_code() == lv.EVENT.CLICKED else None))(full),
                lv.EVENT.CLICKED, None)

    def _set_src(src):
        if _s.get('swap_src') != src:
            _s['swap_src'] = src
            _clear_sel()
        for lbl, val, b in _s.get('srcbtns', []):
            try:
                b.set_style_bg_color(dk.c(dk.ACCENT if val == src
                                          else dk.SURFACE2), 0)
            except Exception:
                pass
        if src == 'packs':
            _build_packs()
        else:
            _build_samples()

    _s['srcbtns'] = []
    for lbl, val in (("Packs", 'packs'), ("Samples", 'samples')):
        b = dk.button(src_seg, lbl, w=120, h=48, font=dk.FONT_S,
                      bg=(dk.ACCENT if _s['swap_src'] == val else dk.SURFACE2))
        b.add_event_cb((lambda v: (lambda e: _set_src(v)
                        if e.get_code() == lv.EVENT.CLICKED else None))(val),
                       lv.EVENT.CLICKED, None)
        _s['srcbtns'].append((lbl, val, b))

    def _use(e):
        if e.get_code() != lv.EVENT.CLICKED:
            return
        if _s.get('swap_sample'):
            _set_sample_swap(note, _s['swap_sample'])
        elif _s.get('swap_sel'):
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
    _set_src(_s['swap_src'])          # build the initial source view


def _select(note):
    _s['note'] = note
    for b, n in _s.get('pads', []):
        b.set_style_bg_color(dk.c(dk.ACCENT if n == note else dk.SURFACE), 0)
    card = _s.get('card')
    if card is None:
        return
    card.clean()
    key = _hit_key_for(note) or ''
    swapped = bool((_inst().get('hit_swaps') or {}).get(str(note)))
    dk.label(card, "%s  (note %d)" % (_NOTE_NAMES.get(note, 'Pad'), note),
             color=dk.WHITE, font=dk.FONT_M)
    r = dk.row(card, h=56, bg=dk.SURFACE2)
    # the hit NAME is a real value, not a placeholder -- TEXT, not MUTED (UX11-6,
    # the UX-REVIEW-7 NEW-4 class the search fields already fixed). _hit_display
    # is sample-aware: WAV basename for a sample swap, else the corpus hit name.
    nl = dk.label(r, _hit_display(key) + (' *' if swapped else ''),
                  color=dk.TEXT, font=dk.FONT_S, w=_s['cw'] - 220)
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
        # its value; these four were tune-by-ear-only. WHITE + FONT_MONO to
        # match the app's readable value/LED style -- the dim TEAL/FONT_S was
        # tiny and low-contrast on the row (UX11-6).
        unit = '' if pkey == 'tune' else '%'
        vl = dk.label(r, "%d%s" % (int(round(cur)), unit), color=dk.WHITE,
                      font=dk.FONT_MONO)

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
    # Drop stale widget handles from a prior build: the no-kit path below
    # returns early without rebuilding pads/card, and swap_btn/swap_packbtn
    # (set inside the pushed swap panel) are never re-set here -- all would
    # otherwise point at deleted LVGL objects on the next rebuild.
    for _k in ('pads', 'card', 'swap_btn', 'swap_packbtn', 'swap_use_btn',
               'srcbtns'):
        _s.pop(_k, None)
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
