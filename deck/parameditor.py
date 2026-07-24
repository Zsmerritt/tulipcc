# parameditor.py -- a generic, data-driven param editor (base for D2 sub-panels).
#
# Feed it a list of param definitions (amyparams.PARAMS or a curated subset) and
# an instrument id; it renders the right deckui control per type (slider /
# stepper / dropdown / toggle), grouped by section, reads/writes each value into
# the instrument's stored params (deckcfg), and calls on_change() after each edit
# so the caller can re-apply to AMY. It knows nothing about "Juno" vs "DX7".
#
# A curated view subclasses this with a param subset + custom labels/order:
#   class JunoEditor(ParamEditor):
#       def __init__(self, iid, **k):
#           super().__init__(iid, defs=[amyparams.PARAM_BY_NAME[n] for n in
#                                       ('oscA_wave', 'filter_freq', ...)], **k)
#       def label_for(self, d): return _JUNO_LABELS.get(d['name'], d['label'])
#
# Basic/Advanced: `show_advanced` includes the advanced-tier params too.

import deckui as dk
import deckcfg
import amyparams
import lvgl as lv


class ParamEditor:
    def __init__(self, iid, defs=None, on_change=None, show_advanced=False,
                 group_headers=True, on_action=None, header_note=None):
        self.iid = iid
        self.defs = defs if defs is not None else amyparams.PARAMS
        self.on_change = on_change
        self.show_advanced = show_advanced
        # In the tabbed editor each tab is one group, so the in-list group header
        # is redundant -- callers set this False.
        self.group_headers = group_headers
        # on_action(d): opens a custom picker for a type-'action' control (the
        # DX7 algorithm modal). header_note(defs) -> (text, color) or None: an
        # optional line drawn atop a tab (the OP page's carrier/modulator role).
        self.on_action = on_action
        self.header_note = header_note
        # What this instrument's PATCH actually bakes, so every control starts
        # from what is SOUNDING rather than from a schema default the patch
        # never had (FxEditor seeds from device_patch_fx for exactly the same
        # reason). Read once at build time: it is a pure function of the patch
        # number and cannot change while the panel is open.
        #
        # Seeding is DISPLAY-ONLY. It is never written back to deckcfg, so an
        # untouched param stays unstored and therefore unsent -- only _set(),
        # which nothing but a real slider/dropdown event calls, ever stores.
        self._ppv = self._load_patch_params()

    def _load_patch_params(self):
        try:
            return amyparams.patch_params(deckcfg.get_instrument(self.iid))
        except Exception:
            return {}

    # ----- overridable hooks for curated subclasses -----
    def label_for(self, d):
        return d.get('label', d['name'])

    def visible_defs(self):
        if self.show_advanced:
            return list(self.defs)
        return [d for d in self.defs if d.get('tier', 'advanced') == 'basic']

    # ----- rendering -----
    def build(self, body):
        if self.header_note is not None:
            try:
                note = self.header_note(self.visible_defs())
            except Exception:
                note = None
            if note:
                text, color = note
                dk.label(body, text, color=color, font=dk.FONT_S)
        seen = []
        for d in self.visible_defs():
            g = d.get('group', '')
            if self.group_headers and g and g not in seen:
                seen.append(g)
                dk.label(body, g, color=dk.MUTED, font=dk.FONT_S)
            self._control(body, d)

    def _control(self, body, d):
        t = d['type']
        if t == 'slider':
            self._slider(body, d)
        elif t == 'dropdown':
            self._dropdown(body, d)
        elif t == 'stepper':
            self._stepper(body, d)
        elif t == 'toggle':
            self._toggle(body, d)
        elif t == 'action':
            self._action(body, d)

    def _action(self, body, d):
        # A row whose value is a BUTTON that hands off to on_action(d) -- used
        # by the DX7 algorithm control to open the picker modal. The button
        # shows the current value (or "patch default" when unresolved), exactly
        # like a slider readout, keeping the honesty marker intact.
        cur, src = self._get_source(d)
        r = dk.row(body)
        dk.label(r, self.label_for(d), color=dk.TEXT)
        txt = self._fmt_value(d, cur, src)
        b = dk.button(r, txt + "  >", w=210, h=52, bg=dk.SURFACE2,
                      font=dk.FONT_S)
        b.add_event_cb(lambda e: (self._fire_action(d)
                       if e.get_code() == lv.EVENT.CLICKED else None),
                       lv.EVENT.CLICKED, None)

    def _fire_action(self, d):
        if self.on_action is not None:
            try:
                self.on_action(d)
            except Exception:
                pass

    # ----- value plumbing -----
    def _stored_params(self):
        """ONLY the params the user has actually set (deckcfg stores nothing
        else). The layer that must never be confused with a seeded display."""
        try:
            return (deckcfg.get_instrument(self.iid) or {}).get('params') or {}
        except Exception:
            return {}

    def _get_source(self, d):
        """(value, 'user'|'patch'|'default') for a control."""
        return amyparams.param_value_source(self._stored_params(), self._ppv,
                                            d['name'], d['default'])

    def _get(self, d):
        return self._get_source(d)[0]

    def _set(self, d, value, flush=True):
        # flush=False during a slider drag: the value lands in deckcfg's RAM
        # cache (live audition via on_change reads it from there) but is not
        # written to flash until the release commit -- a flash write per
        # VALUE_CHANGED tick stalls both cores and wears the config sector.
        deckcfg.set_instrument_param(self.iid, d['name'], value, flush=flush)
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:
                pass

    # ----- value formatting -----
    def _fmt_value(self, d, value, source='user'):
        """Human-readable current value: decimals scaled to the param's
        resolution (whole ms/Hz at scale 1, tenths at 10, hundredths at 100),
        with an optional unit suffix (Hz / ms / dB). Unit '%' formats a 0..1
        fraction as a percentage ("0.50" read as a broken slider; "50 %"
        doesn't -- UX-REVIEW-6 L7).

        A param whose number we would be INVENTING reads "patch default"
        instead. `value` is still a real number there -- the schema fallback --
        but it is OUR guess, and printing it is what told a user their GM patch
        had "attack 0, decay 0, release 0" when it actually had 5/60000/220.
        amyparams.is_fabricated() decides, per param and against AMY's own
        reset defaults; it is deliberately narrow (ten params), because a
        marker on all twenty-five would train the eye to skip it.
        """
        if amyparams.is_fabricated(d, source):
            return 'patch default'
        scale = d.get('scale', 1)
        unit = d.get('unit', '')
        try:
            if unit == '%':
                return "%d %%" % int(round(float(value) * 100))
            dec = 2 if scale >= 100 else (1 if scale >= 10 else 0)
            s = "%.*f" % (dec, float(value))
        except Exception:
            s = str(value)
        return (s + " " + unit) if unit else s

    # ----- per-type controls -----
    def _slider(self, body, d):
        # A card showing the param name + a LIVE value readout on top, above a
        # full-width fat slider (the MPE screen's pattern, generalized). The
        # audit flagged the old bare 14px slider with no number as "blind".
        cur, src = self._get_source(d)
        cell = lv.obj(body)
        cell.set_width(lv.pct(100))
        cell.set_height(96)
        dk._flat(cell, radius=16, bg=dk.SURFACE)
        cell.remove_flag(lv.obj.FLAG.SCROLLABLE)
        cell.set_style_pad_all(0, 0)
        name = dk.label(cell, self.label_for(d), color=dk.TEXT)
        name.align(lv.ALIGN.TOP_LEFT, 20, 12)
        val = dk.label(cell, self._fmt_value(d, cur, src),
                       color=(dk.TEAL if src != 'default' else dk.MUTED),
                       font=dk.FONT_MONO, w=150, align=lv.TEXT_ALIGN.RIGHT)
        val.align(lv.ALIGN.TOP_RIGHT, -20, 12)
        # POSITIONS, not values: amyparams owns the position<->value mapping so
        # the log-curve params (the six envelope times, cutoff) can spread a
        # 0..150000 ms / 16..100000 Hz span over one track without giving up
        # 1 ms / 1 Hz resolution down where the music is. Linear params are the
        # identity case -- position = value*scale rebased to 0 -- and behave
        # exactly as before.
        #
        # curve_pos() is EXACT for every value the curve itself can produce,
        # i.e. everything this slider can store, so a re-render always puts the
        # knob back where the user left it. A value the curve cannot produce (a
        # patch's baked 60000 ms, a setting saved by the old linear slider)
        # shows the nearest position while the readout above keeps printing the
        # TRUE number -- and nothing writes the approximation back, because
        # seeding only ever reads. Only a real touch event stores a value.
        s = dk.slider(cell, amyparams.curve_pos(d, cur), 0,
                      amyparams.curve_steps(d), w=lv.pct(84),
                      cb=self._slider_cb(d, val), color=dk.TEAL, h=26,
                      on_release=self._slider_release_cb(d))
        s.align(lv.ALIGN.BOTTOM_MID, 0, -24)
        # min/max microlabels at the track ends: a knob hard-left on a nonzero
        # minimum (duty 50 %) is indistinguishable from a broken slider without
        # them (UX-REVIEW-6 L7)
        lo = dk.label(cell, self._fmt_value(d, d['min']), color=dk.MUTED,
                      font=dk.FONT_S)
        lo.align(lv.ALIGN.BOTTOM_LEFT, 20, -4)
        hi = dk.label(cell, self._fmt_value(d, d['max']), color=dk.MUTED,
                      font=dk.FONT_S)
        hi.align(lv.ALIGN.BOTTOM_RIGHT, -20, -4)

    def _slider_cb(self, d, val_label=None):
        # Per-tick during a drag: cache-only value update + live audition.
        def cb(e):
            v = amyparams.curve_value(d, e.get_target_obj().get_value())
            self._set(d, v, flush=False)
            if val_label is not None:
                try:
                    val_label.set_text(self._fmt_value(d, v))
                except Exception:
                    pass
        return cb

    def _slider_release_cb(self, d):
        # Finger lifted: commit the final value to flash (one write per drag).
        def cb(e):
            self._set(d, amyparams.curve_value(d, e.get_target_obj().get_value()))
        return cb

    def _dropdown(self, body, d):
        cur = self._get(d)
        vals = d.get('option_values', list(range(len(d['options']))))
        try:
            idx = vals.index(cur)
        except ValueError:
            idx = 0
        r = dk.row(body)
        dk.label(r, self.label_for(d), color=dk.TEXT)
        dd = lv.dropdown(r)
        dd.set_options("\n".join(d['options']))
        dd.set_selected(idx)
        dd.set_width(200)
        _style_dropdown(dd)
        dd.add_event_cb(self._dropdown_cb(d, vals), lv.EVENT.VALUE_CHANGED, None)

    def _dropdown_cb(self, d, vals):
        def cb(e):
            i = e.get_target_obj().get_selected()
            self._set(d, vals[i] if 0 <= i < len(vals) else vals[0])
        return cb

    def _stepper(self, body, d):
        cur = self._get(d)
        r = dk.row(body)
        dk.label(r, self.label_for(d), color=dk.TEXT)
        dk.stepper(r, int(cur), int(d['min']), int(d['max']),
                   lambda v: self._set(d, v), w=210)

    def _toggle(self, body, d):
        cur = self._get(d)
        r = dk.row(body)
        dk.label(r, self.label_for(d), color=dk.TEXT)
        dk.switch(r, bool(cur), lambda v: self._set(d, v))


