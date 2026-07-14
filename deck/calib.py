# calib.py -- deck-native touch calibration (replaces the stock calibrate app).
#
# The stock `tulip.run('calibrate')` is a bare REPL script that blocks in an
# infinite `while` loop waiting on a raw `tulip.touch_callback`. Launched from
# the deck (an active LVGL screen), that raw callback is starved -- the per-frame
# LVGL scheduling floods the MicroPython scheduler queue, so the callback's
# schedule gets dropped, taps on the target never register, and the only way out
# is a reboot.
#
# This version instead uses the SAME LVGL touch path the rest of the deck uses:
# a full-screen button's CLICKED event + tulip.touch(). Taps register exactly
# like every other deck button, and there's always a Cancel (plus the normal task
# bar), so you can never get trapped.
#
# Touch pipeline (see gt911_touchscreen.c):
#     reported_x = raw_x + x_delta
#     reported_y = (raw_y + y_delta) * y_scale
# tulip.touch() returns the *reported* (post-delta) point; tulip.touch_delta()
# reads/sets (x_delta, y_delta, y_scale). We compute the new delta *relative to
# the current one* (the stock script ignored the current delta, which made
# re-calibration drift):
#     new_x = cur_x + mean(target_x - reported_x)
#     new_y = cur_y + mean((target_y - reported_y) / y_scale)     (scale kept)

import tulip
import deckui as dk
import deckcfg
import lvgl as lv

# Sample targets as screen fractions: center + four insets (kept off the extreme
# corners so they don't sit under the Cancel button).
_FRAC = [(0.5, 0.5), (0.13, 0.13), (0.87, 0.13), (0.87, 0.87), (0.13, 0.87)]

_st = {}


def _targets():
    w, h = tulip.screen_size()
    return [(int(w * fx), int(h * fy)) for (fx, fy) in _FRAC]


def _back():
    tulip.run('settings')


def _advance():
    i = _st['i']
    tgts = _st['targets']
    if i >= len(tgts):
        _finish()
        return
    tx, ty = tgts[i]
    _st['dot'].set_pos(tx - 22, ty - 22)
    _st['prompt'].set_text("Tap the dot   (%d / %d)" % (i + 1, len(tgts)))


def _on_tap(e):
    if e.get_code() != lv.EVENT.CLICKED:
        return
    i = _st['i']
    tgts = _st['targets']
    if i >= len(tgts):
        return
    t = tulip.touch()
    _st['samples'].append(((tgts[i][0], tgts[i][1]), (t[0], t[1])))
    _st['i'] = i + 1
    _advance()


def _finish():
    try:
        cx, cy, cs = tulip.touch_delta()
    except Exception:
        cx, cy, cs = 0, 0, 1.0
    if not cs:
        cs = 1.0
    ex = ey = 0.0
    for (tx, ty), (px, py) in _st['samples']:
        ex += (tx - px)
        ey += (ty - py) / cs
    n = len(_st['samples']) or 1
    nx = int(round(cx + ex / n))
    ny = int(round(cy + ey / n))
    _st['new'] = (nx, ny, cs)
    _show_result(nx, ny, cs, (cx, cy, cs))


def _apply(e):
    if e.get_code() != lv.EVENT.CLICKED:
        return
    nx, ny, cs = _st['new']
    try:
        tulip.touch_delta(nx, ny, cs)
    except Exception as err:
        print("calib: apply failed:", err)
    try:
        import deckcfg
        deckcfg.set_value('touch_delta', [nx, ny, cs])
    except Exception:
        pass
    _back()


def _cancel(e):
    if e.get_code() != lv.EVENT.CLICKED:
        return
    _back()


def _show_result(nx, ny, cs, old):
    # Remove every capture-phase widget so the result card stands alone.
    for k in ('capture', 'dot', 'prompt', 'cancel'):
        w = _st.get(k)
        if w is not None:
            try:
                w.delete()
            except Exception:
                pass
            _st[k] = None
    screen = _st['screen']
    dk.label(screen.group, "Calibration result", 40, 130, color=dk.WHITE,
             font=dk.FONT_L)
    dk.label(screen.group,
             "new delta  [%d, %d, %.3f]     was  [%d, %d, %.3f]"
             % (nx, ny, cs, old[0], old[1], old[2]),
             42, 184, color=dk.MUTED, font=dk.FONT_S)
    dk.button(screen.group, "Apply", w=200, h=64, bg=dk.GREEN,
              cb=_apply).set_pos(42, 240)
    dk.button(screen.group, "Cancel", w=200, h=64, bg=dk.SURFACE2,
              cb=_cancel).set_pos(262, 240)


def run(screen):
    _st.clear()
    _st['screen'] = screen
    _st['targets'] = _targets()
    _st['samples'] = []
    _st['i'] = 0

    # Start from a clean slate even if the UIScreen is being re-used (otherwise
    # widgets from a previous calibration pass pile up on top of each other).
    try:
        screen.group.clean()
    except Exception:
        pass

    screen.bg_color = dk.BG
    # Start the title right of the standalone Back button (ui_patch pins it at
    # top-left) so it isn't clipped to "libration". Matches dk.frame's offset.
    try:
        _tx = int(deckcfg.get('ui_btn', 60) * 2.4) + 24
    except Exception:
        _tx = 168
    dk.label(screen.group, "Touch calibration", _tx, 34, color=dk.WHITE,
             font=dk.FONT_L)
    _st['prompt'] = dk.label(screen.group, "", _tx + 2, 84, color=dk.MUTED,
                             font=dk.FONT_M)

    # Full-screen transparent capture button underneath everything.
    w, h = tulip.screen_size()
    cap = lv.button(screen.group)
    cap.set_size(w, h)
    cap.set_pos(0, 0)
    cap.set_style_bg_opa(lv.OPA.TRANSP, 0)
    cap.set_style_border_width(0, 0)
    cap.set_style_shadow_width(0, 0)
    cap.add_event_cb(_on_tap, lv.EVENT.CLICKED, None)
    _st['capture'] = cap

    # The target dot, drawn on top but non-clickable so taps fall through to the
    # capture button below it.
    dot = lv.obj(screen.group)
    dot.set_size(44, 44)
    dk._flat(dot, radius=22, bg=dk.PURPLE)
    dot.remove_flag(lv.obj.FLAG.CLICKABLE)
    _st['dot'] = dot

    # An always-available Cancel, on top of the capture layer. Bottom-center,
    # clear of both the firmware task bar (top-right) and all five targets.
    cancel = dk.button(screen.group, "Cancel", w=160, h=56, bg=dk.SURFACE2,
                       font=dk.FONT_S, cb=_cancel)
    cancel.set_pos(w // 2 - 80, h - 72)
    _st['cancel'] = cancel

    _advance()
    screen.present()
