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
PLACEHOLDER = tulip.color(178, 178, 200)  # one step lighter than MUTED --
                                          # fields sit on SURFACE2 (NEW-4)
WHITE    = tulip.color(255, 255, 255)

ACCENT   = tulip.color(64, 132, 224)   # blue
GREEN    = tulip.color(48, 172, 112)
ORANGE   = tulip.color(228, 132, 44)
PURPLE   = tulip.color(160, 96, 200)
RED      = tulip.color(214, 72, 72)
GRAY     = tulip.color(92, 94, 112)
TEAL     = tulip.color(64, 168, 184)
EDGE     = SURFACE2                    # 1px card edge (elevation, no shadows)


def edge(o):
    """A 1px lighter edge on a card -- the cheap elevation language. Real LVGL
    shadows are software blurs recomputed on every redraw (a permanent tax on
    the panel builds that already flirt with the WDT); a border is free."""
    try:
        o.set_style_border_width(1, 0)
        o.set_style_border_color(c(EDGE), 0)
    except Exception:
        pass


def pressable(b):
    """Pressed 'sink': the control nudges down and darkens while touched --
    feedback with a tiny dirty region and no animation cost."""
    try:
        b.set_style_translate_y(2, lv.STATE.PRESSED)
        b.set_style_bg_opa(200, lv.PART.MAIN | lv.STATE.PRESSED)
    except Exception:
        pass


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
# Monospace (tabular figures) for VALUES -- "440 Hz", "ch2", "50 %" -- so
# numbers don't jump around as they change. unscii_16 ships in the firmware.
FONT_MONO = _pick('font_unscii_16', 'font_montserrat_12')


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
    # Dark background + a title and thin divider. Standalone apps get a top-left
    # Back button (ui_patch), so start the header to its RIGHT -- otherwise the
    # button covers the title (e.g. "Calibration" -> "libration"). Offset is
    # derived from the configured menu-button size so it stays clear at any size.
    screen.bg_color = BG
    try:
        import deckcfg
        back_w = int(deckcfg.get('ui_btn', 60) * 2.4)
    except Exception:
        back_w = 144
    tx = back_w + 24
    label(screen.group, title, tx, 30, color=WHITE, font=FONT_L)
    if subtitle:
        label(screen.group, subtitle, tx + 2, 74, color=MUTED, font=FONT_S)
    d = lv.obj(screen.group)
    d.set_size(tulip.screen_size()[0] - 48, 2)
    d.set_pos(24, 102)
    _flat(d, bg=SURFACE2)


def style_dropdown(dd):
    """Paint an lv.dropdown (button + open list) into the deck palette. The
    default theme renders it maroon/olive, which reads as broken. Shared by the
    param editor and any other dropdown (Settings screensaver, etc.)."""
    try:
        dd.set_style_bg_opa(lv.OPA.COVER, 0)
        dd.set_style_bg_color(c(SURFACE2), 0)
        dd.set_style_text_color(c(WHITE), 0)
        dd.set_style_border_width(0, 0)
        dd.set_style_radius(10, 0)
        dd.set_style_pad_all(10, 0)
    except Exception:
        pass
    try:
        lst = dd.get_list()
        if lst is not None:
            lst.set_style_bg_color(c(SURFACE), 0)
            lst.set_style_text_color(c(WHITE), 0)
            lst.set_style_border_color(c(ACCENT), 0)
            lst.set_style_border_width(1, 0)
            lst.set_style_radius(10, 0)
            lst.set_style_bg_color(c(ACCENT), lv.PART.SELECTED | lv.STATE.CHECKED)
    except Exception:
        pass


def _scrollbar_gutter(body):
    # Reserve a right-side gutter so the scrollbar never overlaps content --
    # slider knobs at max sat exactly under it and couldn't be grabbed. The
    # scrollbar draws in the padding area, i.e. in the gutter, at the right.
    body.set_style_pad_right(18, 0)
    try:
        body.set_style_width(6, lv.PART.SCROLLBAR)
    except Exception:
        pass


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
    _scrollbar_gutter(body)
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
    _scrollbar_gutter(body)
    return body


