# deckui.py -- a small shared design system for Tulip's built-in apps.
#
# Gives every app the same dark, rounded, touch-friendly look with a few
# helpers built on raw LVGL (so we get cards, scrolling lists and precise
# layout that the plain UIX widgets don't offer). Import it from an app:
#
#   import deckui as dk
#   def run(screen):
#       dk.frame(screen, "Settings", "tap to change")
#       body = dk.scroll_body(screen)
#       card = dk.row(body); dk.label(card, "Volume", 0, 0)
#       screen.present()

import tulip
import ui
import lvgl as lv

# --- palette (RGB332 palette indexes via tulip.color) ---
# NOTE: RGB332 keeps 3 bits R, 3 bits G, only 2 bits B (blue in steps of 64),
# so values sit on that grid -- a low blue like 56 rounds to 0 (olive).
BG       = tulip.color(32, 32, 64)     # very dark blue-charcoal background
SURFACE  = tulip.color(64, 64, 96)     # card / row background (dark gray)
SURFACE2 = tulip.color(96, 96, 128)    # lighter surface / inputs / dividers
TEXT     = tulip.color(230, 230, 230)  # primary text
MUTED    = tulip.color(140, 140, 168)  # secondary text
WHITE    = tulip.color(255, 255, 255)

ACCENT   = tulip.color(64, 132, 224)   # blue
GREEN    = tulip.color(48, 172, 112)
ORANGE   = tulip.color(228, 132, 44)
PURPLE   = tulip.color(160, 96, 200)
RED      = tulip.color(214, 72, 72)
GRAY     = tulip.color(92, 94, 112)
TEAL     = tulip.color(64, 168, 184)


def _pick(*names):
    for n in names:
        f = getattr(lv, n, None)
        if f is not None:
            return f
    return lv.font_montserrat_12

# Only montserrat 12/18/24 are compiled into the firmware.
FONT_S = _pick('font_montserrat_12')
FONT_M = _pick('font_montserrat_18', 'font_montserrat_12')
FONT_L = _pick('font_montserrat_24', 'font_montserrat_18')


def c(pal):
    # tulip palette index -> lv color
    return ui.pal_to_lv(pal)


def _flat(o, pad=0, radius=0, bg=None, scroll=False):
    o.set_style_border_width(0, 0)
    o.set_style_pad_all(pad, 0)
    o.set_style_radius(radius, 0)
    if bg is not None:
        o.set_style_bg_color(c(bg), 0)
        o.set_style_bg_opa(lv.OPA.COVER, 0)
    if not scroll:
        o.remove_flag(lv.obj.FLAG.SCROLLABLE)
    return o


def label(parent, text, x=None, y=None, color=TEXT, font=FONT_M, w=None,
          align=lv.TEXT_ALIGN.LEFT):
    l = lv.label(parent)
    l.set_text(text)
    l.set_style_text_color(c(color), 0)
    l.set_style_text_font(font, 0)
    l.set_style_text_align(align, 0)
    if w is not None:
        l.set_width(w)
    if x is not None and y is not None:
        l.set_pos(x, y)
    return l


def frame(screen, title, subtitle=None):
    # Dark background + a left-aligned title and thin divider. Keeps clear of
    # the task-bar buttons in the top-right corner.
    screen.bg_color = BG
    label(screen.group, title, 24, 30, color=WHITE, font=FONT_L)
    if subtitle:
        label(screen.group, subtitle, 26, 74, color=MUTED, font=FONT_S)
    d = lv.obj(screen.group)
    d.set_size(tulip.screen_size()[0] - 48, 2)
    d.set_pos(24, 102)
    _flat(d, bg=SURFACE2)


def scroll_body(screen, top=118, gap=12):
    # A vertical, scrollable flex column that fills the area below the header.
    w, h = tulip.screen_size()
    body = lv.obj(screen.group)
    body.set_size(w - 48, h - top - 16)
    body.set_pos(24, top)
    _flat(body, bg=BG, scroll=True)
    body.set_flex_flow(lv.FLEX_FLOW.COLUMN)
    body.set_style_pad_row(gap, 0)
    body.set_scroll_dir(lv.DIR.VER)
    return body


def scroll_col(parent, w, h, gap=12):
    # A vertical, scrollable flex column of the given size, parented anywhere
    # (unlike scroll_body which assumes a screen + header). Used by the in-shell
    # panels, which fill a shell-supplied container rather than a full screen.
    body = lv.obj(parent)
    _flat(body, bg=BG, scroll=True)
    body.set_size(w, h)
    body.set_flex_flow(lv.FLEX_FLOW.COLUMN)
    body.set_style_pad_row(gap, 0)
    body.set_scroll_dir(lv.DIR.VER)
    return body


