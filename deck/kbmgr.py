# kbmgr.py -- the deck's single owner of the global soft keyboard lifecycle.
#
# The firmware ships ONE lv.keyboard (ui.lv_soft_kb, created by tulip.keyboard()).
# Panels borrow it ad hoc for text fields (Wi-Fi password, patch search, rename,
# ...). Two chronic problems drove this module:
#
#   1. CLOSE CRASHES. The keyboard outlives the panels that borrow it and keeps a
#      raw pointer to its target textarea. Tear a panel down with the keyboard up
#      and its close/checkmark callback pokes the freed textarea -- an LVGL
#      use-after-free that hard-faults the whole device (no Python traceback,
#      reset_cause=HARD; seen live leaving Wi-Fi settings). Past fixes sprinkled
#      dk.close_keyboard() through every navigation path -- per-site whack-a-mole.
#      This module is the ONE structural guard: open() hooks the textarea's own
#      DELETE event so a panel being popped auto-closes the keyboard, and close()
#      detaches the textarea binding and our callbacks BEFORE any overlay delete.
#      When the current event ORIGINATES from the keyboard (the close key deletes
#      the keyboard from inside its own event handler -- the self-deletion crash
#      class), the overlay teardown is deferred to the next LVGL tick.
#
#   2. COVERED FIELDS. The keyboard covers the bottom half of the screen, hiding
#      the field you are typing into (the Settings Wi-Fi password, notably).
#      open() scrolls the field's scrollable ancestor -- or translates the panel
#      content up when nothing scrolls -- so the field clears the keyboard's top
#      edge, and restores it on close. An optional echo strip pinned above the
#      keyboard previews the text being typed (always on for password fields,
#      masked to match the textarea's own last-char reveal).
#
# The raw firmware primitives (create/style/target/hide the overlay) still live
# in deckui (open_keyboard_for / style_keyboard); this module orchestrates them
# and owns everything ABOVE the overlay -- binding, teardown, visibility, echo.
# deckui.autoshow_keyboard / toggle_keyboard_for / close_keyboard delegate here.

import tulip
import ui
import lvgl as lv
import deckui as dk

_DOT = '•'          # the bullet LVGL's password mode masks with


# All lifecycle state in one dict (the decklog._state idiom): module-level,
# single keyboard, single binding at a time.
_s = {}


def _reset_state():
    _s.clear()
    _s.update({
        'ta': None,             # the bound textarea (lv.textarea)
        'panel': None,          # caller-supplied panel (teardown context)
        'open': False,          # is a binding live
        'password': False,      # open-time "this is a password field" hint
        'echo': None,           # echo strip container (lv.obj on layer_top)
        'echo_lbl': None,       # echo strip label
        'echo_len': 0,          # last seen text length (grow => a keystroke)
        'reveal_last': False,   # briefly show the last char (password reveal)
        'reveal_gen': 0,        # cancels a stale re-mask timer
        'in_kb_event': False,   # we are inside a keyboard event dispatch
        'kb_hooked': None,      # the kb object our callbacks are attached to
        'view_adjusted': False, # we scrolled/translated to clear the keyboard
        'scroll_obj': None,     # scrollable ancestor we scrolled...
        'scroll_dy': 0,         # ...and by how much (to reverse on close)
        'translate_obj': None,  # or the content we translated up...
        'translate_prev': 0,    # ...and its prior translate_y (to restore)
    })


_reset_state()


def _log(msg, e=None):
    """Never raise into LVGL: log to decklog and swallow."""
    try:
        import decklog
        decklog.log_exc(msg, e)
    except Exception:
        pass


# --- public API -------------------------------------------------------------

