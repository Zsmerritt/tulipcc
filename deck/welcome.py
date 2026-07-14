# welcome.py -- first-boot onboarding, shown once (see boot.py).
#
# A friendly landing screen instead of a bare Python prompt. Points new users
# at Wi-Fi, choosing an instrument, and the home screen. "Get started" marks
# setup done so it won't show again.

import tulip
import deckui as dk
import deckcfg
import lvgl as lv


def _big_card(parent, x, y, title, sub, color, cb):
    b = lv.button(parent)
    b.set_size(300, 150)
    b.set_pos(x, y)
    dk._flat(b, radius=18, bg=color)
    t = lv.label(b)
    t.set_text(title)
    t.set_style_text_color(dk.c(dk.WHITE), 0)
    t.set_style_text_font(dk.FONT_L, 0)
    t.align(lv.ALIGN.TOP_LEFT, 4, 6)
    s = lv.label(b)
    s.set_text(sub)
    s.set_width(270)
    s.set_style_text_color(dk.c(dk.WHITE), 0)
    s.set_style_text_font(dk.FONT_S, 0)
    s.align(lv.ALIGN.BOTTOM_LEFT, 4, -8)
    b.add_event_cb(cb, lv.EVENT.CLICKED, None)
    return b


def _done_and_run(app):
    deckcfg.set('setup_done', True)
    tulip.run(app)


def run(screen):
    screen.bg_color = dk.BG
    dk.label(screen.group, "Welcome to Tulip", 60, 60, color=dk.WHITE, font=dk.FONT_L)
    dk.label(screen.group,
        "A tiny Python computer for making music. Let's set it up -- you can",
        60, 104, color=dk.MUTED, font=dk.FONT_M)
    dk.label(screen.group,
        "always change any of this later from Settings.",
        60, 130, color=dk.MUTED, font=dk.FONT_M)

    _big_card(screen.group, 60, 190, "1. Wi-Fi",
        "Connect so you can upgrade and share.", dk.ACCENT,
        lambda e: _done_and_run('settings'))
    _big_card(screen.group, 380, 190, "2. Instrument",
        "Pick the synth your MIDI keys play.", dk.PURPLE,
        lambda e: _done_and_run('instrument'))
    _big_card(screen.group, 700, 190, "3. MPE",
        "Optional: per-note expression.", dk.TEAL,
        lambda e: _done_and_run('mpe'))

    dk.button(screen.group, "Get started  " + lv.SYMBOL.RIGHT, w=280, h=68,
        bg=dk.GREEN, font=dk.FONT_L,
        cb=lambda e: _done_and_run('home')).set_pos(60, 400)
    dk.label(screen.group, "You can reopen this with  run('welcome')",
        60, 500, color=dk.MUTED, font=dk.FONT_S)

    screen.present()
