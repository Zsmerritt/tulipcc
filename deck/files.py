# files.py -- a touch file browser for Tulip.
#
# Browse /user, open folders, and Run / Edit / Delete files without dropping
# to the REPL. Delete asks for a second tap to confirm.

import os
import tulip
import deckui as dk
import lvgl as lv

_s = {}


def _fmt_size(n):
    if n < 1024:
        return "%d B" % n
    if n < 1024 * 1024:
        return "%.1f KB" % (n / 1024)
    return "%.1f MB" % (n / (1024 * 1024))


def _is_dir(path):
    try:
        return os.stat(path)[0] & 0x4000 != 0
    except OSError:
        return False


# Guard rails: the firmware editor chokes on binary / huge files (the "crashed
# when I opened certain files" report), and Run only makes sense for .py.
_TEXT_EXT = ('.py', '.txt', '.json', '.md', '.log', '.cfg', '.ini', '.csv')
_EDIT_MAX = 131072      # bytes; bigger than any deck file, smaller than trouble


def _editable(path):
    n = path.lower()
    if not any(n.endswith(x) for x in _TEXT_EXT):
        return False
    try:
        return os.stat(path)[6] <= _EDIT_MAX
    except OSError:
        return False


def _toast(msg, color=None):
    scr = _s.get('screen')
    if scr is not None:
        try:
            dk.toast(scr, msg, color if color is not None else dk.ORANGE)
        except Exception:
            pass


def _set_btn(k, on):
    b = _s.get(k)
    if b is None:
        return
    try:
        b.set_style_opa(255 if on else 102, 0)
        if on:
            b.remove_state(lv.STATE.DISABLED)
        else:
            b.add_state(lv.STATE.DISABLED)
    except Exception:
        pass


def _set_actions(on):
    # Dim AND disable Run/Edit/Delete when nothing is selected -- they looked
    # armed with no selection (UX-REVIEW-6 L5; the old lv.OPA._40 attribute
    # doesn't exist on this build, so the dim silently never applied).
    for k in ('run', 'edit', 'delbtn'):
        _set_btn(k, on)


def _update_up():
    # Show "Up" only inside a subfolder -- at the /user root there's nothing above
    # it, so only the app Back remains (removes the Back/Up ambiguity there).
    b = _s.get('upbtn')
    if b is None:
        return
    at_root = _s.get('path', '/user').rstrip('/') in ('/user', '')
    try:
        if at_root:
            b.add_flag(lv.obj.FLAG.HIDDEN)
        else:
            b.remove_flag(lv.obj.FLAG.HIDDEN)
    except Exception:
        pass


def _select(path, name, btn):
    if _s.get('selbtn') is not None:
        try:
            _s['selbtn'].set_style_bg_color(dk.c(dk.SURFACE), 0)
        except Exception:
            pass
    _s['sel'] = path
    _s['selbtn'] = btn
    _s['confirm'] = False
    btn.set_style_bg_color(dk.c(dk.ACCENT), 0)
    _s['selname'].set_text(name)
    _s['delbtn'].get_child(0).set_text("Delete")
    _s['delbtn'].set_style_bg_color(dk.c(dk.SURFACE2), 0)
    # per-file capability: Run only for .py, Edit only for small text files
    _set_btn('run', path.endswith('.py'))
    _set_btn('edit', _editable(path))
    _set_btn('delbtn', True)


def _open(path):
    _s['path'] = path
    _s['sel'] = None
    _s['selbtn'] = None
    _s['selname'].set_text("nothing selected")
    _s['pathlbl'].set_text(path)
    _set_actions(False)
    _update_up()
    _refresh()


def _refresh():
    body = _s['body']
    body.clean()
    path = _s['path']
    try:
        entries = sorted(os.listdir(path))
    except OSError:
        entries = []
    # folders first
    dirs = [e for e in entries if _is_dir(path.rstrip('/') + '/' + e)]
    files = [e for e in entries if e not in dirs]
    for name in dirs + files:
        full = path.rstrip('/') + '/' + name
        is_dir = name in dirs
        b = lv.button(body)
        b.set_width(lv.pct(100))
        b.set_height(56)
        dk._flat(b, radius=12, bg=dk.SURFACE)
        icon = lv.label(b)
        icon.set_text((lv.SYMBOL.DIRECTORY + "  ") if is_dir else (lv.SYMBOL.FILE + "  "))
        icon.set_style_text_color(dk.c(dk.ORANGE if is_dir else dk.MUTED), 0)
        icon.set_style_text_font(dk.FONT_M, 0)
        icon.align(lv.ALIGN.LEFT_MID, 6, 0)
        nm = lv.label(b)
        nm.set_text(name)
        nm.set_style_text_color(dk.c(dk.TEXT), 0)
        nm.set_style_text_font(dk.FONT_M, 0)
        nm.align(lv.ALIGN.LEFT_MID, 44, 0)
        if not is_dir:
            try:
                sz = lv.label(b)
                sz.set_text(_fmt_size(os.stat(full)[6]))
                sz.set_style_text_color(dk.c(dk.MUTED), 0)
                sz.set_style_text_font(dk.FONT_S, 0)
                sz.align(lv.ALIGN.RIGHT_MID, -12, 0)
            except OSError:
                pass
        if is_dir:
            b.add_event_cb((lambda p: (lambda e: _open(p)))(full), lv.EVENT.CLICKED, None)
        else:
            b.add_event_cb((lambda p, n, bb: (lambda e: _select(p, n, bb)))(full, name, b),
                           lv.EVENT.CLICKED, None)