def open(ta, panel=None, echo=False, password=None):
    """Bring up the soft keyboard bound to `ta` and make `ta` visible above it.

    panel     -- the panel/container the field lives in (visibility + teardown
                 context). When None it is discovered from the textarea's
                 ancestry, so autoshow works without the call site knowing it.
    echo      -- show the preview strip for a non-password field (always shown
                 for password fields regardless).
    password  -- override the password detection (None = detect from the ta).
    """
    if ta is None:
        return
    try:
        same = _s.get('open') and (_s.get('ta') is ta)
        dk.open_keyboard_for(ta)        # raw firmware overlay, (re)targeted
        if same:
            _install_kb_callbacks()     # the overlay may have been recreated
            return
        # Switching to a different field: undo the previous field's view shift
        # and drop its echo before binding the new one.
        _restore_view()
        _detach_echo()
        pw = password
        if pw is None:
            pw = _detect_password(ta)
        _s['ta'] = ta
        _s['panel'] = panel
        _s['open'] = True
        _s['password'] = bool(pw)
        _s['echo_len'] = 0
        _s['reveal_last'] = False
        _s['view_adjusted'] = False
        _install_kb_callbacks()
        _hook_textarea(ta)
        if bool(echo) or bool(pw):
            _build_echo()
        _ensure_visible(ta)
    except Exception as e:
        _log('kbmgr.open failed', e)


def close(from_kb_event=None):
    """Close the keyboard and unbind it. Idempotent and safe when the panel is
    already gone. Detaches the textarea binding and our callbacks FIRST, then
    hides the overlay -- deferred to the next tick when the current event came
    from the keyboard itself (deleting the keyboard inside its own handler is
    the use-after-free crash class)."""
    if not _s.get('open') and _s.get('ta') is None:
        return                          # nothing bound: idempotent no-op
    if from_kb_event is None:
        from_kb_event = bool(_s.get('in_kb_event'))
    # 1. Detach binding + callbacks BEFORE any delete/hide.
    _teardown_binding(touch_kb=True)
    # 2. Hide the overlay -- now, or next tick if we are inside a kb event.
    if from_kb_event:
        try:
            tulip.defer(_hide_overlay, 0, 0)
        except Exception:
            _hide_overlay()
    else:
        _hide_overlay()


def toggle(ta, panel=None, echo=False, password=None):
    """Keyboard-button behavior: dismiss the keyboard if it is up, else open it
    bound to `ta`."""
    try:
        if getattr(ui, 'lv_soft_kb', None) is not None:
            close()
        else:
            open(ta, panel=panel, echo=echo, password=password)
    except Exception as e:
        _log('kbmgr.toggle failed', e)


def height():
    """Height of the soft keyboard if it is on screen, else 0. The single
    source of truth for keyboard-aware panel layout (was instrument._kb_height)."""
    try:
        kb = getattr(ui, 'lv_soft_kb', None)
        if kb is None:
            return 0
        try:
            kb.update_layout()
            h = kb.get_height()
            if h and h > 0:
                return h
        except Exception:
            pass
        return tulip.screen_size()[1] // 2   # LVGL keyboard default: 50%
    except Exception:
        return 0


def is_open():
    return bool(_s.get('open'))


def bound_textarea():
    return _s.get('ta')


# --- overlay teardown -------------------------------------------------------

def _hide_overlay(_x=None):
    """Tear down the firmware overlay (tulip.keyboard() toggles it off). Deleting
    the keyboard fires its DELETE event -> _on_kb_delete -> a second, idempotent
    binding teardown. Late path: guard + log, never raise into LVGL."""
    try:
        if getattr(ui, 'lv_soft_kb', None) is not None:
            tulip.keyboard()
    except Exception as e:
        _log('kbmgr overlay hide failed', e)


def _teardown_binding(touch_kb=True):
    """Detach the textarea binding, our keyboard callbacks, the echo strip and
    the view shift. Idempotent. touch_kb=False when the keyboard object is
    itself being deleted (its DELETE handler) -- we must not poke the dying
    object, only clean up the things that outlive it."""
    if touch_kb:
        try:
            kb = getattr(ui, 'lv_soft_kb', None)
            if kb is not None:
                try:
                    kb.remove_event_cb(_kb_event_cb)
                except Exception:
                    pass
                try:
                    kb.remove_event_cb(_on_kb_delete)
                except Exception:
                    pass
                try:
                    kb.set_textarea(None)   # drop the raw pointer to the field
                except Exception:
                    pass
        except Exception:
            pass
    _s['kb_hooked'] = None
    _restore_view()
    _detach_echo()
    _s['ta'] = None
    _s['panel'] = None
    _s['open'] = False
    _s['echo_len'] = 0
    _s['reveal_last'] = False