def row(parent, h=76, bg=SURFACE):
    # A full-width rounded card that lays its children out left-to-right,
    # pushing the first to the left edge and the last to the right edge.
    r = lv.obj(parent)
    r.set_width(lv.pct(100))
    r.set_height(h)
    _flat(r, radius=16, bg=bg)
    r.set_style_pad_hor(20, 0)
    r.set_flex_flow(lv.FLEX_FLOW.ROW)
    r.set_flex_align(lv.FLEX_ALIGN.SPACE_BETWEEN, lv.FLEX_ALIGN.CENTER,
                     lv.FLEX_ALIGN.CENTER)
    return r


def button(parent, text, w=140, h=52, bg=ACCENT, fg=WHITE, font=FONT_M,
           radius=14, cb=None):
    b = lv.button(parent)
    b.set_size(w, h)
    _flat(b, radius=radius, bg=bg)
    lb = lv.label(b)
    lb.set_text(text)
    lb.set_style_text_color(c(fg), 0)
    lb.set_style_text_font(font, 0)
    lb.center()
    if cb is not None:
        b.add_event_cb(cb, lv.EVENT.CLICKED, None)
    return b


def slider(parent, value, vmin, vmax, w=340, cb=None, color=ACCENT, h=22):
    # h defaults fat (was 14 -- audit flagged the track as "thinner than a
    # finger"). The knob padding grows with the track so the touch target stays
    # comfortably larger than the visible bar.
    s = lv.slider(parent)
    s.set_width(w)
    s.set_height(h)
    s.set_style_bg_opa(lv.OPA.COVER, lv.PART.MAIN)
    s.set_style_bg_color(c(SURFACE2), lv.PART.MAIN)
    s.set_style_bg_color(c(color), lv.PART.INDICATOR)
    s.set_style_bg_color(c(WHITE), lv.PART.KNOB)
    s.set_style_pad_all(max(8, h // 2), lv.PART.KNOB)
    s.set_range(vmin, vmax)
    s.set_value(int(value), lv.ANIM.OFF)
    if cb is not None:
        s.add_event_cb(cb, lv.EVENT.VALUE_CHANGED, None)
    return s


def hgroup(parent, w, h=52, gap=8):
    # A transparent, fixed-width flex row that right-aligns its children
    # (for grouping buttons on the right side of a row).
    g = lv.obj(parent)
    g.set_size(w, h)
    g.set_style_border_width(0, 0)
    g.set_style_pad_all(0, 0)
    g.set_style_bg_opa(lv.OPA.TRANSP, 0)
    g.remove_flag(lv.obj.FLAG.SCROLLABLE)
    g.set_flex_flow(lv.FLEX_FLOW.ROW)
    g.set_style_pad_column(gap, 0)
    g.set_flex_align(lv.FLEX_ALIGN.END, lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER)
    return g


def stepper(parent, value, vmin, vmax, cb, fmt="%d", w=210):
    # A  [-]  value  [+]  control that calls cb(new_value) on each press.
    box = lv.obj(parent)
    box.set_size(w, 52)
    box.set_style_border_width(0, 0)
    box.set_style_pad_all(0, 0)
    box.set_style_bg_opa(lv.OPA.TRANSP, 0)
    box.remove_flag(lv.obj.FLAG.SCROLLABLE)
    box.set_flex_flow(lv.FLEX_FLOW.ROW)
    box.set_style_pad_column(8, 0)
    box.set_flex_align(lv.FLEX_ALIGN.END, lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER)
    st = {'v': value}
    minus = button(box, "-", w=52, h=52, bg=SURFACE2, font=FONT_L)
    lbl = lv.label(box)
    lbl.set_text(fmt % value)
    lbl.set_style_text_color(c(WHITE), 0)
    lbl.set_style_text_font(FONT_M, 0)
    lbl.set_width(w - 2 * 52 - 16)
    lbl.set_style_text_align(lv.TEXT_ALIGN.CENTER, 0)
    plus = button(box, "+", w=52, h=52, bg=SURFACE2, font=FONT_L)

    def _mk(delta):
        def h(e):
            st['v'] = min(vmax, max(vmin, st['v'] + delta))
            lbl.set_text(fmt % st['v'])
            cb(st['v'])
        return h
    minus.add_event_cb(_mk(-1), lv.EVENT.CLICKED, None)
    plus.add_event_cb(_mk(1), lv.EVENT.CLICKED, None)
    return box


def toast(screen, text, color=GREEN):
    # A short-lived status pill at the bottom of the screen.
    t = lv.obj(screen.group)
    t.set_size(lv.SIZE_CONTENT, 44)
    t.set_pos(24, tulip.screen_size()[1] - 60)
    _flat(t, radius=22, bg=color)
    t.set_style_pad_hor(22, 0)
    lb = lv.label(t)
    lb.set_text(text)
    lb.set_style_text_color(c(WHITE), 0)
    lb.set_style_text_font(FONT_M, 0)
    lb.center()

    def _kill(x):
        try:
            t.delete()
        except Exception:
            pass
    tulip.defer(_kill, 0, 1800)
