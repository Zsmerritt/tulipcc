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
# Phase 1 navigation model (deck/PLAN-rework.md) -- every page has a Back, and
# Back frees resources like the old quit did:
#   * Standalone config/utility apps (settings, files, voices, wordpad, worldui,
#     editor, keyboard, ...): the shuffle/app-switcher button is removed and the
#     quit/power button becomes a labeled "< Back" (its action stays
#     screen_quit_callback = free + return Home). Back = quit = free + Home.
#   * Keep-alive apps (KEEP_ALIVE, e.g. the drum sequencer): keep BOTH a Back and
#     a Power. Power quits (stops the beat + frees). Back consults a per-app
#     "busy" probe: if busy, present Home but leave the app running (keeps
#     playing in the background); if idle, quit it. The probes live here so the
#     firmware apps stay untouched (survives tulip.upgrade()).
#
# Call ui_patch.apply() once at boot; call ui_patch.set_scale(px) to resize live.
#
# COUPLING NOTE: this module reaches into frozen ui.py internals (UIScreen
# .draw_task_bar / .screen_quit_callback / the module-global repl_screen &
# lv_launcher). Written against the ui.py shipped in this repo's firmware
# (tulip/shared/py/ui.py on the deck-ui branch, July 2026). After a
# tulip.upgrade() to a newer firmware, re-check those attribute names first if
# task-bar buttons or quit-to-Home misbehave.

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

# Sound-producing apps that may keep running in the background after Back. They
# get both a Back and a Power button; Back keeps them alive only while "busy".
KEEP_ALIVE = {'drums'}

# Per-app "busy" probes (kept here so the firmware apps stay untouched). Each
# takes the app's UIScreen and returns truthy iff it is doing something worth
# keeping alive. drums is busy iff its sequencer has any events. Guards missing
# attributes so a firmware change can't crash the task bar.
_BUSY_PROBE = {
    'drums': lambda scr: bool(getattr(getattr(scr, 'drum_seq', None),
                                      'events', None)),
}

# Back buttons use the deck accent blue (matching the in-shell panel Back) so a
# Back reads differently from the red Power/quit button.
try:
    _BACK_BG = tulip.color(64, 132, 224)
except Exception:
    _BACK_BG = 36

_installed = False


def _log(msg):
    # persistent breadcrumb for the "drops back to Home" glitch (decklog.py)
    try:
        import decklog
        decklog.log(msg)
    except Exception:
        pass


def _sym(name, fallback):
    """An lv.SYMBOL glyph if this build has it, else an ASCII fallback."""
    return getattr(lv.SYMBOL, name, fallback) if hasattr(lv, 'SYMBOL') else fallback


def _should_hide_quit(name):
    """The root app has no quit/power button -- you don't close the root.
    Welcome (first-boot onboarding) has nowhere to go Back to either: its
    only exits are its own step cards / Get started (X-9)."""
    return name == ROOT_APP or name == 'welcome'


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
    wtext = int(px * 2.4)       # wider, for the labeled "< Back" buttons
    f = _font_for(px)
    a = getattr(screen, 'alttab_button', None)
    q = getattr(screen, 'quit_button', None)
    l = getattr(screen, 'launcher_button', None)
    h = getattr(screen, 'home_button', None)
    bk = getattr(screen, 'back_button', None)
    quit_is_back = getattr(screen, '_quit_is_back', False)
    for b, bw in ((a, w), (q, wtext if quit_is_back else w),
                  (l, w), (h, w), (bk, wtext)):
        if b is not None:
            b.set_width(bw)
            b.set_height(px)
            lb = b.get_child(0)
            if lb is not None:
                lb.set_style_text_font(f, 0)
                lb.center()
    # Back ALWAYS lives in the TOP-LEFT corner -- matching the in-shell panel
    # Back (homeshell) -- so Back never flips corners between the shell and
    # standalone apps (audit: "Back flips corners"). The Back is either a
    # dedicated back_button (keep-alive apps) or the quit button repurposed as
    # Back (quit_is_back).
    back_btn = bk if bk is not None else (q if quit_is_back else None)
    if back_btn is not None:
        back_btn.align_to(screen.group, lv.ALIGN.TOP_LEFT, 0, 0)
    # top-right cluster: shuffle (if any), then quit/power -- but only when the
    # quit button is a real Power button, not the repurposed Back.
    anchor = None
    if a is not None:
        a.align_to(screen.group, lv.ALIGN.TOP_RIGHT, 0, 0)
        anchor = a
    if q is not None and not quit_is_back:
        if anchor is not None:
            q.align_to(anchor, lv.ALIGN.OUT_LEFT_MID, 0, 0)
        else:
            q.align_to(screen.group, lv.ALIGN.TOP_RIGHT, 0, 0)
        anchor = q
    # bottom-right: launcher, then Home to its left
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