# --- keyboard + textarea callbacks ------------------------------------------

def _install_kb_callbacks():
    """Attach our VALUE_CHANGED (echo) and DELETE (state sync) callbacks to the
    live keyboard object, once per instance. The firmware recreates the object
    on every open, so re-check the identity each time."""
    try:
        kb = getattr(ui, 'lv_soft_kb', None)
        if kb is None:
            return
        if _s.get('kb_hooked') is kb:
            return
        kb.add_event_cb(_kb_event_cb, lv.EVENT.VALUE_CHANGED, None)
        kb.add_event_cb(_on_kb_delete, lv.EVENT.DELETE, None)
        _s['kb_hooked'] = kb
    except Exception:
        pass


def _kb_event_cb(e):
    """Keyboard VALUE_CHANGED: refresh the echo preview. Runs inside the
    keyboard's own event dispatch, so flag it -- a close() triggered from here
    must defer the overlay teardown."""
    _s['in_kb_event'] = True
    try:
        _refresh_echo()
    except Exception:
        pass
    _s['in_kb_event'] = False


def _on_kb_delete(e):
    """The keyboard object is being deleted (its close key, or our _hide_overlay).
    Sync our state to it WITHOUT touching the dying object."""
    try:
        _teardown_binding(touch_kb=False)
    except Exception:
        pass


def _hook_textarea(ta):
    """The structural close-crash guard: when the bound textarea is deleted (its
    panel popped), auto-close so the outliving keyboard can never poke a freed
    field."""
    try:
        ta.add_event_cb(_on_ta_delete, lv.EVENT.DELETE, None)
    except Exception:
        pass


def _on_ta_delete(e):
    close()


# --- echo strip -------------------------------------------------------------

def _detect_password(ta):
    try:
        return bool(ta.get_password_mode())
    except Exception:
        return False


def _is_password(ta):
    """Live LVGL password state wins (so the Settings eye toggle unmasks the echo
    too); fall back to the open-time hint. On a password field whose live state
    we cannot read, staying masked is the SAFE failure -- never echo a password
    in the clear."""
    try:
        return bool(ta.get_password_mode())
    except Exception:
        return bool(_s.get('password'))


def _build_echo():
    """A one-line preview pinned to the keyboard's top edge. Lives on layer_top
    (above the overlay, unclipped) and is positioned from the measured keyboard
    height; deleted on close. Styled from the deckui design system."""
    try:
        w = tulip.screen_size()[0]
        h = tulip.screen_size()[1]
        kb_h = height()
        eh = 34
        y = h - kb_h - eh
        if y < 0:
            y = 0
        cont = lv.obj(lv.layer_top())
        cont.set_size(w, eh)
        cont.set_pos(0, y)
        dk._flat(cont, bg=dk.SURFACE)
        try:
            cont.set_style_pad_hor(16, 0)
            cont.set_style_pad_ver(0, 0)
            # a thin accent line along the top edge ties it to the keyboard
            cont.set_style_border_width(2, 0)
            cont.set_style_border_color(dk.c(dk.ACCENT), 0)
            cont.set_style_border_side(lv.BORDER_SIDE.TOP, 0)
        except Exception:
            pass
        lbl = dk.label(cont, "", color=dk.WHITE, font=dk.FONT_M)
        try:
            lbl.align(lv.ALIGN.LEFT_MID, 0, 0)
        except Exception:
            pass
        _s['echo'] = cont
        _s['echo_lbl'] = lbl
        _s['echo_len'] = 0
        _update_echo()
    except Exception as e:
        _s['echo'] = None
        _s['echo_lbl'] = None
        _log('kbmgr echo build failed', e)


def _detach_echo():
    cont = _s.get('echo')
    _s['echo'] = None
    _s['echo_lbl'] = None
    if cont is not None:
        try:
            cont.delete()
        except Exception:
            pass


