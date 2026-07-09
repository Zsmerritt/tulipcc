# ui_patch.py -- runtime UI upgrades applied from boot.py.
#
# The task bar and corner launcher live in the frozen ui.py, which a firmware
# upgrade would overwrite -- so instead of editing it, we patch at runtime from
# /user (which survives tulip.upgrade()):
#   * bigger, easier-to-hit task-bar switch/quit + launcher buttons
#   * a taller launcher menu that includes Home and the deck apps
#   * the button size is read from deckcfg ('ui_btn') and editable in Settings
#
# Call ui_patch.apply() once at boot; call ui_patch.set_scale(px) to resize live.

import ui
import tulip
import lvgl as lv

try:
    import deckcfg
    _BTN = deckcfg.get('ui_btn', 60)
except Exception:
    _BTN = 60

_installed = False


def _font_for(px):
    f = getattr(lv, 'font_montserrat_24', None)
    if px >= 52 and f is not None:
        return f
    return getattr(lv, 'font_montserrat_18', lv.font_montserrat_12)


def _size_buttons(screen):
    px = _BTN
    w = int(px * 1.4)
    f = _font_for(px)
    a = getattr(screen, 'alttab_button', None)
    q = getattr(screen, 'quit_button', None)
    l = getattr(screen, 'launcher_button', None)
    for b in (a, q, l):
        if b is not None:
            b.set_width(w)
            b.set_height(px)
            lb = b.get_child(0)
            if lb is not None:
                lb.set_style_text_font(f, 0)
                lb.center()
    if a is not None:
        a.align_to(screen.group, lv.ALIGN.TOP_RIGHT, 0, 0)
    if q is not None and a is not None:
        q.align_to(a, lv.ALIGN.OUT_LEFT_MID, 0, 0)
    if l is not None:
        l.align_to(screen.group, lv.ALIGN.BOTTOM_RIGHT, 0, 0)


def set_scale(px):
    global _BTN
    _BTN = px
    try:
        _size_buttons(tulip.current_uiscreen())
    except Exception:
        pass


# --- bigger launcher menu, with Home + the deck apps ---
_MENU = [
    (lv.SYMBOL.CLOSE,     "Close"),
    (lv.SYMBOL.HOME,      "Home"),
    (lv.SYMBOL.AUDIO,     "Instrument"),
    (lv.SYMBOL.SETTINGS,  "MPE"),
    (lv.SYMBOL.LIST,      "Fleet"),
    (lv.SYMBOL.SETTINGS,  "Settings"),
    (lv.SYMBOL.DIRECTORY, "Files"),
    (lv.SYMBOL.AUDIO,     "Voices"),
    (lv.SYMBOL.AUDIO,     "Juno-6"),
    (lv.SYMBOL.NEXT,      "Drums"),
    (lv.SYMBOL.FILE,      "Editor"),
    (lv.SYMBOL.KEYBOARD,  "Keyboard"),
    (lv.SYMBOL.FILE,      "Wordpad"),
    (lv.SYMBOL.LIST,      "Tulip World"),
    (lv.SYMBOL.POWER,     "Reset"),
]

_RUN = {"Home": "home", "Instrument": "instrument", "MPE": "mpe",
        "Fleet": "fleet",
        "Settings": "settings", "Files": "files", "Voices": "voices",
        "Juno-6": "juno6", "Drums": "drums", "Wordpad": "wordpad",
        "Tulip World": "worldui"}


def _launcher_cb(e):
    if e.get_code() != lv.EVENT.CLICKED:
        return
    text = e.get_target_obj().get_child(1).get_text()
    if ui.lv_launcher is not None:
        ui.lv_launcher.delete()
        ui.lv_launcher = None
    if text in _RUN:
        tulip.run(_RUN[text])
    elif text == "Editor":
        tulip.edit()
    elif text == "Keyboard":
        ui.keyboard()
    elif text == "Reset":
        if tulip.board() != "DESKTOP":
            import machine
            machine.reset()


def _launcher(ignore=True):
    if ui.lv_launcher is not None:
        ui.lv_launcher.delete()
        ui.lv_launcher = None
        return
    lst = lv.list(ui.repl_screen.group)
    ui.lv_launcher = lst
    lst.set_size(320, 560)
    lst.set_align(lv.ALIGN.BOTTOM_RIGHT)
    lst.set_style_text_font(getattr(lv, 'font_montserrat_18', lv.font_montserrat_12), 0)
    for icon, label in _MENU:
        b = lst.add_button(icon, label)
        b.add_event_cb(_launcher_cb, lv.EVENT.CLICKED, None)


def apply():
    global _installed
    if _installed:
        return
    _installed = True
    _orig = ui.UIScreen.draw_task_bar

    def _patched(self):
        _orig(self)
        _size_buttons(self)
    ui.UIScreen.draw_task_bar = _patched
    ui.launcher = _launcher
    # refresh whatever's on screen right now
    try:
        _size_buttons(tulip.current_uiscreen())
    except Exception:
        pass