# --- Phase 1: standalone-app task bar (Back = free + Home; keep-alive gets both) ---
def _set_btn_label(btn, text):
    try:
        lb = btn.get_child(0)
        if lb is not None:
            lb.set_text(text)
    except Exception:
        pass


def _set_btn_bg(btn, pal):
    try:
        btn.set_style_bg_color(ui.pal_to_lv(pal), lv.PART.MAIN)
    except Exception:
        pass


def _go_home():
    if ui.lv_launcher is not None:
        try:
            ui.lv_launcher.delete()
        except Exception:
            pass
        ui.lv_launcher = None
    # tulip.run presents Home if it's already running (leaving the current app in
    # running_apps -- i.e. still alive), or launches it otherwise.
    tulip.run(ROOT_APP)


def _back_keeps_alive(name, screen):
    """True iff Back on a keep-alive app should keep it running (it's busy)."""
    probe = _BUSY_PROBE.get(name)
    if probe is None:
        return False
    try:
        return bool(probe(screen))
    except Exception:
        return False


def _make_back_cb(screen):
    def _cb(e):
        if e.get_code() != lv.EVENT.CLICKED:
            return
        _log("Back tapped in app '%s'" % getattr(screen, 'name', '?'))
        if _back_keeps_alive(screen.name, screen):
            _go_home()                       # busy: leave it playing, show Home
        else:
            screen.screen_quit_callback(e)   # idle: quit == free + Home
    return _cb


def _ensure_back_button(screen):
    if getattr(screen, 'back_button', None) is not None:
        return
    b = lv.button(screen.group)
    b.set_style_bg_color(ui.pal_to_lv(_BACK_BG), lv.PART.MAIN)
    b.set_style_radius(12, lv.PART.MAIN)   # match the shell's rounded Back
    lb = lv.label(b)
    lb.set_style_text_font(lv.font_montserrat_12, 0)
    lb.set_text("%s Back" % _sym('LEFT', "<"))
    lb.set_style_text_align(lv.TEXT_ALIGN.CENTER, 0)
    b.align_to(screen.group, lv.ALIGN.TOP_RIGHT, 0, 0)
    b.add_event_cb(_make_back_cb(screen), lv.EVENT.CLICKED, None)
    screen.back_button = b


def _apply_standalone_taskbar(screen):
    # Remove the shuffle/app-switcher -- Back is the only nav these apps need.
    a = getattr(screen, 'alttab_button', None)
    if a is not None:
        try:
            a.delete()
        except Exception:
            pass
        screen.alttab_button = None
    if screen.name in KEEP_ALIVE:
        # Keep the Power button (quit) and add a separate Back button.
        _set_btn_label(getattr(screen, 'quit_button', None), _sym('POWER', "Off"))
        screen._quit_is_back = False
        try:
            _ensure_back_button(screen)
        except Exception:
            pass
    else:
        # Repurpose the quit/power button as Back (action stays quit = free+Home).
        qb = getattr(screen, 'quit_button', None)
        _set_btn_label(qb, "%s Back" % _sym('LEFT', "<"))
        _set_btn_bg(qb, _BACK_BG)
        try:
            qb.set_style_radius(12, lv.PART.MAIN)   # match the shell's Back
        except Exception:
            pass
        screen._quit_is_back = True


def _install_keyboard_partial():
    """Restyle the soft keyboard the moment it opens. It used to also FORCE
    PARTIAL render mode while the keyboard was up (tear-free but sluggish)
    and unconditionally dropped to DIRECT on close -- stomping Settings >
    Smooth UI for users who run partial full-time. The deck's repaint fixes
    have cut flashing enough that the render mode now follows the Settings
    switch alone; the keyboard no longer touches it in either direction.

    Also filters the frozen ui.py key callback: with a TEXTAREA attached
    (the deck's mode) LVGL's own keyboard handler already inserts the
    character, but ui.py's callback STILL tulip.key_send()s it -- phantom
    keystrokes into the REPL/console under the UI on every press. Only the
    close (keyboard-symbol) key keeps its original behavior."""
    try:
        import ui
        _orig_kb = ui.keyboard
        _orig_cb = ui.lv_soft_kb_cb

        def _filtered_cb(e):
            kb = e.get_target_obj()
            try:
                btn = kb.get_selected_button()
                text = kb.get_button_text(btn)
            except Exception:
                return _orig_cb(e)
            if text and text[0] == lv.SYMBOL.KEYBOARD:
                return _orig_cb(e)          # close key: original teardown
            try:
                ta = kb.get_textarea()
            except Exception:
                ta = None
            if ta is not None:
                return                      # LVGL already typed into the ta
            return _orig_cb(e)              # legacy path (REPL screens)
        ui.lv_soft_kb_cb = _filtered_cb     # ui.keyboard() resolves by name

        def _kb():
            was_up = getattr(ui, 'lv_soft_kb', None) is not None
            _orig_kb()
            now_up = getattr(ui, 'lv_soft_kb', None) is not None
            try:
                if now_up and not was_up:
                    # same-tick restyle: it's ~7 style calls on one button
                    # matrix (cheap); deferring it flashed the theme-black
                    # keyboard for a beat before the deck palette landed
                    import deckui
                    deckui.style_keyboard()
            except Exception:
                pass
        ui.keyboard = _kb
        tulip.keyboard = _kb     # the deck calls tulip.keyboard() (same object)
    except Exception:
        pass


