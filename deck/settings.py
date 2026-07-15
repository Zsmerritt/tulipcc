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


def _amy_volume(v):
    """Set AMY's global volume across firmware generations: older builds expose
    amy.volume(), the current pinned amy only amy.send(volume=). NEVER reference
    amy.volume as an attribute at panel-BUILD time -- that AttributeError killed
    the whole Settings screen (UX-REVIEW-6 C1)."""
    vol = getattr(amy, 'volume', None)
    try:
        if vol is not None:
            vol(v)
        else:
            amy.send(volume=v)
    except Exception:
        pass


def _reload_saver():
    # The screensaver caches brightness/thresholds (it no longer polls the
    # config file); tell it whenever a value it depends on changes.
    try:
        import screensaver
        screensaver.reload()
    except Exception:
        pass


def _screensaver_cb(key):
    def cb(e):
        idx = e.get_target_obj().get_selected()
        deckcfg.set_value(key, sm.screensaver_seconds(idx))
        _reload_saver()
    return cb


def _mpe_switch(v):
    deckcfg.set_value('mpe_enabled', v)
    deckcfg.apply_all()          # re-run the router so the gate takes effect now


def run(screen):
    # A build failure must be VISIBLE: standalone apps that throw in run() get
    # torn down and silently bounce back to the previous screen -- Settings was
    # unreachable for a whole round with no error shown (UX-REVIEW-6 C1).
    try:
        _run(screen)
    except Exception as e:
        try:
            import decklog
            decklog.log_exc("settings build failed", e)
        except Exception:
            pass
        dk.label(screen.group, "Settings failed to build: %r" % e, 24, 140,
                 color=dk.RED, font=dk.FONT_S, w=tulip.screen_size()[0] - 48)
        screen.present()


def _run(screen):
    dk.frame(screen, "Settings", "device configuration")
    # standalone (legacy launcher path) has a taller header, so the columns
    # may need a little scroll -- the shell panel doesn't scroll at all
    w, H = tulip.screen_size()
    body = dk.scroll_col(screen.group, w - 24, H - 118 - 8)
    body.set_pos(12, 118)
    _build_cols(body, screen, w, positioned=False)
    screen.handle_keyboard = True
    screen.present()


def panel(parent, shell=None):
    """Settings as a SHELL PANEL (S3) in TWO FIXED COLUMNS with NO scroll
    container: in DIRECT mode, scrolling repaints the whole scrolled area
    every frame, and Settings was the deck's longest scroller -- the reported
    'full redraws while scrolling'. Two columns fit everything on one screen,
    so there is nothing to scroll (and nothing to repaint)."""
    _build_cols(parent, shell.screen if shell is not None else None,
                tulip.screen_size()[0], positioned=True)


def _vcol(parent, cw, gap=8):
    col = lv.obj(parent)
    col.set_width(cw)
    col.set_height(lv.SIZE_CONTENT)
    col.set_style_border_width(0, 0)
    col.set_style_pad_all(0, 0)
    col.set_style_bg_opa(lv.OPA.TRANSP, 0)
    col.remove_flag(lv.obj.FLAG.SCROLLABLE)
    col.set_flex_flow(lv.FLEX_FLOW.COLUMN)
    col.set_style_pad_row(gap, 0)
    return col


def _build_cols(parent, screen, w, positioned):
    cols = lv.obj(parent)
    if positioned:
        cols.set_pos(24, 8)
        cols.set_size(w - 48, lv.SIZE_CONTENT)
    else:
        cols.set_width(lv.pct(100))
        cols.set_height(lv.SIZE_CONTENT)
    cols.set_style_border_width(0, 0)
    cols.set_style_pad_all(0, 0)
    cols.set_style_bg_opa(lv.OPA.TRANSP, 0)
    cols.remove_flag(lv.obj.FLAG.SCROLLABLE)
    cols.set_flex_flow(lv.FLEX_FLOW.ROW)
    cols.set_style_pad_column(16, 0)
    cw = (w - 48 - 16) // 2
    left = _vcol(cols, cw)
    right = _vcol(cols, cw)
    _build(left, right, cw, screen)


