# home.py -- the Tulip home screen / launcher.
#
# A full-screen grid of large touch tiles for the built-in apps, plus any
# runnable apps you drop in /user. Boots as the main screen (see boot.py);
# the Terminal tile (or control-Tab / the shuffle button) switches to the REPL.

import tulip
import deckui as dk
import lvgl as lv


def _terminal():
    tulip.app('repl')


def _reset():
    if tulip.board() != "DESKTOP":
        import machine
        machine.reset()


# (label, kind, target, color)
#   kind 'run'  -> tulip.run(target)
#   kind 'call' -> target()
_BUILTIN = [
    ("Instrument",   "run",  "instrument",   dk.ACCENT),
    ("MPE",          "run",  "mpe",          dk.PURPLE),
    ("Drums",        "run",  "drums",        dk.ACCENT),
    ("Voices",       "run",  "voices",       dk.TEAL),
    ("Files",        "run",  "files",        dk.GREEN),
    ("Settings",     "run",  "settings",     dk.GREEN),
    ("Editor",       "call", tulip.edit,     dk.GREEN),
    ("Wordpad",      "run",  "wordpad",      dk.GREEN),
    ("Tulip World",  "run",  "worldui",      dk.PURPLE),
    ("Keyboard",     "call", tulip.keyboard, dk.GRAY),
    ("Terminal",     "call", _terminal,      dk.GRAY),
    ("Reset",        "call", _reset,         dk.RED),
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
                    'welcome', 'deckui', 'deckcfg', 'ui_patch')
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
    else:
        target()


def _tile(parent, label, color):
    b = lv.button(parent)
    b.set_size(224, 104)
    dk._flat(b, radius=16, bg=color)
    lb = lv.label(b)
    lb.set_text(label)
    lb.set_style_text_color(dk.c(dk.WHITE), 0)
    lb.set_style_text_font(dk.FONT_M, 0)
    lb.center()
    b.add_event_cb(_cb, lv.EVENT.CLICKED, None)


def run(screen):
    _actions.clear()
    dk.frame(screen, "Tulip", "tap an app  -  Terminal switches to the REPL")

    w, h = tulip.screen_size()
    grid = lv.obj(screen.group)
    grid.set_size(w - 48, h - 118 - 12)
    grid.set_pos(24, 118)
    dk._flat(grid, bg=dk.BG, scroll=True)
    grid.set_flex_flow(lv.FLEX_FLOW.ROW_WRAP)
    grid.set_style_pad_row(16, 0)
    grid.set_style_pad_column(16, 0)
    grid.set_scroll_dir(lv.DIR.VER)

    tiles = list(_BUILTIN)
    for name, mod in _discover_user():
        tiles.append((name, "run", mod, dk.TEAL))

    for label, kind, target, color in tiles:
        _actions[label] = (kind, target)
        _tile(grid, label, color)

    screen.present()
