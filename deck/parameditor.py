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
                 group_headers=True):
        self.iid = iid
        self.defs = defs if defs is not None else amyparams.PARAMS
        self.on_change = on_change
        self.show_advanced = show_advanced
        # In the tabbed editor each tab is one group, so the in-list group header
        # is redundant -- callers set this False.
        self.group_headers = group_headers

    # ----- overridable hooks for curated subclasses -----
    def label_for(self, d):
        return d.get('label', d['name'])

    def visible_defs(self):
        if self.show_advanced:
            return list(self.defs)
        return [d for d in self.defs if d.get('tier', 'advanced') == 'basic']

    # ----- rendering -----
    def build(self, body):
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

    # ----- value plumbing -----
    def _get(self, d):
        return deckcfg.get_instrument_param(self.iid, d['name'], d['default'])

    def _set(self, d, value):
        deckcfg.set_instrument_param(self.iid, d['name'], value)
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:
                pass

    # ----- per-type controls -----
    def _slider(self, body, d):
        scale = d.get('scale', 1)
        cur = self._get(d)
        r = dk.row(body)
        dk.label(r, self.label_for(d), color=dk.TEXT)
        dk.slider(r, int(round(cur * scale)), int(round(d['min'] * scale)),
                  int(round(d['max'] * scale)), w=340,
                  cb=self._slider_cb(d, scale), color=dk.TEAL)

    def _slider_cb(self, d, scale):
        def cb(e):
            raw = e.get_target_obj().get_value()
            self._set(d, (raw / scale) if scale != 1 else raw)
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
        b = dk.button(r, "On" if cur else "Off", w=120, h=52,
                      bg=(dk.GREEN if cur else dk.SURFACE2))
        b.add_event_cb(self._toggle_cb(d, b), lv.EVENT.CLICKED, None)

    def _toggle_cb(self, d, btn):
        def cb(e):
            v = not self._get(d)
            self._set(d, v)
            btn.set_style_bg_color(dk.c(dk.GREEN if v else dk.SURFACE2), 0)
            btn.get_child(0).set_text("On" if v else "Off")
        return cb


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

    def _get(self, d):
        v = deckcfg.device_fx(self.device).get(d['bus'], {}).get(d['name'])
        return v if v is not None else d['default']

    def _set(self, d, value):
        deckcfg.set_device_fx(self.device, d['bus'], d['name'], value)
        if self.on_change is not None:
            try:
                self.on_change()
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
        ed = make_editor(defs)
        ed.group_headers = False
        ed.build(page)
    return tv