class FxEditor(ParamEditor):
    """A ParamEditor variant over a DEVICE's FX buses (reverb/chorus/echo/EQ).
    Same controls, but values read/write via deckcfg.device_fx / set_device_fx
    (per-device, shared by every instrument on that device)."""

    def __init__(self, device, on_change=None, defs=None, group_headers=True):
        super().__init__(None,
                         defs=defs if defs is not None else amyparams.fx_defs(),
                         on_change=on_change, show_advanced=True,
                         group_headers=group_headers)
        self.device = device
        # FX the active patch itself applies: sliders must start from what is
        # actually sounding (a juno's chorus), not from zeros the user then
        # "lowers" in confusion.
        self._pfx = amyparams.device_patch_fx(device)

    def _get_source(self, d):
        # FX values are always CONCRETE (schema default < patch < user), so
        # they never take the "patch default" unknown path -- fx_value already
        # layers the patch's own FX in. Reported as 'user' to keep the plain
        # numeric readout (fx_defs also marks them TRUTH_DECK, so the marker
        # could not fire even if a future caller reported 'default' here).
        return (amyparams.fx_value(deckcfg.device_fx(self.device), self._pfx,
                                   d['bus'], d['name']), 'user')

    def _set(self, d, value, flush=True):
        deckcfg.set_device_fx(self.device, d['bus'], d['name'], value,
                              flush=flush)
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:
                pass


