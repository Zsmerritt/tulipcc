# settings.py -- device settings for Tulip, in a touch UI.
#
# Wi-Fi, volume, brightness, REPL font size, set-time, touch calibration and
# firmware upgrade -- all the things that otherwise need REPL commands.

import tulip
import amy
import deckui as dk
import deckcfg
import kbmgr
import shellmodel as sm
import lvgl as lv


def _sym(name, fallback):
    return getattr(lv.SYMBOL, name, fallback) if hasattr(lv, 'SYMBOL') else fallback


def _amy_volume(v):
    """Set AMY's global volume across firmware generations: older builds expose
    amy.volume(), the current pinned amy only amy.send(volume=). NEVER reference
    amy.volume as an attribute at panel-BUILD time -- that AttributeError killed
    the whole Settings screen (UX-REVIEW-6 C1)."""
    vol = getattr(amy, 'volume', None)
    try:
        # callable() not is-None (E-14): if upstream amy ever grows a
        # module-level volume VALUE, vol(v) would raise here -- swallowed --
        # and volume would silently stop applying.
        if callable(vol):
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
    # standalone (legacy launcher path): the same group builders stacked in
    # one scroll column -- zero duplicate control code, no tabview here.
    w, H = tulip.screen_size()
    body = dk.scroll_col(screen.group, w - 24, H - 118 - 8)
    body.set_pos(12, 118)
    cfg = deckcfg.load()
    _val_slider(body, 'volume', "Volume", cfg.get('volume', 4), 0, 11,
                live=_amy_volume,
                commit=lambda v: deckcfg.set_value('volume', v),
                color=dk.TEAL, slider_w=w - 320)
    _val_slider(body, 'brightness', "Brightness", cfg.get('brightness', 5),
                1, 9, live=tulip.brightness,
                commit=lambda v: (deckcfg.set_value('brightness', v),
                                  _reload_saver()),
                color=dk.TEAL, slider_w=w - 320)
    _build_wifi(body, screen)
    _build_time(body, screen)
    _build_display(body)
    _build_system(body, screen)
    screen.handle_keyboard = True
    screen.present()


class _TabBuilder:
    """Adapter so parameditor.build_tabbed can fill tabs from plain builder
    functions instead of ParamEditor defs (SETTINGS-TABS.md)."""
    def __init__(self, fn):
        self.fn = fn
        self.group_headers = False

    def build(self, page):
        self.fn(page)


def panel(parent, shell=None):
    """Settings as a SHELL PANEL: a PERSISTENT Volume+Brightness strip (the
    two controls a performer touches mid-set are never behind a tab) above a
    3-tab left-rail tabview -- Network / Display / System -- using the FX
    editor's proven build_tabbed pattern (SETTINGS-TABS.md). Tab switches
    repaint only the content page; nothing scrolls."""
    screen = shell.screen if shell is not None else None
    w = tulip.screen_size()[0]
    _build_strip(parent, w)
    import parameditor
    import homeshell
    h = tulip.screen_size()[1] - homeshell.BAR_H
    tv = parameditor.build_tabbed(
        parent,
        [("Network", lambda page, s=screen: (_build_wifi(page, s),
                                             _build_time(page, s))),
         ("Display", lambda page: _build_display(page)),
         ("System", lambda page, s=screen, sh=shell: _build_system(page, s, sh))],
        _TabBuilder, x=8, y=104, w=w - 16, h=h - 112)
    # keyboard left up over a hidden Wi-Fi field on tab switch = the known
    # textarea use-after-free crash family -- close it on every tab change
    try:
        tv.add_event_cb(lambda e: kbmgr.close(),
                        lv.EVENT.VALUE_CHANGED, None)
    except Exception:
        pass


def _build_strip(parent, w):
    strip = lv.obj(parent)
    strip.set_pos(24, 8)
    strip.set_size(w - 48, 88)
    strip.set_style_border_width(0, 0)
    strip.set_style_pad_all(0, 0)
    strip.set_style_bg_opa(lv.OPA.TRANSP, 0)
    strip.remove_flag(lv.obj.FLAG.SCROLLABLE)
    strip.set_flex_flow(lv.FLEX_FLOW.ROW)
    strip.set_style_pad_column(16, 0)
    cfg = deckcfg.load()
    hw = (w - 48 - 16) // 2
    left = _vcol(strip, hw)
    right = _vcol(strip, hw)
    # both TEAL (X-8 direction): orange stays reserved for warning states
    _val_slider(left, 'volume', "Volume", cfg.get('volume', 4), 0, 11,
                live=_amy_volume,
                commit=lambda v: deckcfg.set_value('volume', v),
                color=dk.TEAL, slider_w=hw - 230)
    _val_slider(right, 'brightness', "Brightness", cfg.get('brightness', 5),
                1, 9, live=tulip.brightness,
                commit=lambda v: (deckcfg.set_value('brightness', v),
                                  _reload_saver()),
                color=dk.TEAL, slider_w=hw - 230)


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


