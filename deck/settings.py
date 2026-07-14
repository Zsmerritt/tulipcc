# settings.py -- device settings for Tulip, in a touch UI.
#
# Wi-Fi, volume, brightness, REPL font size, set-time, touch calibration and
# firmware upgrade -- all the things that otherwise need REPL commands.

import tulip
import amy
import deckui as dk
import deckcfg
import shellmodel as sm
import lvgl as lv


def _screensaver_cb(key):
    def cb(e):
        idx = e.get_target_obj().get_selected()
        deckcfg.set(key, sm.screensaver_seconds(idx))
        try:
            import screensaver
            screensaver.reload()
        except Exception:
            pass
    return cb


def _mpe_switch(v):
    deckcfg.set('mpe_enabled', v)
    deckcfg.apply_all()          # re-run the router so the gate takes effect now


def _volume_cb(e):
    v = e.get_target_obj().get_value()
    amy.volume(v)
    deckcfg.set('volume', v)


def _bright_cb(e):
    v = e.get_target_obj().get_value()
    tulip.brightness(v)
    deckcfg.set('brightness', v)


def _make_font_cb(n):
    def cb(e):
        tulip.tfb_font(n)
        deckcfg.set('tfb_font', n)
    return cb


def run(screen):
    dk.frame(screen, "Settings", "device configuration")
    body = dk.scroll_body(screen)
    cfg = deckcfg.load()

    # --- Wi-Fi ---
    wcard = lv.obj(body)
    wcard.set_width(lv.pct(100))
    wcard.set_height(196)
    dk._flat(wcard, radius=16, bg=dk.SURFACE)
    wcard.set_style_pad_all(18, 0)
    dk.label(wcard, "Wi-Fi", 0, 0, color=dk.WHITE, font=dk.FONT_M)
    ip = tulip.ip()
    status = ("connected  " + ip) if ip else "not connected"
    status_lbl = dk.label(wcard, status, 0, 34, color=(dk.GREEN if ip else dk.MUTED), font=dk.FONT_S)

    def _field(text, placeholder, y):
        t = tulip.UIText(text=text, placeholder=placeholder,
            w=300, h=44, bg_color=dk.SURFACE2, fg_color=dk.TEXT, font=dk.FONT_S)
        t.group.set_parent(wcard)
        t.group.set_size(300, 44)
        t.group.set_style_bg_opa(lv.OPA.TRANSP, 0)
        t.group.set_pos(0, y)
        return t
    ssid = _field(cfg.get('wifi_ssid', ''), "network name", 66)
    pw = _field(cfg.get('wifi_pass', ''), "password", 118)

    def connect_cb(e):
        s = ssid.ta.get_text()
        p = pw.ta.get_text()
        deckcfg.set('wifi_ssid', s)
        deckcfg.set('wifi_pass', p)
        status_lbl.set_text("connecting...")
        status_lbl.set_style_text_color(dk.c(dk.MUTED), 0)

        def do_connect(x):
            try:
                got = tulip.wifi(s, p)
            except Exception:
                got = None
            if tulip.ip():
                status_lbl.set_text("connected  " + tulip.ip())
                status_lbl.set_style_text_color(dk.c(dk.GREEN), 0)
                dk.toast(screen, "Wi-Fi connected")
            else:
                status_lbl.set_text("connection failed")
                status_lbl.set_style_text_color(dk.c(dk.RED), 0)
        tulip.defer(do_connect, 0, 100)

    dk.button(wcard, "Connect", w=150, h=44, bg=dk.ACCENT, cb=connect_cb).align(lv.ALIGN.TOP_RIGHT, 0, 66)
    dk.button(wcard, tulip.lv.SYMBOL.KEYBOARD, w=64, h=44, bg=dk.SURFACE2,
        cb=lambda e: tulip.keyboard()).align(lv.ALIGN.TOP_RIGHT, 0, 118)

    # --- Volume ---
    r = dk.row(body)
    dk.label(r, "Volume", color=dk.TEXT)
    dk.slider(r, cfg.get('volume', 4), 0, 11, w=360, cb=_volume_cb, color=dk.GREEN)

    # --- Brightness ---
    r = dk.row(body)
    dk.label(r, "Brightness", color=dk.TEXT)
    dk.slider(r, cfg.get('brightness', 5), 1, 9, w=360, cb=_bright_cb, color=dk.ORANGE)

    # --- REPL font size ---
    r = dk.row(body)
    dk.label(r, "Terminal font", color=dk.TEXT)
    g = dk.hgroup(r, w=352, h=48)
    dk.button(g, "Small", w=104, h=48, bg=dk.SURFACE2, font=dk.FONT_S, cb=_make_font_cb(1))
    dk.button(g, "Medium", w=112, h=48, bg=dk.SURFACE2, font=dk.FONT_S, cb=_make_font_cb(0))
    dk.button(g, "Large", w=104, h=48, bg=dk.SURFACE2, font=dk.FONT_S, cb=_make_font_cb(2))

    # --- Menu / task-bar button size (drives ui_patch) ---
    r = dk.row(body)
    dk.label(r, "Menu button size", color=dk.TEXT)
    dk.slider(r, cfg.get('ui_btn', 60), 40, 104, w=360, cb=_uiscale_cb, color=dk.TEAL)

    # --- Rendering (smoother UI) ---
    r = dk.row(body, h=92)
    col = lv.obj(r)
    col.set_size(500, 60)
    col.set_style_border_width(0, 0)
    col.set_style_bg_opa(lv.OPA.TRANSP, 0)
    col.remove_flag(lv.obj.FLAG.SCROLLABLE)
    col.set_flex_flow(lv.FLEX_FLOW.COLUMN)
    col.set_flex_align(lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.START, lv.FLEX_ALIGN.START)
    dk.label(col, "Smooth UI (partial buffer)", color=dk.TEXT, font=dk.FONT_M)
    dk.label(col, "cleaner touch updates; small memory cost", color=dk.MUTED, font=dk.FONT_S)
    dk.switch(r, bool(cfg.get('render_partial')),
              _render_switch('render_partial', _apply_partial))

    r = dk.row(body)
    dk.label(r, "V-sync (tear-free)", color=dk.TEXT)
    dk.switch(r, bool(cfg.get('render_vsync', True)),
              _render_switch('render_vsync', _apply_vsync))

    # --- Screensaver (dim / sleep after idle) ---
    opts = sm.screensaver_options_str()
    for key, title in (('dim_after', "Dim after"), ('sleep_after', "Sleep after")):
        r = dk.row(body)
        dk.label(r, title, color=dk.TEXT)
        dd = lv.dropdown(r)
        dd.set_options(opts)
        dd.set_selected(sm.screensaver_index(cfg.get(key, 0)))
        dd.set_width(200)
        dd.add_event_cb(_screensaver_cb(key), lv.EVENT.VALUE_CHANGED, None)

    # --- MPE (global gate; off by default, hides all MPE UI when off) ---
    r = dk.row(body)
    dk.label(r, "MPE", color=dk.TEXT)
    dk.switch(r, bool(cfg.get('mpe_enabled')), _mpe_switch)

    # --- System actions ---
    r = dk.row(body)
    dk.label(r, "System", color=dk.TEXT)
    g = dk.hgroup(r, w=440, h=48)
    dk.button(g, "Set time", w=130, h=48, bg=dk.SURFACE2, font=dk.FONT_S,
        cb=lambda e: (tulip.set_time(), dk.toast(screen, "Time set")) if tulip.ip() else dk.toast(screen, "Need Wi-Fi", dk.RED))
    dk.button(g, "Calibrate", w=140, h=48, bg=dk.SURFACE2, font=dk.FONT_S,
        cb=lambda e: tulip.run('calib'))
    dk.button(g, "Upgrade", w=140, h=48, bg=dk.PURPLE, font=dk.FONT_S,
        cb=lambda e: _upgrade(screen))

    screen.handle_keyboard = True
    screen.present()


def _apply_partial(v):
    try:
        tulip.display_partial(1 if v else 0)
    except Exception:
        pass


def _apply_vsync(v):
    try:
        tulip.display_vsync(1 if v else 0)
    except Exception:
        pass


def _render_switch(key, apply_fn):
    def on_change(v):
        deckcfg.set(key, v)
        apply_fn(v)   # live
    return on_change


def _uiscale_cb(e):
    v = e.get_target_obj().get_value()
    deckcfg.set('ui_btn', v)
    try:
        import ui_patch
        ui_patch.set_scale(v)   # live-resize the task-bar + menu buttons now
    except Exception:
        pass


def _upgrade(screen):
    if tulip.ip() is None:
        dk.toast(screen, "Need Wi-Fi to upgrade", dk.RED)
        return
    dk.toast(screen, "Switch to Terminal for upgrade prompts", dk.PURPLE)
    tulip.defer(lambda x: tulip.upgrade(), 0, 400)