def _build(body, right, cw, screen):
    # `body` = left column, `right` = right column (identity/audio left,
    # display/system right). `screen` hosts the toasts.
    cfg = deckcfg.load()

    # --- Wi-Fi ---
    wcard = lv.obj(body)
    wcard.set_width(lv.pct(100))
    wcard.set_height(214)     # was 196 -- the password field grazed the bottom
    dk._flat(wcard, radius=16, bg=dk.SURFACE)
    wcard.set_style_pad_all(18, 0)
    dk.label(wcard, "Wi-Fi", 0, 0, color=dk.WHITE, font=dk.FONT_M)
    ip = tulip.ip()
    status = ("connected  " + ip) if ip else "not connected"
    status_lbl = dk.label(wcard, status, 0, 34, color=(dk.GREEN if ip else dk.MUTED), font=dk.FONT_S)

    def _field(text, placeholder, y):
        t = dk.text_field(wcard, text=text, placeholder=placeholder, w=300, h=44)
        t.group.set_pos(0, y)
        return t
    # NEVER prefill from the store: saved credentials must not render as
    # plain text (before or after reboot). Placeholders say '(saved)' and
    # Connect with empty fields reuses the stored values.
    saved = bool(cfg.get('wifi_ssid'))
    ssid = _field('', "network name (saved)" if saved else "network name", 66)
    pw = _field('', "password (saved)" if saved else "password", 118)
    try:
        pw.ta.set_password_mode(True)      # bullets while typing, too
    except Exception:
        pass

    def connect_cb(e):
        s = ssid.ta.get_text() or cfg.get('wifi_ssid', '')
        p = pw.ta.get_text() or cfg.get('wifi_pass', '')
        deckcfg.set_value('wifi_ssid', s)
        deckcfg.set_value('wifi_pass', p)
        try:
            # typed values must not linger on screen either
            ssid.ta.set_text('')
            pw.ta.set_text('')
            ssid.ta.set_placeholder_text("network name (saved)")
            pw.ta.set_placeholder_text("password (saved)")
        except Exception:
            pass
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
                try:
                    deckcfg.sync_time()   # NTP + localize: clock works now
                except Exception:
                    pass
                try:
                    # the top bar polls only every 30s -- poke it so 'offline'
                    # flips immediately
                    import home
                    home._shell.refresh_status()
                except Exception:
                    pass
            else:
                status_lbl.set_text("connection failed")
                status_lbl.set_style_text_color(dk.c(dk.RED), 0)
        tulip.defer(do_connect, 0, 100)

    dk.button(wcard, "Connect", w=150, h=44, bg=dk.ACCENT, cb=connect_cb).align(lv.ALIGN.TOP_RIGHT, 0, 66)
    dk.button(wcard, tulip.lv.SYMBOL.KEYBOARD, w=64, h=44, bg=dk.SURFACE2,
        cb=lambda e: dk.toggle_keyboard_for(ssid.ta)).align(lv.ALIGN.TOP_RIGHT, 0, 118)

    # --- Volume ---
    _val_slider(body, 'volume', "Volume", cfg.get('volume', 4), 0, 11,
                live=_amy_volume,
                commit=lambda v: deckcfg.set_value('volume', v),
                color=dk.GREEN, slider_w=cw - 230)

    # --- Brightness ---
    _val_slider(body, 'brightness', "Brightness", cfg.get('brightness', 5), 1, 9,
                live=tulip.brightness,
                commit=lambda v: (deckcfg.set_value('brightness', v),
                                  _reload_saver()),
                color=dk.ORANGE, slider_w=cw - 230)

    # --- Clock format ---
    def _clock_switch(v):
        deckcfg.set_value('clock_24h', v)
        try:
            import home
            home._shell.refresh_status()   # repaint the bar clock now
        except Exception:
            pass
    def _debug_switch(v):
        deckcfg.set_value('debug', v)
        try:
            import decklog
            decklog.set_debug(v)
        except Exception:
            pass
        try:
            import home
            home._shell.refresh_status()   # show/hide the bar readout now
        except Exception:
            pass

    # clock format + debug mode share one row (the no-scroll columns are full)
    r = dk.row(body, h=56)
    dk.label(r, "24-hour clock", color=dk.TEXT)
    dk.switch(r, bool(cfg.get('clock_24h', True)), _clock_switch)
    dk.label(r, "Debug", color=dk.TEXT)
    dk.switch(r, bool(cfg.get('debug', False)), _debug_switch)

    # --- REPL font size (active size highlighted) ---
    r = dk.row(body, h=56)
    dk.label(r, "Terminal font", color=dk.TEXT)
    g = dk.hgroup(r, w=300, h=48)
    cur_font = cfg.get('tfb_font', 0)
    _fontbtns = []

    def _paint_fonts(active):
        for b, code in _fontbtns:
            b.set_style_bg_color(dk.c(dk.ACCENT if code == active else dk.SURFACE2), 0)

    def _font_cb(n):
        def cb(e):
            tulip.tfb_font(n)
            deckcfg.set_value('tfb_font', n)
            _paint_fonts(n)
        return cb
    for lbl, code, bw in (("Small", 1, 92), ("Medium", 0, 100), ("Large", 2, 92)):
        b = dk.button(g, lbl, w=bw, h=48, font=dk.FONT_S,
                      bg=(dk.ACCENT if code == cur_font else dk.SURFACE2),
                      cb=_font_cb(code))
        _fontbtns.append((b, code))

    # ================= RIGHT column =================
    # --- Menu / task-bar button size (drives ui_patch) ---
    _val_slider(right, 'ui_btn', "Menu button size", cfg.get('ui_btn', 60), 40, 104,
                live=_uiscale_live,
                commit=lambda v: deckcfg.set_value('ui_btn', v),
                color=dk.TEAL, slider_w=cw - 250)

    # --- Rendering (smoother UI) ---
    r = dk.row(right, h=88)
    col = lv.obj(r)
    col.set_size(cw - 150, 60)
    col.set_style_border_width(0, 0)
    col.set_style_bg_opa(lv.OPA.TRANSP, 0)
    col.remove_flag(lv.obj.FLAG.SCROLLABLE)
    col.set_flex_flow(lv.FLEX_FLOW.COLUMN)
    col.set_flex_align(lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.START, lv.FLEX_ALIGN.START)
    dk.label(col, "Smooth UI (partial buffer)", color=dk.TEXT, font=dk.FONT_M)
    dk.label(col, "cleaner touch; small memory cost", color=dk.MUTED, font=dk.FONT_S)
    dk.switch(r, bool(cfg.get('render_partial')),
              _render_switch('render_partial', _apply_partial))

    r = dk.row(right, h=60)
    dk.label(r, "V-sync (tear-free)", color=dk.TEXT)
    dk.switch(r, bool(cfg.get('render_vsync', True)),
              _render_switch('render_vsync', _apply_vsync))

    # --- Screensaver (dim / sleep after idle) ---
    opts = sm.screensaver_options_str()
    for key, title in (('dim_after', "Dim after"), ('sleep_after', "Sleep after")):
        r = dk.row(right, h=60)
        dk.label(r, title, color=dk.TEXT)
        dd = lv.dropdown(r)
        dd.set_options(opts)
        dd.set_selected(sm.screensaver_index(cfg.get(key, 0)))
        dd.set_width(190)
        try:
            # near the screen bottom: opening DOWN clipped the last options
            dd.set_dir(lv.DIR.TOP)
        except Exception:
            pass
        dk.style_dropdown(dd)
        dd.add_event_cb(_screensaver_cb(key), lv.EVENT.VALUE_CHANGED, None)

    # --- MPE (global gate; off by default, hides all MPE UI when off) ---
    r = dk.row(right, h=60)
    dk.label(r, "MPE", color=dk.TEXT)
    dk.switch(r, bool(cfg.get('mpe_enabled')), _mpe_switch)

    # --- System actions ---
    r = dk.row(right, h=64)
    dk.label(r, "System", color=dk.TEXT)
    g = dk.hgroup(r, w=cw - 110, h=48)
    dk.button(g, "Set time", w=112, h=48, bg=dk.SURFACE2, font=dk.FONT_S,
        cb=lambda e: (deckcfg.sync_time(), dk.toast(screen, "Time set")) if tulip.ip() else dk.toast(screen, "Need Wi-Fi", dk.RED))
    dk.button(g, "Calibrate", w=118, h=48, bg=dk.SURFACE2, font=dk.FONT_S,
        cb=lambda e: tulip.run('calib'))
    dk.button(g, "Upgrade", w=112, h=48, bg=dk.PURPLE, font=dk.FONT_S,
        cb=lambda e: _upgrade(screen))


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
        deckcfg.set_value(key, v)
        apply_fn(v)   # live
    return on_change