# --- star icon (favorites) ---------------------------------------------------
# LVGL's built-in symbol font has no star, so we rasterize one 5-point star
# once into a shared ARGB8888 image (white + alpha, 2x2 supersampled edges)
# and recolor it per use. Keep both dsc and buffer referenced forever.
_STAR = {}


def star_src(size=28):
    got = _STAR.get(size)
    if got is not None:
        return got[0]
    import math
    cx = cy = (size - 1) / 2
    R = size * 0.48
    r = R * 0.42
    pts = []
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        rad = R if i % 2 == 0 else r
        pts.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))

    def inside(px, py):
        cnt = 0
        for i in range(10):
            x1, y1 = pts[i]
            x2, y2 = pts[(i + 1) % 10]
            if (y1 > py) != (y2 > py):
                if x1 + (py - y1) * (x2 - x1) / (y2 - y1) > px:
                    cnt ^= 1
        return cnt

    buf = bytearray(size * size * 4)
    idx = 0
    for y in range(size):
        for x in range(size):
            hits = (inside(x + 0.25, y + 0.25) + inside(x + 0.75, y + 0.25)
                    + inside(x + 0.25, y + 0.75) + inside(x + 0.75, y + 0.75))
            buf[idx] = buf[idx + 1] = buf[idx + 2] = 255   # B,G,R
            buf[idx + 3] = (0, 64, 128, 192, 255)[hits]    # A
            idx += 4
    dsc = lv.image_dsc_t()
    dsc.header.cf = lv.COLOR_FORMAT.ARGB8888
    dsc.header.w = size
    dsc.header.h = size
    dsc.header.stride = size * 4
    dsc.data_size = len(buf)
    dsc.data = buf
    _STAR[size] = (dsc, buf)
    return dsc


# Favorite-star colors chosen FOR RGB332: ORANGE (228,132,44) quantized to a
# muddy olive nearly isoluminant with the MUTED empty star (X-4) -- you
# couldn't tell starred from unstarred. Pure bright yellow survives the 8-bit
# palette; the empty star drops to a dark grey for real contrast.
STAR_ON = tulip.color(255, 224, 0)
STAR_OFF = tulip.color(72, 74, 92)


def star(parent, filled, size=28, on_color=STAR_ON, off_color=STAR_OFF):
    """A star lv.image; use star_set(img, filled) to flip its state."""
    img = lv.image(parent)
    img.set_src(star_src(size))
    img.set_style_image_recolor_opa(255, 0)
    img.set_style_image_recolor(c(on_color if filled else off_color), 0)
    return img


def star_set(img, filled, on_color=STAR_ON, off_color=STAR_OFF):
    img.set_style_image_recolor(c(on_color if filled else off_color), 0)


def row(parent, h=76, bg=SURFACE):
    # A full-width rounded card that lays its children out left-to-right,
    # pushing the first to the left edge and the last to the right edge.
    r = lv.obj(parent)
    r.set_width(lv.pct(100))
    r.set_height(h)
    _flat(r, radius=16, bg=bg)
    edge(r)
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
    pressable(b)
    b.set_style_bg_color(c(SURFACE2), lv.PART.MAIN | lv.STATE.DISABLED)  # NEW-3
    lb = lv.label(b)
    lb.set_text(text)
    lb.set_style_text_color(c(fg), 0)
    lb.set_style_text_font(font, 0)
    lb.center()
    if cb is not None:
        b.add_event_cb(cb, lv.EVENT.CLICKED, None)
    return b