def _refresh_echo():
    """A keystroke landed: reveal the last char on a password field (matching
    LVGL's own last-char reveal), then re-mask shortly after. Backspace / mode
    switches (length did not grow) never reveal."""
    ta = _s.get('ta')
    if ta is None or _s.get('echo_lbl') is None:
        return
    try:
        txt = ta.get_text() or ''
    except Exception:
        txt = ''
    n = len(txt)
    prev = _s.get('echo_len', 0)
    _s['echo_len'] = n
    if _is_password(ta) and n > prev:
        _s['reveal_last'] = True
        _s['reveal_gen'] = _s.get('reveal_gen', 0) + 1
        gen = _s['reveal_gen']

        def _mask(_x):
            if _s.get('reveal_gen') == gen:
                _s['reveal_last'] = False
                _update_echo()
        try:
            tulip.defer(_mask, 0, 1200)
        except Exception:
            _s['reveal_last'] = False
    else:
        _s['reveal_last'] = False
    _update_echo()


def _update_echo():
    lbl = _s.get('echo_lbl')
    ta = _s.get('ta')
    if lbl is None or ta is None:
        return
    try:
        txt = ta.get_text() or ''
    except Exception:
        txt = ''
    if _is_password(ta):
        n = len(txt)
        if n == 0:
            shown = ''
        elif _s.get('reveal_last'):
            shown = (_DOT * (n - 1)) + txt[-1]
        else:
            shown = _DOT * n
    else:
        shown = txt
    try:
        lbl.set_text(shown)
    except Exception:
        pass


# --- focus visibility -------------------------------------------------------

def _find_scroller(o):
    """Nearest scrollable ancestor of `o`, or None."""
    try:
        cur = o
        for _i in range(12):
            try:
                cur = cur.get_parent()
            except Exception:
                return None
            if cur is None:
                return None
            try:
                if cur.has_flag(lv.obj.FLAG.SCROLLABLE):
                    return cur
            except Exception:
                pass
        return None
    except Exception:
        return None


def _top_content(o):
    """The outermost content object under the screen (translate fallback when
    nothing scrolls)."""
    try:
        cur = o
        last = o
        for _i in range(12):
            try:
                parent = cur.get_parent()
            except Exception:
                return last
            if parent is None:
                return last
            last = cur
            cur = parent
        return last
    except Exception:
        return None


def _ensure_visible(ta):
    """Scroll (or translate) so the bound field clears the keyboard's top edge.
    No-op if the field already sits above the keyboard. Records what it changed
    so _restore_view can reverse it on close."""
    try:
        h = tulip.screen_size()[1]
        kb_h = height()
        if kb_h <= 0:
            return
        margin = 12
        kb_top = h - kb_h
        area = lv.area_t()
        try:
            ta.get_coords(area)
            bottom = area.y2
        except Exception:
            return
        overlap = bottom - (kb_top - margin)
        if overlap <= 0:
            return                      # already clear of the keyboard
        scroller = _find_scroller(ta)
        if scroller is not None:
            try:
                scroller.scroll_by(0, -overlap, lv.ANIM.OFF)
                _s['scroll_obj'] = scroller
                _s['scroll_dy'] = overlap
                _s['view_adjusted'] = True
                return
            except Exception:
                pass
        top = _top_content(ta)
        if top is not None:
            try:
                cur = 0
                try:
                    cur = top.get_style_translate_y(0)
                except Exception:
                    cur = 0
                top.set_style_translate_y(cur - overlap, 0)
                _s['translate_obj'] = top
                _s['translate_prev'] = cur
                _s['view_adjusted'] = True
            except Exception:
                pass
    except Exception as e:
        _log('kbmgr ensure_visible failed', e)


def _restore_view():
    """Reverse whatever _ensure_visible changed."""
    if not _s.get('view_adjusted'):
        return
    obj = _s.get('scroll_obj')
    dy = _s.get('scroll_dy', 0)
    if obj is not None and dy:
        try:
            obj.scroll_by(0, dy, lv.ANIM.OFF)
        except Exception:
            pass
    tobj = _s.get('translate_obj')
    if tobj is not None:
        try:
            tobj.set_style_translate_y(_s.get('translate_prev', 0), 0)
        except Exception:
            pass
    _s['scroll_obj'] = None
    _s['scroll_dy'] = 0
    _s['translate_obj'] = None
    _s['translate_prev'] = 0
    _s['view_adjusted'] = False
