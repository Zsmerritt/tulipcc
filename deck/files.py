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


def _set_actions(on):
    # Dim AND disable Run/Edit/Delete when nothing is selected -- they looked
    # armed with no selection (UX-REVIEW-6 L5; the old lv.OPA._40 attribute
    # doesn't exist on this build, so the dim silently never applied).
    for k in ('run', 'edit', 'delbtn'):
        b = _s.get(k)
        if b is not None:
            try:
                b.set_style_opa(255 if on else 102, 0)
                if on:
                    b.remove_state(lv.STATE.DISABLED)
                else:
                    b.add_state(lv.STATE.DISABLED)
            except Exception:
                pass


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
    _set_actions(True)


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
    if name.endswith('.py'):
        import upysh
        upysh.cd(_s['path'])
        tulip.run(name[:-3])


def _edit_cb(e):
    if _s.get('sel'):
        tulip.edit(_s['sel'])


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


def run(screen):
    dk.frame(screen, "Files", "browse /user")
    _s.clear()
    _s['path'] = '/user'

    # header controls (top-right area is clear of task bar? put on left under title)
    _s['pathlbl'] = dk.label(screen.group, "/user", 300, 40, color=dk.MUTED, font=dk.FONT_S)
    _s['upbtn'] = dk.button(screen.group, lv.SYMBOL.UP + " Up", w=110, h=44,
        bg=dk.SURFACE2, font=dk.FONT_S, cb=_up_cb)
    _s['upbtn'].set_pos(470, 30)

    _s['body'] = dk.scroll_body(screen, top=118)
    # leave room for the action bar
    _s['body'].set_height(tulip.screen_size()[1] - 118 - 88)

    # action bar
    bar = lv.obj(screen.group)
    bar.set_size(tulip.screen_size()[0] - 48, 64)
    bar.set_pos(24, tulip.screen_size()[1] - 76)
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
    screen.present()