def slider(parent, value, vmin, vmax, w=340, cb=None, color=ACCENT, h=22,
           on_release=None):
    # h defaults fat (was 14 -- audit flagged the track as "thinner than a
    # finger"). The knob padding grows with the track so the touch target stays
    # comfortably larger than the visible bar.
    #
    # cb fires on every VALUE_CHANGED (continuously during a drag) -- keep it
    # cheap: update labels, live-audition. on_release fires once when the finger
    # lifts -- that's where config writes (flash!) and synth rebuilds belong.
    s = lv.slider(parent)
    s.set_width(w)
    s.set_height(h)
    s.set_style_bg_opa(lv.OPA.COVER, lv.PART.MAIN)
    s.set_style_bg_color(c(SURFACE2), lv.PART.MAIN)
    s.set_style_bg_color(c(color), lv.PART.INDICATOR)
    s.set_style_bg_color(c(WHITE), lv.PART.KNOB)
    # knob pad h//3 (was h//2): the fatter knob poked past card corners at the
    # range ends (UX-REVIEW-7 N3); target stays ~40px with the track height
    s.set_style_pad_all(max(6, h // 3), lv.PART.KNOB)
    # flat, intentional disabled colors (not the theme's RGB332 alarm-hue
    # mix) -- AND zero the theme's DISABLED color FILTER, which otherwise
    # applies on top of these flat colors and quantizes them back to the
    # olive/black the flat colors were meant to avoid (fresh-eyes F-4)
    for part in (lv.PART.MAIN, lv.PART.INDICATOR, lv.PART.KNOB):
        try:
            s.set_style_color_filter_opa(0, part | lv.STATE.DISABLED)
        except Exception:
            pass
    s.set_style_bg_color(c(SURFACE2), lv.PART.MAIN | lv.STATE.DISABLED)
    s.set_style_bg_color(c(SURFACE2), lv.PART.INDICATOR | lv.STATE.DISABLED)
    s.set_style_bg_color(c(MUTED), lv.PART.KNOB | lv.STATE.DISABLED)
    s.set_range(vmin, vmax)
    s.set_value(int(value), lv.ANIM.OFF)
    # Grow the TOUCH area well beyond the drawn track (visuals unchanged):
    # every slider was hard to grab -- the finger has to land ON the thin
    # track/knob. ext_click_area accepts presses this many px around the
    # widget, so a near-miss still grabs the slider.
    try:
        s.set_ext_click_area(28)
    except Exception:
        pass
    if cb is not None:
        s.add_event_cb(cb, lv.EVENT.VALUE_CHANGED, None)
    if on_release is not None:
        s.add_event_cb(on_release, lv.EVENT.RELEASED, None)
    return s


# approximate line heights of the compiled montserrat fonts, for centering
_FONT_LH = {12: 14, 18: 22, 24: 28}


def text_field(parent, text='', placeholder='', w=300, h=44, font=None):
    """A tulip.UIText wrapped so the text doesn't clip (UX-REVIEW-6 H2): the
    textarea is vertically centered in its box via explicit padding (the raw
    UIText's theme padding overflows small heights, cutting the line in half),
    and the placeholder gets a legible MUTED color instead of the theme's.
    Returns the UIText; position via .group like before."""
    if font is None:
        font = FONT_S
    # Dark WELL for the field (BG, the darkest tone, + a thin edge): on the
    # old SURFACE2 fill the placeholder blended into the card behind it --
    # "impossible to read" on the patch picker. Text and placeholder both pop
    # against the well.
    t = tulip.UIText(text=text, placeholder=placeholder, w=w, h=h,
                     bg_color=BG, fg_color=WHITE, font=font)
    t.group.set_parent(parent)
    t.group.set_size(w, h)
    t.group.set_style_bg_opa(lv.OPA.TRANSP, 0)
    try:
        t.group.set_style_pad_all(0, 0)
        t.group.set_style_border_width(0, 0)
    except Exception:
        pass
    # center the single text line: split the leftover height above/below it
    lh = 14
    for f, v in _FONT_LH.items():
        if font is getattr(lv, 'font_montserrat_%d' % f, None):
            lh = v
    pad = max(2, (h - lh) // 2)
    try:
        t.ta.set_style_pad_ver(pad, 0)
        t.ta.set_style_pad_hor(12, 0)
        t.ta.align(lv.ALIGN.CENTER, 0, 0)
        t.ta.set_style_radius(10, 0)
        t.ta.set_style_border_width(1, 0)
        t.ta.set_style_border_color(c(SURFACE2), 0)
    except Exception:
        pass
    try:
        # PLACEHOLDER, not MUTED: MUTED on SURFACE2 failed arm's-length
        # legibility in search + both Wi-Fi fields (UX-REVIEW-7 NEW-4)
        t.ta.set_style_text_color(c(PLACEHOLDER), lv.PART.TEXTAREA_PLACEHOLDER)
    except Exception:
        pass
    autoshow_keyboard(t.ta)
    return t


def style_keyboard():
    """Paint the firmware soft keyboard (ui.lv_soft_kb) into the deck palette.
    It ships in the LVGL default olive/khaki (UX-REVIEW-6 L1), and autoshow put
    it on the main path (search, rename), so it must match the system."""
    try:
        import ui
        kb = getattr(ui, 'lv_soft_kb', None)
        if kb is None:
            return
        kb.set_style_bg_color(c(SURFACE), lv.PART.MAIN)
        kb.set_style_bg_opa(lv.OPA.COVER, lv.PART.MAIN)
        kb.set_style_border_width(0, lv.PART.MAIN)
        kb.set_style_bg_color(c(SURFACE2), lv.PART.ITEMS)
        kb.set_style_text_color(c(WHITE), lv.PART.ITEMS)
        kb.set_style_radius(8, lv.PART.ITEMS)
        kb.set_style_bg_color(c(ACCENT), lv.PART.ITEMS | lv.STATE.CHECKED)
        # KILL the theme's style TRANSITIONS on the key matrix: releasing a
        # key starts a fade-back animation, and every animation frame
        # invalidates the WHOLE keyboard widget -- in DIRECT mode that is
        # the visible full-keyboard flash on each keypress. With no
        # transition, a press/release invalidates just the one key.
        for part in (lv.PART.MAIN, lv.PART.ITEMS):
            for state in (0, lv.STATE.PRESSED, lv.STATE.CHECKED,
                          lv.STATE.FOCUSED, lv.STATE.FOCUS_KEY):
                try:
                    kb.set_style_transition(None, part | state)
                except Exception:
                    pass
        # pressed keys still give instant visual feedback (no anim needed)
        kb.set_style_bg_color(c(ACCENT), lv.PART.ITEMS | lv.STATE.PRESSED)
    except Exception:
        pass


def open_keyboard_for(ta):
    """Open the soft keyboard (if not already up) TARGETING `ta`.

    The frozen ui.py keyboard only emits tulip.key_send() key events, which
    reach LVGL textareas solely on screens with handle_keyboard=True -- so in
    shell panels the keyboard LOOKED fine but typed nothing. set_textarea()
    engages LVGL's own built-in keyboard handler, which inserts characters
    into the textarea directly, no key-event plumbing needed."""
    try:
        import ui
        if getattr(ui, 'lv_soft_kb', None) is None:
            tulip.keyboard()
            # style in the SAME tick: restyling a button-matrix is ~7 style
            # calls on one object -- cheap -- and a deferred restyle painted
            # the theme-black keyboard for a beat before the deck one
            style_keyboard()
        kb = getattr(ui, 'lv_soft_kb', None)
        if kb is not None:
            kb.set_textarea(ta)
    except Exception:
        pass


def toggle_keyboard_for(ta):
    """Keyboard-button behavior: close the keyboard if it's up, else open it
    targeting `ta`."""
    try:
        import ui
        if getattr(ui, 'lv_soft_kb', None) is not None:
            tulip.keyboard()          # toggle off
        else:
            open_keyboard_for(ta)
    except Exception:
        pass


def autoshow_keyboard(ta):
    """Pop the on-screen keyboard when a text field is focused (tapped). Guards
    against ui.keyboard()'s toggle by only opening it when it isn't already up,
    and always (re)targets the keyboard at this field."""
    def _cb(e):
        open_keyboard_for(ta)
    try:
        ta.add_event_cb(_cb, lv.EVENT.FOCUSED, None)
    except Exception:
        pass


def close_keyboard():
    """Hide the soft keyboard and DETACH it from its textarea. Must run
    before tearing down any screen/panel that hosts text fields: the global
    keyboard outlives them holding a raw pointer to its target textarea, and
    its close/checkmark callback poking the deleted field hard-crashed the
    whole device (LVGL use-after-free; seen live leaving Wi-Fi settings with
    the keyboard up). Safe no-op when the keyboard isn't showing."""
    try:
        import ui
        kb = getattr(ui, 'lv_soft_kb', None)
        if kb is not None:
            try:
                kb.set_textarea(None)
            except Exception:
                pass
            tulip.keyboard()          # toggle off (tears down the overlay)
    except Exception:
        pass


def switch(parent, value, on_change=None, color=GREEN):
    # A real toggle switch -- unambiguous on/off state (vs a button labelled
    # "On"/"Off", which reads as either the current state or the action).
    # on_change(new_bool) fires on toggle.
    sw = lv.switch(parent)
    sw.set_size(64, 34)
    sw.set_style_bg_color(c(SURFACE2), lv.PART.MAIN)
    sw.set_style_bg_opa(lv.OPA.COVER, lv.PART.MAIN)
    # Indicator color on BOTH the plain and CHECKED selectors (belt+braces).
    sw.set_style_bg_color(c(color), lv.PART.INDICATOR)
    sw.set_style_bg_color(c(color), lv.PART.INDICATOR | lv.STATE.CHECKED)
    sw.set_style_bg_opa(lv.OPA.COVER, lv.PART.INDICATOR | lv.STATE.CHECKED)
    sw.set_style_bg_color(c(WHITE), lv.PART.KNOB)
    # flat disabled colors (theme mix + RGB332 = alarm hues, NEW-3)
    sw.set_style_bg_color(c(SURFACE2), lv.PART.MAIN | lv.STATE.DISABLED)
    sw.set_style_bg_color(c(SURFACE2), lv.PART.INDICATOR | lv.STATE.DISABLED)
    sw.set_style_bg_color(c(MUTED), lv.PART.KNOB | lv.STATE.DISABLED)
    if value:
        # State LAST, styles first: add_state(CHECKED) starts the theme's
        # style-transition ANIMATION toward whatever the CHECKED style is at
        # that instant -- with the old order that was the theme's blue, the
        # anim finished ~200ms later painting blue over our green, and nothing
        # repainted until a much later invalidate. THE actual root cause of
        # the M3 green/blue flip-flop (UX-REVIEW-6/7; verified on-device).
        sw.add_state(lv.STATE.CHECKED)
    if on_change is not None:
        def _cb(e):
            try:
                on_change(sw.has_state(lv.STATE.CHECKED))
            except Exception:
                pass
        sw.add_event_cb(_cb, lv.EVENT.VALUE_CHANGED, None)
    return sw


def confirm(title, message, on_yes, yes_text="Delete", yes_bg=RED):
    # A modal confirmation over everything (lv.layer_top blocks the background).
    # Cancel dismisses; the yes button runs on_yes() then dismisses. Used to gate
    # destructive actions (remove instrument, etc.).
    w, h = tulip.screen_size()
    ov = lv.obj(lv.layer_top())
    ov.set_size(w, h)
    ov.set_pos(0, 0)
    ov.set_style_border_width(0, 0)
    ov.set_style_pad_all(0, 0)
    ov.set_style_bg_color(c(BG), 0)
    ov.set_style_bg_opa(200, 0)
    ov.remove_flag(lv.obj.FLAG.SCROLLABLE)

    card = lv.obj(ov)
    card.set_size(600, 280)
    card.center()
    _flat(card, radius=20, bg=SURFACE)
    card.remove_flag(lv.obj.FLAG.SCROLLABLE)
    label(card, title, 32, 30, color=WHITE, font=FONT_L)
    label(card, message, 32, 84, color=MUTED, font=FONT_M, w=536)

    def _close():
        try:
            ov.delete()
        except Exception:
            pass

    cancel = button(card, "Cancel", w=240, h=60, bg=SURFACE2, font=FONT_M)
    cancel.set_pos(32, 190)
    cancel.add_event_cb(lambda e: _close() if e.get_code() == lv.EVENT.CLICKED
                        else None, lv.EVENT.CLICKED, None)
    yes = button(card, yes_text, w=240, h=60, bg=yes_bg, font=FONT_M)
    yes.set_pos(600 - 240 - 32, 190)

    def _do(e):
        if e.get_code() != lv.EVENT.CLICKED:
            return
        _close()
        try:
            on_yes()
        except Exception:
            pass
    yes.add_event_cb(_do, lv.EVENT.CLICKED, None)
    return ov


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