def _uiscale_live(v):
    try:
        import ui_patch
        ui_patch.set_scale(v)   # live-resize the task-bar + menu buttons now
    except Exception:
        pass


_sv = {}   # settings-slider value labels, by key


def _val_slider(body, key, name, value, lo, hi, live, commit, color, fmt="%d",
                slider_w=250):
    """A slider row with a LIVE value readout (title + value stacked left, fat
    slider right) -- so Volume/Brightness/Menu-size aren't set blind.

    `live(v)` runs per drag tick (cheap hardware apply: amy.volume, backlight);
    `commit(v)` runs once on release (config write -- one flash write per drag,
    not one per tick)."""
    r = dk.row(body, h=88)
    col = lv.obj(r)
    col.set_size(190, 60)
    col.set_style_border_width(0, 0)
    col.set_style_pad_all(0, 0)
    col.set_style_bg_opa(lv.OPA.TRANSP, 0)
    col.remove_flag(lv.obj.FLAG.SCROLLABLE)
    col.set_flex_flow(lv.FLEX_FLOW.COLUMN)
    col.set_style_pad_row(4, 0)
    col.set_flex_align(lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.START,
                       lv.FLEX_ALIGN.START)
    dk.label(col, name, color=dk.TEXT, font=dk.FONT_M)
    _sv[key] = dk.label(col, fmt % value, color=dk.TEAL, font=dk.FONT_MONO)

    def cb(e):
        v = e.get_target_obj().get_value()
        try:
            _sv[key].set_text(fmt % v)
        except Exception:
            pass
        live(v)

    def done(e):
        commit(e.get_target_obj().get_value())
    dk.slider(r, value, lo, hi, w=slider_w, cb=cb, color=color, on_release=done)


def _upgrade(screen):
    if tulip.ip() is None:
        dk.toast(screen, "Need Wi-Fi to upgrade", dk.RED)
        return
    dk.toast(screen, "Switch to Terminal for upgrade prompts", dk.PURPLE)
    tulip.defer(lambda x: tulip.upgrade(), 0, 400)