def _run_cb(e):
    if not _s.get('sel'):
        return
    name = _s['sel'].rsplit('/', 1)[-1]
    if not name.endswith('.py'):
        _toast("Only .py files can run")
        return
    try:
        import upysh
        upysh.cd(_s['path'])
        tulip.run(name[:-3])
    except Exception as ex:
        _toast("Run failed: %r" % ex, dk.RED)


def _edit_cb(e):
    p = _s.get('sel')
    if not p:
        return
    if not _editable(p):
        _toast("Only small text files can be edited")
        return
    try:
        tulip.edit(p)
    except Exception as ex:
        _toast("Edit failed: %r" % ex, dk.RED)


def _delete_cb(e):
    if not _s.get('sel'):
        return
    if not _s.get('confirm'):
        _s['confirm'] = True
        _s['delbtn'].get_child(0).set_text("Confirm?")
        _s['delbtn'].set_style_bg_color(dk.c(dk.RED), 0)
        return
    try:
        os.remove(_s['sel'])
    except OSError as ex:
        print("delete failed:", ex)
    _open(_s['path'])


def _up_cb(e):
    p = _s['path'].rstrip('/')
    if '/' in p[1:]:
        _open(p.rsplit('/', 1)[0] or '/')


def _build(base, w, h, top, screen):
    """Everything below the title: path + Up, the file list, the action bar.
    `base` is either a shell panel (panel mode) or screen.group (standalone);
    (w, h) is the drawable area, `top` where content starts."""
    _s['path'] = '/user'
    _s['screen'] = screen     # toast host

    _s['pathlbl'] = dk.label(base, "/user", 24, top + 12, color=dk.MUTED,
                             font=dk.FONT_S)
    _s['upbtn'] = dk.button(base, lv.SYMBOL.UP + " Up", w=110, h=44,
        bg=dk.SURFACE2, font=dk.FONT_S, cb=_up_cb)
    _s['upbtn'].set_pos(w - 24 - 110, top)

    body = dk.scroll_col(base, w - 48, h - top - 56 - 88)
    body.set_pos(24, top + 52)
    _s['body'] = body

    # action bar
    bar = lv.obj(base)
    bar.set_size(w - 48, 64)
    bar.set_pos(24, h - 76)
    dk._flat(bar, radius=16, bg=dk.SURFACE)
    bar.set_style_pad_hor(16, 0)
    bar.set_flex_flow(lv.FLEX_FLOW.ROW)
    bar.set_flex_align(lv.FLEX_ALIGN.SPACE_BETWEEN, lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER)
    _s['selname'] = dk.label(bar, "nothing selected", color=dk.MUTED, font=dk.FONT_S)
    g = dk.hgroup(bar, w=380, h=44)
    _s['run'] = dk.button(g, "Run", w=110, h=44, bg=dk.GREEN, font=dk.FONT_S, cb=_run_cb)
    _s['edit'] = dk.button(g, "Edit", w=110, h=44, bg=dk.ACCENT, font=dk.FONT_S, cb=_edit_cb)
    _s['delbtn'] = dk.button(g, "Delete", w=120, h=44, bg=dk.SURFACE2, font=dk.FONT_S, cb=_delete_cb)

    _set_actions(False)     # nothing selected yet
    _update_up()            # hide Up at the /user root
    _refresh()


def panel(parent, shell=None):
    """Files as a shell panel (S3): shell Back/breadcrumb + the panel-error
    safety net; standalone chrome retired from the main path."""
    import homeshell
    _s.clear()
    w, H = tulip.screen_size()
    _build(parent, w, H - homeshell.BAR_H, 8,
           shell.screen if shell is not None else None)


def run(screen):
    # standalone (REPL launcher) -- same content under a frame header
    _s.clear()
    dk.frame(screen, "Files", "browse /user")
    w, H = tulip.screen_size()
    _build(screen.group, w, H, 118, screen)
    screen.present()