def _build_wifi(body, screen):
    """The Wi-Fi card (Network tab). `screen` hosts the toasts."""
    cfg = deckcfg.load()
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
        # full tab width available now -- wider fields, less crowding
        t = dk.text_field(wcard, text=text, placeholder=placeholder, w=420,
                          h=44)
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

    # Eye toggle: reveal ONLY while actively typing a NEW password. The field
    # is never prefilled from the store, so this can never expose a saved
    # credential -- and Connect force-masks again.
    _eye = {'shown': False}

    def _toggle_eye(e, btn):
        _eye['shown'] = not _eye['shown']
        try:
            pw.ta.set_password_mode(not _eye['shown'])
            btn.get_child(0).set_text(
                _sym('EYE_CLOSE', 'hide') if _eye['shown']
                else _sym('EYE_OPEN', 'show'))
        except Exception:
            pass

    def _mask_pw():
        _eye['shown'] = False
        try:
            pw.ta.set_password_mode(True)
            eyebtn.get_child(0).set_text(_sym('EYE_OPEN', 'show'))
        except Exception:
            pass

    def connect_cb(e):
        s = ssid.ta.get_text() or cfg.get('wifi_ssid', '')
        p = pw.ta.get_text() or cfg.get('wifi_pass', '')
        status_lbl.set_text("connecting...")
        status_lbl.set_style_text_color(dk.c(dk.MUTED), 0)

        def do_connect(x):
            try:
                got = tulip.wifi(s, p)
            except Exception:
                got = None
            if tulip.ip():
                # save credentials only once they are PROVEN to work -- a typo
                # must not overwrite a known-good password. Clearing the boxes
                # happens ONLY here: the typed text must not linger once it is
                # a real (stored) credential.
                deckcfg.set_value('wifi_ssid', s)
                deckcfg.set_value('wifi_pass', p)
                try:
                    ssid.ta.set_text('')
                    pw.ta.set_text('')
                    ssid.ta.set_placeholder_text("network name (saved)")
                    pw.ta.set_placeholder_text("password (saved)")
                except Exception:
                    pass
                _mask_pw()
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
                # FAILURE: keep the typed text (typing on the deck is slow --
                # fixing one character beats retyping everything), keep the
                # eye state, save nothing. What's on screen is only ever what
                # the user just typed, never a stored credential.
                status_lbl.set_text("connection failed -- not saved, edit and retry")
                status_lbl.set_style_text_color(dk.c(dk.RED), 0)
        tulip.defer(do_connect, 0, 100)

    dk.button(wcard, "Connect", w=150, h=44, bg=dk.ACCENT, cb=connect_cb).align(lv.ALIGN.TOP_RIGHT, 0, 66)
    eyebtn = dk.button(wcard, _sym('EYE_OPEN', 'show'), w=64, h=44, bg=dk.SURFACE2)
    eyebtn.add_event_cb(
        (lambda b: (lambda e: _toggle_eye(e, b)
                    if e.get_code() == lv.EVENT.CLICKED else None))(eyebtn),
        lv.EVENT.CLICKED, None)
    eyebtn.align(lv.ALIGN.TOP_RIGHT, -74, 118)
    dk.button(wcard, tulip.lv.SYMBOL.KEYBOARD, w=64, h=44, bg=dk.SURFACE2,
        cb=lambda e: kbmgr.toggle(ssid.ta, echo=True)).align(lv.ALIGN.TOP_RIGHT, 0, 118)


def _build_time(body, screen):
    """Time group (Network tab): Set time + clock format -- 'time comes
    from the network', so it lives with Wi-Fi."""
    cfg = deckcfg.load()
    r = dk.row(body, h=60)
    dk.label(r, "Time", color=dk.TEXT)
    dk.button(r, "Set time now", w=170, h=48, bg=dk.SURFACE2, font=dk.FONT_S,
        cb=lambda e: (deckcfg.sync_time(), dk.toast(screen, "Time set"))
        if tulip.ip() else dk.toast(screen, "Need Wi-Fi", dk.RED))

    def _clock_switch(v):
        deckcfg.set_value('clock_24h', v)
        try:
            import home
            home._shell.refresh_status()   # repaint the bar clock now
        except Exception:
            pass
    r = dk.row(body, h=56)
    dk.label(r, "24-hour clock", color=dk.TEXT)
    dk.switch(r, bool(cfg.get('clock_24h', True)), _clock_switch)


