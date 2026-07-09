# ui_patch.py -- runtime UI upgrades applied from boot.py.
#
# The task bar and corner launcher live in the frozen ui.py, which a firmware
# upgrade would overwrite -- so instead of editing it, we patch at runtime from
# /user (which survives tulip.upgrade()):
#   * bigger, easier-to-hit task-bar switch/quit + launcher buttons
#   * a taller launcher menu that includes Home and the deck apps
#   * the button size is read from deckcfg ('ui_btn') and editable in Settings
#
# Home is the ROOT, not the REPL:
#   * Home shows no quit/power button -- you don't close the root.
#   * Quitting any other app returns to Home (falling back to the REPL only if
#     Home somehow isn't running), instead of always dropping to the REPL.
#   * The REPL keeps running (everything depends on it) as a normal switchable
#     "Terminal" app, and gets a Home button on its task bar as a way back.
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

# The app that is the structural root of the deck. It can't be quit, and it is
# the return target when any other app is quit -- so the REPL stops being "root".
ROOT_APP = 'home'

_installed = False


def _should_hide_quit(name):
    """The root app has no quit/power button -- you don't close the root."""
    return name == ROOT_APP


def _quit_target(name, running):
    """Where should quitting the app `name` return to?

    Returns the name of the app to present, or None if `name` must not be quit.
    The REPL and the root (Home) can't be quit; every other app returns to Home,
    falling back to the REPL if Home isn't running (so nothing is ever orphaned).
    """
    if name == 'repl' or name == ROOT_APP:
        return None
    if ROOT_APP in running:
        return ROOT_APP
    return 'repl'


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
    h = getattr(screen, 'home_button', None)
    for b in (a, q, l, h):
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
    if h is not None and l is not None:
        h.align_to(l, lv.ALIGN.OUT_LEFT_MID, 0, 0)


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


# --- Home affordance on the REPL/Terminal task bar ---
def _home_cb(e):
    if e.get_code() != lv.EVENT.CLICKED:
        return
    if ui.lv_launcher is not None:
        ui.lv_launcher.delete()
        ui.lv_launcher = None
    # run() switches to Home if it's already running, or launches it otherwise.
    tulip.run(ROOT_APP)


def _ensure_home_button(screen):
    # The REPL is a normal switchable "Terminal" app now, so give it a one-tap
    # way back to Home (next to its launcher button in the bottom-right).
    if getattr(screen, 'home_button', None) is not None:
        return
    b = lv.button(screen.group)
    b.set_style_bg_color(ui.pal_to_lv(36), lv.PART.MAIN)
    b.set_style_radius(0, lv.PART.MAIN)
    lb = lv.label(b)
    lb.set_style_text_font(lv.font_montserrat_12, 0)
    lb.set_text(lv.SYMBOL.HOME)
    lb.set_style_text_align(lv.TEXT_ALIGN.CENTER, 0)
    b.align_to(screen.group, lv.ALIGN.BOTTOM_RIGHT, 0, 0)
    b.add_event_cb(_home_cb, lv.EVENT.CLICKED, None)
    screen.home_button = b


def apply():
    global _installed
    if _installed:
        return
    _installed = True
    _orig_draw = ui.UIScreen.draw_task_bar
    _orig_quit = ui.UIScreen.screen_quit_callback

    def _patched_draw(self):
        _orig_draw(self)
        # Home is the root: strip the quit/power button the firmware just drew.
        if _should_hide_quit(self.name):
            qb = getattr(self, 'quit_button', None)
            if qb is not None:
                try:
                    qb.delete()
                except Exception:
                    pass
                self.quit_button = None
        # The REPL/Terminal gets a Home button as its way back to the root.
        if self.name == 'repl':
            try:
                _ensure_home_button(self)
            except Exception:
                pass
        _size_buttons(self)

    def _patched_quit(self, e):
        # control-Q and the power button both land here. Return to Home instead
        # of the REPL; refuse to quit the REPL or the root (would orphan Home).
        target = _quit_target(self.name, ui.running_apps)
        if target is None:
            return
        if target == ROOT_APP:
            # The firmware's quit ends with `repl_screen.present()`; temporarily
            # point that module global at Home so all the cleanup still runs but
            # we land on Home. (repl_screen is looked up as a global in ui.py.)
            saved = ui.repl_screen
            try:
                ui.repl_screen = ui.running_apps[ROOT_APP]
                _orig_quit(self, e)
            finally:
                ui.repl_screen = saved
        else:
            _orig_quit(self, e)

    ui.UIScreen.draw_task_bar = _patched_draw
    ui.UIScreen.screen_quit_callback = _patched_quit
    ui.launcher = _launcher
    # refresh whatever's on screen right now
    try:
        _size_buttons(tulip.current_uiscreen())
    except Exception:
        pass
