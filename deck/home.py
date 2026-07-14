# home.py -- the Tulip home screen / launcher.
#
# A top-bar navigation shell (homeshell.py) over a grid of large touch tiles for
# the built-in apps, plus any runnable apps you drop in /user. Boots as the main
# screen (see boot.py); the top bar carries per-instance instrument chips, and
# the Terminal tile (or control-Tab) switches to the REPL. The firmware task bar
# is stripped on Home by ui_patch.py, so this shell is the sole navigation.

import tulip
import deckui as dk
import homeshell
import shellmodel as sm
import lvgl as lv

# The live shell, set by run(); chip taps and 'panel' tiles open panels on it.
_shell = None


def _terminal():
    tulip.app('repl')


def _reset():
    if tulip.board() != "DESKTOP":
        import machine
        machine.reset()


def _open_rack(shell):
    # The Instruments rack (list of all instruments; tap a row to edit).
    import rack
    if sm.open_panel_action(shell.top_key(), 'rack') == 'rebuild':
        shell.rebuild_top(rack.panel, "Instruments", key='rack')
    else:
        shell.push(rack.panel, "Instruments", key='rack')


def _open_devices(shell):
    import devices
    if sm.open_panel_action(shell.top_key(), 'devices') == 'rebuild':
        shell.rebuild_top(devices.panel, "Devices", key='devices')
    else:
        shell.push(devices.panel, "Devices", key='devices')


def _open_devices_chip(shell, device):
    # A top-bar device-chip tap opens the Devices panel.
    _open_devices(shell)


# --- submenus (a tile that pushes a panel of items; Back returns to Home) ---
def _sub_tile(parent, shell, label, kind, target, color):
    b = lv.button(parent)
    b.set_size(200, 96)
    dk._flat(b, radius=16, bg=color)
    lb = lv.label(b)
    lb.set_text(label)
    lb.set_style_text_color(dk.c(dk.WHITE), 0)
    lb.set_style_text_font(dk.FONT_M, 0)
    lb.center()

    def cb(e):
        if e.get_code() != lv.EVENT.CLICKED:
            return
        if kind == 'run':
            tulip.run(target)
        elif kind == 'panel':
            if shell is not None:
                target(shell)
        else:
            target()
    b.add_event_cb(cb, lv.EVENT.CLICKED, None)


def _submenu_builder(items):
    def build(parent, shell):
        parent.set_flex_flow(lv.FLEX_FLOW.ROW_WRAP)
        parent.set_style_pad_all(20, 0)
        parent.set_style_pad_row(16, 0)
        parent.set_style_pad_column(16, 0)
        parent.set_scroll_dir(lv.DIR.VER)
        for label, kind, target, color in items:
            _sub_tile(parent, shell, label, kind, target, color)
    return build


def _open_submenu(shell, title, key, items):
    builder = _submenu_builder(items)
    if sm.open_panel_action(shell.top_key(), key) == 'rebuild':
        shell.rebuild_top(builder, title, key=key)
    else:
        shell.push(builder, title, key=key)


_SYSTEM = [
    ("Settings", "run",  "settings", dk.GREEN),
    ("Terminal", "call", _terminal,  dk.GRAY),
    ("Reset",    "call", _reset,     dk.RED),
]

_APPS = [
    ("Editor",      "call", tulip.edit,     dk.GREEN),
    ("Wordpad",     "run",  "wordpad",      dk.GREEN),
    ("Tulip World", "run",  "worldui",      dk.PURPLE),
    ("Keyboard",    "call", tulip.keyboard, dk.GRAY),
    ("Voices",      "run",  "voices",       dk.TEAL),
]


def _open_system(shell):
    _open_submenu(shell, "System", 'system', list(_SYSTEM))


def _open_apps(shell):
    items = list(_APPS)
    for name, mod in _discover_user():
        items.append((name, "run", mod, dk.TEAL))
    _open_submenu(shell, "Apps", 'apps', items)