def _style_dropdown(dd):
    """Deck-palette a dropdown (shared helper lives in deckui now)."""
    dk.style_dropdown(dd)


def _style_tabview(tv):
    """Paint an lv.tabview's bar + tab buttons into the deck palette (default
    theme renders the bar/buttons in maroon/olive)."""
    try:
        tv.set_style_bg_color(dk.c(dk.BG), 0)
        tv.set_style_border_width(0, 0)
    except Exception:
        pass
    try:
        bar = tv.get_tab_bar()
    except Exception:
        bar = None
    if bar is None:
        return
    try:
        bar.set_style_bg_opa(lv.OPA.COVER, 0)
        bar.set_style_bg_color(dk.c(dk.SURFACE), 0)
        bar.set_style_border_width(0, 0)
        bar.set_style_pad_all(6, 0)
        bar.set_style_pad_row(6, 0)
    except Exception:
        pass
    try:
        n = bar.get_child_count()
    except Exception:
        n = 0
    for i in range(n):
        try:
            btn = bar.get_child(i)
            btn.set_style_bg_opa(lv.OPA.COVER, 0)
            btn.set_style_bg_color(dk.c(dk.SURFACE2), 0)
            # PLACEHOLDER (lighter than MUTED): inactive tab labels were below
            # arm's-length contrast on SURFACE2 (UX-REVIEW-7 N4)
            btn.set_style_text_color(dk.c(dk.PLACEHOLDER), 0)
            btn.set_style_radius(10, 0)
            btn.set_style_border_width(0, 0)
        except Exception:
            pass
    # ACTIVE tab painted EXPLICITLY: the STATE.CHECKED style selector never
    # rendered on this build, so the selected tab looked recessed while the
    # inactive ones read as raised buttons -- inverted hierarchy on every
    # tabbed panel (fresh-eyes F-3). Recolor on each switch: ~4 small
    # buttons, and the tab switch already repaints the content page.
    _paint_active_tab(tv)
    try:
        tv.add_event_cb(lambda e: _paint_active_tab(tv),
                        lv.EVENT.VALUE_CHANGED, None)
    except Exception:
        pass


