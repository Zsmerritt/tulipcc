# settings.py -- device settings for Tulip, in a touch UI.
#
# Wi-Fi, volume, brightness, REPL font size, set-time, touch calibration and
# firmware upgrade -- all the things that otherwise need REPL commands.

import tulip
import amy
import deckui as dk
import deckcfg
import lvgl as lv


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

    # --- System actions ---
    r = dk.row(body)
    dk.label(r, "System", color=dk.TEXT)
    g = dk.hgroup(r, w=440, h=48)
    dk.button(g, "Set time", w=130, h=48, bg=dk.SURFACE2, font=dk.FONT_S,
        cb=lambda e: (tulip.set_time(), dk.toast(screen, "Time set")) if tulip.ip() else dk.toast(screen, "Need Wi-Fi", dk.RED))
    dk.button(g, "Calibrate", w=140, h=48, bg=dk.SURFACE2, font=dk.FONT_S,
        cb=lambda e: tulip.run('calibrate'))
    dk.button(g, "Upgrade", w=140, h=48, bg=dk.PURPLE, font=dk.FONT_S,
        cb=lambda e: _upgrade(screen))

    screen.handle_keyboard = True
    screen.present()


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
