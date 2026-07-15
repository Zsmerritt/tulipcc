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
    # The single highest-stakes action on the device -- gate it behind a confirm
    # so one stray tap can't drop a live performance session.
    if tulip.board() == "DESKTOP":
        return

    def _do():
        import machine
        machine.reset()
    dk.confirm("Reset device?", "This reboots now and drops the current session.",
               _do, yes_text="Reset")


def _open_settings(shell):
    import settings
    if sm.open_panel_action(shell.top_key(), 'settings') == 'rebuild':
        shell.rebuild_top(settings.panel, "Settings", key='settings')
    else:
        shell.push(settings.panel, "Settings", key='settings')


def _open_files(shell):
    import files
    if sm.open_panel_action(shell.top_key(), 'files') == 'rebuild':
        shell.rebuild_top(files.panel, "Files", key='files')
    else:
        shell.push(files.panel, "Files", key='files')


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
        # center like the root grid -- tiles clustered top-left left ~70% of
        # the panel dead (UX-REVIEW-6 L4)
        try:
            parent.set_flex_align(lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER,
                                  lv.FLEX_ALIGN.CENTER)
        except Exception:
            pass
        for label, kind, target, color in items:
            _sub_tile(parent, shell, label, kind, target, color)
    return build


def _open_submenu(shell, title, key, items):
    builder = _submenu_builder(items)
    if sm.open_panel_action(shell.top_key(), key) == 'rebuild':
        shell.rebuild_top(builder, title, key=key)
    else:
        shell.push(builder, title, key=key)


_APPS = [
    ("Editor",      "call", tulip.edit,     dk.GREEN),
    ("Wordpad",     "run",  "wordpad",      dk.GREEN),
    ("Tulip World", "run",  "worldui",      dk.PURPLE),
    ("Keyboard",    "call", tulip.keyboard, dk.GRAY),
    ("Drums (legacy)", "run", "drums", dk.GRAY),  # drums are now instruments
    ("Voices (legacy)", "run", "voices",     dk.GRAY),
]


def _open_system(shell):
    # System is the catch-all: Files + Apps (nested) + device config. Built inline
    # so it can reference _open_apps (defined below) at call time.
    # Settings/Files open as SHELL PANELS now (S3): same Back/breadcrumb as
    # everything else, and a crash shows the shell's panel-error label instead
    # of silently bouncing (the way Settings died in UX-REVIEW-6 C1).
    items = [
        ("Files",    "panel", _open_files,    dk.GREEN),
        ("Apps",     "panel", _open_apps,     dk.PURPLE),
        ("Settings", "panel", _open_settings, dk.GREEN),
        ("Terminal", "call",  _terminal,      dk.GRAY),
        ("Reset",    "call",  _reset,         dk.RED),
    ]
    _open_submenu(shell, "System", 'system', items)


def _open_apps(shell):
    items = list(_APPS)
    for name, mod in _discover_user():
        items.append((name, "run", mod, dk.TEAL))
    _open_submenu(shell, "Apps", 'apps', items)


def _discover_user():
    import os
    apps = []
    known = {'Instruments', 'Devices', 'System'}
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


def _root_footer(shell):
    """The bottom entries of the rack-as-home root: Devices + System, side by
    side. (Devices is also always one chip-tap away in the top bar.)"""
    def footer(body):
        r = lv.obj(body)
        r.set_width(lv.pct(100))
        r.set_height(64)
        r.set_style_border_width(0, 0)
        r.set_style_pad_all(0, 0)
        r.set_style_bg_opa(lv.OPA.TRANSP, 0)
        r.remove_flag(lv.obj.FLAG.SCROLLABLE)
        r.set_flex_flow(lv.FLEX_FLOW.ROW)
        r.set_style_pad_column(12, 0)
        r.set_flex_align(lv.FLEX_ALIGN.SPACE_BETWEEN, lv.FLEX_ALIGN.CENTER,
                         lv.FLEX_ALIGN.CENTER)
        for label, opener, color in (("Devices", _open_devices, dk.TEAL),
                                     ("System", _open_system, dk.GRAY)):
            b = dk.button(r, label, w=470, h=60, bg=color, font=dk.FONT_M)
            b.add_event_cb((lambda op: (lambda e: (op(shell)
                            if e.get_code() == lv.EVENT.CLICKED else None)))(opener),
                           lv.EVENT.CLICKED, None)
    return footer


def _build_root(parent, shell):
    # S1: Home IS the rack. The screen a performer glances at (instruments,
    # their sounds, enable switches) is the root, not a tile grid one level up
    # -- patch changes drop from four taps to three, and the root shows state
    # instead of navigation. Devices/System live in a footer row (and the
    # top-bar chips still open Devices).
    import rack
    rack.panel(parent, shell, footer=_root_footer(shell))


def run(screen):
    global _shell
    _shell = homeshell.HomeShell(screen, root_title="Instruments")
    _shell.on_chip = _open_devices_chip
    # key='rack' so rack's own "am I on top?" checks (_do_remove's rebuild,
    # open_panel_action) treat the root as the rack panel it is.
    _shell.push(_build_root, "Instruments", key='rack')
    screen.present()
    return _shell