def _paint_active_tab(tv):
    try:
        bar = tv.get_tab_bar()
        try:
            act = tv.get_tab_active()
        except AttributeError:
            act = tv.get_active()
        for i in range(bar.get_child_count()):
            btn = bar.get_child(i)
            on = (i == act)
            bg = dk.c(dk.ACCENT if on else dk.SURFACE2)
            fg = dk.c(dk.WHITE if on else dk.PLACEHOLDER)
            # The theme's exact CHECKED-state style outranks local
            # default-state props (round-2 F-3: maroon/teal active tab),
            # so pin the same colors at CHECKED as well.
            for sel in (0, lv.STATE.CHECKED):
                btn.set_style_bg_color(bg, sel)
                btn.set_style_text_color(fg, sel)
                btn.set_style_bg_opa(lv.OPA.COVER, sel)
    except Exception:
        pass


def build_tabbed(parent, tabs, make_editor, x=0, y=0, w=None, h=None,
                 tab_bar=140):
    """Build an lv.tabview with a LEFT tab bar; one tab per (label, defs) in
    `tabs`, each filled by make_editor(defs).build(page). Returns the tabview.

    make_editor(defs) -> a ParamEditor/FxEditor for that tab's defs (its group
    header is suppressed since the tab label already names the group)."""
    import tulip
    if w is None:
        w = tulip.screen_size()[0]
    tv = lv.tabview(parent)
    try:
        tv.set_tab_bar_position(lv.DIR.LEFT)
    except Exception:
        pass
    try:
        tv.set_tab_bar_size(tab_bar)
    except Exception:
        pass
    if h is not None:
        tv.set_size(w, h)
    tv.set_pos(x, y)
    try:
        tv.set_style_bg_color(dk.c(dk.BG), 0)
    except Exception:
        pass
    # Build the FIRST tab synchronously (it's what the user sees), then fill
    # the remaining tabs one per deferred tick. Building every tab's full
    # control set in one LVGL callback is heavy enough to contribute to the
    # interrupt-WDT reboot on brisk navigation (UX-REVIEW-6 H1) -- chunking
    # keeps each tick short so CPU1 never starves.
    pages = []
    for label, defs in tabs:
        page = tv.add_tab(label)
        # dk.row() relies on the parent being a flex column; the raw tab page
        # isn't one, so its controls would stack at (0,0) and overlap.
        try:
            page.set_flex_flow(lv.FLEX_FLOW.COLUMN)
            page.set_style_pad_row(12, 0)
            page.set_scroll_dir(lv.DIR.VER)
        except Exception:
            pass
        pages.append((page, defs))

    def _fill(page, defs):
        ed = make_editor(defs)
        ed.group_headers = False
        ed.build(page)

    if pages:
        _fill(*pages[0])

    def _fill_next(i):
        def _do(x):
            if i >= len(pages):
                return
            try:
                _fill(*pages[i])       # throws if the tabview was deleted
            except Exception:
                return                 # panel gone (user navigated away): stop
            _fill_next(i + 1)
        try:
            tulip.defer(_do, 0, 25)
        except Exception:
            _do(None)                  # no defer (host tests): build inline
    _fill_next(1)
    _style_tabview(tv)
    return tv