def _build_display(body):
    """Display tab: screensaver, render mode, terminal font."""
    cfg = deckcfg.load()

    # --- Screensaver (dim / sleep after idle) ---
    opts = sm.screensaver_options_str()
    for key, title in (('dim_after', "Dim after"), ('sleep_after', "Sleep after")):
        r = dk.row(body, h=60)
        dk.label(r, title, color=dk.TEXT)
        dd = lv.dropdown(r)
        dd.set_options(opts)
        dd.set_selected(sm.screensaver_index(cfg.get(key, 0)))
        dd.set_width(190)
        dk.style_dropdown(dd)
        dd.add_event_cb(_screensaver_cb(key), lv.EVENT.VALUE_CHANGED, None)

    # --- Rendering (smoother UI) ---
    r = dk.row(body, h=88)
    col = lv.obj(r)
    col.set_size(360, 60)
    col.set_style_border_width(0, 0)
    col.set_style_bg_opa(lv.OPA.TRANSP, 0)
    col.remove_flag(lv.obj.FLAG.SCROLLABLE)
    col.set_flex_flow(lv.FLEX_FLOW.COLUMN)
    col.set_flex_align(lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.START, lv.FLEX_ALIGN.START)
    dk.label(col, "Smooth UI (partial buffer)", color=dk.TEXT, font=dk.FONT_M)
    dk.label(col, "cleaner touch; small memory cost", color=dk.MUTED, font=dk.FONT_S)
    dk.switch(r, bool(cfg.get('render_partial')),
              _render_switch('render_partial', _apply_partial))

    r = dk.row(body, h=60)
    dk.label(r, "V-sync (tear-free)", color=dk.TEXT)
    dk.switch(r, bool(cfg.get('render_vsync', True)),
              _render_switch('render_vsync', _apply_vsync))

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


def _switch_row(body, title, sub, value, on_change, h=76):
    """A switch row with a one-line MUTED subtitle (discoverability)."""
    r = dk.row(body, h=h)
    col = lv.obj(r)
    col.set_size(560, 60)
    col.set_style_border_width(0, 0)
    col.set_style_bg_opa(lv.OPA.TRANSP, 0)
    col.remove_flag(lv.obj.FLAG.SCROLLABLE)
    col.set_flex_flow(lv.FLEX_FLOW.COLUMN)
    col.set_flex_align(lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.START,
                       lv.FLEX_ALIGN.START)
    dk.label(col, title, color=dk.TEXT, font=dk.FONT_M)
    dk.label(col, sub, color=dk.MUTED, font=dk.FONT_S)
    dk.switch(r, value, on_change)


def _open_debug_panel(shell, key, title, module_name):
    """cb factory for a debug-tile button: lazily imports `module_name`
    (profiler / logs) ONLY on tap, then pushes its panel() -- or rebuilds
    the top panel in place if the same one is already open (mirrors
    deckui._open_settings / devices.open_fx's rebuild-vs-push choice)."""
    def cb(e):
        # MicroPython has no importlib -- __import__(name) is the portable
        # form and, for a bare top-level module name, returns the module
        # object directly (confirmed on-device; importlib.import_module
        # raised ImportError there and silently swallowed the tap).
        mod = __import__(module_name)
        if sm.open_panel_action(shell.top_key(), key) == 'rebuild':
            shell.rebuild_top(mod.panel, title, key=key)
        else:
            shell.push(mod.panel, title, key=key)
    return cb


def _build_debug_tools(body, shell):
    """Two debug-only tiles: Profiler (live core-load + memory) and Logs
    (live decklog tail). Rows only -- the actual screens are built lazily
    when tapped (see _open_debug_panel)."""
    r = dk.row(body, h=64)
    dk.label(r, "Profiler", color=dk.TEXT)
    dk.button(r, "Open", w=120, h=48, bg=dk.SURFACE2, font=dk.FONT_S,
              cb=_open_debug_panel(shell, 'profiler', "Profiler", 'profiler'))

    r = dk.row(body, h=64)
    dk.label(r, "Logs", color=dk.TEXT)
    dk.button(r, "Open", w=120, h=48, bg=dk.SURFACE2, font=dk.FONT_S,
              cb=_open_debug_panel(shell, 'logs', "Logs", 'logs'))