def _install_safe_midi_drain():
    """Re-register tulip's MIDI drain with a FAULT-ISOLATED version.

    The frozen midi.c_fired_midi_event aborts its whole drain loop if ANY
    registered callback raises -- every message still in the C queue then
    strands until the NEXT MIDI event schedules a new drain. On a chord that
    reads as 'I pressed a key and had to press it again to register'. Each
    callback is now isolated per message (first failure per callback is
    logged), so the queue always drains."""
    try:
        import midi as _midi
        _logged = []

        def _drain(is_sysex):
            if is_sysex:
                if _midi.sysex_callback is not None:
                    try:
                        _midi.sysex_callback(tulip.sysex_in())
                    except Exception:
                        pass
            # Read the WHOLE backlog first, COALESCING the high-rate streams:
            # controllers emit pressure/bend/CC continuously while keys are
            # held (visible in the MIDI monitor), and at Python per-message
            # cost a chord's note-ons queued behind dozens of stale pressure
            # values -- the reported 300ms 'router catching up' lag. Only the
            # LATEST pressure/bend per channel (and CC per controller) in a
            # backlog survives; notes always dispatch, in order.
            batch = []
            latest = {}
            m = tulip.midi_in()
            while m is not None and len(m) > 0:
                st = m[0] & 0xF0
                if st == 0xD0 or st == 0xE0:
                    key = m[0]                      # status+channel
                elif st == 0xA0 and len(m) > 1:
                    key = (m[0], m[1])              # polytouch: per note
                elif st == 0xB0 and len(m) > 1:
                    key = (m[0], m[1])              # CC: per controller
                else:
                    key = None                      # notes etc: never coalesce
                if key is not None:
                    i = latest.get(key)
                    if i is not None:
                        batch[i] = m                # supersede in place
                        m = tulip.midi_in()
                        continue
                    latest[key] = len(batch)
                batch.append(m)
                m = tulip.midi_in()
            for msg in batch:
                for c in tuple(_midi.MIDI_CALLBACKS):
                    try:
                        c(msg)
                    except Exception as e:
                        if id(c) not in _logged:
                            _logged.append(id(c))
                            _log("midi callback raised: %r" % e)
        tulip.midi_callback(_drain)
    except Exception:
        pass


def apply():
    global _installed
    if _installed:
        return
    _installed = True
    _install_keyboard_partial()
    _install_safe_midi_drain()
    _orig_draw = ui.UIScreen.draw_task_bar
    _orig_quit = ui.UIScreen.screen_quit_callback

    def _patched_draw(self):
        _orig_draw(self)
        if _should_hide_quit(self.name):
            # Home is the root and owns its own top-bar nav (homeshell.py): strip
            # every firmware task-bar button the firmware just drew.
            for attr in ('quit_button', 'alttab_button', 'launcher_button'):
                b = getattr(self, attr, None)
                if b is not None:
                    try:
                        b.delete()
                    except Exception:
                        pass
                    setattr(self, attr, None)
        elif self.name == 'repl':
            # The REPL/Terminal gets a Home button as its way back to the root.
            try:
                _ensure_home_button(self)
            except Exception:
                pass
        else:
            # Every other standalone app: shuffle removed, quit becomes Back
            # (keep-alive apps also get a Power button).
            try:
                _apply_standalone_taskbar(self)
            except Exception:
                pass
        _size_buttons(self)

    def _patched_quit(self, e):
        # control-Q and the power button both land here. Return to Home instead
        # of the REPL; refuse to quit the REPL or the root (would orphan Home).
        _log("screen_quit_callback for app '%s'" % getattr(self, 'name', '?'))
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