# Home tiles (~6). Two open submenus via the panel stack.
# (label, kind, target, color)
#   kind 'run'   -> tulip.run(target)
#   kind 'call'  -> target()
#   kind 'panel' -> target(_shell)  (opens an in-shell panel / submenu)
_BUILTIN = [
    ("Instruments", "panel", _open_rack,    dk.ACCENT),
    ("Devices",     "panel", _open_devices, dk.TEAL),
    ("Drums",       "run",   "drums",       dk.ACCENT),
    ("Files",       "run",   "files",       dk.GREEN),
    ("System",      "panel", _open_system,  dk.GRAY),
    ("Apps",        "panel", _open_apps,    dk.PURPLE),
]

_actions = {}


def _discover_user():
    import os
    apps = []
    known = {lbl for (lbl, _, _, _) in _BUILTIN}
    try:
        entries = sorted(os.listdir('/user'))
    except OSError:
        return apps
    deck_modules = ('boot', 'home', 'settings', 'instrument', 'mpe', 'files',
                    'welcome', 'deckui', 'deckcfg', 'ui_patch', 'fleet',
                    'forwarder', 'amyfleet', 'homeshell', 'shellmodel',
                    'navshell', 'calib', 'rack', 'devices', 'screensaver',
                    'voices', 'wordpad', 'worldui', 'drums')
    for entry in entries:
        if entry.startswith('.'):
            continue
        path = '/user/' + entry
        if tulip.is_folder(path):
            if tulip.exists(path + '/' + entry + '.py') and entry not in known:
                apps.append((entry, entry))
        elif entry.endswith('.py'):
            name = entry[:-3]
            if name in deck_modules:
                continue
            try:
                with open(path) as f:
                    if 'def run(' in f.read():
                        apps.append((name, name))
            except OSError:
                pass
    return apps


def _cb(e):
    if e.get_code() != lv.EVENT.CLICKED:
        return
    label = e.get_target_obj().get_child(0).get_text()
    act = _actions.get(label)
    if act is None:
        return
    kind, target = act
    if kind == 'run':
        tulip.run(target)
    elif kind == 'panel':
        if _shell is not None:
            target(_shell)
    else:
        target()


def _tile(parent, label, color, subtitle=None):
    b = lv.button(parent)
    b.set_size(200, 96)
    dk._flat(b, radius=16, bg=color)
    lb = lv.label(b)
    lb.set_text(label)
    lb.set_style_text_color(dk.c(dk.WHITE), 0)
    lb.set_style_text_font(dk.FONT_M, 0)
    if subtitle:
        lb.align(lv.ALIGN.TOP_LEFT, 14, 14)
        sub = lv.label(b)
        sub.set_text(subtitle)
        sub.set_style_text_color(dk.c(dk.WHITE), 0)
        sub.set_style_text_font(dk.FONT_S, 0)
        sub.align(lv.ALIGN.BOTTOM_LEFT, 14, -14)
    else:
        lb.center()
    b.add_event_cb(_cb, lv.EVENT.CLICKED, None)


def _build_root(parent, shell):
    # The root panel: a wrapping grid of app tiles. `parent` is a full-size
    # scrollable panel supplied by the shell.
    parent.set_flex_flow(lv.FLEX_FLOW.ROW_WRAP)
    parent.set_style_pad_all(20, 0)
    parent.set_style_pad_row(16, 0)
    parent.set_style_pad_column(16, 0)
    parent.set_scroll_dir(lv.DIR.VER)

    # Root tiles are the fixed six; discovered /user apps live under Apps.
    # per-tile subtitles (Devices shows the live board count)
    subs = {}
    try:
        import deckcfg
        subs['Devices'] = sm.devices_subtitle(deckcfg.device_list())
    except Exception:
        pass

    _actions.clear()
    for label, kind, target, color in _BUILTIN:
        _actions[label] = (kind, target)
        _tile(parent, label, color, subs.get(label))


def run(screen):
    global _shell
    _shell = homeshell.HomeShell(screen, root_title="Home")
    _shell.on_chip = _open_devices_chip
    _shell.push(_build_root, "Home")
    screen.present()
    return _shell