def _build_system(body, screen, shell=None):
    """System tab: MPE gate, Debug, touch, firmware, power."""
    cfg = deckcfg.load()

    _switch_row(body, "MPE", "per-note expression; shows MPE controls "
                "on instruments", bool(cfg.get('mpe_enabled')), _mpe_switch)

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
            # re-arm the clock at the new cadence (debug repaints every 2s
            # for a live RAM readout; the subscription is otherwise made
            # once at 30s -- review F-18)
            home._shell._clock_running = False
            home._shell._start_clock()
        except Exception:
            pass
    _switch_row(body, "Debug", "status-bar RAM readout + verbose log",
                bool(cfg.get('debug', False)), _debug_switch)

    # Profiler / Logs tiles: debug-mode ONLY (mirror the status-bar debug
    # readout's gate), and only when there's a shell to push a panel onto --
    # the standalone launcher path (tulip.run('settings'), no HomeShell) has
    # nowhere to push these, so they'd be dead taps there. Both screens are
    # imported lazily, on tap, inside _open_debug_panel -- NOT here -- so
    # Debug mode adds zero build cost to Settings itself (REVIEW-UI).
    if shell is not None and bool(cfg.get('debug', False)):
        _build_debug_tools(body, shell)

    r = dk.row(body, h=64)
    dk.label(r, "Touch", color=dk.TEXT)
    dk.button(r, "Calibrate", w=150, h=48, bg=dk.SURFACE2, font=dk.FONT_S,
        cb=lambda e: tulip.run('calib'))

    r = dk.row(body, h=64)
    dk.label(r, "Firmware", color=dk.TEXT)
    fwg = dk.hgroup(r, w=420, h=48)
    # (stays SURFACE2 per X-5 -- the rarest, riskiest action shouldn't be
    # the loudest element in Settings)
    dk.button(fwg, "Upgrade", w=150, h=48, bg=dk.SURFACE2, font=dk.FONT_S,
        cb=lambda e: _upgrade(screen))
    # Ping-pong "safe update": reboot into the 80MHz flasher so the next image
    # is written at a thermally safe flash clock (see deck/PINGPONG.md). Two-tap
    # armed like Factory reset because it reboots the deck; the host tool
    # (flash_pingpong.py) then streams the new play image. Flash mode
    # auto-recovers to play if no host shows up, so a mis-tap can't strand the
    # deck.
    dk.button(fwg, "Safe update", w=170, h=48, bg=dk.SURFACE2, font=dk.FONT_S,
        cb=(lambda st={'armed': False}: (lambda e: _safe_update(screen, st)))())

    # Power: Restart (benign, one tap) vs Factory reset (destructive, LAST
    # row, red treatment, two-tap arm -- nothing sits below it).
    r2 = dk.row(body, h=64)
    dk.label(r2, "Power", color=dk.TEXT)
    g2 = dk.hgroup(r2, w=420, h=48)

    def _restart(e):
        try:
            import machine
            machine.reset()
        except Exception:
            pass
    dk.button(g2, "Restart", w=130, h=48, bg=dk.SURFACE2, font=dk.FONT_S,
              cb=_restart)

    _frstate = {'armed': False}

    def _freset(e, btn):
        if not _frstate['armed']:
            _frstate['armed'] = True
            btn.set_style_bg_color(dk.c(dk.RED), 0)
            btn.set_style_text_color(dk.c(dk.WHITE), 0)   # (fg rests RED now)
            dk.toast(screen, "Tap again: ERASES all settings + reboots",
                     dk.RED)
            return
        try:
            import os
            os.remove(deckcfg.PATH)
        except OSError:
            pass
        try:
            import machine
            machine.reset()
        except Exception:
            pass
    # Destructive signal at rest (X-5): red text + red border distinguish it
    # from the benign Restart; the two-tap arm still turns it solid red.
    frbtn = dk.button(g2, "Factory reset", w=190, h=48, bg=dk.SURFACE2,
                      fg=dk.RED, font=dk.FONT_S)
    try:
        frbtn.set_style_border_width(2, 0)
        frbtn.set_style_border_color(dk.c(dk.RED), 0)
    except Exception:
        pass
    frbtn.add_event_cb(
        (lambda b: (lambda e: _freset(e, b)
                    if e.get_code() == lv.EVENT.CLICKED else None))(frbtn),
        lv.EVENT.CLICKED, None)


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


def _safe_update(screen, st):
    """Two-tap: arm the ping-pong update and reboot into the 80MHz flasher.

    First tap warns; second tap calls flashmode.arm_and_reboot() (sets the NVS
    flag, set_boot(flasher), reset). Fully guarded -- if flashmode/NVS isn't
    available the deck is NOT rebooted, so nothing can be stranded.
    """
    if not st.get('armed'):
        st['armed'] = True
        dk.toast(screen, "Tap again: reboots into safe-update mode",
                 dk.PURPLE)
        return
    try:
        import flashmode
        # arm first (no reset yet) so a failure is visible instead of a reboot
        # into nowhere
        if not flashmode.request_update():
            dk.toast(screen, "Safe update unavailable on this build", dk.RED)
            st['armed'] = False
            return
        dk.toast(screen, "Rebooting into safe-update mode...", dk.PURPLE)
        tulip.defer(lambda x: flashmode._reset(), 0, 600)
    except Exception:
        dk.toast(screen, "Safe update unavailable on this build", dk.RED)
        st['armed'] = False
